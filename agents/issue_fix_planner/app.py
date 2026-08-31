"""Gradio app for Issue Fix Planner agent."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import gradio as gr
from agents.issue_fix_planner.logic import run_issue_fix_planner


def format_plan(result: dict) -> str:
    """Format implementation plan for display."""
    if result.get("status") == "error":
        return f"❌ Error: {result.get('error_message')}"

    plan = result.get("plan", "No plan generated")
    issue_title = result.get("issue_title", "Unknown")
    issue_url = result.get("issue_url", "")

    output = [
        f"# Implementation Plan\n",
        f"**Issue:** {issue_title}\n",
        f"**URL:** [{issue_url}]({issue_url})\n\n",
        f"## Plan\n",
        plan,
    ]

    return "\n".join(output)


def generate_plan(issue_url: str) -> str:
    """Generate implementation plan for the given issue URL."""
    if not issue_url or not issue_url.strip():
        return "Please enter a GitHub issue URL (e.g., https://github.com/user/repo/issues/123)"

    result = run_issue_fix_planner(issue_url.strip())
    return format_plan(result)


if __name__ == "__main__":
    demo = gr.Interface(
        fn=generate_plan,
        inputs=gr.Textbox(
            label="GitHub Issue URL",
            placeholder="https://github.com/user/repo/issues/123",
            lines=1,
        ),
        outputs=gr.Markdown(label="Implementation Plan"),
        title="Issue Fix Planner",
        description="Generate implementation plans for GitHub issues without writing code",
        examples=[
            ["https://github.com/anthropics/anthropic-sdk-python/issues/1"],
            ["https://github.com/gradio-app/gradio/issues/1"],
        ],
    )

    demo.launch(share=False, server_name="127.0.0.1", server_port=7862, show_error=True)
