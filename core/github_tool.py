"""GitHub interaction tools for micro-agents."""

import logging
import os
import re
import time
import requests
from dotenv import load_dotenv

from core.targets import USERNAME_RE

logger = logging.getLogger(__name__)


GITHUB_API_BASE = "https://api.github.com"

# GitHub's own rate limit is shared across every visitor of a deployed instance when GITHUB_TOKEN
# isn't set (60 req/hr unauthenticated). This process-wide state tracks the last known budget from
# response headers so we can fail fast with a clear message instead of burning through it silently
# mid-request and surfacing a confusing 403 partway through a multi-call fetch.
_rate_limit_state = {"remaining": None, "reset_at": None}
_RATE_LIMIT_FLOOR = 2

# This app only ever reads from GitHub (repo metadata, README, file listing, issues, PRs). A token
# with broader scopes than recommended (`public_repo`, read-only) is more powerful than this app
# needs, so if one is misconfigured we want a visible warning rather than silent over-privilege.
_EXPECTED_TOKEN_SCOPES = {"public_repo", ""}
_token_scope_checked = False


# Limits on what fetch_repo hands to downstream prompts.
_MAX_TREE_ENTRIES = 100
_MAX_MANIFESTS = 4
_MAX_MANIFEST_CHARS = 8000
MANIFEST_FILES = {
    "requirements.txt",
    "pyproject.toml",
    "Pipfile",
    "package.json",
    "go.mod",
    "Cargo.toml",
    "Gemfile",
    "pom.xml",
    "build.gradle",
    "composer.json",
}


class GitHubAPIError(Exception):
    """Raised when GitHub API returns an error."""

    pass


def _check_rate_limit_budget() -> None:
    """Raise early if the last known GitHub rate limit budget is nearly exhausted."""
    remaining = _rate_limit_state.get("remaining")
    reset_at = _rate_limit_state.get("reset_at")
    if remaining is not None and remaining <= _RATE_LIMIT_FLOOR and reset_at and time.time() < reset_at:
        wait_s = int(reset_at - time.time())
        raise GitHubAPIError(
            f"GitHub rate limit nearly exhausted ({remaining} requests left); resets in {wait_s}s. "
            "Set GITHUB_TOKEN for a much higher limit, or try again later."
        )


def _record_rate_limit(resp: requests.Response) -> None:
    """Cache the rate limit budget reported by GitHub on this response, if present."""
    try:
        remaining = resp.headers.get("X-RateLimit-Remaining")
        reset = resp.headers.get("X-RateLimit-Reset")
        if remaining is not None:
            _rate_limit_state["remaining"] = int(remaining)
        if reset is not None:
            _rate_limit_state["reset_at"] = int(reset)
    except (TypeError, ValueError):
        pass

    _warn_if_token_overscoped(resp)


def _warn_if_token_overscoped(resp: requests.Response) -> None:
    """Log a one-time warning if GITHUB_TOKEN reports scopes broader than read-only public_repo."""
    global _token_scope_checked
    if _token_scope_checked:
        return

    scopes_header = resp.headers.get("X-OAuth-Scopes")
    if scopes_header is None:
        return  # No token used, or a fine-grained PAT that doesn't report classic scopes here

    _token_scope_checked = True
    scopes = {s.strip() for s in scopes_header.split(",")}
    if not scopes.issubset(_EXPECTED_TOKEN_SCOPES):
        logger.warning(
            f"GITHUB_TOKEN has scopes {sorted(scopes)}, broader than the recommended read-only "
            "'public_repo' scope. Consider issuing a token scoped to public_repo (or a fine-grained "
            "PAT with read-only repository access) instead."
        )


def _get_headers() -> dict:
    """Get headers for GitHub API requests, including auth token if available."""
    load_dotenv()
    headers = {"Accept": "application/vnd.github.v3+json"}
    github_token = os.getenv("GITHUB_TOKEN")
    if github_token:
        headers["Authorization"] = f"token {github_token}"
    return headers


