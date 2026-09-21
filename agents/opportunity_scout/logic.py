"""Core logic for Opportunity Scout agent.

Compares a developer's public GitHub repositories (fetched from the GitHub API) against current
job-market web search results. The repository evidence is computed in code; the LLM only reasons
over the evidence and search results it is given, and cited URLs are checked against the results.
"""

import logging
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.github_tool import fetch_user_profile, GitHubAPIError
from core.llm import call_llm, LLMUnavailableError
from core.llm_utils import extract_json, wrap_untrusted
from core.search import search
from agents.opportunity_scout.prompts import (
    SYSTEM_PROMPT,
    ANALYSIS_PROMPT,
    MARKET_QUERIES,
    RESPONSE_SCHEMA,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MAX_MARKET_RESULTS = 8
MAX_REPOS_FOR_LLM = 20
_SNIPPET_CHARS = 300


def run_opportunity_scout(github_username: str) -> dict:
    """
    Identify career opportunities for a GitHub developer.

    Args:
        github_username: GitHub username to analyze

    Returns:
        A dict with:
        - status: "success" or "error"
        - github_username
        - current_skills: list of skills demonstrated in the developer's public repos
        - skill_gaps: list of {skill, reason, source_url}; source_url is one of the market search
          results, or None when the gap is the model's own judgment
        - job_suggestions: list of {role, fit}
        - project_idea: {title, description, skills_built}
        - evidence: what the analysis was based on (repos analyzed, languages, topics, activity)
        - market_sources: list of {title, url} for the search results the analysis was given

        On error, returns a dict with error_message and status: "error"
    """
    logger.info(f"Analyzing opportunities for: {github_username}")

    if not github_username or not github_username.strip():
        return {"error_message": "GitHub username required", "status": "error"}

    # Step 1: Public repositories from the GitHub API
    try:
        profile = fetch_user_profile(github_username)
    except GitHubAPIError as e:
        logger.error(f"Failed to fetch GitHub profile: {e}")
        return {"error_message": str(e), "status": "error"}

    if not profile["repos"]:
        return {
            "error_message": (
                f"{profile['login']} has no public original repositories to analyze "
                "(forks are excluded), so there is nothing to base an analysis on."
            ),
            "status": "error",
        }
    evidence = _build_evidence(profile)

    # Step 2: Current job-market data from web search
    market = _search_market()
    if not market:
        return {
            "error_message": "Could not retrieve current job-market data, so no comparison can be made. Please try again.",
            "status": "error",
        }

    # Step 3: Compare the two
    prompt = ANALYSIS_PROMPT.format(
        github_username=profile["login"],
        evidence=wrap_untrusted(_format_evidence(evidence)),
        market=wrap_untrusted(_format_market(market)),
    )
    allowed_urls = {m["url"] for m in market}

    analysis = None
    for attempt in range(2):
        try:
            response = call_llm(
                prompt if attempt == 0 else prompt + "\n\nYour previous response was invalid. Respond with ONLY the JSON object described.",
                system=SYSTEM_PROMPT,
                schema=RESPONSE_SCHEMA,
                schema_name="opportunity_analysis",
            )
        except LLMUnavailableError as e:
            logger.error(f"Analysis failed: {e}")
            return {"error_message": str(e), "status": "error"}
        except Exception as e:
            logger.error(f"Unexpected analysis error: {e}")
            return {"error_message": "Failed to generate analysis. Please try again.", "status": "error"}
        analysis = _parse_analysis(response, allowed_urls)
        if analysis is not None:
            break

    if analysis is None:
        return {"error_message": "Failed to generate a valid analysis. Please try again.", "status": "error"}

    return {
        "status": "success",
        "github_username": profile["login"],
        "current_skills": analysis["current_skills"],
        "skill_gaps": analysis["skill_gaps"],
        "job_suggestions": analysis["opportunities"],
        "project_idea": analysis["project_idea"],
        "evidence": evidence,
        "market_sources": [{"title": m["title"], "url": m["url"]} for m in market],
    }


def _build_evidence(profile: dict) -> dict:
    """Summarize the repositories deterministically: languages, topics, activity, notable repos."""
    repos = profile["repos"]
    languages = Counter(r["language"] for r in repos if r["language"])
    topics = Counter(t for r in repos for t in r["topics"])
    cutoff = datetime.now(timezone.utc) - timedelta(days=365)
    recent = [r for r in repos if _pushed_after(r["pushed_at"], cutoff)]
    notable = sorted(repos, key=lambda r: (-r["stars"], r["name"]))[:MAX_REPOS_FOR_LLM]
    return {
        "bio": profile["bio"],
        "repos_analyzed": len(repos),
        "repos_on_account": profile["public_repos"],
        "forks_excluded": profile["forks_excluded"],
        "repos_pushed_last_12_months": len(recent),
        "languages": languages.most_common(8),
        "topics": topics.most_common(15),
        "notable_repos": [
            {k: r[k] for k in ("name", "description", "language", "stars", "topics")} for r in notable
        ],
    }


def _pushed_after(pushed_at: str, cutoff: datetime) -> bool:
    try:
        return datetime.fromisoformat(pushed_at.replace("Z", "+00:00")) >= cutoff
    except ValueError:
        return False


def _format_evidence(evidence: dict) -> str:
    lines = [
        f"Bio: {evidence['bio'] or '(none)'}",
        f"Public non-fork repositories analyzed: {evidence['repos_analyzed']} "
        f"(account lists {evidence['repos_on_account']} public repos; {evidence['forks_excluded']} forks excluded)",
        f"Repositories pushed in the last 12 months: {evidence['repos_pushed_last_12_months']}",
        "Languages by repository count: " + (", ".join(f"{n} ({c})" for n, c in evidence["languages"]) or "(none)"),
        "Topics: " + (", ".join(f"{n} ({c})" for n, c in evidence["topics"]) or "(none)"),
        "Notable repositories (by stars):",
    ]
    for r in evidence["notable_repos"]:
        lines.append(f"- {r['name']} [{r['language'] or 'n/a'}, {r['stars']} stars]: {r['description'] or '(no description)'}")
    return "\n".join(lines)


def _search_market() -> list[dict]:
    """Current job-market search results, de-duplicated by URL. Empty if every search failed."""
    year = date.today().year
    results, seen = [], set()
    for template in MARKET_QUERIES:
        try:
            found = search(template.format(year=year), max_results=5)
        except Exception as e:
            logger.warning(f"Market search failed: {e}")
            continue
        for r in found:
            url = r.get("url", "")
            if url.startswith(("http://", "https://")) and url not in seen:
                seen.add(url)
                results.append({"title": r.get("title", ""), "url": url, "snippet": r.get("snippet", "")})
    return results[:MAX_MARKET_RESULTS]


def _format_market(market: list[dict]) -> str:
    return "\n".join(
        f"[{i}] {m['title']}\n    URL: {m['url']}\n    {m['snippet'][:_SNIPPET_CHARS]}" for i, m in enumerate(market, 1)
    )


def _str_list(value) -> list[str]:
    return [s.strip() for s in value if isinstance(s, str) and s.strip()] if isinstance(value, list) else []


def _parse_analysis(response: str, allowed_urls: set[str]) -> Optional[dict]:
    """
    Parse and validate the LLM's analysis.

    Malformed list entries are dropped. A skill gap's source_url is kept only if it exactly matches
    one of the search results the model was given; otherwise it becomes None. Returns None if the
    response isn't usable (not an object, no project idea, or nothing to report).
    """
    try:
        data = extract_json(response)
    except ValueError:
        logger.warning("Failed to parse analysis JSON")
        return None
    if not isinstance(data, dict):
        return None

    gaps = []
    for g in data.get("skill_gaps") if isinstance(data.get("skill_gaps"), list) else []:
        if not isinstance(g, dict) or not isinstance(g.get("skill"), str) or not g["skill"].strip():
            continue
        url = g.get("source_url")
        gaps.append(
            {
                "skill": g["skill"].strip(),
                "reason": g["reason"].strip() if isinstance(g.get("reason"), str) else "",
                "source_url": url if isinstance(url, str) and url in allowed_urls else None,
            }
        )

    opportunities = []
    for o in data.get("opportunities") if isinstance(data.get("opportunities"), list) else []:
        if isinstance(o, dict) and isinstance(o.get("role"), str) and o["role"].strip():
            opportunities.append(
                {"role": o["role"].strip(), "fit": o["fit"].strip() if isinstance(o.get("fit"), str) else ""}
            )

    idea = data.get("project_idea")
    if not (isinstance(idea, dict) and isinstance(idea.get("title"), str) and idea["title"].strip()
            and isinstance(idea.get("description"), str) and idea["description"].strip()):
        return None

    if not gaps and not opportunities:
        return None

    return {
        "current_skills": _str_list(data.get("current_skills")),
        "skill_gaps": gaps,
        "opportunities": opportunities,
        "project_idea": {
            "title": idea["title"].strip(),
            "description": idea["description"].strip(),
            "skills_built": _str_list(idea.get("skills_built")),
        },
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
            print(f"Repos analyzed: {result['evidence']['repos_analyzed']}")
            print("\nSkill gaps:")
            for gap in result["skill_gaps"]:
                print(f"  - {gap['skill']}: {gap['reason']}")
            print(f"\n3-day project: {result['project_idea']['title']}")

    except Exception as e:
        print(f"Error: {e}")
