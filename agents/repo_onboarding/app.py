"""Gradio app for Repo Onboarding agent."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import gradio as gr
from agents.repo_onboarding.logic import generate_onboarding_guide


def format_guide(guide: dict) -> str:
    """Format onboarding guide for display."""
    if "error_message" in guide:
        return f"❌ Error: {guide['error_message']}"

    output = []
    output.append(f"# {guide.get('project_name', 'Project')}\n")
    output.append(f"**Overview:** {guide.get('overview', 'N/A')}\n")

    tech_stack = guide.get("tech_stack", [])
    if tech_stack:
        output.append(f"**Tech Stack:** {', '.join(tech_stack)}\n")

    setup_steps = guide.get("setup_steps", [])
    if setup_steps:
        output.append("## Setup Steps\n")
        for i, step in enumerate(setup_steps, 1):
            output.append(f"{i}. {step}\n")

    project_structure = guide.get("project_structure", {})
    if project_structure:
        output.append("## Project Structure\n")
        for dir_name, description in list(project_structure.items())[:10]:
            output.append(f"- **{dir_name}:** {description}\n")

    key_concepts = guide.get("key_concepts", [])
    if key_concepts:
        output.append("## Key Concepts\n")
        for concept in key_concepts[:5]:
            output.append(f"- {concept}\n")

    dev_workflow = guide.get("development_workflow", {})
    if dev_workflow:
        output.append("## Development Workflow\n")
        for task, description in dev_workflow.items():
            output.append(f"**{task.replace('_', ' ').title()}:** {description}\n")

    common_tasks = guide.get("common_tasks", {})
    if common_tasks:
        output.append("## Common Commands\n")
        for task, command in common_tasks.items():
            output.append(f"- `{command}` - {task.replace('_', ' ')}\n")

    resources = guide.get("resources", [])
    if resources:
        output.append("## Resources\n")
        for resource in resources[:5]:
            output.append(f"- {resource}\n")

    return "\n".join(output)


def generate_guide(repo_url: str) -> str:
    """Generate onboarding guide for the given repo URL."""
    if not repo_url or not repo_url.strip():
        return "Please enter a GitHub repository URL (e.g., https://github.com/user/repo)"

    guide = generate_onboarding_guide(repo_url.strip())
    return format_guide(guide)


if __name__ == "__main__":
    demo = gr.Interface(
        fn=generate_guide,
        inputs=gr.Textbox(
            label="GitHub Repository URL",
            placeholder="https://github.com/user/repo",
            lines=1,
        ),
        outputs=gr.Markdown(label="Onboarding Guide"),
        title="Repo Onboarding Guide Generator",
        description="Generate comprehensive contributor onboarding guides for GitHub repositories",
        examples=[
            ["https://github.com/anthropics/anthropic-sdk-python"],
            ["https://github.com/gradio-app/gradio"],
        ],
    )

    demo.launch(share=False, server_name="127.0.0.1", server_port=7861, show_error=True)
