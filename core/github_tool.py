"""GitHub interaction tools for micro-agents."""

import os
import re
import requests
from dotenv import load_dotenv


GITHUB_API_BASE = "https://api.github.com"


class GitHubAPIError(Exception):
    """Raised when GitHub API returns an error."""

    pass


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


def fetch_repo(url: str) -> dict:
    """
    Fetch repository metadata from GitHub.

    Includes repo info, README content, and top-level file tree.

    Args:
        url: The GitHub repository URL (e.g., https://github.com/owner/repo)

    Returns:
        A dict with keys: name, owner, description, url, readme, files

    Raises:
        GitHubAPIError: On 404, rate limit, or invalid URL
    """
    owner, repo = _parse_repo_url(url)
    headers = _get_headers()

    try:
        # Fetch repo metadata
        repo_url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}"
        resp = requests.get(repo_url, headers=headers, timeout=10)
        resp.raise_for_status()
        repo_data = resp.json()

        result = {
            "name": repo_data["name"],
            "owner": repo_data["owner"]["login"],
            "description": repo_data.get("description", ""),
            "url": repo_data["html_url"],
            "readme": "",
            "files": [],
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

        return result

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            raise GitHubAPIError(f"Repository not found: {url}")
        elif e.response.status_code == 403:
            raise GitHubAPIError("GitHub rate limit exceeded")
        raise GitHubAPIError(f"GitHub API error: {e}")
    except Exception as e:
        raise GitHubAPIError(f"Failed to fetch repository: {e}")


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

    try:
        issue_url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/issues/{issue_num}"
        resp = requests.get(issue_url, headers=headers, timeout=10)
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
        raise GitHubAPIError(f"GitHub API error: {e}")
    except Exception as e:
        raise GitHubAPIError(f"Failed to fetch issue: {e}")


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

    try:
        pr_url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pr_num}"
        resp = requests.get(pr_url, headers=headers, timeout=10)
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
        raise GitHubAPIError(f"GitHub API error: {e}")
    except Exception as e:
        raise GitHubAPIError(f"Failed to fetch pull request: {e}")


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
