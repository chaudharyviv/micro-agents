"""Gradio app for Security Audit agent."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import gradio as gr
from agents.security_audit.logic import generate_security_audit


def format_audit(audit: dict) -> str:
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
    output.append(f"**Overall Risk:** {risk_icon} {risk_level}\n")

    # Critical Issues
    critical = audit.get("critical_issues", [])
    if critical:
        output.append(f"## Critical Issues ({len(critical)})\n")
        for issue in critical:
            output.append(f"### ⚠️ {issue.get('issue', 'N/A')}\n")
            output.append(f"**Impact:** {issue.get('impact', 'N/A')}\n")
            output.append(f"**Remediation:** {issue.get('remediation', 'N/A')}\n\n")

    # Safe Contribution Practices
    practices = audit.get("safe_contribution_practices", [])
    if practices:
        output.append("## Safe Contribution Practices\n")
        for practice in practices:
            output.append(f"- {practice}\n")

    # Onboarding Security Checklist
    checklist = audit.get("onboarding_security_checklist", [])
    if checklist:
        output.append("## Security Checklist for New Contributors\n")
        for i, item in enumerate(checklist, 1):
            output.append(f"{i}. {item}\n")

    # Remediation Roadmap
    roadmap = audit.get("remediation_roadmap", [])
    if roadmap:
        output.append("## Remediation Roadmap\n")
        for phase in roadmap:
            output.append(f"### Phase {phase.get('phase', '?')}: {phase.get('timeframe', 'N/A')}\n")
            for action in phase.get("actions", []):
                output.append(f"- {action}\n")

    # Security Recommendations
    recommendations = audit.get("security_recommendations", [])
    if recommendations:
        output.append("## General Security Recommendations\n")
        for rec in recommendations:
            output.append(f"- {rec}\n")

    return "\n".join(output)


def generate_audit(repo_url: str) -> str:
    """Generate security audit for the given repo URL."""
    if not repo_url or not repo_url.strip():
        return "Please enter a GitHub repository URL (e.g., https://github.com/user/repo)"

    audit = generate_security_audit(repo_url.strip())
    return format_audit(audit)


if __name__ == "__main__":
    demo = gr.Interface(
        fn=generate_audit,
        inputs=gr.Textbox(
            label="GitHub Repository URL",
            placeholder="https://github.com/user/repo",
            lines=1,
        ),
        outputs=gr.Markdown(label="Security Audit"),
        title="Security Audit Generator",
        description="Comprehensive security audit combining contributor onboarding with CVE analysis",
        examples=[
            ["https://github.com/anthropics/anthropic-sdk-python"],
            ["https://github.com/gradio-app/gradio"],
        ],
    )

    demo.launch(share=False, server_name="127.0.0.1", server_port=7864, show_error=True)
