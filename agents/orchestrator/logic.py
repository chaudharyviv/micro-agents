"""Core logic for Orchestrator agent: a real tool-calling agent loop (see core/harness.py) that
lets the model decide which specialist(s) to call, rather than a hand-coded route-then-synthesize
pipeline."""

import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.budget import get_budget, request_scope
from core.errors import BudgetExceededError
from core.harness import Tool, run_harness
from core.memory import run_with_memory
from core.safe_markdown import collect_urls, sanitize_markdown, urls_in_text
from core.targets import (
    extract_issue_refs,
    extract_repo_refs,
    extract_usernames,
    parse_issue_ref,
    parse_repo_ref,
    parse_username,
)
from agents.blog_scout.logic import scout_blog_ideas
from agents.repo_onboarding.logic import generate_onboarding_guide
from agents.cve_impact.logic import analyze_cve_impact
from agents.issue_fix_planner.logic import run_issue_fix_planner
from agents.do_i_care.logic import run_do_i_care
from agents.opportunity_scout.logic import run_opportunity_scout
from agents.security_audit.logic import generate_security_audit
from agents.orchestrator.prompts import HARNESS_SYSTEM_PROMPT

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _run_blog_scout(input: str):
    return scout_blog_ideas(input or "trending")


def _run_do_i_care(input: str):
    headlines = [h.strip() for h in input.split(";") if h.strip()]
    return run_do_i_care(headlines)


# input_kind says what the input identifies, and so how it is checked against the user's own words:
# "repo" / "issue" / "user" are parsed and compared exactly; "text" (a topic or headlines) has no
# identifier to parse, so it falls back to a fuzzy word-overlap check.
MAX_TOOL_CALLS = 3

# Every specialist's "run" takes a single input string and returns dict/list output.
# Wrappers above normalize blog_scout (optional topic) and do_i_care (list of headlines)
# to that same shape so each can be exposed as a uniform single-argument tool.
SPECIALISTS = {
    "blog_scout": {
        "input_kind": "text",
        "description": "Suggests blog post ideas backed by real search results, for a given topic.",
        "input_hint": "a topic (or leave blank / use 'trending')",
        "run": _run_blog_scout,
    },
    "repo_onboarding": {
        "input_kind": "repo",
        "description": "Generates a contributor onboarding guide for a GitHub repository.",
        "input_hint": "a GitHub repository URL",
        "run": generate_onboarding_guide,
    },
    "cve_impact": {
        "input_kind": "repo",
        "description": "Analyzes a GitHub repository's dependencies for known CVEs and security risk.",
        "input_hint": "a GitHub repository URL",
        "run": analyze_cve_impact,
    },
    "issue_fix_planner": {
        "input_kind": "issue",
        "description": "Creates a non-code implementation plan for a GitHub issue.",
        "input_hint": "a GitHub issue URL",
        "run": run_issue_fix_planner,
    },
    "do_i_care": {
        "input_kind": "text",
        "description": "Scores a batch of news headlines for relevance and explains why they matter.",
        "input_hint": "one or more headlines separated by ';'",
        "run": _run_do_i_care,
    },
    "opportunity_scout": {
        "input_kind": "user",
        "description": "Finds skill gaps, job suggestions, and a project idea for a GitHub user.",
        "input_hint": "a GitHub username",
        "run": run_opportunity_scout,
    },
    "security_audit": {
        "input_kind": "repo",
        "description": (
            "Full security audit for a repo: combines onboarding + CVE analysis into one report. "
            "Prefer this over picking repo_onboarding and cve_impact separately."
        ),
        "input_hint": "a GitHub repository URL",
        "run": generate_security_audit,
    },
}


