"""Core logic for Issue Fix Planner agent."""

import json
import logging
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.github_tool import fetch_issue, fetch_repo, GitHubAPIError
from core.llm import call_llm, LLMUnavailableError
from core.llm_utils import wrap_untrusted
from core.search import search
from agents.issue_fix_planner.prompts import (
    SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MAX_ISSUE_BODY_CHARS = 4000


def run_issue_fix_planner(issue_url: str) -> dict:
    """
    Generate an implementation plan for a GitHub issue without writing code.

    Args:
        issue_url: GitHub issue URL (e.g., https://github.com/user/repo/issues/123)

    Returns:
        A dict with implementation plan data:
        - plan: the implementation plan text
        - issue_title: title of the issue
        - issue_url: original URL
        - status: "success" or "error"

        On error, returns a dict with error_message and issue_url.
    """
    logger.info(f"Generating fix plan for: {issue_url}")

    # Step 1: Fetch the issue
    try:
        issue_data = fetch_issue(issue_url)
        logger.info(f"Fetched issue: {issue_data.get('title', 'Unknown')}")
    except GitHubAPIError as e:
        logger.error(f"Failed to fetch issue: {e}")
        return {"error_message": str(e), "issue_url": issue_url, "status": "error"}
    except Exception as e:
        logger.error(f"Unexpected error fetching issue: {e}")
        return {
            "error_message": "Failed to fetch issue. Please try again.",
            "issue_url": issue_url,
            "status": "error",
        }

    issue_title = issue_data.get("title", "Unknown")
    issue_body = issue_data.get("body", "")

    # Step 2: Extract repo URL from issue URL and fetch repo data
    try:
        repo_url = extract_repo_url(issue_url)
        repo_data = fetch_repo(repo_url)
        logger.info(f"Fetched repo data for: {repo_data.get('name', 'Unknown')}")
    except Exception as e:
        logger.error(f"Failed to fetch repo: {e}")
        repo_data = {}

    # Step 3: Search for related files
    search_query = f"{repo_data.get('name', '')} {issue_title}"
    try:
        search_results = search(search_query, max_results=5)
        logger.info(f"Found {len(search_results)} search results")
        search_summary = "\n".join(
            [f"- {r['title']}: {r['snippet'][:100]}" for r in search_results[:3]]
        )
    except Exception as e:
        logger.error(f"Search failed: {e}")
        search_summary = "No search results available"

    # Step 4: Generate implementation plan
    repo_context = f"""
Repository: {repo_data.get('name', 'Unknown')}
Description: {repo_data.get('description', 'N/A')}
Language: {repo_data.get('language', 'Unknown')}

File Structure (first 100 entries):
{repo_data.get('file_tree', 'Not available')[:1000]}

README (first 1000 chars):
{repo_data.get('readme', 'Not available')[:1000]}

Related Search Results:
{search_summary}
"""

    user_prompt = USER_PROMPT_TEMPLATE.format(
        issue_url=issue_url,
        issue_details=wrap_untrusted(f"Title: {issue_title}\nBody: {(issue_body or '')[:MAX_ISSUE_BODY_CHARS]}"),
        repo_data=wrap_untrusted(repo_context),
    )

    try:
        plan = call_llm(user_prompt, system=SYSTEM_PROMPT)
        logger.info(f"Generated plan: {plan[:100]}...")
    except LLMUnavailableError as e:
        logger.error(f"LLM call failed: {e}")
        return {"error_message": str(e), "issue_url": issue_url, "status": "error"}
    except Exception as e:
        logger.error(f"Unexpected LLM error: {e}")
        return {
            "error_message": "Failed to generate plan. Please try again.",
            "issue_url": issue_url,
            "status": "error",
        }

    return {
        "plan": plan,
        "issue_title": issue_title,
        "issue_url": issue_url,
        "status": "success",
    }


def extract_repo_url(issue_url: str) -> str:
    """Extract repo URL from issue URL."""
    parts = issue_url.rstrip("/").split("/")
    if len(parts) >= 5:
        return f"https://github.com/{parts[-4]}/{parts[-3]}"
    return "https://github.com/anthropics/anthropic-sdk-python"


if __name__ == "__main__":
    print("=== Issue Fix Planner Smoke Test ===\n")

    issue_url = "https://github.com/anthropics/anthropic-sdk-python/issues/1"
    print(f"Generating fix plan for: {issue_url}\n")

    try:
        result = run_issue_fix_planner(issue_url)

        if result.get("status") == "error":
            print(f"Error: {result.get('error_message')}")
        else:
            print(f"Issue: {result.get('issue_title')}")
            print(f"\nImplementation Plan:")
            print(result.get("plan", "No plan generated"))
            print("\nPlan generated successfully!")

    except Exception as e:
        print(f"Error: {e}")