def _parse_repo_url(url: str) -> tuple[str, str]:
    """Parse owner and repo from a GitHub URL."""
    match = re.match(r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", url)
    if not match:
        raise GitHubAPIError(f"Invalid GitHub repository URL: {url}")
    return match.group(1), match.group(2)


def _parse_issue_url(url: str) -> tuple[str, str, int]:
    """Parse owner, repo, and issue number from a GitHub URL."""
    match = re.match(
        r"https?://github\.com/([^/]+)/([^/]+)/issues/(\d+)/?$", url
    )
    if not match:
        raise GitHubAPIError(f"Invalid GitHub issue URL: {url}")
    return match.group(1), match.group(2), int(match.group(3))


def _parse_pr_url(url: str) -> tuple[str, str, int]:
    """Parse owner, repo, and PR number from a GitHub URL."""
    match = re.match(r"https?://github\.com/([^/]+)/([^/]+)/pull/(\d+)/?$", url)
    if not match:
        raise GitHubAPIError(f"Invalid GitHub pull request URL: {url}")
    return match.group(1), match.group(2), int(match.group(3))


def _format_file_tree(files: list[dict]) -> str:
    """Render the top-level listing as one entry per line, directories suffixed with '/'."""
    lines = [f["name"] + ("/" if f["type"] == "dir" else "") for f in files[:_MAX_TREE_ENTRIES]]
    if len(files) > _MAX_TREE_ENTRIES:
        lines.append(f"... ({len(files) - _MAX_TREE_ENTRIES} more)")
    return "\n".join(lines)


def _fetch_manifests(owner: str, repo: str, files: list[dict], headers: dict) -> dict[str, str]:
    """Fetch raw content of top-level dependency manifests. Failures are non-fatal."""
    manifests: dict[str, str] = {}
    names = [f["name"] for f in files if f["type"] == "file" and f["name"] in MANIFEST_FILES]
    for name in names[:_MAX_MANIFESTS]:
        try:
            resp = requests.get(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{name}",
                headers={**headers, "Accept": "application/vnd.github.v3.raw"},
                timeout=10,
            )
            if resp.status_code == 200:
                manifests[name] = resp.text[:_MAX_MANIFEST_CHARS]
        except Exception as e:
            logger.warning(f"Could not fetch manifest {name} for {owner}/{repo}: {e}")
    return manifests


def fetch_repo(url: str) -> dict:
    """
    Fetch repository metadata from GitHub.

    Includes repo info, README content, top-level file tree, and dependency manifests.

    Args:
        url: The GitHub repository URL (e.g., https://github.com/owner/repo)

    Returns:
        A dict with keys: name, owner, description, url, language, stars, readme,
        files (list of {name, type, size}), file_tree (newline-separated string, dirs end in '/'),
        manifests (dict of top-level manifest filename -> raw content)

    Raises:
        GitHubAPIError: On 404, rate limit, or invalid URL
    """
    owner, repo = _parse_repo_url(url)
    headers = _get_headers()
    _check_rate_limit_budget()

    try:
        # Fetch repo metadata
        repo_url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}"
        resp = requests.get(repo_url, headers=headers, timeout=10)
        _record_rate_limit(resp)
        resp.raise_for_status()
        repo_data = resp.json()

        result = {
            "name": repo_data["name"],
            "owner": repo_data["owner"]["login"],
            "description": repo_data.get("description") or "",
            "url": repo_data["html_url"],
            "language": repo_data.get("language") or "Unknown",
            "stars": repo_data.get("stargazers_count", 0),
            "readme": "",
            "files": [],
            "file_tree": "",
            "manifests": {},
        }

        # Fetch README
        readme_url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/readme"
        try:
            readme_resp = requests.get(
                readme_url,
                headers={**headers, "Accept": "application/vnd.github.v3.raw"},
                timeout=10,
            )
            if readme_resp.status_code == 200:
                result["readme"] = readme_resp.text[:2000]  # Truncate for size
        except Exception:
            pass  # README not found is OK

        # Fetch top-level file tree
        contents_url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents"
        try:
            contents_resp = requests.get(contents_url, headers=headers, timeout=10)
            if contents_resp.status_code == 200:
                for item in contents_resp.json():
                    result["files"].append(
                        {
                            "name": item["name"],
                            "type": item["type"],  # "file" or "dir"
                            "size": item.get("size", 0),
                        }
                    )
        except Exception:
            pass  # Contents fetch failure is non-fatal

        result["file_tree"] = _format_file_tree(result["files"])
        result["manifests"] = _fetch_manifests(owner, repo, result["files"], headers)

        return result

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            raise GitHubAPIError(f"Repository not found: {url}")
        elif e.response.status_code == 403:
            raise GitHubAPIError("GitHub rate limit exceeded")
        logger.error(f"GitHub API error fetching repo: {e}")
        raise GitHubAPIError("GitHub API error. Please try again.")
    except Exception as e:
        logger.error(f"Unexpected error fetching repo: {e}")
        raise GitHubAPIError("Failed to fetch repository. Please try again.")


