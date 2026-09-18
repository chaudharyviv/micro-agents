"""Core logic for Orchestrator agent: a real tool-calling agent loop (see core/harness.py) that
lets the model decide which specialist(s) to call, rather than a hand-coded route-then-synthesize
pipeline."""

import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.harness import Tool, run_harness
from core.memory import run_with_memory
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


# Every specialist's "run" takes a single input string and returns dict/list output.
# Wrappers above normalize blog_scout (optional topic) and do_i_care (list of headlines)
# to that same shape so each can be exposed as a uniform single-argument tool.
SPECIALISTS = {
    "blog_scout": {
        "description": "Suggests blog post ideas backed by real search results, for a given topic.",
        "input_hint": "a topic (or leave blank / use 'trending')",
        "run": _run_blog_scout,
    },
    "repo_onboarding": {
        "description": "Generates a contributor onboarding guide for a GitHub repository.",
        "input_hint": "a GitHub repository URL",
        "run": generate_onboarding_guide,
    },
    "cve_impact": {
        "description": "Analyzes a GitHub repository's dependencies for known CVEs and security risk.",
        "input_hint": "a GitHub repository URL",
        "run": analyze_cve_impact,
    },
    "issue_fix_planner": {
        "description": "Creates a non-code implementation plan for a GitHub issue.",
        "input_hint": "a GitHub issue URL",
        "run": run_issue_fix_planner,
    },
    "do_i_care": {
        "description": "Scores a batch of news headlines for relevance and explains why they matter.",
        "input_hint": "one or more headlines separated by ';'",
        "run": _run_do_i_care,
    },
    "opportunity_scout": {
        "description": "Finds skill gaps, job suggestions, and a project idea for a GitHub user.",
        "input_hint": "a GitHub username",
        "run": run_opportunity_scout,
    },
    "security_audit": {
        "description": (
            "Full security audit for a repo: combines onboarding + CVE analysis into one report. "
            "Prefer this over picking repo_onboarding and cve_impact separately."
        ),
        "input_hint": "a GitHub repository URL",
        "run": generate_security_audit,
    },
}


def _input_matches_task(spec_input: str, task: str) -> bool:
    """
    Sanity-check that a tool call's input is actually grounded in the user's own task text, rather
    than a target the model picked up from an earlier tool result (e.g. a prompt injection attempt
    embedded in fetched repo/issue content tricking it into targeting something else).
    """
    task_lower = task.lower()
    tokens = [t for t in re.split(r"[^a-z0-9]+", spec_input.lower()) if len(t) >= 3]
    if not tokens:
        return True  # nothing meaningful to check (e.g. a bare "trending")
    matched = sum(1 for t in tokens if t in task_lower)
    return matched / len(tokens) >= 0.6


RECALL_TOOL_NAME = "recall_memory"


def _make_specialist_tool(name: str, spec: dict, cache, session, on_event) -> Tool:
    def _run(input: str):
        output, hit = run_with_memory(name, input, spec["run"], cache, session)
        if hit and on_event is not None:
            on_event("memory_hit", {"tool": name, "args": {"input": input}})
        return output

    def _validate(args: dict, task: str) -> bool:
        spec_input = args.get("input", "")
        # An input the user already ran earlier in this session was itself grounded in one of their
        # tasks, so follow-ups like "do the same again" can reuse it without repeating the URL.
        if session is not None and session.has_input(spec_input):
            return True
        return _input_matches_task(spec_input, task)

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
        - report: synthesized Markdown report
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
        result = run_harness(
            task=task,
            tools=build_tools(cache, session, on_event),
            system_prompt=HARNESS_SYSTEM_PROMPT,
            max_steps=3,
            on_event=on_event,
        )
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
                "blocked as a precaution. Please rephrase your task to clearly include the target "
                "(repo URL, GitHub username, issue URL, or topic)."
            ),
            "status": "error",
        }

    specialists_used = [
        {"specialist": c["tool"], "input": c["args"].get("input", "")} for c in specialist_calls
    ]

    return {
        "report": result.final_message,
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
