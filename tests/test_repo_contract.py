"""Contract tests: real fetch_repo output must carry what each consumer agent reads.

The agent tests mock fetch_repo with hand-written dicts, so they can't catch drift between
what fetch_repo returns and what agents read. Here only the HTTP layer is mocked; fetch_repo
runs for real and its output is fed through each consumer, asserting on the LLM prompt.
"""

import pytest
import requests_mock

from core.github_tool import fetch_repo

REPO_URL = "https://github.com/owner/repo"
API = "https://api.github.com/repos/owner/repo"

REQUIREMENTS = "flask==2.0.1\nrequests==2.25.0\n"


@pytest.fixture
def github_http():
    with requests_mock.Mocker() as m:
        m.get(
            API,
            json={
                "name": "repo",
                "owner": {"login": "owner"},
                "description": "A test repo",
                "html_url": REPO_URL,
                "language": "Python",
                "stargazers_count": 4242,
            },
        )
        m.get(f"{API}/readme", text="# Readme body")
        m.get(
            f"{API}/contents",
            json=[
                {"name": "src", "type": "dir", "size": 0},
                {"name": "requirements.txt", "type": "file", "size": 30},
                {"name": "LICENSE", "type": "file", "size": 10},
            ],
        )
        m.get(f"{API}/contents/requirements.txt", text=REQUIREMENTS)
        yield m


class TestFetchRepoShape:
    def test_returns_fields_consumers_read(self, github_http):
        result = fetch_repo(REPO_URL)

        assert result["language"] == "Python"
        assert result["stars"] == 4242
        assert result["file_tree"].splitlines() == ["src/", "requirements.txt", "LICENSE"]
        assert result["manifests"] == {"requirements.txt": REQUIREMENTS}

    def test_null_language_and_description(self):
        with requests_mock.Mocker() as m:
            m.get(
                API,
                json={
                    "name": "repo",
                    "owner": {"login": "owner"},
                    "description": None,
                    "html_url": REPO_URL,
                    "language": None,
                    "stargazers_count": 0,
                },
            )
            m.get(f"{API}/readme", status_code=404)
            m.get(f"{API}/contents", status_code=404)

            result = fetch_repo(REPO_URL)

        assert result["language"] == "Unknown"
        assert result["description"] == ""
        assert result["file_tree"] == ""
        assert result["manifests"] == {}

    def test_manifest_fetch_failure_is_non_fatal(self, github_http):
        github_http.get(f"{API}/contents/requirements.txt", status_code=500)

        result = fetch_repo(REPO_URL)

        assert result["manifests"] == {}
        assert result["file_tree"]  # rest of the result is intact

    def test_file_tree_is_capped(self, github_http):
        github_http.get(
            f"{API}/contents",
            json=[{"name": f"f{i}.txt", "type": "file", "size": 1} for i in range(150)],
        )

        result = fetch_repo(REPO_URL)

        lines = result["file_tree"].splitlines()
        assert len(lines) == 101
        assert lines[-1] == "... (50 more)"


class TestConsumersReceiveRealData:
    def test_repo_onboarding_prompt(self, github_http, mocker):
        from agents.repo_onboarding.logic import generate_onboarding_guide

        llm = mocker.patch("agents.repo_onboarding.logic.call_llm", return_value="{}")

        generate_onboarding_guide(REPO_URL)

        prompt = llm.call_args_list[0].args[0]
        assert "Language: Python" in prompt
        assert "Stars: 4242" in prompt
        assert "requirements.txt" in prompt
        assert "src/" in prompt
        assert "No file tree available" not in prompt

    def test_cve_impact_end_to_end(self, github_http, mocker):
        """Real fetch_repo -> real manifest parser -> real OSV client; only HTTP is mocked."""
        from agents.cve_impact.logic import analyze_cve_impact

        osv = "https://api.osv.dev/v1"
        github_http.post(f"{osv}/querybatch", json={"results": [{"vulns": [{"id": "GHSA-aaaa-bbbb-cccc"}]}, {}]})
        github_http.get(
            f"{osv}/vulns/GHSA-aaaa-bbbb-cccc",
            json={
                "id": "GHSA-aaaa-bbbb-cccc",
                "aliases": ["CVE-2024-1"],
                "summary": "Bad thing",
                "database_specific": {"severity": "HIGH"},
                "affected": [
                    {
                        "package": {"name": "flask", "ecosystem": "PyPI"},
                        "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "2.2.5"}]}],
                    }
                ],
            },
        )
        llm = mocker.patch("agents.cve_impact.logic.call_llm", return_value="{}")

        result = analyze_cve_impact(REPO_URL)

        batch_req = next(r for r in github_http.request_history if r.url.endswith("/querybatch"))
        queries = batch_req.json()["queries"]
        assert queries == [{"package": {"name": "flask", "ecosystem": "PyPI"}, "version": "2.0.1"},
                           {"package": {"name": "requests", "ecosystem": "PyPI"}, "version": "2.25.0"}]
        assert result["dependencies_checked"] == 2
        assert result["cve_analysis"][0]["cve_id"] == "CVE-2024-1"
        assert result["cve_analysis"][0]["package"] == "flask"
        assert result["risk_level"] == "High"
        assert "CVE-2024-1" in llm.call_args_list[0].args[0]  # the LLM is handed OSV's findings

    def test_issue_fix_planner_prompt(self, github_http, mocker):
        from agents.issue_fix_planner.logic import run_issue_fix_planner

        mocker.patch(
            "agents.issue_fix_planner.logic.fetch_issue",
            return_value={"title": "Bug", "body": "It breaks", "labels": [], "url": "u"},
        )
        mocker.patch("agents.issue_fix_planner.logic.search", return_value=[])
        llm = mocker.patch("agents.issue_fix_planner.logic.call_llm", return_value="{}")

        run_issue_fix_planner(f"{REPO_URL}/issues/1")

        prompt = llm.call_args_list[0].args[0]
        assert "Language: Python" in prompt
        assert "requirements.txt" in prompt
        assert "Not available" not in prompt
