"""Loading and validating eval cases (agents/<agent>/evals.jsonl)."""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from evals.checks import validate_spec

ROOT = Path(__file__).resolve().parent.parent
AGENTS = [
    "blog_scout",
    "repo_onboarding",
    "cve_impact",
    "security_audit",
    "issue_fix_planner",
    "do_i_care",
    "opportunity_scout",
    "orchestrator",
]

_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_ALLOWED_KEYS = {"id", "description", "input", "checks", "offline", "attempts"}


class CaseError(Exception):
    """One or more problems in the case files; `problems` lists every one, so they can be fixed together."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass
class Case:
    agent: str
    id: str
    description: str
    input: Any
    checks: list[dict]
    offline: bool = False  # runs the real agent code with the network blocked (validation and error paths)
    attempts: Optional[int] = None  # overrides the run-wide attempt count
    line: int = field(default=0, repr=False)

    @property
    def key(self) -> str:
        return f"{self.agent}/{self.id}"


def cases_path(agent: str) -> Path:
    return ROOT / "agents" / agent / "evals.jsonl"


def golden_path(agent: str, case_id: str) -> Path:
    return ROOT / "evals" / "golden" / agent / f"{case_id}.json"


def _input_problem(agent: str, value: Any) -> Optional[str]:
    if agent == "do_i_care":
        ok = isinstance(value, list) and all(isinstance(v, str) for v in value)
        return None if ok else "input must be a list of strings"
    if agent == "blog_scout":
        return None if value is None or isinstance(value, str) else "input must be a string or null"
    return None if isinstance(value, str) else "input must be a string"


def load_cases(agent: str) -> list[Case]:
    """Parse and validate an agent's cases. Raises CaseError listing every problem found."""
    path = cases_path(agent)
    if not path.exists():
        raise CaseError([f"{agent}: no evals.jsonl at {path}"])

    cases: list[Case] = []
    problems: list[str] = []
    seen: set[str] = set()
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        where = f"{agent} line {n}"
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as e:
            problems.append(f"{where}: invalid JSON ({e})")
            continue
        if not isinstance(raw, dict):
            problems.append(f"{where}: each line must be an object")
            continue

        legacy = {"check", "expected_behavior"} & set(raw)
        if legacy:
            problems.append(f"{where}: old-format keys {sorted(legacy)}; use 'description' and a 'checks' list")
        extra = set(raw) - _ALLOWED_KEYS - legacy
        if extra:
            problems.append(f"{where}: unknown keys {sorted(extra)}")
        case_id = raw.get("id")
        if not isinstance(case_id, str) or not _ID.match(case_id):
            problems.append(f"{where}: 'id' must be lower-case letters, digits and hyphens")
            continue
        where = f"{agent}/{case_id}"
        if case_id in seen:
            problems.append(f"{where}: duplicate id")
        seen.add(case_id)
        if not isinstance(raw.get("description"), str) or not raw["description"].strip():
            problems.append(f"{where}: 'description' is required")
        if "input" not in raw:
            problems.append(f"{where}: 'input' is required")
        else:
            bad = _input_problem(agent, raw["input"])
            if bad:
                problems.append(f"{where}: {bad}")
        checks = raw.get("checks")
        if not isinstance(checks, list) or not checks:
            problems.append(f"{where}: 'checks' must be a non-empty list")
            checks = []
        for spec in checks:
            problems.extend(f"{where}: {p}" for p in validate_spec(spec))
        if not isinstance(raw.get("offline", False), bool):
            problems.append(f"{where}: 'offline' must be true or false")
        attempts = raw.get("attempts")
        if attempts is not None and not (isinstance(attempts, int) and not isinstance(attempts, bool) and attempts >= 1):
            problems.append(f"{where}: 'attempts' must be a positive integer")

        cases.append(
            Case(agent, case_id, raw.get("description", ""), raw.get("input"), checks,
                 bool(raw.get("offline", False)), attempts if isinstance(attempts, int) else None, n)
        )

    if not cases and not problems:
        problems.append(f"{agent}: no cases")
    if problems:
        raise CaseError(problems)
    return cases


def load_all(agents: Optional[list[str]] = None) -> dict[str, list[Case]]:
    """Load every agent's cases, collecting problems across all files before raising."""
    loaded: dict[str, list[Case]] = {}
    problems: list[str] = []
    for agent in agents or AGENTS:
        try:
            loaded[agent] = load_cases(agent)
        except CaseError as e:
            problems.extend(e.problems)
    if problems:
        raise CaseError(problems)
    return loaded