def _text_overlaps_task(spec_input: str, task: str) -> bool:
    """
    Fuzzy check for free-text inputs (a blog topic, headlines): most of the input's words must
    appear in the task. This is weak by nature and used only where there is no identifier to match
    exactly; the cost of a wrong guess is a web search or a scoring call, not a fetch of some
    other repo or user.
    """
    if spec_input.strip().lower() in ("", "trending"):
        return True  # blog_scout's own default when the task names no topic
    task_lower = task.lower()
    tokens = [t for t in re.split(r"[^a-z0-9]+", spec_input.lower()) if len(t) >= 3]
    if not tokens:
        return True  # nothing meaningful to check
    matched = sum(1 for t in tokens if t in task_lower)
    return matched / len(tokens) >= 0.6


def _is_grounded(kind: str, spec_input: str, sources: list[str]) -> bool:
    """
    True if the target in `spec_input` is one the user themselves named in `sources` (their task
    text, and inputs already accepted earlier in their session).

    Repos, issues and users are compared exactly on parsed identifiers, so a lookalike such as
    github.com/other/thing, or a repo whose name merely shares words with the task, is refused.
    """
    if kind == "repo":
        ref = parse_repo_ref(spec_input)
        return ref is not None and any(ref in extract_repo_refs(s) for s in sources)
    if kind == "issue":
        ref = parse_issue_ref(spec_input)
        return ref is not None and any(ref in extract_issue_refs(s) for s in sources)
    if kind == "user":
        name = parse_username(spec_input)
        return name is not None and any(name in extract_usernames(s) for s in sources)
    return _text_overlaps_task(spec_input, sources[0])  # free text: checked against the task only


RECALL_TOOL_NAME = "recall_memory"


def _make_specialist_tool(name: str, spec: dict, cache, session, on_event) -> Tool:
    def _run(input: str):
        output, hit = run_with_memory(name, input, spec["run"], cache, session)
        if hit and on_event is not None:
            on_event("memory_hit", {"tool": name, "args": {"input": input}})
        return output

    def _validate(args: dict, task: str) -> bool:
        spec_input = args.get("input", "")
        if not isinstance(spec_input, str):
            return False
        kind = spec.get("input_kind", "text")
        if kind == "text":
            # Re-running an exact input from earlier in this session is fine for follow-ups.
            return (session is not None and session.has_input(spec_input)) or _is_grounded(kind, spec_input, [task])
        # Inputs recorded earlier in this session were typed by the user or passed this same check,
        # so follow-ups like "audit that repo too" can reuse them without repeating the URL.
        sources = [task] + (session.inputs() if session is not None else [])
        return _is_grounded(kind, spec_input, sources)

    return Tool(
        name=name,
        description=spec["description"],
        parameters={
            "type": "object",
            "properties": {"input": {"type": "string", "description": spec["input_hint"]}},
            "required": ["input"],
        },
        run=_run,
        validate=_validate,
    )


def _make_recall_tool(session) -> Tool:
    def _recall():
        runs = session.recent(10)
        if not runs:
            return {"runs": [], "note": "No earlier runs in this session."}
        return {
            "runs": [
                {k: r[k] for k in ("agent", "input", "summary", "age_seconds")} for r in runs
            ]
        }

    return Tool(
        name=RECALL_TOOL_NAME,
        description=(
            "Look up this session's earlier agent runs (agent, input, short summary, age). Use it when "
            "the user refers to earlier work ('that repo', 'again', 'compare with before')."
        ),
        parameters={"type": "object", "properties": {}},
        run=_recall,
    )


def build_tools(cache=None, session=None, on_event=None) -> list[Tool]:
    """Specialists as tools, wrapped with memory; plus a recall tool when session memory exists."""
    tools = [_make_specialist_tool(n, s, cache, session, on_event) for n, s in SPECIALISTS.items()]
    if session is not None:
        tools.append(_make_recall_tool(session))
    return tools


