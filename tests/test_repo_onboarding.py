"""Tests for Repo Onboarding agent."""

import json
import pytest
from unittest.mock import MagicMock

from agents.repo_onboarding.logic import generate_onboarding_guide, _parse_guide


class TestGenerateOnboardingGuide:
    """Tests for generate_onboarding_guide function."""

    def test_generate_guide_with_valid_repo(self, mocker):
        """Test guide generation with valid repo data."""
        mock_repo_data = {
            "name": "test-repo",
            "description": "Test repository",
            "language": "Python",
            "stars": 100,
            "file_tree": "src/\n  __init__.py\n  main.py\ntests/\n  test_main.py\n",
            "readme": "# Test Repo\n\nThis is a test repository.",
        }
        mocker.patch(
            "agents.repo_onboarding.logic.fetch_repo", return_value=mock_repo_data
        )

        guide_response = json.dumps(
            {
                "project_name": "Test Repo",
                "overview": "A test repository for demonstration",
                "tech_stack": ["Python", "pytest"],
                "setup_steps": ["pip install -r requirements.txt"],
                "project_structure": {"src": "Source code"},
                "key_concepts": ["Modular design"],
                "development_workflow": {"testing": "pytest"},
                "common_tasks": {"run_tests": "pytest"},
            }
        )
        mocker.patch(
            "agents.repo_onboarding.logic.call_llm", return_value=guide_response
        )

        result = generate_onboarding_guide("https://github.com/test/repo")

        assert result["project_name"] == "Test Repo"
        assert result["overview"] == "A test repository for demonstration"
        assert "Python" in result["tech_stack"]
        assert result["repo_url"] == "https://github.com/test/repo"

    def test_generate_guide_fetch_repo_fails(self, mocker):
        """Test fallback when repo fetch fails."""
        mocker.patch(
            "agents.repo_onboarding.logic.fetch_repo",
            side_effect=Exception("Fetch failed"),
        )

        result = generate_onboarding_guide("https://github.com/test/repo")

        assert "error_message" in result
        assert "Failed to fetch" in result["error_message"]

    def test_generate_guide_llm_fails(self, mocker):
        """Test fallback when LLM call fails."""
        mock_repo_data = {
            "name": "test",
            "description": "Test",
            "language": "Python",
            "stars": 0,
            "file_tree": "",
            "readme": "",
        }
        mocker.patch(
            "agents.repo_onboarding.logic.fetch_repo", return_value=mock_repo_data
        )
        mocker.patch(
            "agents.repo_onboarding.logic.call_llm",
            side_effect=Exception("LLM unavailable"),
        )

        result = generate_onboarding_guide("https://github.com/test/repo")

        assert "error_message" in result
        assert "LLM service" in result["error_message"]

    def test_generate_guide_parse_failure_with_retry(self, mocker):
        """Test that guide generation retries on parse failure."""
        mock_repo_data = {
            "name": "test",
            "description": "Test",
            "language": "Python",
            "stars": 0,
            "file_tree": "",
            "readme": "",
        }
        mocker.patch(
            "agents.repo_onboarding.logic.fetch_repo", return_value=mock_repo_data
        )

        invalid_response = "{not valid json}"
        valid_response = json.dumps(
            {
                "project_name": "Test",
                "overview": "Test project",
                "tech_stack": ["Python"],
                "setup_steps": ["pip install"],
                "development_workflow": {"testing": "pytest"},
            }
        )

        mock_llm = mocker.patch(
            "agents.repo_onboarding.logic.call_llm",
            side_effect=[invalid_response, valid_response],
        )

        result = generate_onboarding_guide("https://github.com/test/repo")

        assert mock_llm.call_count == 2
        assert result["project_name"] == "Test"

    def test_generate_guide_all_retries_fail(self, mocker):
        """Test fallback when all parse attempts fail."""
        mock_repo_data = {
            "name": "test",
            "description": "Test",
            "language": "Python",
            "stars": 0,
            "file_tree": "",
            "readme": "",
        }
        mocker.patch(
            "agents.repo_onboarding.logic.fetch_repo", return_value=mock_repo_data
        )
        mocker.patch(
            "agents.repo_onboarding.logic.call_llm",
            side_effect=["{invalid}", "{also invalid}"],
        )

        result = generate_onboarding_guide("https://github.com/test/repo")

        assert "error_message" in result
        assert "parse" in result["error_message"].lower()


class TestParseGuide:
    """Tests for _parse_guide function."""

    def test_parse_valid_guide(self):
        """Test parsing valid JSON guide."""
        response = json.dumps(
            {
                "project_name": "Test",
                "overview": "Test project",
                "tech_stack": ["Python"],
                "setup_steps": ["step1"],
                "development_workflow": {"testing": "pytest"},
            }
        )

        guide = _parse_guide(response, "https://github.com/test/repo")

        assert guide is not None
        assert guide["project_name"] == "Test"

    def test_parse_markdown_wrapped_json(self):
        """Test parsing JSON wrapped in markdown code blocks."""
        response = """```json
{
  "project_name": "Test",
  "overview": "Test project",
  "tech_stack": ["Python"],
  "setup_steps": ["step1"],
  "development_workflow": {"testing": "pytest"}
}
```"""

        guide = _parse_guide(response, "https://github.com/test/repo")

        assert guide is not None
        assert guide["project_name"] == "Test"

    def test_parse_invalid_json(self):
        """Test parsing invalid JSON."""
        response = "{not valid}"

        guide = _parse_guide(response, "https://github.com/test/repo")

        assert guide is None

    def test_parse_missing_required_fields(self):
        """Test that parsing fails if required fields are missing."""
        response = json.dumps(
            {
                "project_name": "Test",
                # Missing overview, tech_stack, setup_steps, development_workflow
            }
        )

        guide = _parse_guide(response, "https://github.com/test/repo")

        assert guide is None

    def test_parse_not_object(self):
        """Test parsing response that's not a JSON object."""
        response = json.dumps(["array", "not", "object"])

        guide = _parse_guide(response, "https://github.com/test/repo")

        assert guide is None

    def test_parse_with_extra_fields(self):
        """Test that parsing handles extra fields gracefully."""
        response = json.dumps(
            {
                "project_name": "Test",
                "overview": "Test project",
                "tech_stack": ["Python"],
                "setup_steps": ["step1"],
                "development_workflow": {"testing": "pytest"},
                "extra_field": "should be ignored",
            }
        )

        guide = _parse_guide(response, "https://github.com/test/repo")

        assert guide is not None
        assert guide["project_name"] == "Test"
        assert guide["extra_field"] == "should be ignored"
