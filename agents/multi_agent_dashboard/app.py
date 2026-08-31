"""Multi-agent dashboard orchestrating all agents."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import gradio as gr
from agents.blog_scout.logic import scout_blog_ideas
from agents.repo_onboarding.logic import generate_onboarding_guide
from agents.cve_impact.logic import analyze_cve_impact
from agents.security_audit.logic import generate_security_audit


def format_blog_ideas(ideas: list[dict]) -> str:
    """Format blog ideas for display."""
    if not ideas:
        return "No ideas generated."

    output = []
    for i, idea in enumerate(ideas, 1):
        title = idea.get("title", "N/A")
        pitch = idea.get("pitch", "N/A")
        source_title = idea.get("source_title", "N/A")
        source_url = idea.get("source_url", "N/A")

        output.append(f"### Idea {i}: {title}\n")
        output.append(f"**Pitch:** {pitch}\n")
        output.append(f"**Source:** [{source_title}]({source_url})\n")

    return "\n".join(output)


def format_onboarding_guide(guide: dict) -> str:
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

    return "\n".join(output)


def format_cve_analysis(analysis: dict) -> str:
    """Format CVE analysis for display."""
    if "error_message" in analysis:
        return f"❌ Error: {analysis['error_message']}"

    output = []
    output.append(f"# Security Analysis\n")
    output.append(f"**Summary:** {analysis.get('summary', 'N/A')}\n")

    risk_level = analysis.get("risk_level", "Unknown")
    risk_colors = {
        "Critical": "🔴",
        "High": "🟠",
        "Medium": "🟡",
        "Low": "🟢",
    }
    risk_icon = risk_colors.get(risk_level, "⚪")
    output.append(f"**Risk Level:** {risk_icon} {risk_level}\n")

    cves = analysis.get("cve_analysis", [])
    if cves:
        output.append(f"## CVEs Found ({len(cves)})\n")
        for cve in cves[:5]:
            output.append(f"- **{cve.get('cve_id', 'N/A')}**: {cve.get('severity', 'N/A')}\n")

    return "\n".join(output)


def format_security_audit(audit: dict) -> str:
    """Format security audit for display."""
    if "error_message" in audit:
        return f"❌ Error: {audit['error_message']}"

    output = []
    output.append(f"# Security Audit\n")
    output.append(f"**Summary:** {audit.get('audit_summary', 'N/A')}\n")

    risk_level = audit.get("overall_risk", "Unknown")
    risk_colors = {
        "Critical": "🔴",
        "High": "🟠",
        "Medium": "🟡",
        "Low": "🟢",
    }
    risk_icon = risk_colors.get(risk_level, "⚪")
    output.append(f"**Risk Level:** {risk_icon} {risk_level}\n")

    critical = audit.get("critical_issues", [])
    if critical:
        output.append(f"## Critical Issues ({len(critical)})\n")
        for issue in critical[:3]:
            output.append(f"- ⚠️ {issue.get('issue', 'N/A')}\n")

    practices = audit.get("safe_contribution_practices", [])
    if practices:
        output.append("## Safe Practices\n")
        for practice in practices[:3]:
            output.append(f"- {practice}\n")

    return "\n".join(output)


def blog_scout(topic: str = None) -> str:
    """Blog Scout agent: Generate blog ideas from topics."""
    if not topic or not topic.strip():
        topic = None

    ideas = scout_blog_ideas(topic=topic)
    return format_blog_ideas(ideas)


def onboarding(repo_url: str) -> str:
    """Repo Onboarding agent: Generate contributor guides."""
    if not repo_url or not repo_url.strip():
        return "Please enter a GitHub repository URL"

    guide = generate_onboarding_guide(repo_url.strip())
    return format_onboarding_guide(guide)


def security_check(repo_url: str) -> str:
    """CVE Impact agent: Analyze security vulnerabilities."""
    if not repo_url or not repo_url.strip():
        return "Please enter a GitHub repository URL"

    analysis = analyze_cve_impact(repo_url.strip())
    return format_cve_analysis(analysis)


def audit(repo_url: str) -> str:
    """Security Audit agent: Comprehensive security audit combining onboarding + CVE analysis."""
    if not repo_url or not repo_url.strip():
        return "Please enter a GitHub repository URL"

    audit_result = generate_security_audit(repo_url.strip())
    return format_security_audit(audit_result)


if __name__ == "__main__":
    with gr.Blocks(title="Multi-Agent Dashboard") as demo:
        gr.Markdown(
            """
# 🤖 Micro-Agents Framework
## Multi-Agent Intelligence System

Choose an agent to analyze content or repositories:
- **Blog Scout**: Generate blog ideas from trends
- **Repo Onboarding**: Create contributor guides
- **Security Analyzer**: Assess CVE impact
            """
        )

        with gr.Tabs():
            # Blog Scout Tab
            with gr.TabItem("📝 Blog Scout"):
                gr.Markdown("Generate blog ideas from trending topics or specific topics")
                topic_input = gr.Textbox(
                    label="Topic (optional)",
                    placeholder="Enter a topic, or leave blank for trending topics",
                )
                blog_output = gr.Markdown()
                blog_button = gr.Button("Generate Ideas")
                blog_button.click(fn=blog_scout, inputs=topic_input, outputs=blog_output)

                gr.Examples(
                    examples=[[""], ["machine learning"], ["cloud computing trends"]],
                    inputs=topic_input,
                )

            # Repo Onboarding Tab
            with gr.TabItem("🚀 Repo Onboarding"):
                gr.Markdown("Generate comprehensive contributor onboarding guides")
                repo_url_input = gr.Textbox(
                    label="GitHub Repository URL",
                    placeholder="https://github.com/user/repo",
                )
                onboarding_output = gr.Markdown()
                onboarding_button = gr.Button("Generate Guide")
                onboarding_button.click(
                    fn=onboarding, inputs=repo_url_input, outputs=onboarding_output
                )

                gr.Examples(
                    examples=[
                        ["https://github.com/anthropics/anthropic-sdk-python"],
                        ["https://github.com/gradio-app/gradio"],
                    ],
                    inputs=repo_url_input,
                )

            # Security Analyzer Tab
            with gr.TabItem("🔒 Security Analyzer"):
                gr.Markdown("Analyze CVE impact and security vulnerabilities")
                repo_url_input2 = gr.Textbox(
                    label="GitHub Repository URL",
                    placeholder="https://github.com/user/repo",
                )
                security_output = gr.Markdown()
                security_button = gr.Button("Analyze Security")
                security_button.click(
                    fn=security_check, inputs=repo_url_input2, outputs=security_output
                )

                gr.Examples(
                    examples=[
                        ["https://github.com/anthropics/anthropic-sdk-python"],
                        ["https://github.com/gradio-app/gradio"],
                    ],
                    inputs=repo_url_input2,
                )

            # Security Audit Tab
            with gr.TabItem("🛡️ Security Audit"):
                gr.Markdown("Comprehensive security audit combining onboarding guide with CVE analysis")
                repo_url_input3 = gr.Textbox(
                    label="GitHub Repository URL",
                    placeholder="https://github.com/user/repo",
                )
                audit_output = gr.Markdown()
                audit_button = gr.Button("Generate Audit")
                audit_button.click(
                    fn=audit, inputs=repo_url_input3, outputs=audit_output
                )

                gr.Examples(
                    examples=[
                        ["https://github.com/anthropics/anthropic-sdk-python"],
                        ["https://github.com/gradio-app/gradio"],
                    ],
                    inputs=repo_url_input3,
                )

    demo.launch(share=False, server_name="127.0.0.1", server_port=7863, show_error=True)
