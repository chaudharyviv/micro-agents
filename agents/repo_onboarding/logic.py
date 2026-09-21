"""Core logic for Repo Onboarding agent."""

import logging
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.github_tool import fetch_repo, GitHubAPIError
from core.llm import call_llm, LLMUnavailableError
from core.llm_utils import extract_json, wrap_untrusted
from agents.repo_onboarding.prompts import (
    RESPONSE_SCHEMA,
    SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def generate_onboarding_guide(repo_url: str) -> dict:
    """
    Generate a comprehensive onboarding guide for a GitHub repository.

    Args:
        repo_url: GitHub repository URL (e.g., https://github.com/user/repo)

    Returns:
        A dict with onboarding guide data:
        - project_name: name of the project
        - overview: description
        - tech_stack: list of technologies
        - setup_steps: list of setup instructions
        - project_structure: dict of directory descriptions
        - key_concepts: list of architectural concepts
        - development_workflow: dict with branching, testing, building, deploying
        - common_tasks: dict with commands (run_tests, build, etc)
        - resources: list of resource links
        - repo_url: original URL

        On error, returns a dict with error_message and repo_url.
    """
    logger.info(f"Generating onboarding guide for: {repo_url}")

    # Fetch repository data
    try:
        repo_data = fetch_repo(repo_url)
        logger.info(f"Fetched repo data: {len(repo_data.get('file_tree', ''))} chars in file tree")
    except GitHubAPIError as e:
        logger.error(f"Failed to fetch repo: {e}")
        return {"error_message": str(e), "repo_url": repo_url}
    except Exception as e:
        logger.error(f"Unexpected error fetching repo: {e}")
        return {"error_message": "Failed to fetch repository. Please try again.", "repo_url": repo_url}

    # Format repo data for LLM
    formatted_repo_data = f"""
Repository Name: {repo_data.get('name', 'Unknown')}
Description: {repo_data.get('description', 'N/A')}
Language: {repo_data.get('language', 'Unknown')}
Stars: {repo_data.get('stars', 0)}

File Structure (first 100 entries):
{repo_data.get('file_tree', 'No file tree available')}

README Content (first 2000 chars):
{repo_data.get('readme', 'No README found')[:2000]}
"""

    user_prompt = USER_PROMPT_TEMPLATE.format(
        repo_url=repo_url, repo_data=wrap_untrusted(formatted_repo_data)
    )

    # Call LLM to generate guide
    logger.info("Calling LLM to generate onboarding guide")
    try:
        llm_response = call_llm(user_prompt, system=SYSTEM_PROMPT, schema=RESPONSE_SCHEMA, schema_name="onboarding_guide")
        logger.info(f"LLM response: {llm_response[:100]}...")
    except LLMUnavailableError as e:
        logger.error(f"LLM call failed: {e}")
        return {"error_message": str(e), "repo_url": repo_url}
    except Exception as e:
        logger.error(f"Unexpected LLM error: {e}")
        return {"error_message": "LLM service unavailable. Please try again.", "repo_url": repo_url}

    # Parse LLM response
    guide = _parse_guide(llm_response, repo_url)

    if guide is None:
        # Retry once
        logger.warning("Parse failed. Retrying with error message.")
        error_msg = "Your previous response was not valid JSON. Please respond again with ONLY a JSON object."
        retry_prompt = user_prompt + "\n\n" + error_msg

        try:
            llm_response = call_llm(retry_prompt, system=SYSTEM_PROMPT, schema=RESPONSE_SCHEMA, schema_name="onboarding_guide")
            guide = _parse_guide(llm_response, repo_url)
        except Exception as e:
            logger.error(f"LLM retry failed: {e}")

    # Fallback if still no valid guide
    if guide is None:
        logger.error("Failed to parse LLM response even after retry")
        return {
            "error_message": "Failed to parse onboarding guide. Please try again.",
            "repo_url": repo_url,
        }

    guide["repo_url"] = repo_url
    return guide


def _parse_guide(response: str, repo_url: str) -> Optional[dict]:
    """
    Parse JSON response from LLM.

    Args:
        response: LLM's JSON response
        repo_url: Original repository URL for context

    Returns:
        Parsed guide dict (project_structure normalized to a dict), or None if parsing fails
    """
    try:
        guide = extract_json(response)

        if not isinstance(guide, dict):
            logger.error("Response is not a JSON object")
            return None

        # Validate required fields
        required_fields = [
            "project_name",
            "overview",
            "tech_stack",
            "setup_steps",
            "development_workflow",
        ]
        if not all(k in guide for k in required_fields):
            logger.warning(f"Guide missing required fields: {required_fields}")
            return None

        guide["project_structure"] = _structure_to_dict(guide.get("project_structure"))

        logger.info(f"Successfully parsed onboarding guide for {guide.get('project_name')}")
        return guide

    except ValueError as e:
        logger.error(f"JSON parse error: {e}")
        return None
    except Exception as e:
        logger.error(f"Parse error: {e}")
        return None


def _structure_to_dict(structure) -> dict:
    """project_structure as {path: description}, from the model's [{path, description}] (or a dict)."""
    if isinstance(structure, dict):
        return {str(k): str(v) for k, v in structure.items()}
    if isinstance(structure, list):
        return {
            str(e["path"]): str(e.get("description", ""))
            for e in structure
            if isinstance(e, dict) and e.get("path")
        }
    return {}


if __name__ == "__main__":
    print("=== Repo Onboarding Smoke Test ===\n")

    repo_url = "https://github.com/anthropics/anthropic-sdk-python"
    print(f"Generating onboarding guide for: {repo_url}\n")

    try:
        guide = generate_onboarding_guide(repo_url)

        if "error_message" in guide:
            print(f"Error: {guide['error_message']}")
        else:
            print(f"Project: {guide.get('project_name', 'N/A')}")
            print(f"Overview: {guide.get('overview', 'N/A')}")
            print(f"Tech Stack: {', '.join(guide.get('tech_stack', []))}")
            print(f"\nSetup Steps ({len(guide.get('setup_steps', []))} steps):")
            for i, step in enumerate(guide.get("setup_steps", [])[:3], 1):
                print(f"  {i}. {step[:80]}...")
            print(f"\nDevelopment Workflow:")
            for key, value in guide.get("development_workflow", {}).items():
                print(f"  {key}: {str(value)[:80]}...")
            print(f"\nGuide generated successfully!")

    except Exception as e:
        print(f"Error: {e}")
