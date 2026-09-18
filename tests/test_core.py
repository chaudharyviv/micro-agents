"""Tests for core module functions with mocked API calls."""

import pytest
from unittest.mock import patch, MagicMock
import requests_mock

from core.llm import call_llm, LLMUnavailableError, MODEL_MAP
from core.search import web_search
from core.github_tool import fetch_repo, fetch_issue, fetch_pr, GitHubAPIError


class TestLLM:
    """Tests for core/llm.py"""

    def test_call_llm_openai_success(self, mocker):
        """Test successful call via OpenAI API."""
        mocker.patch.dict("os.environ", {"OPENAI_API_KEY": "test-openai-key"})

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices[0].message.content = "Hello"
        mock_client.chat.completions.create.return_value = mock_response

        mock_openai = MagicMock(return_value=mock_client)
        mocker.patch("core.llm.OpenAI", mock_openai)

        result = call_llm("Say hello")
        assert result == "Hello"
        mock_openai.assert_called_once_with(api_key="test-openai-key")

    def test_call_llm_retries_once_on_failure(self, mocker):
        """Test that a failed first attempt is retried before succeeding."""
        mocker.patch.dict("os.environ", {"OPENAI_API_KEY": "test-openai-key"})

        mock_response = MagicMock()
        mock_response.choices[0].message.content = "Hello after retry"
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [
            Exception("Transient error"),
            mock_response,
        ]

        mock_openai = MagicMock(return_value=mock_client)
        mocker.patch("core.llm.OpenAI", mock_openai)

        result = call_llm("Say hello")
        assert result == "Hello after retry"
        assert mock_client.chat.completions.create.call_count == 2

    def test_call_llm_both_attempts_fail(self, mocker):
        """Test LLMUnavailableError when both attempts fail."""
        mocker.patch.dict("os.environ", {"OPENAI_API_KEY": "test-openai-key"})

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("OpenAI error")

        mock_openai = MagicMock(return_value=mock_client)
        mocker.patch("core.llm.OpenAI", mock_openai)

        with pytest.raises(LLMUnavailableError):
            call_llm("Say hello")

    def test_call_llm_no_keys_configured(self, mocker):
        """Test LLMUnavailableError when no API key is available."""
        mocker.patch.dict("os.environ", {}, clear=True)

        with pytest.raises(LLMUnavailableError):
            call_llm("Say hello")

    def test_call_llm_model_mapping(self, mocker):
        """Test that model parameter uses MODEL_MAP correctly."""
        mocker.patch.dict("os.environ", {"OPENAI_API_KEY": "test-openai-key"})

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices[0].message.content = "Response"
        mock_client.chat.completions.create.return_value = mock_response

        mock_openai = MagicMock(return_value=mock_client)
        mocker.patch("core.llm.OpenAI", mock_openai)

        call_llm("prompt", model="large")

        # Verify the correct model from MODEL_MAP was used
        call_args = mock_client.chat.completions.create.call_args
        assert call_args[1]["model"] == MODEL_MAP["large"]

    def test_call_llm_with_system_message(self, mocker):
        """Test that system message is passed correctly."""
        mocker.patch.dict("os.environ", {"OPENAI_API_KEY": "test-openai-key"})

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices[0].message.content = "Response"
        mock_client.chat.completions.create.return_value = mock_response

        mock_openai = MagicMock(return_value=mock_client)
        mocker.patch("core.llm.OpenAI", mock_openai)

        custom_system = "You are a code expert"
        call_llm("Write Python", system=custom_system)

        # Verify system message was used
        call_args = mock_client.chat.completions.create.call_args
        messages = call_args[1]["messages"]
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == custom_system


