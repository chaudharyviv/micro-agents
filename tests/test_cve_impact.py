"""Tests for CVE Impact agent."""

import json
import pytest
from unittest.mock import MagicMock

from agents.cve_impact.logic import analyze_cve_impact, _parse_analysis, _extract_dependencies


class TestAnalyzeCVEImpact:
    """Tests for analyze_cve_impact function."""

    def test_analyze_with_valid_repo(self, mocker):
        """Test CVE analysis with valid repo data."""
        mock_repo_data = {
            "name": "secure-app",
            "description": "A secure application",
            "language": "Python",
            "stars": 50,
            "file_tree": "src/\nrequirements.txt\n",
            "readme": "# Secure App\n\nBuilt with Django and Flask.",
        }
        mocker.patch(
            "agents.cve_impact.logic.fetch_repo", return_value=mock_repo_data
        )

        mock_search_results = [
            {
                "title": "CVE-2024-1234 Django RCE",
                "url": "https://example.com/cve",
                "snippet": "Remote code execution in Django",
            }
        ]
        mocker.patch(
            "agents.cve_impact.logic.web_search", return_value=mock_search_results
        )

        analysis_response = json.dumps(
            {
                "summary": "Found 1 CVE affecting Django",
                "risk_level": "High",
                "cve_analysis": [
                    {
                        "cve_id": "CVE-2024-1234",
                        "package": "django",
                        "severity": "High",
                        "description": "RCE in Django",
                        "impact": "Could allow code execution",
                        "remediation": "Update Django to 5.0.1+",
                    }
                ],
                "common_themes": ["Deserialization issues"],
                "remediation_priority": [
                    {"rank": 1, "action": "Update Django", "effort": "Low"}
                ],
                "recommendations": ["Keep dependencies updated"],
            }
        )
        mocker.patch(
            "agents.cve_impact.logic.call_llm", return_value=analysis_response
        )

        result = analyze_cve_impact("https://github.com/test/repo")

        assert result["risk_level"] == "High"
        assert len(result["cve_analysis"]) == 1
        assert result["cve_analysis"][0]["cve_id"] == "CVE-2024-1234"

    def test_analyze_fetch_repo_fails(self, mocker):
        """Test fallback when repo fetch fails."""
        mocker.patch(
            "agents.cve_impact.logic.fetch_repo", side_effect=Exception("Fetch failed")
        )

        result = analyze_cve_impact("https://github.com/test/repo")

        assert "error_message" in result
        assert "Failed to fetch" in result["error_message"]

    def test_analyze_llm_fails(self, mocker):
        """Test fallback when LLM call fails."""
        mock_repo_data = {
            "name": "test",
            "description": "Test",
            "language": "Python",
            "stars": 0,
            "file_tree": "requirements.txt",
            "readme": "Test repo",
        }
        mocker.patch(
            "agents.cve_impact.logic.fetch_repo", return_value=mock_repo_data
        )
        mocker.patch("agents.cve_impact.logic.web_search", return_value=[])
        mocker.patch(
            "agents.cve_impact.logic.call_llm",
            side_effect=Exception("LLM unavailable"),
        )

        result = analyze_cve_impact("https://github.com/test/repo")

        assert "error_message" in result
        assert "LLM service" in result["error_message"]

    def test_analyze_no_cves_found(self, mocker):
        """Test analysis when no CVEs are found."""
        mock_repo_data = {
            "name": "safe-app",
            "description": "A safe app",
            "language": "Python",
            "stars": 10,
            "file_tree": "src/",
            "readme": "Safe",
        }
        mocker.patch(
            "agents.cve_impact.logic.fetch_repo", return_value=mock_repo_data
        )
        mocker.patch("agents.cve_impact.logic.web_search", return_value=[])

        analysis_response = json.dumps(
            {
                "summary": "No known CVEs found",
                "risk_level": "Low",
                "cve_analysis": [],
                "common_themes": [],
                "remediation_priority": [],
                "recommendations": ["Continue monitoring"],
            }
        )
        mocker.patch(
            "agents.cve_impact.logic.call_llm", return_value=analysis_response
        )

        result = analyze_cve_impact("https://github.com/test/repo")

        assert result["risk_level"] == "Low"
        assert len(result["cve_analysis"]) == 0

    def test_analyze_parse_failure_with_retry(self, mocker):
        """Test that analysis retries on parse failure."""
        mock_repo_data = {
            "name": "test",
            "description": "Test",
            "language": "Python",
            "stars": 0,
            "file_tree": "",
            "readme": "",
        }
        mocker.patch(
            "agents.cve_impact.logic.fetch_repo", return_value=mock_repo_data
        )
        mocker.patch("agents.cve_impact.logic.web_search", return_value=[])

        invalid_response = "{not valid json}"
        valid_response = json.dumps(
            {
                "summary": "Analysis",
                "risk_level": "Medium",
                "cve_analysis": [],
                "common_themes": [],
                "remediation_priority": [],
                "recommendations": [],
            }
        )

        mock_llm = mocker.patch(
            "agents.cve_impact.logic.call_llm",
            side_effect=[invalid_response, valid_response],
        )

        result = analyze_cve_impact("https://github.com/test/repo")

        assert mock_llm.call_count == 2
        assert result["risk_level"] == "Medium"


