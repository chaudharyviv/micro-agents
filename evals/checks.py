"""Assertions for eval cases.

Each check is a named function registered with `@check`. A case lists checks as small dicts,
`{"type": "count", "path": "skill_gaps", "min": 1}`, and every check must pass for the case to pass.

Checks are meant to fail on wrong output, not merely on absent output: they look at content (grounding,
ranking, routing, consistency), and a check name or parameter that doesn't exist is a configuration error
rather than a pass. Each check has a known-good and a known-bad fixture in tests/test_eval_checks.py.
"""

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

MISSING = object()

# Text an agent must never hand to a user as if it were a result.
PLACEHOLDERS = [
    "see full response", "see full analysis", "analysis from llm", "review the detailed analysis",
    "unable to determine", "no suggestions available", "no project idea generated",
]

SENTINEL_TITLE = "unable to generate ideas"


@dataclass
class Outcome:
    ok: bool
    detail: str = ""


@dataclass
class Check:
    name: str
    fn: Callable[[Any, dict], Outcome]
    required: frozenset
    optional: frozenset
    network: bool  # needs the network (e.g. verifies against a live service); skipped when replaying


REGISTRY: dict[str, Check] = {}


def check(name: str, required=(), optional=(), network: bool = False):
    def register(fn):
        REGISTRY[name] = Check(name, fn, frozenset(required), frozenset(optional), network)
        return fn

    return register


def validate_spec(spec: Any) -> list[str]:
    """Problems with a check spec (empty list if it's valid). Called when cases are loaded."""
    if not isinstance(spec, dict) or "type" not in spec:
        return [f"check must be an object with a 'type': {spec!r}"]
    entry = REGISTRY.get(spec["type"])
    if entry is None:
        return [f"unknown check type {spec['type']!r}"]
    params = set(spec) - {"type"}
    problems = []
    if entry.required - params:
        problems.append(f"{spec['type']}: missing {sorted(entry.required - params)}")
    if params - entry.required - entry.optional:
        problems.append(f"{spec['type']}: unknown parameters {sorted(params - entry.required - entry.optional)}")
    return problems


def run_check(spec: dict, output: Any) -> Outcome:
    try:
        return REGISTRY[spec["type"]].fn(output, spec)
    except Exception as e:  # a check that crashes is a failure with a reason, never a pass
        return Outcome(False, f"{spec['type']} raised {type(e).__name__}: {e}")


# --- helpers -----------------------------------------------------------------------------------------

def resolve(output: Any, path: str) -> Any:
    """Value at a dotted path ('' is the output itself), or MISSING."""
    cur = output
    if path == "":
        return cur
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return MISSING
    return cur


def is_nonempty(value: Any) -> bool:
    if value is MISSING or value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) > 0
    return True


def text_of(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, default=str)


def is_error(output: Any) -> bool:
    return isinstance(output, dict) and (bool(output.get("error_message")) or output.get("status") == "error")


def error_text(output: Any) -> str:
    return str(output.get("error_message", "")) if isinstance(output, dict) else ""


def _items(output: Any, path: str):
    value = resolve(output, path)
    return value if isinstance(value, list) else None


# --- generic checks ----------------------------------------------------------------------------------

@check("success")
def _success(output, spec):
    if is_error(output):
        return Outcome(False, f"agent returned an error: {error_text(output)[:160]}")
    if isinstance(output, list) and output and isinstance(output[0], dict) and str(output[0].get("title", "")).lower().startswith(SENTINEL_TITLE):
        return Outcome(False, f"agent returned its failure placeholder: {output[0].get('pitch', '')[:120]}")
    if not isinstance(output, (dict, list)):
        return Outcome(False, f"output is a {type(output).__name__}, expected dict or list")
    return Outcome(True)


@check("error", required=["contains_any"])
def _error(output, spec):
    if not is_error(output):
        return Outcome(False, "expected an error result, got a successful one")
    msg = error_text(output).lower()
    wanted = [w.lower() for w in spec["contains_any"]]
    if not any(w in msg for w in wanted):
        return Outcome(False, f"error message {error_text(output)[:160]!r} mentions none of {wanted}")
    return Outcome(True)


@check("nonempty", required=["paths"])
def _nonempty(output, spec):
    empty = [p for p in spec["paths"] if not is_nonempty(resolve(output, p))]
    return Outcome(not empty, f"missing or empty: {empty}" if empty else "")