class TestSearch:
    """Tests for core/search.py"""

    def test_web_search_tavily_success(self, mocker):
        """Test successful search via Tavily API."""
        mocker.patch.dict("os.environ", {"TAVILY_API_KEY": "test-tavily-key"})

        mock_tavily_client = MagicMock()
        mock_tavily_client.search.return_value = {
            "results": [
                {
                    "title": "Result 1",
                    "url": "https://example.com/1",
                    "content": "This is result 1",
                },
                {
                    "title": "Result 2",
                    "url": "https://example.com/2",
                    "content": "This is result 2",
                },
            ]
        }

        mock_tavily = MagicMock(return_value=mock_tavily_client)
        mocker.patch("core.search.TavilyClient", mock_tavily)

        results = web_search("python")

        assert len(results) == 2
        assert results[0]["title"] == "Result 1"
        assert results[0]["url"] == "https://example.com/1"
        assert results[0]["snippet"] == "This is result 1"
        assert "title" in results[0]
        assert "url" in results[0]
        assert "snippet" in results[0]

    def test_web_search_tavily_fallback_to_ddg(self, mocker):
        """Test fallback to DuckDuckGo when Tavily fails."""
        mocker.patch.dict("os.environ", {"TAVILY_API_KEY": "test-tavily-key"})

        # Mock Tavily failure
        mocker.patch("core.search.TavilyClient", side_effect=Exception("Tavily error"))

        # Mock DuckDuckGo success - note: list() is called on the result
        mock_results = [
            {
                "title": "DDG Result 1",
                "href": "https://ddg.com/1",
                "body": "DuckDuckGo result",
            },
            {
                "title": "DDG Result 2",
                "href": "https://ddg.com/2",
                "body": "Another DDG result",
            },
        ]
        mock_ddgs = MagicMock()
        mock_ddgs.text.return_value = iter(mock_results)

        mock_ddgs_class = MagicMock(return_value=mock_ddgs)
        mocker.patch("core.search.DDGS", mock_ddgs_class)

        results = web_search("python")

        assert len(results) == 2
        assert results[0]["title"] == "DDG Result 1"
        assert results[0]["url"] == "https://ddg.com/1"
        assert results[0]["snippet"] == "DuckDuckGo result"

    def test_web_search_both_backends_fail(self, mocker):
        """Test exception when both backends fail."""
        mocker.patch.dict("os.environ", {"TAVILY_API_KEY": "test-key"})

        mocker.patch("core.search.TavilyClient", side_effect=Exception("Tavily error"))
        mocker.patch("core.search.DDGS", side_effect=Exception("DDG error"))

        with pytest.raises(Exception, match="All search backends failed"):
            web_search("python")

    def test_web_search_no_tavily_key(self, mocker):
        """Test fallback to DuckDuckGo when Tavily key is missing."""
        mocker.patch.dict("os.environ", {}, clear=True)

        mock_results = [
            {
                "title": "Result",
                "href": "https://example.com",
                "body": "content",
            }
        ]
        mock_ddgs = MagicMock()
        mock_ddgs.text.return_value = iter(mock_results)

        mock_ddgs_class = MagicMock(return_value=mock_ddgs)
        mocker.patch("core.search.DDGS", mock_ddgs_class)

        results = web_search("query")
        assert len(results) == 1

    def test_web_search_max_results(self, mocker):
        """Test that max_results parameter is passed correctly."""
        mocker.patch.dict("os.environ", {"TAVILY_API_KEY": "test-key"})

        mock_tavily_client = MagicMock()
        mock_tavily_client.search.return_value = {"results": []}

        mock_tavily = MagicMock(return_value=mock_tavily_client)
        mocker.patch("core.search.TavilyClient", mock_tavily)

        web_search("query", max_results=10)

        # Verify max_results was passed to Tavily
        call_args = mock_tavily_client.search.call_args
        assert call_args[1]["max_results"] == 10