class TestParseAnalysis:
    """Tests for _parse_analysis function."""

    def test_parse_valid_analysis(self):
        """Test parsing valid JSON analysis."""
        response = json.dumps(
            {
                "summary": "Test analysis",
                "risk_level": "High",
                "cve_analysis": [],
                "common_themes": [],
                "remediation_priority": [],
                "recommendations": [],
            }
        )

        analysis = _parse_analysis(response, "https://github.com/test/repo")

        assert analysis is not None
        assert analysis["risk_level"] == "High"

    def test_parse_markdown_wrapped_json(self):
        """Test parsing JSON wrapped in markdown."""
        response = """```json
{
  "summary": "Test",
  "risk_level": "Low",
  "cve_analysis": [],
  "common_themes": [],
  "remediation_priority": [],
  "recommendations": []
}
```"""

        analysis = _parse_analysis(response, "https://github.com/test/repo")

        assert analysis is not None
        assert analysis["risk_level"] == "Low"

    def test_parse_invalid_json(self):
        """Test parsing invalid JSON."""
        response = "{invalid"

        analysis = _parse_analysis(response, "https://github.com/test/repo")

        assert analysis is None

    def test_parse_missing_required_fields(self):
        """Test that parsing fails if required fields are missing."""
        response = json.dumps({"summary": "Only summary"})

        analysis = _parse_analysis(response, "https://github.com/test/repo")

        assert analysis is None

    def test_parse_not_object(self):
        """Test parsing response that's not a JSON object."""
        response = json.dumps(["array", "instead", "of", "object"])

        analysis = _parse_analysis(response, "https://github.com/test/repo")

        assert analysis is None


class TestExtractDependencies:
    """Tests for _extract_dependencies function."""

    def test_extract_from_file_tree(self):
        """Test extracting dependencies from file tree."""
        file_tree = """
src/
package.json
requirements.txt
Gemfile
"""
        readme = "A test project"

        deps = _extract_dependencies(file_tree, readme)

        assert "nodejs" in deps or "npm" in deps
        assert "python" in deps
        assert "ruby" in deps

    def test_extract_from_readme(self):
        """Test extracting dependencies from README mentions."""
        file_tree = "src/\nmain.py"
        readme = """
# My Project

This project uses Django, Flask, and FastAPI.
Built with TensorFlow and PyTorch.
"""

        deps = _extract_dependencies(file_tree, readme)

        assert "django" in deps
        assert "flask" in deps
        assert "fastapi" in deps

    def test_extract_empty(self):
        """Test extraction when no dependencies are found."""
        file_tree = "src/\nREADME.md"
        readme = "Generic project"

        deps = _extract_dependencies(file_tree, readme)

        # Should return fallback
        assert isinstance(deps, list)
        assert len(deps) > 0

    def test_extract_mixed_sources(self):
        """Test extraction from both file tree and README."""
        file_tree = "requirements.txt\npackage.json"
        readme = "Uses Django and Express.js"

        deps = _extract_dependencies(file_tree, readme)

        # Should include from both sources
        assert len(deps) >= 2
