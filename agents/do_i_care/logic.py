"""Core logic for Do I Care agent: score every item, filter to the relevant few, analyze those."""

import logging
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.llm import call_llm, LLMUnavailableError
from core.llm_utils import extract_json, wrap_untrusted
from agents.do_i_care.prompts import (
    SYSTEM_PROMPT,
    SCORING_PROMPT,
    SCORING_SCHEMA,
    ANALYSIS_SCHEMA,
    ANALYSIS_PROMPT,
    DEFAULT_PROFILE,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MAX_ITEMS = 20
MAX_ITEM_CHARS = 300
MAX_PROFILE_CHARS = 1000
TOP_N = 3
MIN_RELEVANT_SCORE = 5  # items scoring below this are filtered out even if they'd make the top N
_PROFILE_PREFIX = "profile:"

_RETRY_NOTE = "\n\nYour previous response was invalid. Respond with ONLY the JSON object described."


def run_do_i_care(headlines_batch: list[str], user_profile: Optional[str] = None) -> dict:
    """
    Score headlines for relevance to a profile, keep the most relevant, and explain them.

    The profile can be passed as `user_profile`, or as a first item starting with "Profile:" (so
    single-string callers such as the UI and orchestrator can supply one). Without either, a default
    profile (a senior engineer interested in AI/ML and developer tools) is used.

    Args:
        headlines_batch: List of headlines/news items
        user_profile: User's profile/interests

    Returns:
        A dict with:
        - status: "success" or "error"
        - top_items: up to 3 items, each {rank, headline, score, score_reason, why_it_matters,
          suggested_action}. why_it_matters/suggested_action are None if the analysis step failed.
        - scores: every scored item as {headline, score}, highest first
        - original_count, analyzed_count, skipped_count: items given / scored / beyond the 20-item limit
        - profile_used: "custom" or "default", and profile: the text scored against
        - analysis_available: False if the explanation step failed
        - message: present when no item was relevant enough

        On error, returns a dict with error_message and status: "error"
    """
    items = [str(h).strip() for h in (headlines_batch or [])]
    if items and user_profile is None and items[0].lower().startswith(_PROFILE_PREFIX):
        user_profile = items[0][len(_PROFILE_PREFIX):]
        items = items[1:]
    items = [i[:MAX_ITEM_CHARS] for i in items if i]

    logger.info(f"Analyzing {len(items)} items for relevance")
    if not items:
        return {"error_message": "No headlines provided", "status": "error"}

    profile_used = "custom" if user_profile and user_profile.strip() else "default"
    profile = user_profile.strip()[:MAX_PROFILE_CHARS] if profile_used == "custom" else DEFAULT_PROFILE

    scored_items = items[:MAX_ITEMS]

    # Step 1: score every item
    scoring_prompt = SCORING_PROMPT.format(
        user_profile=profile, items=wrap_untrusted(_numbered(scored_items))
    )
    try:
        scores = _ask_json(scoring_prompt, lambda r: _parse_scores(r, len(scored_items)), SCORING_SCHEMA, "relevance_scores")
    except LLMUnavailableError as e:
        logger.error(f"Scoring LLM call failed: {e}")
        return {"error_message": str(e), "status": "error"}
    except Exception as e:
        logger.error(f"Unexpected scoring error: {e}")
        return {"error_message": "Failed to score items. Please try again.", "status": "error"}
    if not scores:
        return {"error_message": "Could not score the items (the model's response was unusable). Please try again.", "status": "error"}

    # Step 2: rank (ties keep input order) and filter
    ranked = sorted(scores, key=lambda i: (-scores[i][0], i))
    top_ids = [i for i in ranked if scores[i][0] >= MIN_RELEVANT_SCORE][:TOP_N]

    result = {
        "status": "success",
        "top_items": [],
        "scores": [{"headline": scored_items[i - 1], "score": scores[i][0]} for i in ranked],
        "original_count": len(items),
        "analyzed_count": len(scored_items),
        "skipped_count": len(items) - len(scored_items),
        "profile_used": profile_used,
        "profile": profile,
        "analysis_available": True,
    }
    if len(scores) < len(scored_items):
        result["unscored_count"] = len(scored_items) - len(scores)

    if not top_ids:
        result["message"] = (
            f"None of the {len(scores)} scored items looked relevant to this profile "
            f"(none scored {MIN_RELEVANT_SCORE} or higher)."
        )
        return result

    # Step 3: explain the survivors
    analysis_prompt = ANALYSIS_PROMPT.format(
        user_profile=profile,
        items=wrap_untrusted("\n".join(f"{i}. {scored_items[i - 1]}" for i in top_ids)),
    )
    try:
        analysis = _ask_json(analysis_prompt, lambda r: _parse_analysis(r, set(top_ids)), ANALYSIS_SCHEMA, "item_analysis")
    except Exception as e:
        logger.error(f"Analysis LLM call failed: {e}")
        analysis = None
    if not analysis:
        result["analysis_available"] = False
        analysis = {}

    for rank, i in enumerate(top_ids, 1):
        a = analysis.get(i, {})
        result["top_items"].append(
            {
                "rank": rank,
                "headline": scored_items[i - 1],
                "score": scores[i][0],
                "score_reason": scores[i][1],
                "why_it_matters": a.get("why_it_matters"),
                "suggested_action": a.get("suggested_action"),
            }
        )
    return result


def _numbered(items: list[str]) -> str:
    return "\n".join(f"{n}. {item}" for n, item in enumerate(items, 1))


def _ask_json(prompt: str, parse, schema: dict, schema_name: str):
    """Call the LLM for structured output and parse it; retry once if unusable. None if both fail."""
    for attempt in range(2):
        response = call_llm(
            prompt if attempt == 0 else prompt + _RETRY_NOTE,
            system=SYSTEM_PROMPT,
            schema=schema,
            schema_name=schema_name,
        )
        parsed = parse(response)
        if parsed:
            return parsed
        logger.warning(f"Unusable LLM response (attempt {attempt + 1})")
    return None


def _valid_id(raw, allowed) -> Optional[int]:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, str) and raw.strip().isdigit():
        raw = int(raw.strip())
    return raw if isinstance(raw, int) and raw in allowed else None


