"""Tests for Security Audit agent."""

import json
import pytest
from unittest.mock import MagicMock, call

from agents.security_audit.logic import (
    generate_security_audit,
    _parse_audit,
    _format_guide_for_audit,
    _format_analysis_for_audit,
)


class TestGenerateSecurityAudit:
    """Tests for generate_security_audit function."""

    def test_generate_audit_with_all_components(self, mocker):
        """Test audit generation with valid repo, guide, and CVE analysis."""
        # Mock repo fetch
        mock_repo_data = {
            "name": "secure-app",
            "description": "A secure app",
            "language": "Python",
            "stars": 100,
            "file_tree": "src/",
            "readme": "Secure app",
        }
        mocker.patch(
            "agents.security_audit.logic.fetch_repo", return_value=mock_repo_data
        )

        # Mock onboarding guide
        mock_guide = {
            "project_name": "Secure App",
            "overview": "A secure application",
            "tech_stack": ["Python"],
            "setup_steps": ["pip install"],
            "development_workflow": {"testing": "pytest"},
        }
        mocker.patch(
            "agents.security_audit.logic.generate_onboarding_guide",
            return_value=mock_guide,
        )

        # Mock CVE analysis
        mock_cve = {
            "summary": "Low risk",
            "risk_level": "Low",
            "cve_analysis": [],
            "recommendations": ["Keep dependencies updated"],
        }
        mocker.patch(
            "agents.security_audit.logic.analyze_cve_impact", return_value=mock_cve
        )

        # Mock LLM response
        audit_response = json.dumps(
            {
                "audit_summary": "Low risk application with good practices",
                "overall_risk": "Low",
                "critical_issues": [],
                "safe_contribution_practices": ["Follow security guidelines"],
                "onboarding_security_checklist": ["Review security docs"],
                "remediation_roadmap": [],
                "security_recommendations": ["Keep dependencies updated"],
            }
        )
        mocker.patch(
            "agents.security_audit.logic.call_llm", return_value=audit_response
        )

        result = generate_security_audit("https://github.com/test/repo")

        assert result["overall_risk"] == "Low"
        assert "audit_summary" in result
        assert result["repo_url"] == "https://github.com/test/repo"

    def test_generate_audit_calls_both_agents(self, mocker):
        """Test that audit generation calls both Onboarding and CVE agents."""
        mock_repo_data = {
            "name": "test",
            "description": "Test",
            "language": "Python",
            "stars": 0,
            "file_tree": "",
            "readme": "",
        }
        mocker.patch(
            "agents.security_audit.logic.fetch_repo", return_value=mock_repo_data
        )

        mock_guide_fn = mocker.patch(
            "agents.security_audit.logic.generate_onboarding_guide",
            return_value={"project_name": "Test", "tech_stack": []},
        )
        mock_cve_fn = mocker.patch(
            "agents.security_audit.logic.analyze_cve_impact",
            return_value={"summary": "Test", "risk_level": "Low"},
        )

        audit_response = json.dumps(
            {
                "audit_summary": "Test",
                "overall_risk": "Low",
                "safe_contribution_practices": [],
                "onboarding_security_checklist": [],
                "remediation_roadmap": [],
                "security_recommendations": [],
            }
        )
        mocker.patch(
            "agents.security_audit.logic.call_llm", return_value=audit_response
        )

        generate_security_audit("https://github.com/test/repo")

        # Verify both agents were called
        assert mock_guide_fn.call_count == 1
        assert mock_cve_fn.call_count == 1

    def test_generate_audit_fetch_repo_fails(self, mocker):
        """Test fallback when repo fetch fails."""
        mocker.patch(
            "agents.security_audit.logic.fetch_repo",
            side_effect=Exception("Fetch failed"),
        )

        result = generate_security_audit("https://github.com/test/repo")

        assert "error_message" in result
        assert "Failed to fetch" in result["error_message"]

    def test_generate_audit_llm_fails(self, mocker):
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
            "agents.security_audit.logic.fetch_repo", return_value=mock_repo_data
        )
        mocker.patch(
            "agents.security_audit.logic.generate_onboarding_guide",
            return_value={"project_name": "Test", "tech_stack": []},
        )
        mocker.patch(
            "agents.security_audit.logic.analyze_cve_impact",
            return_value={"summary": "Test", "risk_level": "Low"},
        )
        mocker.patch(
            "agents.security_audit.logic.call_llm",
            side_effect=Exception("LLM unavailable"),
        )

        result = generate_security_audit("https://github.com/test/repo")

        assert "error_message" in result
        assert "LLM service" in result["error_message"]

    def test_generate_audit_handles_component_errors(self, mocker):
        """Test that audit handles errors from sub-agents gracefully."""
        mock_repo_data = {
            "name": "test",
            "description": "Test",
            "language": "Python",
            "stars": 0,
            "file_tree": "",
            "readme": "",
        }
        mocker.patch(
            "agents.security_audit.logic.fetch_repo", return_value=mock_repo_data
        )

        # Onboarding fails
        mocker.patch(
            "agents.security_audit.logic.generate_onboarding_guide",
            return_value={"error_message": "Fetch failed"},
        )

        # CVE analysis succeeds
        mocker.patch(
            "agents.security_audit.logic.analyze_cve_impact",
            return_value={"summary": "Test", "risk_level": "Low"},
        )

        audit_response = json.dumps(
            {
                "audit_summary": "Audit created despite onboarding error",
                "overall_risk": "Medium",
                "safe_contribution_practices": [],
                "onboarding_security_checklist": [],
                "remediation_roadmap": [],
                "security_recommendations": [],
            }
        )
        mocker.patch(
            "agents.security_audit.logic.call_llm", return_value=audit_response
        )

        # Should still generate audit
        result = generate_security_audit("https://github.com/test/repo")

        assert "error_message" not in result
        assert result["overall_risk"] == "Medium"