@check("one_of", required=["path", "values"])
def _one_of(output, spec):
    value = resolve(output, spec["path"])
    return Outcome(value in spec["values"], f"{spec['path']} is {value!r}, expected one of {spec['values']}")


@check("equals", required=["path", "value"])
def _equals(output, spec):
    value = resolve(output, spec["path"])
    return Outcome(value == spec["value"], f"{spec['path']} is {value!r}, expected {spec['value']!r}")


@check("at_least", required=["path", "value"])
def _at_least(output, spec):
    value = resolve(output, spec["path"])
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and value >= spec["value"]
    return Outcome(ok, f"{spec['path']} is {value!r}, expected at least {spec['value']}")


@check("count", required=["path"], optional=["min", "max"])
def _count(output, spec):
    items = _items(output, spec["path"])
    if items is None:
        return Outcome(False, f"{spec['path'] or 'output'} is not a list")
    lo, hi = spec.get("min", 0), spec.get("max")
    ok = len(items) >= lo and (hi is None or len(items) <= hi)
    return Outcome(ok, f"{spec['path'] or 'output'} has {len(items)} items, expected {lo}..{hi if hi is not None else 'inf'}")


@check("each", required=["path"], optional=["nonempty", "startswith"])
def _each(output, spec):
    items = _items(output, spec["path"])
    if not items:
        return Outcome(False, f"{spec['path'] or 'output'} is not a non-empty list")
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            return Outcome(False, f"item {i} is not an object")
        empty = [f for f in spec.get("nonempty", []) if not is_nonempty(item.get(f))]
        if empty:
            return Outcome(False, f"item {i} has empty {empty}")
        for field, prefixes in spec.get("startswith", {}).items():
            if not str(item.get(field, "")).startswith(tuple(prefixes)):
                return Outcome(False, f"item {i} {field}={item.get(field)!r} does not start with {prefixes}")
    return Outcome(True)


@check("unique", required=["path", "field"])
def _unique(output, spec):
    items = _items(output, spec["path"]) or []
    values = [str(i.get(spec["field"], "")).strip().lower() for i in items if isinstance(i, dict)]
    dupes = sorted({v for v in values if values.count(v) > 1})
    return Outcome(not dupes, f"duplicate {spec['field']} values: {dupes}")


@check("contains_any", required=["path", "values"])
def _contains_any(output, spec):
    haystack = text_of(resolve(output, spec["path"])).lower()
    wanted = [v.lower() for v in spec["values"]]
    found = any(w in haystack for w in wanted)
    return Outcome(found, f"{spec['path']} mentions none of {wanted}")


@check("no_placeholder")
def _no_placeholder(output, spec):
    text = json.dumps(output, default=str).lower()
    hits = [p for p in PLACEHOLDERS if p in text]
    return Outcome(not hits, f"placeholder text in output: {hits}")


@check("word_count", required=["path", "min", "max"])
def _word_count(output, spec):
    value = resolve(output, spec["path"])
    n = len(value.split()) if isinstance(value, str) else -1
    return Outcome(spec["min"] <= n <= spec["max"], f"{spec['path']} has {n} words, expected {spec['min']}..{spec['max']}")


@check("has_sections", required=["path", "sections"])
def _has_sections(output, spec):
    text = text_of(resolve(output, spec["path"])).lower()
    missing = [s for s in spec["sections"] if s.lower() not in text]
    return Outcome(not missing, f"missing sections: {missing}")


# Code and PR-workflow the issue planner is forbidden to produce. The word "code" alone is fine: a plan
# about code will use it.
_CODE_LINE = re.compile(
    r"^\s*(def \w+\(|class \w+[(:]|import \w+|from [\w.]+ import |function \w+\(|const \w+ =|git (commit|push|checkout|add|clone)\b|\$ \w+)",
    re.MULTILINE,
)
_PR_ACTION = re.compile(r"\b(open|create|submit|raise|file)\s+(a\s+|the\s+)?(new\s+)?(pull request|pr)\b", re.IGNORECASE)


@check("no_code", required=["path"])
def _no_code(output, spec):
    text = text_of(resolve(output, spec["path"]))
    problems = []
    if "```" in text:
        problems.append("fenced code block")
    if _CODE_LINE.search(text):
        problems.append(f"code-like line: {_CODE_LINE.search(text).group(0).strip()!r}")
    if _PR_ACTION.search(text):
        problems.append(f"PR suggestion: {_PR_ACTION.search(text).group(0)!r}")
    return Outcome(not problems, "; ".join(problems))