_USERNAME_RE = USERNAME_RE
_MAX_USER_REPOS = 100


def fetch_user_profile(username: str) -> dict:
    """
    Fetch a GitHub user's public profile and their most recently pushed public repositories.

    Forks are excluded from `repos`, since they aren't the user's own work.

    Args:
        username: A GitHub username (not a URL)

    Returns:
        A dict with keys: login, name, bio, public_repos (total on the account), repos_fetched
        (how many were listed, before excluding forks), forks_excluded, and repos: a list of
        {name, description, language, stars, topics, pushed_at, archived}.

    Raises:
        GitHubAPIError: On invalid username, unknown user, or rate limit
    """
    username = (username or "").strip().lstrip("@")
    if not _USERNAME_RE.match(username):
        raise GitHubAPIError(f"Invalid GitHub username: {username!r}")

    headers = _get_headers()
    _check_rate_limit_budget()

    try:
        user_resp = requests.get(f"{GITHUB_API_BASE}/users/{username}", headers=headers, timeout=10)
        _record_rate_limit(user_resp)
        user_resp.raise_for_status()
        user = user_resp.json()

        repos_resp = requests.get(
            f"{GITHUB_API_BASE}/users/{username}/repos",
            params={"type": "owner", "sort": "pushed", "per_page": _MAX_USER_REPOS},
            headers=headers,
            timeout=10,
        )
        _record_rate_limit(repos_resp)
        repos_resp.raise_for_status()
        listed = repos_resp.json()

        repos = [
            {
                "name": r["name"],
                "description": r.get("description") or "",
                "language": r.get("language") or "",
                "stars": r.get("stargazers_count", 0),
                "topics": r.get("topics", []),
                "pushed_at": r.get("pushed_at") or "",
                "archived": bool(r.get("archived")),
            }
            for r in listed
            if not r.get("fork")
        ]
        return {
            "login": user["login"],
            "name": user.get("name") or "",
            "bio": user.get("bio") or "",
            "public_repos": user.get("public_repos", 0),
            "repos_fetched": len(listed),
            "forks_excluded": len(listed) - len(repos),
            "repos": repos,
        }

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            raise GitHubAPIError(f"GitHub user not found: {username}")
        elif e.response.status_code == 403:
            raise GitHubAPIError("GitHub rate limit exceeded")
        logger.error(f"GitHub API error fetching user: {e}")
        raise GitHubAPIError("GitHub API error. Please try again.")
    except Exception as e:
        logger.error(f"Unexpected error fetching user: {e}")
        raise GitHubAPIError("Failed to fetch GitHub user. Please try again.")


def fetch_issue(url: str) -> dict:
    """
    Fetch an issue from a GitHub repository.

    Args:
        url: The GitHub issue URL (e.g., https://github.com/owner/repo/issues/123)

    Returns:
        A dict with keys: title, body, labels, comments_count, url

    Raises:
        GitHubAPIError: On 404, rate limit, or invalid URL
    """
    owner, repo, issue_num = _parse_issue_url(url)
    headers = _get_headers()
    _check_rate_limit_budget()

    try:
        issue_url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/issues/{issue_num}"
        resp = requests.get(issue_url, headers=headers, timeout=10)
        _record_rate_limit(resp)
        resp.raise_for_status()
        issue_data = resp.json()

        return {
            "title": issue_data["title"],
            "body": issue_data.get("body", ""),
            "labels": [label["name"] for label in issue_data.get("labels", [])],
            "comments_count": issue_data.get("comments", 0),
            "url": issue_data["html_url"],
        }

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            raise GitHubAPIError(f"Issue not found: {url}")
        elif e.response.status_code == 403:
            raise GitHubAPIError("GitHub rate limit exceeded")
        logger.error(f"GitHub API error fetching issue: {e}")
        raise GitHubAPIError("GitHub API error. Please try again.")
    except Exception as e:
        logger.error(f"Unexpected error fetching issue: {e}")
        raise GitHubAPIError("Failed to fetch issue. Please try again.")


