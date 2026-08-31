"""Gradio app for CVE Impact agent."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import gradio as gr
from agents.cve_impact.logic import analyze_cve_impact


def format_analysis(analysis: dict) -> str:
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
        for cve in cves:
            severity = cve.get("severity", "Unknown")
            severity_colors = {
                "Critical": "🔴",
                "High": "🟠",
                "Medium": "🟡",
                "Low": "🟢",
            }
            severity_icon = severity_colors.get(severity, "⚪")
            output.append(
                f"### {severity_icon} {cve.get('cve_id', 'N/A')} ({cve.get('package', 'N/A')})\n"
            )
            output.append(f"**Severity:** {severity}\n")
            output.append(f"**Description:** {cve.get('description', 'N/A')}\n")
            output.append(f"**Impact:** {cve.get('impact', 'N/A')}\n")
            output.append(f"**Remediation:** {cve.get('remediation', 'N/A')}\n\n")

    common_themes = analysis.get("common_themes", [])
    if common_themes:
        output.append("## Common Themes\n")
        for theme in common_themes[:5]:
            output.append(f"- {theme}\n")

    priority = analysis.get("remediation_priority", [])
    if priority:
        output.append("## Remediation Priority\n")
        for item in priority[:5]:
            rank = item.get("rank", "?")
            action = item.get("action", "N/A")
            effort = item.get("effort", "?")
            output.append(f"{rank}. {action} (Effort: {effort})\n")

    recommendations = analysis.get("recommendations", [])
    if recommendations:
        output.append("## Security Recommendations\n")
        for rec in recommendations[:5]:
            output.append(f"- {rec}\n")

    return "\n".join(output)


def analyze_repo(repo_url: str) -> str:
    """Analyze CVE impact for the given repo URL."""
    if not repo_url or not repo_url.strip():
        return "Please enter a GitHub repository URL (e.g., https://github.com/user/repo)"

    analysis = analyze_cve_impact(repo_url.strip())
    return format_analysis(analysis)


if __name__ == "__main__":
    demo = gr.Interface(
        fn=analyze_repo,
        inputs=gr.Textbox(
            label="GitHub Repository URL",
            placeholder="https://github.com/user/repo",
            lines=1,
        ),
        outputs=gr.Markdown(label="Security Analysis"),
        title="CVE Impact Analyzer",
        description="Analyze security vulnerabilities and CVE impact on GitHub repositories",
        examples=[
            ["https://github.com/anthropics/anthropic-sdk-python"],
            ["https://github.com/gradio-app/gradio"],
        ],
    )

    demo.launch(share=False, server_name="127.0.0.1", server_port=7862, show_error=True)