class TestGitHub:
    """Tests for core/github_tool.py"""

    def test_fetch_repo_success(self):
        """Test successful repo fetch with normalized response."""
        with requests_mock.Mocker() as m:
            m.get(
                "https://api.github.com/repos/owner/repo",
                json={
                    "name": "repo",
                    "owner": {"login": "owner"},
                    "description": "A test repo",
                    "html_url": "https://github.com/owner/repo",
                },
            )
            m.get(
                "https://api.github.com/repos/owner/repo/readme",
                text="# README",
            )
            m.get(
                "https://api.github.com/repos/owner/repo/contents",
                json=[
                    {"name": "file.py", "type": "file", "size": 100},
                    {"name": "dir", "type": "dir", "size": 0},
                ],
            )

            result = fetch_repo("https://github.com/owner/repo")

            assert result["name"] == "repo"
            assert result["owner"] == "owner"
            assert result["description"] == "A test repo"
            assert len(result["files"]) == 2
            assert "readme" in result
            assert "url" in result

    def test_fetch_repo_404(self):
        """Test repo fetch raises GitHubAPIError on 404."""
        with requests_mock.Mocker() as m:
            m.get(
                "https://api.github.com/repos/owner/notfound",
                status_code=404,
            )

            with pytest.raises(GitHubAPIError, match="not found"):
                fetch_repo("https://github.com/owner/notfound")

    def test_fetch_repo_rate_limit(self):
        """Test repo fetch handles rate limiting."""
        with requests_mock.Mocker() as m:
            m.get(
                "https://api.github.com/repos/owner/repo",
                status_code=403,
            )

            with pytest.raises(GitHubAPIError, match="rate limit"):
                fetch_repo("https://github.com/owner/repo")

    def test_fetch_repo_invalid_url(self):
        """Test repo fetch rejects invalid URL format."""
        with pytest.raises(GitHubAPIError, match="Invalid"):
            fetch_repo("not-a-valid-url")

    def test_fetch_issue_success(self):
        """Test successful issue fetch."""
        with requests_mock.Mocker() as m:
            m.get(
                "https://api.github.com/repos/owner/repo/issues/42",
                json={
                    "title": "Bug in feature X",
                    "body": "This is a bug",
                    "labels": [{"name": "bug"}, {"name": "high-priority"}],
                    "comments": 5,
                    "html_url": "https://github.com/owner/repo/issues/42",
                },
            )

            result = fetch_issue("https://github.com/owner/repo/issues/42")

            assert result["title"] == "Bug in feature X"
            assert result["body"] == "This is a bug"
            assert "bug" in result["labels"]
            assert result["comments_count"] == 5
            assert "url" in result

    def test_fetch_issue_invalid_url(self):
        """Test issue fetch rejects invalid URL format."""
        with pytest.raises(GitHubAPIError, match="Invalid"):
            fetch_issue("https://github.com/owner/repo/issues/notanumber")

    def test_fetch_pr_success(self):
        """Test successful PR fetch."""
        with requests_mock.Mocker() as m:
            m.get(
                "https://api.github.com/repos/owner/repo/pulls/10",
                json={
                    "title": "Add new feature",
                    "body": "This PR adds feature X",
                    "additions": 150,
                    "deletions": 30,
                    "html_url": "https://github.com/owner/repo/pull/10",
                },
            )
            m.get(
                "https://api.github.com/repos/owner/repo/pulls/10/files",
                json=[
                    {
                        "filename": "src/main.py",
                        "additions": 100,
                        "deletions": 10,
                        "status": "modified",
                    },
                    {
                        "filename": "tests/test_main.py",
                        "additions": 50,
                        "deletions": 20,
                        "status": "modified",
                    },
                ],
            )

            result = fetch_pr("https://github.com/owner/repo/pull/10")

            assert result["title"] == "Add new feature"
            assert result["additions"] == 150
            assert result["deletions"] == 30
            assert len(result["files"]) == 2
            assert result["files"][0]["filename"] == "src/main.py"
            assert "url" in result

    def test_fetch_pr_invalid_url(self):
        """Test PR fetch rejects invalid URL format."""
        with pytest.raises(GitHubAPIError, match="Invalid"):
            fetch_pr("https://github.com/owner/repo/pull/notanumber")

    def test_github_auth_token_included(self):
        """Test that GitHub token is included in headers when available."""
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}):
            with requests_mock.Mocker() as m:
                m.get(
                    "https://api.github.com/repos/owner/repo",
                    json={
                        "name": "repo",
                        "owner": {"login": "owner"},
                        "description": "Test",
                        "html_url": "https://github.com/owner/repo",
                    },
                )
                m.get(
                    "https://api.github.com/repos/owner/repo/readme",
                    status_code=404,
                )
                m.get(
                    "https://api.github.com/repos/owner/repo/contents",
                    json=[],
                )

                fetch_repo("https://github.com/owner/repo")

                # Verify token was in the request
                assert "Authorization" in m.request_history[0].headers
                assert "test-token" in m.request_history[0].headers["Authorization"]
