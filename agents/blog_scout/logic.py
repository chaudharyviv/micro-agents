"""Core logic for Blog Idea Scout agent."""

import json
import logging
import sys
from pathlib import Path
from typing import Optional

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.search import web_search
from core.llm import call_llm
from agents.blog_scout.prompts import (
    SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
    DEFAULT_TOPICS,
)


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def scout_blog_ideas(topic: Optional[str] = None) -> list[dict]:
    """
    Generate blog ideas based on trending topics or a specific topic.

    If no topic is provided, searches default topics (GitHub trending,
    AI agent engineering, platform engineering trends).

    Args:
        topic: Optional specific topic to search for blog ideas.
               If None, uses DEFAULT_TOPICS.

    Returns:
        A list of blog idea dicts, each with:
        - title: proposed blog post title
        - pitch: one-sentence pitch
        - source_url: URL from search results that inspired this
        - source_title: title of that source

        If no ideas can be generated, returns a list with one
        explanatory dict rather than raising or returning empty list.
    """
    # Determine search queries
    if topic and topic.strip():
        queries = [topic.strip()]
        logger.info(f"Searching for topic: {topic}")
    else:
        queries = DEFAULT_TOPICS
        logger.info(f"Using default topics: {queries}")

    # Collect search results
    all_results = []
    search_result_urls = set()  # Track URLs for validation

    for query in queries:
        logger.info(f"Searching: {query}")
        try:
            results = web_search(query, max_results=5)
            all_results.extend(results)
            for result in results:
                if "url" in result:
                    search_result_urls.add(result["url"])
                    logger.info(f"  Found: {result.get('title', 'N/A')} ({result['url']})")
        except Exception as e:
            logger.warning(f"Search failed for '{query}': {e}")

    if not all_results:
        logger.warning("No search results found")
        return [
            {
                "title": "Unable to generate ideas",
                "pitch": "No search results were returned. Please try again later.",
                "source_url": "",
                "source_title": "",
            }
        ]

    # Format search results for LLM
    formatted_results = "\n".join(
        [
            f"- {r.get('title', 'Untitled')}: {r.get('snippet', 'No description')} ({r.get('url', 'N/A')})"
            for r in all_results
        ]
    )

    user_prompt = USER_PROMPT_TEMPLATE.format(search_results=formatted_results)

    # Call LLM to generate ideas
    logger.info("Calling LLM to generate blog ideas")
    try:
        llm_response = call_llm(user_prompt, system=SYSTEM_PROMPT)
        logger.info(f"LLM response: {llm_response[:100]}...")
    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        return [
            {
                "title": "Unable to generate ideas",
                "pitch": f"LLM service unavailable: {str(e)[:50]}",
                "source_url": "",
                "source_title": "",
            }
        ]

    # Parse LLM response (with one retry on failure)
    ideas = _parse_ideas(llm_response, search_result_urls)

    if ideas is None:
        # Retry once
        logger.warning("Parse failed. Retrying with error message.")
        error_msg = "Your previous response was not valid JSON. Please respond again with ONLY a JSON array."
        retry_prompt = user_prompt + "\n\n" + error_msg

        try:
            llm_response = call_llm(retry_prompt, system=SYSTEM_PROMPT)
            ideas = _parse_ideas(llm_response, search_result_urls)
        except Exception as e:
            logger.error(f"LLM retry failed: {e}")

    # Fallback if still no valid ideas
    if ideas is None:
        logger.error("Failed to parse LLM response even after retry")
        return [
            {
                "title": "Unable to generate ideas",
                "pitch": "Failed to parse blog idea suggestions. Please try again.",
                "source_url": "",
                "source_title": "",
            }
        ]

    return ideas if ideas else [
        {
            "title": "Unable to generate ideas",
            "pitch": "No valid blog ideas were generated.",
            "source_url": "",
            "source_title": "",
        }
    ]


def _parse_ideas(response: str, search_result_urls: set) -> Optional[list[dict]]:
    """
    Parse JSON response from LLM.

    Args:
        response: LLM's JSON response (may be wrapped in markdown code blocks)
        search_result_urls: Set of valid URLs from search results

    Returns:
        Parsed list of ideas, or None if parsing fails
    """
    try:
        # Extract JSON from markdown code blocks if present
        clean_response = response.strip()
        if clean_response.startswith("```"):
            # Remove markdown code block wrapper
            lines = clean_response.split("\n")
            # Find the actual JSON content
            json_lines = []
            in_json = False
            for line in lines:
                if line.startswith("```"):
                    in_json = not in_json
                    continue
                if in_json or (not line.startswith("```") and json_lines):
                    json_lines.append(line)
            clean_response = "\n".join(json_lines).strip()

        ideas = json.loads(clean_response)

        if not isinstance(ideas, list):
            logger.error("Response is not a JSON array")
            return None

        # Validate each idea has required fields and URLs are from search results
        validated_ideas = []
        for i, idea in enumerate(ideas):
            if not isinstance(idea, dict):
                logger.warning(f"Idea {i} is not a dict, skipping")
                continue

            # Check required fields
            if not all(k in idea for k in ["title", "pitch", "source_url", "source_title"]):
                logger.warning(f"Idea {i} missing required fields, skipping")
                continue

            # Validate source URL is from search results
            source_url = idea.get("source_url", "").strip()
            if source_url and source_url not in search_result_urls:
                logger.warning(
                    f"Idea {i} source_url '{source_url}' not in search results, skipping"
                )
                continue

            validated_ideas.append(idea)

        if not validated_ideas:
            logger.warning("No ideas survived validation")
            return None

        logger.info(f"Successfully parsed {len(validated_ideas)} ideas")
        return validated_ideas

    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e}")
        return None
    except Exception as e:
        logger.error(f"Parse error: {e}")
        return None


if __name__ == "__main__":
    print("=== Blog Scout Smoke Test ===\n")

    # Test with default topics
    print("Generating blog ideas from default topics...\n")
    try:
        ideas = scout_blog_ideas()
        print(f"Generated {len(ideas)} idea(s):\n")
        for i, idea in enumerate(ideas, 1):
            print(f"{i}. {idea.get('title', 'N/A')}")
            print(f"   Pitch: {idea.get('pitch', 'N/A')}")
            print(f"   Source: {idea.get('source_title', 'N/A')}")
            print(f"   URL: {idea.get('source_url', 'N/A')}\n")
    except Exception as e:
        print(f"Error: {e}")

    # Test with specific topic
    print("\n" + "=" * 50)
    print("Generating blog ideas for a specific topic...\n")
    try:
        ideas = scout_blog_ideas(topic="Rust programming language")
        print(f"Generated {len(ideas)} idea(s):\n")
        for i, idea in enumerate(ideas, 1):
            print(f"{i}. {idea.get('title', 'N/A')}")
            print(f"   Pitch: {idea.get('pitch', 'N/A')}")
            print(f"   Source: {idea.get('source_title', 'N/A')}")
            print(f"   URL: {idea.get('source_url', 'N/A')}\n")
    except Exception as e:
        print(f"Error: {e}")
