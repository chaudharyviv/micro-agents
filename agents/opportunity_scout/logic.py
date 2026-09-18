"""Core logic for Opportunity Scout agent."""

import json
import logging
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.llm import call_llm, LLMUnavailableError
from core.search import search
from agents.opportunity_scout.prompts import (
    SYSTEM_PROMPT,
    ANALYSIS_PROMPT,
    REPO_ANALYSIS_PROMPT,
    JOB_MARKET_PROMPT,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run_opportunity_scout(github_username: str) -> dict:
    """
    Identify career opportunities for a GitHub developer.

    Args:
        github_username: GitHub username to analyze

    Returns:
        A dict with:
        - status: "success" or "error"
        - skill_gaps: identified skill gaps
        - job_suggestions: suggested roles/opportunities
        - project_idea: 3-day project to build missing skills

        On error, returns a dict with error_message and status: "error"
    """
    logger.info(f"Analyzing opportunities for: {github_username}")

    if not github_username or not github_username.strip():
        return {
            "error_message": "GitHub username required",
            "status": "error",
        }

    github_username = github_username.strip()

    # Step 1: Get job market trends
    try:
        job_market_response = call_llm(JOB_MARKET_PROMPT, system=SYSTEM_PROMPT)
        logger.info(f"Got job market trends: {job_market_response[:100]}...")
    except Exception as e:
        logger.error(f"Failed to get job market trends: {e}")
        job_market_response = "Unable to fetch trends"

    # Step 2: Search for developer's GitHub info and related topics
    try:
        search_results = search(f"GitHub {github_username} repositories", max_results=5)
        search_summary = "\n".join(
            [
                f"- {r['title']}: {r['snippet'][:100]}"
                for r in search_results[:3]
            ]
        )
        logger.info(f"Found {len(search_results)} search results")
    except Exception as e:
        logger.error(f"Search failed: {e}")
        search_summary = "No search results available"

    # Step 3: Analyze job market
    try:
        trending_analysis = call_llm(JOB_MARKET_PROMPT, system=SYSTEM_PROMPT)
        logger.info(f"Job market analysis: {trending_analysis[:100]}...")
    except Exception as e:
        logger.error(f"Job market analysis failed: {e}")
        trending_analysis = "Unable to analyze job market"

    # Step 4: Generate opportunity analysis
    analysis_prompt = ANALYSIS_PROMPT.format(
        github_username=github_username,
        repo_data=search_summary,
        job_trends=trending_analysis,
    )

    try:
        analysis = call_llm(analysis_prompt, system=SYSTEM_PROMPT)
        logger.info(f"Generated analysis: {analysis[:100]}...")
    except LLMUnavailableError as e:
        logger.error(f"Analysis failed: {e}")
        return {"error_message": str(e), "status": "error"}
    except Exception as e:
        logger.error(f"Unexpected analysis error: {e}")
        return {"error_message": "Failed to generate analysis. Please try again.", "status": "error"}

    # Parse the analysis
    parsed = _parse_analysis(analysis)

    return {
        "status": "success",
        "github_username": github_username,
        "skill_gaps": parsed.get("skill_gaps", "Unable to determine"),
        "job_suggestions": parsed.get("opportunities", "No suggestions available"),
        "project_idea": parsed.get("project_idea", "No project idea generated"),
        "full_analysis": analysis,
    }


def _parse_analysis(response: str) -> dict:
    """Parse analysis response from LLM."""
    try:
        clean_response = response.strip()
        if "```" in clean_response:
            lines = clean_response.split("\n")
            json_lines = []
            in_json = False
            for line in lines:
                if "```" in line:
                    in_json = not in_json
                    continue
                if in_json or (not line.startswith("```") and json_lines):
                    json_lines.append(line)
            clean_response = "\n".join(json_lines).strip()

        data = json.loads(clean_response)
        return data
    except (json.JSONDecodeError, ValueError):
        logger.warning("Failed to parse analysis JSON, returning text summary")
        return {
            "skill_gaps": "See full analysis",
            "opportunities": "See full analysis",
            "project_idea": "See full analysis",
        }


if __name__ == "__main__":
    print("=== Opportunity Scout Smoke Test ===\n")

    username = "torvalds"
    print(f"Analyzing opportunities for: {username}\n")

    try:
        result = run_opportunity_scout(username)

        if result.get("status") == "error":
            print(f"Error: {result.get('error_message')}")
        else:
            print(f"Developer: {result.get('github_username')}")
            print(f"\nSkill Gaps:")
            print(result.get("skill_gaps", "N/A"))
            print(f"\nJob Suggestions:")
            print(result.get("job_suggestions", "N/A"))
            print(f"\n3-Day Project Idea:")
            print(result.get("project_idea", "N/A"))
            print("\nAnalysis completed successfully!")

    except Exception as e:
        print(f"Error: {e}")
