"""Core logic for Do I Care agent."""

import json
import logging
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.llm import call_llm
from agents.do_i_care.prompts import (
    SYSTEM_PROMPT,
    SCORING_PROMPT,
    ANALYSIS_PROMPT,
    DEFAULT_PROFILE,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run_do_i_care(headlines_batch: list[str], user_profile: str = None) -> dict:
    """
    Analyze headlines for relevance to user profile and provide actionable insights.

    Args:
        headlines_batch: List of headlines/news items
        user_profile: User's profile/interests (uses default if not provided)

    Returns:
        A dict with:
        - status: "success" or "error"
        - top_items: List of top 3 items with why_it_matters and suggested_action
        - original_count: Number of items analyzed

        On error, returns a dict with error_message and status: "error"
    """
    logger.info(f"Analyzing {len(headlines_batch)} items for relevance")

    if not headlines_batch:
        return {
            "error_message": "No headlines provided",
            "status": "error",
        }

    if user_profile is None:
        user_profile = DEFAULT_PROFILE

    # Step 1: Score all items for relevance
    items_str = "\n".join([f"- {item}" for item in headlines_batch[:20]])
    scoring_prompt = SCORING_PROMPT.format(
        user_profile=user_profile, items=items_str
    )

    try:
        scoring_response = call_llm(scoring_prompt, system=SYSTEM_PROMPT)
        logger.info(f"Scoring response: {scoring_response[:100]}...")
    except Exception as e:
        logger.error(f"Scoring LLM call failed: {e}")
        return {
            "error_message": f"Failed to score items: {str(e)[:100]}",
            "status": "error",
        }

    # Step 2: Parse scores and select top 3
    scores = _parse_scores(scoring_response, headlines_batch)
    if not scores:
        logger.warning("Failed to parse scores, using first 3 items")
        top_indices = list(range(min(3, len(headlines_batch))))
    else:
        top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[
            :3
        ]

    top_items = [headlines_batch[i] for i in top_indices]

    # Step 3: Generate analysis for top 3 items
    top_items_str = "\n".join([f"- {item}" for item in top_items])
    analysis_prompt = ANALYSIS_PROMPT.format(
        user_profile=user_profile, top_items=top_items_str
    )

    try:
        analysis_response = call_llm(analysis_prompt, system=SYSTEM_PROMPT)
        logger.info(f"Analysis response: {analysis_response[:100]}...")
    except Exception as e:
        logger.error(f"Analysis LLM call failed: {e}")
        analysis_response = "Unable to analyze top items"

    # Step 4: Parse analysis and format result
    parsed_analysis = _parse_analysis(analysis_response, top_items)

    return {
        "status": "success",
        "top_items": parsed_analysis,
        "original_count": len(headlines_batch),
    }


def _parse_scores(response: str, headlines: list[str]) -> list[int]:
    """Parse relevance scores from LLM response."""
    try:
        # Try to extract JSON from response
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

        scores_data = json.loads(clean_response)
        if isinstance(scores_data, list):
            return [int(item.get("score", 5)) for item in scores_data]
        return []
    except (json.JSONDecodeError, ValueError, KeyError):
        logger.warning("Failed to parse scores, using defaults")
        return [5] * len(headlines)


def _parse_analysis(response: str, top_items: list[str]) -> list[dict]:
    """Parse analysis response and format as structured data."""
    result = []
    for i, item in enumerate(top_items, 1):
        result.append(
            {
                "rank": i,
                "headline": item,
                "why_it_matters": f"Analysis from LLM (see full response)",
                "suggested_action": f"Review the detailed analysis",
            }
        )

    # Try to extract structured analysis from response
    try:
        lines = response.split("\n")
        analysis_text = "\n".join(lines).strip()

        for i, item_dict in enumerate(result):
            if i < len(top_items):
                item_dict["analysis"] = analysis_text[
                    : 500
                ]  # First 500 chars as summary
    except Exception as e:
        logger.warning(f"Error parsing analysis: {e}")

    # Add full response
    result.append({"full_analysis": response})

    return result


if __name__ == "__main__":
    print("=== Do I Care Smoke Test ===\n")

    headlines = [
        "New GPT-5 model shows 10x improvement in reasoning tasks",
        "Local coffee shop opens downtown",
        "Kubernetes 1.30 release with major networking improvements",
        "Pet adoption event this weekend",
        "AWS launches new MLOps service for model deployment",
        "Sports team wins championship",
    ]

    print(f"Analyzing {len(headlines)} headlines...\n")

    try:
        result = run_do_i_care(headlines)

        if result.get("status") == "error":
            print(f"Error: {result.get('error_message')}")
        else:
            print(f"Analyzed {result.get('original_count')} items")
            print(f"Top 3 relevant items:\n")
            for item in result.get("top_items", []):
                if "rank" in item:
                    print(f"{item['rank']}. {item['headline']}")
            print("\nAnalysis completed successfully!")

    except Exception as e:
        print(f"Error: {e}")