def run_orchestrator(task: str, on_event=None, session=None, cache=None) -> dict:
    """
    Run the orchestrator agent harness on a free-text task: the model decides which 1-2
    specialist tool(s) to call and synthesizes their results into a report.

    Args:
        task: A free-text description of what the user wants, e.g.
              "Review https://github.com/user/repo for onboarding and security".
        on_event: Optional callback forwarded to core.harness.run_harness, for a caller (e.g. a
                  UI) to observe tool calls as they happen. See run_harness's docstring for events;
                  this function also emits "memory_hit" {tool, args} when a result came from cache.
        session: Optional core.memory.SessionMemory. When given, every specialist run is recorded
                 to it and the model gets a `recall_memory` tool to look at earlier runs.
        cache: Optional core.memory.ResultCache shared across sessions (public-URL agents only).

    Returns:
        A dict with:
        - report: synthesized Markdown report, with images removed and links limited to URLs the user
          named or that appeared in a tool result's structured URL fields (see core.safe_markdown)
        - sources: the URLs the report's links were checked against
        - specialists_used: list of {"specialist": name, "input": extracted input}
        - used_memory: True if the model consulted session memory via `recall_memory`
        - status: "success" or "error"

        On error, returns a dict with error_message and status: "error".
    """
    if not task or not task.strip():
        return {"error_message": "Please describe the task.", "status": "error"}

    task = task.strip()
    logger.info(f"Running orchestrator harness for task: {task}")

    try:
        # Bounds the model calls this run can trigger in total, including those inside specialists.
        # If the caller (e.g. the UI) already opened a scope, that one is used instead.
        with request_scope(max_calls=get_budget().limits.orchestrator_request_calls):
            result = run_harness(
                task=task,
                tools=build_tools(cache, session, on_event),
                system_prompt=HARNESS_SYSTEM_PROMPT,
                max_steps=3,
                max_tool_calls=MAX_TOOL_CALLS,
                on_event=on_event,
            )
    except BudgetExceededError as e:
        logger.warning(f"Orchestrator stopped by usage limit ({e.scope}): {e}")
        return {"error_message": str(e), "status": "error"}
    except RuntimeError as e:
        logger.error(f"Harness unavailable: {e}")
        return {"error_message": str(e), "status": "error"}

    if not result.tool_calls:
        return {
            "error_message": (
                "Could not determine which specialist(s) to use for this task. "
                "Try being more specific (mention a repo URL, GitHub username, issue URL, or topic)."
            ),
            "status": "error",
        }

    specialist_calls = [c for c in result.tool_calls if c["tool"] != RECALL_TOOL_NAME]
    blocked = [c for c in specialist_calls if isinstance(c["output"], dict) and "blocked" in str(c["output"].get("error", ""))]
    if specialist_calls and len(blocked) == len(specialist_calls):
        return {
            "error_message": (
                "The routing decision picked a target that doesn't appear in your task text, so it was "
                "blocked as a precaution. Please include the target explicitly: the full GitHub repo or "
                "issue URL, or a username written as '@name' or 'GitHub user name'."
            ),
            "status": "error",
        }

    specialists_used = [
        {"specialist": c["tool"], "input": c["args"].get("input", "")} for c in specialist_calls
    ]

    # The report is model output shaped by fetched (untrusted) content. Keep only links to URLs the
    # user named, or that appear in a structured URL field of a tool result; drop images and defang
    # everything else. Free text inside tool results is deliberately not a source of allowed URLs.
    allowed = urls_in_text(task)
    if session is not None:
        for earlier_input in session.inputs():
            allowed |= urls_in_text(earlier_input)
    allowed |= collect_urls([c["output"] for c in specialist_calls])

    return {
        "report": sanitize_markdown(result.final_message, allowed),
        "sources": sorted(allowed)[:100],
        "specialists_used": specialists_used,
        "used_memory": len(specialist_calls) < len(result.tool_calls),
        "status": "success",
    }


if __name__ == "__main__":
    print("=== Orchestrator Harness Smoke Test ===\n")

    task = "Review https://github.com/anthropics/anthropic-sdk-python for security issues"
    print(f"Task: {task}\n")

    try:
        result = run_orchestrator(task)

        if result.get("status") == "error":
            print(f"Error: {result.get('error_message')}")
        else:
            print(f"Specialists used: {result.get('specialists_used')}\n")
            print(f"Report:\n{result.get('report')}")
            print("\nOrchestration completed successfully!")

    except Exception as e:
        print(f"Error: {e}")
