"""Evaluation suite for micro-agents.

    python evals/run_evals.py --replay          # no network or keys: what CI runs on every change
    python evals/run_evals.py                   # live: real APIs, real models (costs a little money)
    python evals/run_evals.py --record          # live, and refresh the recorded outputs in evals/golden/
    python evals/run_evals.py --update-baseline # live, and save this run as the baseline to compare against

Cases live in agents/<agent>/evals.jsonl (see evals/cases.py) and assert on content via evals/checks.py.

Modes
    live    every case runs the real agent. Model spend is capped by --max-model-calls.
    replay  cases marked "offline" (validation and error paths) run the real agent code with all network
            access blocked; the others are checked against the output recorded in evals/golden/. This
            catches drift between what agents return and what the checks expect, with no secrets.

Exit codes
    0  every agent met the pass-rate bar and nothing regressed against the baseline
    1  a quality failure: an agent is below --min-pass-rate, or a case that passed in the baseline now fails
    2  infrastructure (rate limits, outages, budget) stopped cases from running, and no quality failure was seen
    3  the suite could not run: invalid case files, or missing keys for a live run
"""

import argparse
import json
import logging
import os
import re
import socket
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals.adapters import call_agent
from evals.cases import AGENTS, ROOT, Case, CaseError, golden_path, load_all
from evals.checks import REGISTRY, error_text, is_error, run_check

BASELINE_PATH = ROOT / "evals" / "baseline.json"

# An error message that means the environment stopped the agent, not that the agent was wrong. Deliberately
# narrow: "Failed to parse ..." is the agent's fault and must count as a failure.
INFRA_RE = re.compile(
    r"rate limit|GitHub API error|Could not reach|Could not retrieve|backend unavailable|service unavailable"
    r"|usage limit|at capacity|reached its limit of \d+ model calls|No search results were returned",
    re.IGNORECASE,
)

PASS, FAIL, ERROR = "pass", "fail", "error"


@dataclass
class CaseResult:
    agent: str
    id: str
    status: str  # pass | fail | error (infrastructure)
    failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    attempts: int = 1
    seconds: float = 0.0
    output: Any = field(default=None, repr=False)
    offline: bool = False

    @property
    def key(self) -> str:
        return f"{self.agent}/{self.id}"


# --- running one case ---------------------------------------------------------------------------------

@contextmanager
def no_network():
    """Make any socket connection fail loudly, and record that it was attempted."""
    attempted = {"count": 0}
    real_connect, real_create = socket.socket.connect, socket.create_connection

    def blocked(*args, **kwargs):
        attempted["count"] += 1
        raise OSError("network access is blocked in offline eval mode")

    socket.socket.connect, socket.create_connection = blocked, blocked
    try:
        yield attempted
    finally:
        socket.socket.connect, socket.create_connection = real_connect, real_create


def infra_problem(case: Case, output: Any) -> Optional[str]:
    """The message, if this output is the environment failing rather than the agent being wrong."""
    if any(spec["type"] == "error" for spec in case.checks):
        return None  # the case expects an error; whatever it is gets judged by the check
    if isinstance(output, list) and output and isinstance(output[0], dict):
        pitch = str(output[0].get("pitch", ""))
        if str(output[0].get("title", "")).lower().startswith("unable to generate") and INFRA_RE.search(pitch):
            return pitch
    if is_error(output) and INFRA_RE.search(error_text(output)):
        return error_text(output)
    return None


def evaluate(case: Case, output: Any, replay: bool) -> tuple[list[str], list[str]]:
    """(failures, skipped): every check run against the output. Network checks are skipped when replaying."""
    failures, skipped = [], []
    for spec in case.checks:
        if replay and REGISTRY[spec["type"]].network:
            skipped.append(spec["type"])
            continue
        outcome = run_check(spec, output)
        if not outcome.ok:
            failures.append(f"{spec['type']}: {outcome.detail}" if outcome.detail else spec["type"])
    return failures, skipped


def _call(case: Case, request_calls: int) -> Any:
    from core.budget import request_scope

    with request_scope(max_calls=request_calls):
        return call_agent(case.agent, case.input)


