"""Multi-tab Gradio interface for Micro-Agents project."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import gradio as gr

# Import all agent functions
from agents.blog_scout.logic import scout_blog_ideas
from agents.repo_onboarding.logic import generate_onboarding_guide
from agents.cve_impact.logic import analyze_cve_impact
from agents.issue_fix_planner.logic import run_issue_fix_planner
from agents.do_i_care.logic import run_do_i_care
from agents.opportunity_scout.logic import run_opportunity_scout
from agents.security_audit.logic import generate_security_audit


def format_blog_ideas(ideas):
    """Format blog ideas for display."""
    if isinstance(ideas, dict) and "error_message" in ideas:
        return f"❌ Error: {ideas['error_message']}"
    if not ideas:
        return "No ideas generated."

    output = []
    for i, idea in enumerate(ideas, 1):
        title = idea.get("title", "N/A")
        pitch = idea.get("pitch", "N/A")
        source_title = idea.get("source_title", "N/A")
        source_url = idea.get("source_url", "N/A")
        output.append(f"### Idea {i}: {title}\n**Pitch:** {pitch}\n**Source:** [{source_title}]({source_url})\n")
    return "\n".join(output)


def format_guide(guide):
    """Format onboarding guide for display."""
    if "error_message" in guide:
        return f"❌ Error: {guide['error_message']}"

    output = [f"# {guide.get('project_name', 'Project')}\n"]
    output.append(f"**Overview:** {guide.get('overview', 'N/A')}\n")

    tech_stack = guide.get("tech_stack", [])
    if tech_stack:
        output.append(f"**Tech Stack:** {', '.join(tech_stack)}\n")

    setup_steps = guide.get("setup_steps", [])
    if setup_steps:
        output.append("## Setup Steps\n")
        for i, step in enumerate(setup_steps, 1):
            output.append(f"{i}. {step}\n")

    return "\n".join(output)


def format_cve(analysis):
    """Format CVE analysis for display."""
    if "error_message" in analysis:
        return f"❌ Error: {analysis['error_message']}"

    output = [f"# Security Analysis\n"]
    output.append(f"**Summary:** {analysis.get('summary', 'N/A')}\n")
    risk_level = analysis.get("risk_level", "Unknown")
    output.append(f"**Risk Level:** {risk_level}\n")
    return "\n".join(output)


def format_plan(result):
    """Format implementation plan for display."""
    if result.get("status") == "error":
        return f"❌ Error: {result.get('error_message')}"
    return f"# {result.get('issue_title', 'Plan')}\n\n{result.get('plan', 'No plan generated')}"


def format_analysis(result):
    """Format analysis results for display."""
    if result.get("status") == "error":
        return f"❌ Error: {result.get('error_message')}"
    output = []
    for item in result.get("top_items", []):
        if "rank" in item:
            output.append(f"## #{item['rank']}: {item['headline']}\n")
    return "\n".join(output) if output else "No analysis available"


def format_opportunities(result):
    """Format opportunity analysis for display."""
    if result.get("status") == "error":
        return f"❌ Error: {result.get('error_message')}"
    output = [
        f"# Career Opportunities\n",
        f"**Developer:** @{result.get('github_username')}\n\n",
        f"## Skill Gaps\n{result.get('skill_gaps', 'N/A')}\n\n",
        f"## Job Suggestions\n{result.get('job_suggestions', 'N/A')}\n\n",
        f"## 3-Day Project Idea\n{result.get('project_idea', 'N/A')}",
    ]
    return "\n".join(output)


# Agent wrapper functions
def blog_scout_tab(topic):
    if not topic:
        topic = "trending"
    ideas = scout_blog_ideas(topic)
    return format_blog_ideas(ideas)


def repo_onboarding_tab(repo_url):
    if not repo_url:
        return "Please enter a repository URL"
    guide = generate_onboarding_guide(repo_url.strip())
    return format_guide(guide)


def cve_impact_tab(query):
    if not query:
        return "Please enter a CVE ID or software name"
    analysis = analyze_cve_impact(query.strip())
    return format_cve(analysis)


def issue_planner_tab(issue_url):
    if not issue_url:
        return "Please enter an issue URL"
    result = run_issue_fix_planner(issue_url.strip())
    return format_plan(result)


def do_i_care_tab(headlines_text):
    if not headlines_text:
        return "Please enter headlines"
    headlines = [line.strip() for line in headlines_text.split("\n") if line.strip()]
    if not headlines:
        return "Please enter at least one headline"
    result = run_do_i_care(headlines)
    return format_analysis(result)


def opportunity_scout_tab(username):
    if not username:
        return "Please enter a GitHub username"
    result = run_opportunity_scout(username.strip())
    return format_opportunities(result)


def security_audit_tab(repo_url):
    if not repo_url:
        return "Please enter a repository URL"
    audit = generate_security_audit(repo_url.strip())
    if "error_message" in audit:
        return f"❌ Error: {audit['error_message']}"
    return f"# Security Audit\n\n{audit.get('audit_summary', 'No audit generated')}"


# Build Gradio interface
with gr.Blocks(title="Micro-Agents", theme=gr.themes.Soft()) as demo:
    gr.Markdown("# 🤖 Micro-Agents Dashboard")
    gr.Markdown(
        "Six specialized AI agents for different tasks: blog ideas, repo analysis, security, planning, and opportunity discovery."
    )

    with gr.Tabs():
        with gr.Tab("📝 Blog Idea Scout"):
            with gr.Row():
                topic_input = gr.Textbox(
                    label="Topic",
                    placeholder="e.g., machine learning, Python, web development",
                    lines=1,
                )
            output = gr.Markdown()
            topic_input.change(blog_scout_tab, inputs=topic_input, outputs=output)
            gr.Button("Scout Ideas").click(blog_scout_tab, inputs=topic_input, outputs=output)

        with gr.Tab("🏗️ Repo Onboarding"):
            with gr.Row():
                repo_input = gr.Textbox(
                    label="Repository URL",
                    placeholder="https://github.com/user/repo",
                    lines=1,
                )
            output = gr.Markdown()
            gr.Button("Generate Guide").click(repo_onboarding_tab, inputs=repo_input, outputs=output)

        with gr.Tab("🔒 CVE Impact"):
            with gr.Row():
                cve_input = gr.Textbox(
                    label="CVE ID or Software",
                    placeholder="e.g., CVE-2024-1234 or Log4j",
                    lines=1,
                )
            output = gr.Markdown()
            gr.Button("Analyze").click(cve_impact_tab, inputs=cve_input, outputs=output)

        with gr.Tab("🛠️ Issue Fix Planner"):
            with gr.Row():
                issue_input = gr.Textbox(
                    label="Issue URL",
                    placeholder="https://github.com/user/repo/issues/123",
                    lines=1,
                )
            output = gr.Markdown()
            gr.Button("Generate Plan").click(issue_planner_tab, inputs=issue_input, outputs=output)

        with gr.Tab("🎯 Do I Care?"):
            with gr.Row():
                headlines_input = gr.Textbox(
                    label="Headlines (one per line)",
                    placeholder="Enter news items...",
                    lines=8,
                )
            output = gr.Markdown()
            gr.Button("Analyze Relevance").click(do_i_care_tab, inputs=headlines_input, outputs=output)

        with gr.Tab("🚀 Opportunity Scout"):
            with gr.Row():
                username_input = gr.Textbox(
                    label="GitHub Username",
                    placeholder="e.g., torvalds",
                    lines=1,
                )
            output = gr.Markdown()
            gr.Button("Discover Opportunities").click(opportunity_scout_tab, inputs=username_input, outputs=output)

        with gr.Tab("🔍 Security Audit"):
            with gr.Row():
                audit_repo_input = gr.Textbox(
                    label="Repository URL",
                    placeholder="https://github.com/user/repo",
                    lines=1,
                )
            output = gr.Markdown()
            gr.Button("Audit").click(security_audit_tab, inputs=audit_repo_input, outputs=output)

    gr.Markdown(
        """
        ---
        **About Micro-Agents:** Six independent AI agents using open-weight LLMs (Groq).
        Each agent specializes in a different task without external frameworks.
        [Learn more](https://github.com/user/micro-agents)
        """
    )


if __name__ == "__main__":
    demo.launch(share=False, show_error=True)