class TestParseAudit:
    """Tests for _parse_audit function."""

    def test_parse_valid_audit(self):
        """Test parsing valid JSON audit."""
        response = json.dumps(
            {
                "audit_summary": "Test audit",
                "overall_risk": "High",
                "safe_contribution_practices": [],
                "onboarding_security_checklist": [],
                "remediation_roadmap": [],
                "security_recommendations": [],
            }
        )

        audit = _parse_audit(response, "https://github.com/test/repo")

        assert audit is not None
        assert audit["overall_risk"] == "High"

    def test_parse_markdown_wrapped_json(self):
        """Test parsing JSON wrapped in markdown."""
        response = """```json
{
  "audit_summary": "Test",
  "overall_risk": "Medium",
  "safe_contribution_practices": [],
  "onboarding_security_checklist": [],
  "remediation_roadmap": [],
  "security_recommendations": []
}
```"""

        audit = _parse_audit(response, "https://github.com/test/repo")

        assert audit is not None
        assert audit["overall_risk"] == "Medium"

    def test_parse_invalid_json(self):
        """Test parsing invalid JSON."""
        response = "{invalid"

        audit = _parse_audit(response, "https://github.com/test/repo")

        assert audit is None

    def test_parse_missing_required_fields(self):
        """Test that parsing fails if required fields are missing."""
        response = json.dumps({"audit_summary": "Only summary"})

        audit = _parse_audit(response, "https://github.com/test/repo")

        assert audit is None

    def test_parse_not_object(self):
        """Test parsing response that's not a JSON object."""
        response = json.dumps(["array", "not", "object"])

        audit = _parse_audit(response, "https://github.com/test/repo")

        assert audit is None


class TestFormatGuideForAudit:
    """Tests for _format_guide_for_audit function."""

    def test_format_complete_guide(self):
        """Test formatting a complete onboarding guide."""
        guide = {
            "project_name": "Test Project",
            "overview": "A test project",
            "tech_stack": ["Python", "Django"],
            "setup_steps": ["pip install", "python manage.py migrate"],
        }

        formatted = _format_guide_for_audit(guide)

        assert "Test Project" in formatted
        assert "Python" in formatted
        assert "pip install" in formatted

    def test_format_minimal_guide(self):
        """Test formatting guide with minimal fields."""
        guide = {
            "project_name": "Minimal",
        }

        formatted = _format_guide_for_audit(guide)

        assert "Minimal" in formatted
        assert isinstance(formatted, str)


class TestFormatAnalysisForAudit:
    """Tests for _format_analysis_for_audit function."""

    def test_format_complete_analysis(self):
        """Test formatting a complete CVE analysis."""
        analysis = {
            "summary": "High risk project",
            "risk_level": "High",
            "cve_analysis": [
                {
                    "cve_id": "CVE-2024-1234",
                    "severity": "High",
                    "description": "Test CVE",
                }
            ],
            "recommendations": ["Update dependencies"],
        }

        formatted = _format_analysis_for_audit(analysis)

        assert "High risk" in formatted
        assert "CVE-2024-1234" in formatted
        assert "Update dependencies" in formatted

    def test_format_analysis_no_cves(self):
        """Test formatting analysis with no CVEs."""
        analysis = {
            "summary": "Low risk",
            "risk_level": "Low",
            "cve_analysis": [],
            "recommendations": [],
        }

        formatted = _format_analysis_for_audit(analysis)

        assert "Low risk" in formatted
        assert isinstance(formatted, str)
