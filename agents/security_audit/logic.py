"""Core logic for Security Audit agent (combines Onboarding + CVE Impact)."""

import json
import logging
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from agents.repo_onboarding.logic import generate_onboarding_guide
from agents.cve_impact.logic import analyze_cve_impact
from core.github_tool import fetch_repo
from core.llm import call_llm
from agents.security_audit.prompts import (
    SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def generate_security_audit(repo_url: str) -> dict:
    """
    Generate a comprehensive security audit by combining:
    1. Repository onboarding guide
    2. CVE vulnerability analysis

    Args:
        repo_url: GitHub repository URL

    Returns:
        A dict with security audit data including:
        - audit_summary: overview of security posture
        - overall_risk: Critical/High/Medium/Low
        - critical_issues: list of critical vulnerabilities
        - safe_contribution_practices: security practices for contributors
        - onboarding_security_checklist: setup security steps
        - remediation_roadmap: phased remediation plan
        - security_recommendations: best practices

        On error, returns dict with error_message and repo_url.
    """
    logger.info(f"Generating security audit for: {repo_url}")

    # Step 1: Fetch repo metadata
    try:
        repo_data = fetch_repo(repo_url)
        repo_name = repo_data.get("name", "Unknown")
        logger.info(f"Fetched repo: {repo_name}")
    except Exception as e:
        logger.error(f"Failed to fetch repo: {e}")
        return {
            "error_message": f"Failed to fetch repository: {str(e)[:100]}",
            "repo_url": repo_url,
        }

    # Step 2: Generate onboarding guide (call Repo Onboarding agent)
    logger.info("Generating onboarding guide...")
    try:
        onboarding_guide = generate_onboarding_guide(repo_url)

        if "error_message" in onboarding_guide:
            onboarding_text = f"[Onboarding generation failed: {onboarding_guide['error_message']}]"
        else:
            # Format onboarding guide for inclusion in audit
            onboarding_text = _format_guide_for_audit(onboarding_guide)

        logger.info("Onboarding guide generated")
    except Exception as e:
        logger.error(f"Onboarding generation failed: {e}")
        onboarding_text = f"[Could not generate onboarding guide: {str(e)[:100]}]"

    # Step 3: Analyze CVE impact (call CVE Impact agent)
    logger.info("Analyzing CVE impact...")
    try:
        cve_analysis = analyze_cve_impact(repo_url)

        if "error_message" in cve_analysis:
            cve_text = f"[CVE analysis failed: {cve_analysis['error_message']}]"
        else:
            # Format CVE analysis for inclusion in audit
            cve_text = _format_analysis_for_audit(cve_analysis)

        logger.info("CVE analysis generated")
    except Exception as e:
        logger.error(f"CVE analysis failed: {e}")
        cve_text = f"[Could not analyze CVEs: {str(e)[:100]}]"

    # Step 4: Call LLM to generate unified audit
    logger.info("Generating unified security audit...")
    user_prompt = USER_PROMPT_TEMPLATE.format(
        repo_url=repo_url,
        repo_name=repo_name,
        onboarding_guide=onboarding_text,
        cve_analysis=cve_text,
    )

    try:
        llm_response = call_llm(user_prompt, system=SYSTEM_PROMPT)
        logger.info(f"LLM response: {llm_response[:100]}...")
    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        return {
            "error_message": f"LLM service unavailable: {str(e)[:100]}",
            "repo_url": repo_url,
        }

    # Step 5: Parse LLM response
    audit = _parse_audit(llm_response, repo_url)

    if audit is None:
        # Retry once
        logger.warning("Parse failed. Retrying with error message.")
        error_msg = "Your previous response was not valid JSON. Please respond again with ONLY a JSON object."
        retry_prompt = user_prompt + "\n\n" + error_msg

        try:
            llm_response = call_llm(retry_prompt, system=SYSTEM_PROMPT)
            audit = _parse_audit(llm_response, repo_url)
        except Exception as e:
            logger.error(f"LLM retry failed: {e}")

    # Fallback if still no valid audit
    if audit is None:
        logger.error("Failed to parse LLM response even after retry")
        return {
            "error_message": "Failed to generate security audit. Please try again.",
            "repo_url": repo_url,
        }

    audit["repo_url"] = repo_url
    return audit


def _format_guide_for_audit(guide: dict) -> str:
    """Format onboarding guide for inclusion in audit report."""
    lines = []
    lines.append(f"Project: {guide.get('project_name', 'Unknown')}")
    lines.append(f"Overview: {guide.get('overview', 'N/A')}")
    lines.append(
        f"Tech Stack: {', '.join(guide.get('tech_stack', []))}"
    )
    lines.append("Setup Steps:")
    for step in guide.get("setup_steps", [])[:3]:
        lines.append(f"  - {step}")
    return "\n".join(lines)


def _format_analysis_for_audit(analysis: dict) -> str:
    """Format CVE analysis for inclusion in audit report."""
    lines = []
    lines.append(f"Summary: {analysis.get('summary', 'N/A')}")
    lines.append(f"Risk Level: {analysis.get('risk_level', 'Unknown')}")

    cves = analysis.get("cve_analysis", [])
    if cves:
        lines.append(f"CVEs Found: {len(cves)}")
        for cve in cves[:5]:
            lines.append(
                f"  - {cve.get('cve_id', 'N/A')}: {cve.get('severity', 'N/A')} - {cve.get('description', 'N/A')}"
            )

    recommendations = analysis.get("recommendations", [])
    if recommendations:
        lines.append("Recommendations:")
        for rec in recommendations[:3]:
            lines.append(f"  - {rec}")

    return "\n".join(lines)


def _parse_audit(response: str, repo_url: str) -> Optional[dict]:
    """
    Parse JSON response from LLM.

    Args:
        response: LLM's JSON response (may be wrapped in markdown code blocks)
        repo_url: Original repository URL for context

    Returns:
        Parsed audit dict, or None if parsing fails
    """
    try:
        # Extract JSON from markdown code blocks if present
        clean_response = response.strip()
        if clean_response.startswith("```"):
            lines = clean_response.split("\n")
            json_lines = []
            in_json = False
            for line in lines:
                if line.startswith("```"):
                    in_json = not in_json
                    continue
                if in_json or (not line.startswith("```") and json_lines):
                    json_lines.append(line)
            clean_response = "\n".join(json_lines).strip()

        audit = json.loads(clean_response)

        if not isinstance(audit, dict):
            logger.error("Response is not a JSON object")
            return None

        # Validate required fields
        required_fields = ["audit_summary", "overall_risk"]
        if not all(k in audit for k in required_fields):
            logger.warning(f"Audit missing required fields: {required_fields}")
            return None

        logger.info(f"Successfully parsed security audit (risk: {audit.get('overall_risk')})")
        return audit

    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e}")
        return None
    except Exception as e:
        logger.error(f"Parse error: {e}")
        return None


if __name__ == "__main__":
    print("=== Security Audit Smoke Test ===\n")

    repo_url = "https://github.com/anthropics/anthropic-sdk-python"
    print(f"Generating security audit for: {repo_url}\n")

    try:
        audit = generate_security_audit(repo_url)

        if "error_message" in audit:
            print(f"Error: {audit['error_message']}")
        else:
            print(f"Audit Summary: {audit.get('audit_summary', 'N/A')}")
            print(f"Overall Risk: {audit.get('overall_risk', 'N/A')}")

            critical = audit.get("critical_issues", [])
            if critical:
                print(f"\nCritical Issues: {len(critical)}")
                for issue in critical[:2]:
                    print(f"  - {issue.get('issue', 'N/A')}")

            practices = audit.get("safe_contribution_practices", [])
            if practices:
                print(f"\nSafe Practices:")
                for practice in practices[:3]:
                    print(f"  - {practice}")

            print(f"\nAudit generated successfully!")

    except Exception as e:
        print(f"Error: {e}")
