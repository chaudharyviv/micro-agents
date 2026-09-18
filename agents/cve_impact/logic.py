"""Core logic for CVE Impact agent."""

import json
import logging
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.github_tool import fetch_repo, GitHubAPIError
from core.search import web_search
from core.llm import call_llm, LLMUnavailableError
from agents.cve_impact.prompts import (
    SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def analyze_cve_impact(repo_url: str) -> dict:
    """
    Analyze CVE impact for a GitHub repository.

    Args:
        repo_url: GitHub repository URL (e.g., https://github.com/user/repo)

    Returns:
        A dict with CVE analysis:
        - summary: overview of security posture
        - risk_level: Critical/High/Medium/Low
        - cve_analysis: list of CVE details
        - common_themes: patterns in vulnerabilities
        - remediation_priority: ordered list of actions
        - recommendations: security best practices
        - repo_url: original URL

        On error, returns a dict with error_message and repo_url.
    """
    logger.info(f"Analyzing CVE impact for: {repo_url}")

    # Fetch repository data
    try:
        repo_data = fetch_repo(repo_url)
        logger.info(f"Fetched repo: {repo_data.get('name', 'Unknown')}")
    except GitHubAPIError as e:
        logger.error(f"Failed to fetch repo: {e}")
        return {"error_message": str(e), "repo_url": repo_url}
    except Exception as e:
        logger.error(f"Unexpected error fetching repo: {e}")
        return {"error_message": "Failed to fetch repository. Please try again.", "repo_url": repo_url}

    # Extract dependencies from repo data
    repo_name = repo_data.get("name", "Unknown")
    repo_desc = repo_data.get("description", "")
    repo_lang = repo_data.get("language", "Unknown")
    file_tree = repo_data.get("file_tree", "")

    # Parse dependencies from file tree (look for requirements.txt, package.json, etc)
    dependencies = _extract_dependencies(file_tree, repo_data.get("readme", ""))
    logger.info(f"Extracted {len(dependencies)} dependencies")

    if not dependencies:
        logger.warning("No dependencies detected")
        dependencies = ["No dependencies detected in package files"]

    # Search for CVEs related to these dependencies
    cves_found = _search_cves(repo_name, dependencies)
    logger.info(f"Found {len(cves_found)} potential CVEs")

    # Format data for LLM
    cves_formatted = "\n".join(cves_found) if cves_found else "No CVEs found in search results"
    deps_str = ", ".join(dependencies[:20]) if dependencies else "Unknown"

    user_prompt = USER_PROMPT_TEMPLATE.format(
        repo_url=repo_url,
        repo_name=repo_name,
        repo_description=repo_desc,
        repo_language=repo_lang,
        dependencies=deps_str,
        cves=cves_formatted,
    )

    # Call LLM to analyze CVE impact
    logger.info("Calling LLM to analyze CVE impact")
    try:
        llm_response = call_llm(user_prompt, system=SYSTEM_PROMPT)
        logger.info(f"LLM response: {llm_response[:100]}...")
    except LLMUnavailableError as e:
        logger.error(f"LLM call failed: {e}")
        return {"error_message": str(e), "repo_url": repo_url}
    except Exception as e:
        logger.error(f"Unexpected LLM error: {e}")
        return {"error_message": "LLM service unavailable. Please try again.", "repo_url": repo_url}

    # Parse LLM response
    analysis = _parse_analysis(llm_response, repo_url)

    if analysis is None:
        # Retry once
        logger.warning("Parse failed. Retrying with error message.")
        error_msg = "Your previous response was not valid JSON. Please respond again with ONLY a JSON object."
        retry_prompt = user_prompt + "\n\n" + error_msg

        try:
            llm_response = call_llm(retry_prompt, system=SYSTEM_PROMPT)
            analysis = _parse_analysis(llm_response, repo_url)
        except Exception as e:
            logger.error(f"LLM retry failed: {e}")

    # Fallback if still no valid analysis
    if analysis is None:
        logger.error("Failed to parse LLM response even after retry")
        return {
            "error_message": "Failed to analyze CVE impact. Please try again.",
            "repo_url": repo_url,
        }

    analysis["repo_url"] = repo_url
    return analysis


def _extract_dependencies(file_tree: str, readme: str) -> list[str]:
    """
    Extract dependencies from file tree and README.

    Returns list of package names that likely have dependencies.
    """
    dependencies = set()

    # Look for dependency file patterns
    patterns = {
        r"requirements\.txt": ["python"],
        r"package\.json": ["nodejs", "npm"],
        r"Gemfile": ["ruby"],
        r"pom\.xml": ["java", "maven"],
        r"go\.mod": ["go"],
        r"Cargo\.toml": ["rust"],
        r"\.csproj": ["dotnet"],
    }

    for pattern, langs in patterns.items():
        if re.search(pattern, file_tree, re.IGNORECASE):
            dependencies.update(langs)

    # Look for common packages mentioned in README
    common_packages = [
        "django",
        "flask",
        "fastapi",
        "pandas",
        "numpy",
        "tensorflow",
        "pytorch",
        "react",
        "vue",
        "angular",
        "express",
        "rails",
        "spring",
        "gradle",
        "maven",
    ]

    readme_lower = readme.lower()
    for pkg in common_packages:
        if pkg in readme_lower:
            dependencies.add(pkg)

    return list(dependencies) if dependencies else ["generic", "unknown"]


def _search_cves(repo_name: str, dependencies: list[str]) -> list[str]:
    """
    Search for CVEs related to project dependencies.

    Returns list of CVE findings.
    """
    cves = []

    # Search for each dependency
    for dep in dependencies[:5]:  # Limit to first 5 to avoid too many searches
        query = f"CVE {dep} vulnerability 2024 2023"
        try:
            results = web_search(query, max_results=3)
            for result in results:
                cve_text = f"- {result.get('title', '')}: {result.get('snippet', '')}"
                cves.append(cve_text)
                logger.info(f"  Found CVE mention: {result.get('title', '')}")
        except Exception as e:
            logger.warning(f"Failed to search CVEs for {dep}: {e}")

    return cves[:10]  # Limit to 10 results


def _parse_analysis(response: str, repo_url: str) -> Optional[dict]:
    """
    Parse JSON response from LLM.

    Args:
        response: LLM's JSON response (may be wrapped in markdown code blocks)
        repo_url: Original repository URL for context

    Returns:
        Parsed analysis dict, or None if parsing fails
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

        analysis = json.loads(clean_response)

        if not isinstance(analysis, dict):
            logger.error("Response is not a JSON object")
            return None

        # Validate required fields
        required_fields = ["summary", "risk_level"]
        if not all(k in analysis for k in required_fields):
            logger.warning(f"Analysis missing required fields: {required_fields}")
            return None

        logger.info(f"Successfully parsed CVE analysis (risk: {analysis.get('risk_level')})")
        return analysis

    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e}")
        return None
    except Exception as e:
        logger.error(f"Parse error: {e}")
        return None


if __name__ == "__main__":
    print("=== CVE Impact Smoke Test ===\n")

    repo_url = "https://github.com/anthropics/anthropic-sdk-python"
    print(f"Analyzing CVE impact for: {repo_url}\n")

    try:
        analysis = analyze_cve_impact(repo_url)

        if "error_message" in analysis:
            print(f"Error: {analysis['error_message']}")
        else:
            print(f"Summary: {analysis.get('summary', 'N/A')}")
            print(f"Risk Level: {analysis.get('risk_level', 'N/A')}")

            cves = analysis.get("cve_analysis", [])
            if cves:
                print(f"\nCVEs Found: {len(cves)}")
                for cve in cves[:3]:
                    print(f"  - {cve.get('cve_id', 'N/A')}: {cve.get('severity', 'N/A')}")

            recommendations = analysis.get("recommendations", [])
            if recommendations:
                print(f"\nRecommendations:")
                for rec in recommendations[:3]:
                    print(f"  - {rec}")

            print(f"\nAnalysis generated successfully!")

    except Exception as e:
        print(f"Error: {e}")