@check("urls_subset", required=["path", "field", "of_path", "of_field"])
def _urls_subset(output, spec):
    """Every non-null item[field] under `path` is one of the of_field values under `of_path` (grounding)."""
    items = _items(output, spec["path"]) or []
    allowed = {i.get(spec["of_field"]) for i in (_items(output, spec["of_path"]) or []) if isinstance(i, dict)}
    stray = [i.get(spec["field"]) for i in items if isinstance(i, dict) and i.get(spec["field"]) and i[spec["field"]] not in allowed]
    return Outcome(not stray, f"{spec['path']}.{spec['field']} not among {spec['of_path']}.{spec['of_field']}: {stray[:3]}")


# --- agent-specific checks ---------------------------------------------------------------------------

@check("blog_ok_or_explained")
def _blog_ok_or_explained(output, spec):
    """For unpromising topics: real ideas, or one placeholder that says why. Never nothing, never junk."""
    if not isinstance(output, list) or not output:
        return Outcome(False, "expected a non-empty list")
    first = output[0]
    if str(first.get("title", "")).lower().startswith(SENTINEL_TITLE):
        return Outcome(is_nonempty(first.get("pitch")), "failure placeholder has no explanation")
    bad = [i for i in output if not (is_nonempty(i.get("title")) and str(i.get("source_url", "")).startswith("http"))]
    return Outcome(not bad, f"{len(bad)} ideas lack a title or source URL")


@check("ranking", required=["relevant"], optional=["min_hits", "max_offtopic"])
def _ranking(output, spec):
    """The top items are the relevant headlines (case-insensitive exact match), and few or none are not."""
    relevant = {h.strip().lower() for h in spec["relevant"]}
    tops = [str(i.get("headline", "")).strip().lower() for i in (resolve(output, "top_items") or [])]
    hits = sum(1 for t in tops if t in relevant)
    off = len(tops) - hits
    lo, hi = spec.get("min_hits", 1), spec.get("max_offtopic", 0)
    return Outcome(hits >= lo and off <= hi, f"{hits} relevant and {off} off-topic items in top {len(tops)}: {tops}; wanted >= {lo} relevant, <= {hi} off-topic")


@check("excludes", required=["substrings"])
def _excludes(output, spec):
    tops = [str(i.get("headline", "")).lower() for i in (resolve(output, "top_items") or [])]
    leaked = [t for t in tops for s in spec["substrings"] if s.lower() in t]
    return Outcome(not leaked, f"filtered-out items reached the top: {leaked}")


@check("none_relevant")
def _none_relevant(output, spec):
    message = resolve(output, "message")
    ok = resolve(output, "top_items") == [] and is_nonempty(message)
    shown = None if message is MISSING else message
    return Outcome(ok, f"expected no top items and an explanation, got {len(resolve(output, 'top_items') or [])} items, message={shown!r}")


@check("analysis_complete")
def _analysis_complete(output, spec):
    items = resolve(output, "top_items")
    if not isinstance(items, list) or not items:
        return Outcome(False, "no top items")
    if output.get("analysis_available") is not True:
        return Outcome(False, "the explanation step failed (analysis_available is not true)")
    for pos, item in enumerate(items, 1):
        if item.get("rank") != pos:
            return Outcome(False, f"item {pos} has rank {item.get('rank')}")
        if not (is_nonempty(item.get("why_it_matters")) and is_nonempty(item.get("suggested_action"))):
            return Outcome(False, f"item {pos} lacks why_it_matters or suggested_action")
    scores = [i.get("score", 0) for i in items]
    if scores != sorted(scores, reverse=True):
        return Outcome(False, f"scores not in descending order: {scores}")
    return Outcome(True)


_RISK_RANK = {"Low": 1, "Medium": 2, "High": 3, "Critical": 4}