def _parse_scores(response: str, count: int) -> dict[int, tuple[int, str]]:
    """
    Parse {id: (score, reason)} from the scoring response.

    Only entries with an id in 1..count and a score in 1..10 are kept; the first entry for an id
    wins. Items the model skipped or garbled are simply absent (never given a default score).
    """
    try:
        data = extract_json(response)
    except ValueError:
        return {}
    if isinstance(data, dict):  # tolerate {"scores": [...]}
        data = next((v for v in data.values() if isinstance(v, list)), None)
    if not isinstance(data, list):
        return {}

    scores: dict[int, tuple[int, str]] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        item_id = _valid_id(entry.get("id"), range(1, count + 1))
        score = entry.get("score")
        if item_id is None or item_id in scores or isinstance(score, bool) or not isinstance(score, (int, float)):
            continue
        if not 1 <= score <= 10:
            continue
        reason = entry.get("reason")
        scores[item_id] = (int(round(score)), reason.strip() if isinstance(reason, str) else "")
    return scores


def _parse_analysis(response: str, allowed_ids: set[int]) -> dict[int, dict]:
    """Parse {id: {why_it_matters, suggested_action}} for the ids that were asked about."""
    try:
        data = extract_json(response)
    except ValueError:
        return {}
    if isinstance(data, dict):
        data = next((v for v in data.values() if isinstance(v, list)), None)
    if not isinstance(data, list):
        return {}

    analysis: dict[int, dict] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        item_id = _valid_id(entry.get("id"), allowed_ids)
        why, action = entry.get("why_it_matters"), entry.get("suggested_action")
        if item_id is None or item_id in analysis or not isinstance(why, str) or not why.strip():
            continue
        analysis[item_id] = {
            "why_it_matters": why.strip(),
            "suggested_action": action.strip() if isinstance(action, str) and action.strip() else None,
        }
    return analysis


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
            print(f"Analyzed {result.get('analyzed_count')} of {result.get('original_count')} items")
            for item in result.get("top_items", []):
                print(f"{item['rank']}. [{item['score']}] {item['headline']}")
                print(f"   Why: {item['why_it_matters']}")
                print(f"   Do:  {item['suggested_action']}")
            if result.get("message"):
                print(result["message"])

    except Exception as e:
        print(f"Error: {e}")