def fetch_pr(url: str) -> dict:
    """
    Fetch a pull request from a GitHub repository.

    Includes title, body, and diff summary (file list and stats, not full diff).

    Args:
        url: The GitHub PR URL (e.g., https://github.com/owner/repo/pull/123)

    Returns:
        A dict with keys: title, body, files, additions, deletions, url

    Raises:
        GitHubAPIError: On 404, rate limit, or invalid URL
    """
    owner, repo, pr_num = _parse_pr_url(url)
    headers = _get_headers()
    _check_rate_limit_budget()

    try:
        pr_url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pr_num}"
        resp = requests.get(pr_url, headers=headers, timeout=10)
        _record_rate_limit(resp)
        resp.raise_for_status()
        pr_data = resp.json()

        result = {
            "title": pr_data["title"],
            "body": pr_data.get("body", ""),
            "files": [],
            "additions": pr_data.get("additions", 0),
            "deletions": pr_data.get("deletions", 0),
            "url": pr_data["html_url"],
        }

        # Fetch files changed
        files_url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pr_num}/files"
        try:
            files_resp = requests.get(files_url, headers=headers, timeout=10)
            if files_resp.status_code == 200:
                for file_data in files_resp.json():
                    result["files"].append(
                        {
                            "filename": file_data["filename"],
                            "additions": file_data.get("additions", 0),
                            "deletions": file_data.get("deletions", 0),
                            "status": file_data.get("status", ""),
                        }
                    )
        except Exception:
            pass  # Files fetch failure is non-fatal

        return result

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            raise GitHubAPIError(f"Pull request not found: {url}")
        elif e.response.status_code == 403:
            raise GitHubAPIError("GitHub rate limit exceeded")
        logger.error(f"GitHub API error fetching PR: {e}")
        raise GitHubAPIError("GitHub API error. Please try again.")
    except Exception as e:
        logger.error(f"Unexpected error fetching PR: {e}")
        raise GitHubAPIError("Failed to fetch pull request. Please try again.")


if __name__ == "__main__":
    print("Smoke tests for GitHub tools:")

    # Test repo fetch
    try:
        repo_data = fetch_repo("https://github.com/anthropics/anthropic-sdk-python")
        print(f"\n✓ fetch_repo: {repo_data.get('name', 'N/A')} by {repo_data.get('owner', 'N/A')}")
        desc = repo_data.get("description", "")
        if desc:
            print(f"  Description: {desc[:60]}...")
        print(f"  Top-level files: {len(repo_data.get('files', []))} items")
    except Exception as e:
        print(f"✗ fetch_repo failed: {e}")

    # Test issue fetch
    try:
        # Using a stable public issue
        issue_data = fetch_issue(
            "https://github.com/anthropics/anthropic-sdk-python/issues/1"
        )
        print(f"\n✓ fetch_issue: {issue_data.get('title', 'N/A')}")
        print(f"  Labels: {issue_data.get('labels', [])}")
        print(f"  Comments: {issue_data.get('comments_count', 0)}")
    except Exception as e:
        print(f"✗ fetch_issue failed: {e}")

    # Test PR fetch (using a different repo with more PRs)
    try:
        pr_data = fetch_pr(
            "https://github.com/anthropics/anthropic-sdk-python/pull/100"
        )
        print(f"\n✓ fetch_pr: {pr_data.get('title', 'N/A')}")
        print(f"  Changes: +{pr_data.get('additions', 0)} -{pr_data.get('deletions', 0)}")
        print(f"  Files changed: {len(pr_data.get('files', []))}")
    except GitHubAPIError as e:
        print(f"✗ fetch_pr not found (expected for this test): {e}")
    except Exception as e:
        print(f"✗ fetch_pr failed: {e}")