def expected_risk(findings: list) -> str:
    """
    What risk_level must be for these findings, stated independently of the agent's own code (an oracle
    that imports the function under test would agree with it even when it is wrong).

    The rule: a finding at an exact pinned version counts at its severity; one at a declared range floor
    counts at most as Medium (the installed version may be newer); an unknown severity counts as Medium;
    the level is the highest of these, and Low when there are no findings.
    """
    level = "Low"
    for f in findings:
        severity = "Medium" if f.get("severity") == "Unknown" else f.get("severity")
        if f.get("version_basis") != "exact" and _RISK_RANK.get(severity, 0) > _RISK_RANK["Medium"]:
            severity = "Medium"
        if _RISK_RANK.get(severity, 0) > _RISK_RANK[level]:
            level = severity
    return level


@check("risk_consistent")
def _risk_consistent(output, spec):
    """risk_level is exactly what the findings imply; the model has no say in it."""
    findings = resolve(output, "cve_analysis")
    level = resolve(output, "risk_level")
    if not isinstance(findings, list):
        return Outcome(False, "cve_analysis is not a list")
    if not findings:
        return Outcome(level in ("Low", "Unknown"), f"no findings but risk_level is {level!r}")
    expected = expected_risk(findings)
    return Outcome(level == expected, f"risk_level is {level!r} but the findings imply {expected!r}")


@check("osv_ids_exist", network=True)
def _osv_ids_exist(output, spec):
    """Each finding's ID really is an OSV advisory (or alias of one) for that package: nothing is invented."""
    import requests

    findings = (resolve(output, "cve_analysis") or [])[:15]
    for f in findings:
        vid = str(f.get("url", "")).rsplit("/", 1)[-1]
        resp = requests.get(f"https://api.osv.dev/v1/vulns/{vid}", timeout=15)
        if resp.status_code != 200:
            return Outcome(False, f"{f.get('cve_id')}: advisory {vid} not found in OSV (HTTP {resp.status_code})")
        record = resp.json()
        ids = {record.get("id")} | set(record.get("aliases", []))
        if f.get("cve_id") not in ids:
            return Outcome(False, f"{f.get('cve_id')} is not {vid} or one of its aliases {sorted(ids)}")
        names = {a.get("package", {}).get("name", "").lower() for a in record.get("affected", [])}
        if str(f.get("package", "")).lower() not in names and names:
            return Outcome(False, f"{f.get('package')} is not among the packages {sorted(names)} that {vid} affects")
    return Outcome(True)


@check("routes", required=["any_of"], optional=["repo", "user"])
def _routes(output, spec):
    """The orchestrator called only the expected specialists, each on exactly the target the user named."""
    from core.targets import parse_repo_ref, parse_username

    used = resolve(output, "specialists_used")
    if not isinstance(used, list) or not used:
        return Outcome(False, "no specialists were used")
    for u in used:
        if u.get("specialist") not in spec["any_of"]:
            return Outcome(False, f"unexpected specialist {u.get('specialist')!r}; wanted one of {spec['any_of']}")
        if "repo" in spec and parse_repo_ref(u.get("input", "")) != parse_repo_ref(spec["repo"]):
            return Outcome(False, f"{u.get('specialist')} ran on {u.get('input')!r}, not {spec['repo']!r}")
        if "user" in spec and parse_username(u.get("input", "")) != spec["user"].lower():
            return Outcome(False, f"{u.get('specialist')} ran on {u.get('input')!r}, not {spec['user']!r}")
    return Outcome(True)


@check("report_mentions", required=["any_of"])
def _report_mentions(output, spec):
    report = str(resolve(output, "report") or "").lower()
    return Outcome(any(w.lower() in report for w in spec["any_of"]), f"report mentions none of {spec['any_of']}")


@check("links_safe")
def _links_safe(output, spec):
    """No image, and no link beyond those grounded in the report's own sources."""
    from core.safe_markdown import sanitize_markdown

    report = str(resolve(output, "report") or "")
    sources = resolve(output, "sources") or []
    if "![" in report:
        return Outcome(False, "report contains an image")
    cleaned = sanitize_markdown(report, sources)
    return Outcome(cleaned == report, "report contains links or markup that sanitizing would change")


@check("error_or_report")
def _error_or_report(output, spec):
    """For unclear tasks: a clear error, or a real report backed by a specialist. Not a crash, not empty."""
    if not isinstance(output, dict):
        return Outcome(False, "output is not a dict")
    if output.get("status") == "error":
        return Outcome(is_nonempty(output.get("error_message")), "error result has no message")
    ok = len(str(output.get("report", ""))) > 30 and is_nonempty(output.get("specialists_used"))
    return Outcome(ok, "success result lacks a report or a specialist")