def run_case(case: Case, mode: str, attempts: Optional[int] = None, request_calls: int = 30) -> CaseResult:
    """Run one case in `mode` ('live' or 'replay') and judge it."""
    replay = mode == "replay"
    started = time.time()
    result = CaseResult(case.agent, case.id, FAIL, offline=case.offline)

    if replay and not case.offline:
        path = golden_path(case.agent, case.id)
        if not path.exists():
            result.failures = [f"no recorded output at {path.relative_to(ROOT)} (record one with: python evals/run_evals.py --record)"]
            return result
        output = json.loads(path.read_text(encoding="utf-8"))["output"]
        failures, skipped = evaluate(case, output, replay=True)
        result.output, result.failures = output, failures
        result.status = FAIL if failures else PASS
        result.notes = [f"replayed; skipped network checks: {skipped}"] if skipped else ["replayed"]
        result.seconds = time.time() - started
        return result

    limit = case.attempts or attempts or 1
    for attempt in range(1, limit + 1):
        result.attempts = attempt
        try:
            if replay:
                with no_network() as network:
                    output = _call(case, request_calls)
                if network["count"]:
                    result.failures = [f"the agent attempted network access {network['count']} time(s) in offline mode"]
                    result.status = FAIL
                    break
            else:
                output = _call(case, request_calls)
        except Exception as e:
            result.failures = [f"agent raised {type(e).__name__}: {str(e)[:160]}"]
            result.status = FAIL
            continue

        result.output = output
        problem = infra_problem(case, output)
        if problem:
            result.status, result.failures = ERROR, [f"infrastructure: {problem[:160]}"]
            break
        failures, _ = evaluate(case, output, replay=replay)
        result.failures = failures
        result.status = FAIL if failures else PASS
        if not failures:
            break

    if result.status == PASS and result.attempts > 1:
        result.notes.append(f"passed on attempt {result.attempts} of {limit} (flaky)")
    result.seconds = time.time() - started
    return result


# --- suite, baseline, exit code -----------------------------------------------------------------------

def agent_summary(results: list[CaseResult]) -> dict[str, dict]:
    summary: dict[str, dict] = {}
    for r in results:
        s = summary.setdefault(r.agent, {PASS: 0, FAIL: 0, ERROR: 0})
        s[r.status] += 1
    for s in summary.values():
        judged = s[PASS] + s[FAIL]
        s["total"] = judged + s[ERROR]
        s["rate"] = s[PASS] / judged if judged else None  # infrastructure errors are not counted against the agent
    return summary


def load_baseline(path: Path) -> Optional[dict[str, str]]:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8")).get("cases", {})


