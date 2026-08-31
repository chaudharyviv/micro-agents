"""Gradio app for Opportunity Scout agent."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import gradio as gr
from agents.opportunity_scout.logic import run_opportunity_scout


def format_opportunities(result: dict) -> str:
    """Format opportunity analysis for display."""
    if result.get("status") == "error":
        return f"❌ Error: {result.get('error_message')}"

    output = [
        f"# Career Opportunities\n",
        f"**Developer:** @{result.get('github_username')}\n\n",
        f"## Skill Gaps\n",
        f"{result.get('skill_gaps', 'N/A')}\n\n",
        f"## Job Suggestions\n",
        f"{result.get('job_suggestions', 'N/A')}\n\n",
        f"## 3-Day Project Idea\n",
        f"{result.get('project_idea', 'N/A')}\n\n",
        f"---\n\n## Full Analysis\n",
        f"{result.get('full_analysis', 'No analysis available')}",
    ]

    return "\n".join(output)


def analyze_developer(github_username: str) -> str:
    """Analyze career opportunities for a GitHub developer."""
    if not github_username or not github_username.strip():
        return "Please enter a GitHub username (e.g., torvalds)"

    result = run_opportunity_scout(github_username.strip())
    return format_opportunities(result)


if __name__ == "__main__":
    demo = gr.Interface(
        fn=analyze_developer,
        inputs=gr.Textbox(
            label="GitHub Username",
            placeholder="e.g., torvalds, gvanrossum",
            lines=1,
        ),
        outputs=gr.Markdown(label="Opportunity Analysis"),
        title="Opportunity Scout",
        description="Discover career opportunities based on GitHub activity and skills",
        examples=[
            ["torvalds"],
            ["gvanrossum"],
        ],
    )

    demo.launch(share=False, server_name="127.0.0.1", server_port=7864, show_error=True)
