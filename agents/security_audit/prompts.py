"""Prompts for Security Audit agent."""

SYSTEM_PROMPT = """You are a comprehensive security auditor. Your role is to synthesize repository
information, contributor onboarding needs, and CVE vulnerability data into a single, actionable
security audit report.

You receive:
1. Repository onboarding guide (for understanding project structure and workflow)
2. CVE impact analysis (for understanding security vulnerabilities)
3. Repository metadata (stars, language, description)

Create a unified security audit that covers:
1. Security posture overview
2. Critical vulnerabilities needing immediate action
3. Recommended security improvements for contributors
4. Safe onboarding practices given the vulnerability profile
5. Risk-adjusted roadmap for remediation

Balance security urgency with practical contributor experience. Avoid overwhelming with details -
prioritize actionable recommendations. Assume the reader wants to contribute safely and securely."""

USER_PROMPT_TEMPLATE = """Please create a comprehensive security audit for this repository:

Repository: {repo_url}
Name: {repo_name}

=== CONTRIBUTOR ONBOARDING GUIDE ===
{onboarding_guide}

=== SECURITY VULNERABILITY ANALYSIS ===
{cve_analysis}

Generate a unified security audit as a JSON object:
{{
  "repo_url": "{repo_url}",
  "audit_summary": "2-3 sentence overview of security posture and contributor safety",
  "overall_risk": "Critical|High|Medium|Low",
  "critical_issues": [
    {{
      "issue": "Description of critical vulnerability or security issue",
      "impact": "How it affects contributors and users",
      "remediation": "What to do immediately"
    }},
    ...
  ],
  "safe_contribution_practices": [
    "Recommended practice for contributors given the security profile",
    ...
  ],
  "onboarding_security_checklist": [
    "Security step contributors should follow during setup",
    ...
  ],
  "remediation_roadmap": [
    {{
      "phase": 1,
      "timeframe": "Immediate (this week)",
      "actions": ["action 1", "action 2"]
    }},
    {{
      "phase": 2,
      "timeframe": "Short-term (this month)",
      "actions": ["action 1", "action 2"]
    }},
    {{
      "phase": 3,
      "timeframe": "Medium-term (this quarter)",
      "actions": ["action 1", "action 2"]
    }}
  ],
  "security_recommendations": [
    "General security best practice for this project",
    ...
  ]
}}

Ensure all JSON is valid and complete. Base recommendations only on provided data."""

DEFAULT_REPOS = [
    "https://github.com/anthropics/anthropic-sdk-python",
    "https://github.com/gradio-app/gradio",
]