def save_baseline(path: Path, results: list[CaseResult]) -> None:
    cases = {r.key: r.status for r in sorted(results, key=lambda r: r.key) if r.status != ERROR}
    path.write_text(
        json.dumps({"recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "cases": cases}, indent=2) + "\n",
        encoding="utf-8",
    )


def regressions(results: list[CaseResult], baseline: Optional[dict[str, str]]) -> list[str]:
    if not baseline:
        return []
    return sorted(r.key for r in results if r.status == FAIL and baseline.get(r.key) == PASS)


def decide(results: list[CaseResult], min_rate: float, baseline: Optional[dict[str, str]]) -> tuple[int, list[str]]:
    """(exit code, reasons). See the module docstring for what each code means."""
    reasons: list[str] = []
    summary = agent_summary(results)
    low = [f"{a} {s['rate']:.0%} < {min_rate:.0%}" for a, s in summary.items() if s["rate"] is not None and s["rate"] < min_rate]
    reg = regressions(results, baseline)
    if low:
        reasons.append("below the pass-rate bar: " + ", ".join(low))
    if reg:
        reasons.append("regressed since the baseline: " + ", ".join(reg))
    if low or reg:
        return 1, reasons
    unrun = [a for a, s in summary.items() if s["rate"] is None]
    errors = [r.key for r in results if r.status == ERROR]
    if errors or unrun:
        if errors:
            reasons.append(f"{len(errors)} case(s) could not run because of infrastructure problems")
        if unrun:
            reasons.append("no case could be judged for: " + ", ".join(unrun))
        return 2, reasons
    return 0, reasons


# --- reporting ----------------------------------------------------------------------------------------

def render_text(results: list[CaseResult], code: int, reasons: list[str], min_rate: float, verbose: bool,
                baseline: Optional[dict[str, str]], mode: str, model_calls: Optional[int]) -> str:
    out = []
    summary = agent_summary(results)
    out.append("")
    out.append("=" * 78)
    out.append(f"EVAL RESULTS ({mode})")
    out.append("=" * 78)
    out.append(f"{'agent':<20}{'passed':>8}{'failed':>8}{'infra':>7}{'pass rate':>11}")
    out.append("-" * 78)
    for agent in AGENTS:
        if agent not in summary:
            continue
        s = summary[agent]
        rate = "n/a" if s["rate"] is None else f"{s['rate']:.0%}"
        flag = "" if s["rate"] is None or s["rate"] >= min_rate else "   <-- below bar"
        out.append(f"{agent:<20}{s[PASS]:>8}{s[FAIL]:>8}{s[ERROR]:>7}{rate:>11}{flag}")
    total = {k: sum(s[k] for s in summary.values()) for k in (PASS, FAIL, ERROR)}
    judged = total[PASS] + total[FAIL]
    out.append("-" * 78)
    out.append(f"{'TOTAL':<20}{total[PASS]:>8}{total[FAIL]:>8}{total[ERROR]:>7}{(total[PASS] / judged if judged else 0):>11.0%}")
    if model_calls is not None:
        out.append(f"model calls used: {model_calls}")

    for r in results:
        if r.status == PASS and not verbose:
            continue
        mark = {PASS: "PASS ", FAIL: "FAIL ", ERROR: "INFRA"}[r.status]
        out.append(f"  {mark} {r.key}  ({r.seconds:.1f}s{f', attempt {r.attempts}' if r.attempts > 1 else ''})")
        for f in r.failures:
            out.append(f"        - {f}")
        if verbose:
            for n in r.notes:
                out.append(f"        . {n}")

    flaky = [r.key for r in results if r.status == PASS and r.attempts > 1]
    if flaky:
        out.append(f"\nflaky (passed only after a retry): {', '.join(flaky)}")
    new = [r.key for r in results if baseline is not None and r.key not in baseline]
    if new:
        out.append(f"not in the baseline yet: {', '.join(new)}")
    out.append("")
    verdict = {0: "OK", 1: "FAILED (quality)", 2: "INCOMPLETE (infrastructure)", 3: "COULD NOT RUN"}[code]
    out.append(f"{verdict}" + ("" if not reasons else ": " + "; ".join(reasons)))
    return "\n".join(out)


def render_markdown(results: list[CaseResult], code: int, reasons: list[str], min_rate: float, mode: str) -> str:
    summary = agent_summary(results)
    lines = [f"## Evals ({mode}): {'passed' if code == 0 else 'not passing'}", "", "| Agent | Passed | Failed | Infra | Pass rate |", "|---|---:|---:|---:|---:|"]
    for agent in AGENTS:
        if agent in summary:
            s = summary[agent]
            lines.append(f"| {agent} | {s[PASS]} | {s[FAIL]} | {s[ERROR]} | {'n/a' if s['rate'] is None else format(s['rate'], '.0%')} |")
    bad = [r for r in results if r.status != PASS]
    if bad:
        lines += ["", "### Not passing", ""]
        for r in bad:
            lines.append(f"- **{r.key}** ({'infrastructure' if r.status == ERROR else 'failed'})")
            lines.extend(f"  - {f}" for f in r.failures)
    if reasons:
        lines += ["", "> " + "; ".join(reasons)]
    return "\n".join(lines) + "\n"


# --- recording ----------------------------------------------------------------------------------------

def record_golden(result: CaseResult, case: Case) -> None:
    path = golden_path(result.agent, result.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "agent": result.agent, "id": result.id, "input": case.input,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "output": result.output,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


# --- command line -------------------------------------------------------------------------------------

def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--replay", action="store_true", help="no network or keys: offline cases run for real, the rest replay recorded outputs")
    p.add_argument("--record", action="store_true", help="live run; save passing outputs to evals/golden/")
    p.add_argument("--update-baseline", action="store_true", help="live run; save this run's pass/fail as the baseline")
    p.add_argument("--agent", action="append", choices=AGENTS, help="only this agent (repeatable)")
    p.add_argument("--only", help="only cases whose id contains this text")
    p.add_argument("--attempts", type=int, help="attempts per case unless the case sets its own (default 1)")
    p.add_argument("--min-pass-rate", type=float, help="per-agent bar (default 0.8 live, 1.0 replay)")
    p.add_argument("--baseline", type=Path, help="baseline file (default evals/baseline.json, live runs only)")
    p.add_argument("--no-baseline", action="store_true", help="don't compare against a baseline")
    p.add_argument("--max-model-calls", type=int, default=250, help="cap on model calls for a live run (default 250)")
    p.add_argument("--json-out", type=Path, help="write a JSON report here")
    p.add_argument("--list", action="store_true", help="list cases and exit")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    args = parse_args(sys.argv[1:] if argv is None else argv)
    mode = "replay" if args.replay else "live"
    logging.disable(logging.INFO)  # the agents log progress at INFO; the report is the output here

    if args.replay and (args.record or args.update_baseline):
        print("--record and --update-baseline need a live run; drop --replay.", file=sys.stderr)
        return 3
    try:
        loaded = load_all(args.agent)
    except CaseError as e:
        print("Invalid eval case files:", file=sys.stderr)
        for problem in e.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 3

    cases = [c for agent in (args.agent or AGENTS) for c in loaded[agent] if not args.only or args.only in c.id]
    if args.list:
        for c in cases:
            print(f"{c.key:50} {'offline' if c.offline else 'network'}  {c.description[:80]}")
        return 0
    if not cases:
        print("No cases selected.", file=sys.stderr)
        return 3

    model_calls: Optional[int] = None
    if mode == "live":
        from dotenv import load_dotenv

        load_dotenv()
        if not os.getenv("OPENAI_API_KEY"):
            print("A live run needs OPENAI_API_KEY (set it, or use --replay).", file=sys.stderr)
            return 3
        if not os.getenv("GITHUB_TOKEN"):
            print("Note: no GITHUB_TOKEN set; GitHub's unauthenticated limit (60 requests/hour) is likely to be hit.", file=sys.stderr)
        from core.budget import Budget, BudgetLimits, set_budget

        n = args.max_model_calls
        set_budget(Budget(BudgetLimits(daily_calls=n, hourly_calls=n, daily_tokens=n * 8000, client_daily_calls=n,
                                       request_calls=30, orchestrator_request_calls=30), persist_path=None))

    min_rate = args.min_pass_rate if args.min_pass_rate is not None else (1.0 if args.replay else 0.8)
    baseline = None
    if mode == "live" and not args.no_baseline:
        baseline = load_baseline(args.baseline or BASELINE_PATH)

    results: list[CaseResult] = []
    by_key = {c.key: c for c in cases}
    for case in cases:
        r = run_case(case, mode, args.attempts)
        results.append(r)
        print(f"  {r.status.upper():5} {r.key} ({r.seconds:.1f}s)", flush=True)
        if args.record and r.status == PASS and not case.offline:
            record_golden(r, case)

    if mode == "live":
        from core.budget import get_budget

        model_calls = get_budget().snapshot()["day_calls"]

    code, reasons = decide(results, min_rate, baseline)
    print(render_text(results, code, reasons, min_rate, args.verbose, baseline, mode, model_calls))

    if args.update_baseline:
        if any(r.status == ERROR for r in results):
            print("Not updating the baseline: some cases hit infrastructure errors.", file=sys.stderr)
        else:
            save_baseline(args.baseline or BASELINE_PATH, results)
            print(f"Baseline saved to {(args.baseline or BASELINE_PATH)}")
    if args.json_out:
        args.json_out.write_text(
            json.dumps({"mode": mode, "exit_code": code, "reasons": reasons, "summary": agent_summary(results),
                        "cases": [{"key": r.key, "status": r.status, "failures": r.failures, "attempts": r.attempts, "seconds": round(r.seconds, 2)} for r in results]},
                       indent=2) + "\n", encoding="utf-8")
    summary_file = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as f:
            f.write(render_markdown(results, code, reasons, min_rate, mode))
    return code


if __name__ == "__main__":
    sys.exit(main())
