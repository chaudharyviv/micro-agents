"""Tests for CVE Impact agent."""

import json

import pytest

from agents.cve_impact.logic import _parse_analysis, _risk_level, analyze_cve_impact
from core.osv import OSVError

URL = "https://github.com/test/repo"
GHSA = "GHSA-aaaa-bbbb-cccc"


def repo(manifests=None, files=None):
    """A repo dict in the shape the real fetch_repo returns."""
    manifests = manifests if manifests is not None else {"requirements.txt": "requests==2.25.0\nflask>=2.0\nnotpinned\n"}
    return {
        "name": "repo",
        "owner": "test",
        "description": "A test repo",
        "url": URL,
        "language": "Python",
        "stars": 5,
        "readme": "",
        "files": files if files is not None else [{"name": n, "type": "file", "size": 1} for n in manifests],
        "file_tree": "\n".join(manifests),
        "manifests": manifests,
    }


def advisory(vid=GHSA, cve="CVE-2024-1", severity="HIGH", pkg="requests", fixed="2.32.4"):
    return {
        "id": vid,
        "aliases": [cve] if cve else [],
        "summary": "Something bad",
        "database_specific": {"severity": severity},
        "affected": [
            {
                "package": {"name": pkg, "ecosystem": "PyPI"},
                "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": fixed}]}],
            }
        ],
    }


@pytest.fixture
def osv(mocker):
    """Patch the OSV layer: requests==2.25.0 (dep 0) has one advisory, flask floor (dep 1) has none."""
    batch = mocker.patch("agents.cve_impact.logic.query_batch", return_value=[[GHSA], []])
    mocker.patch("agents.cve_impact.logic.fetch_details", return_value={GHSA: advisory()})
    return batch


def llm_json(**overrides):
    body = {
        "summary": "One high severity issue in requests.",
        "impacts": {"CVE-2024-1": "Could leak credentials on redirect."},
        "common_themes": ["Credential handling"],
        "recommendations": ["Pin and update dependencies"],
    }
    body.update(overrides)
    return json.dumps(body)


class TestAnalyzeCVEImpact:
    def test_findings_come_from_osv_and_llm_adds_prose(self, mocker, osv):
        mocker.patch("agents.cve_impact.logic.fetch_repo", return_value=repo())
        mocker.patch("agents.cve_impact.logic.call_llm", return_value=llm_json())

        result = analyze_cve_impact(URL)

        assert result["data_source"] == "OSV.dev"
        assert result["analysis_source"] == "osv+llm"
        assert result["risk_level"] == "High"
        assert result["dependencies_checked"] == 2
        assert [d["name"] for d in result["dependencies_unchecked"]] == ["notpinned"]
        f = result["cve_analysis"][0]
        assert f["cve_id"] == "CVE-2024-1"
        assert f["advisory_ids"] == [GHSA]
        assert f["package"] == "requests" and f["version"] == "2.25.0" and f["version_basis"] == "exact"
        assert f["fixed_in"] == ["2.32.4"]
        assert f["impact"] == "Could leak credentials on redirect."
        assert "2.32.4" in f["remediation"]
        assert result["remediation_priority"][0]["fixes"] == ["CVE-2024-1"]
        assert result["summary"] == "One high severity issue in requests."

    def test_only_checkable_versions_are_queried(self, mocker, osv):
        mocker.patch("agents.cve_impact.logic.fetch_repo", return_value=repo())
        mocker.patch("agents.cve_impact.logic.call_llm", return_value=llm_json())

        analyze_cve_impact(URL)

        queried = [(d.name, d.version, d.basis) for d in osv.call_args.args[0]]
        assert queried == [("requests", "2.25.0", "exact"), ("flask", "2.0", "range_floor")]

    def test_llm_cannot_set_risk_level(self, mocker, osv):
        mocker.patch("agents.cve_impact.logic.fetch_repo", return_value=repo())
        mocker.patch("agents.cve_impact.logic.call_llm", return_value=llm_json(risk_level="Low"))

        assert analyze_cve_impact(URL)["risk_level"] == "High"

    def test_llm_invented_id_discards_prose(self, mocker, osv):
        mocker.patch("agents.cve_impact.logic.fetch_repo", return_value=repo())
        mocker.patch(
            "agents.cve_impact.logic.call_llm",
            return_value=llm_json(summary="Also affected by CVE-2099-99999, a critical RCE."),
        )

        result = analyze_cve_impact(URL)

        assert result["analysis_source"] == "osv"
        assert "CVE-2099-99999" not in json.dumps(result)
        assert result["cve_analysis"][0]["impact"] == ""
        assert result["summary"].startswith("Found 1 known vulnerability (")

    def test_llm_impact_for_unknown_finding_is_ignored(self, mocker, osv):
        mocker.patch("agents.cve_impact.logic.fetch_repo", return_value=repo())
        mocker.patch(
            "agents.cve_impact.logic.call_llm",
            return_value=llm_json(impacts={"CVE-2024-1": "ok", "not-a-finding": "x"}),
        )

        result = analyze_cve_impact(URL)

        assert len(result["cve_analysis"]) == 1
        assert result["cve_analysis"][0]["impact"] == "ok"

    def test_llm_unavailable_still_returns_osv_findings(self, mocker, osv):
        mocker.patch("agents.cve_impact.logic.fetch_repo", return_value=repo())
        mocker.patch("agents.cve_impact.logic.call_llm", side_effect=Exception("down"))

        result = analyze_cve_impact(URL)

        assert "error_message" not in result
        assert result["analysis_source"] == "osv"
        assert result["cve_analysis"][0]["cve_id"] == "CVE-2024-1"

    def test_parse_failure_retries_once(self, mocker, osv):
        mocker.patch("agents.cve_impact.logic.fetch_repo", return_value=repo())
        llm = mocker.patch("agents.cve_impact.logic.call_llm", side_effect=["{not json}", llm_json()])

        result = analyze_cve_impact(URL)

        assert llm.call_count == 2
        assert result["analysis_source"] == "osv+llm"

    def test_no_findings_skips_llm(self, mocker):
        mocker.patch("agents.cve_impact.logic.fetch_repo", return_value=repo())
        mocker.patch("agents.cve_impact.logic.query_batch", return_value=[[], []])
        mocker.patch("agents.cve_impact.logic.fetch_details", return_value={})
        llm = mocker.patch("agents.cve_impact.logic.call_llm")

        result = analyze_cve_impact(URL)

        llm.assert_not_called()
        assert result["risk_level"] == "Low"
        assert result["cve_analysis"] == []
        assert "No known vulnerabilities" in result["summary"]

    def test_osv_outage_is_an_error_not_a_clean_bill(self, mocker):
        mocker.patch("agents.cve_impact.logic.fetch_repo", return_value=repo())
        mocker.patch("agents.cve_impact.logic.query_batch", side_effect=OSVError("Could not reach the OSV.dev vulnerability database"))

        result = analyze_cve_impact(URL)

        assert "OSV.dev" in result["error_message"]
        assert "cve_analysis" not in result

    def test_no_manifests_is_unknown_not_low(self, mocker):
        mocker.patch("agents.cve_impact.logic.fetch_repo", return_value=repo(manifests={}, files=[]))
        batch = mocker.patch("agents.cve_impact.logic.query_batch")

        result = analyze_cve_impact(URL)

        batch.assert_not_called()
        assert result["risk_level"] == "Unknown"
        assert "not evidence" in result["summary"]

    def test_unsupported_manifest_is_reported(self, mocker):
        data = repo(manifests={}, files=[{"name": "Cargo.toml", "type": "file", "size": 1}])
        mocker.patch("agents.cve_impact.logic.fetch_repo", return_value=data)

        result = analyze_cve_impact(URL)

        assert result["risk_level"] == "Unknown"
        assert result["manifests_not_analyzed"] == ["Cargo.toml"]
        assert "Cargo.toml" in result["summary"] and "not supported" in result["summary"]

    def test_fetch_repo_fails(self, mocker):
        mocker.patch("agents.cve_impact.logic.fetch_repo", side_effect=Exception("Fetch failed"))

        result = analyze_cve_impact(URL)

        assert "Failed to fetch" in result["error_message"]

    def test_duplicate_dependencies_queried_once(self, mocker):
        data = repo(manifests={"requirements.txt": "requests==2.25.0\nrequests==2.25.0\n"})
        batch = mocker.patch("agents.cve_impact.logic.query_batch", return_value=[[]])
        mocker.patch("agents.cve_impact.logic.fetch_details", return_value={})
        mocker.patch("agents.cve_impact.logic.fetch_repo", return_value=data)

        analyze_cve_impact(URL)

        assert len(batch.call_args.args[0]) == 1


class TestRiskLevel:
    @staticmethod
    def f(severity, basis="exact"):
        return {"severity": severity, "version_basis": basis}

    def test_levels_for_exact_pins(self):
        assert _risk_level([]) == "Low"
        assert _risk_level([self.f("Low"), self.f("Critical")]) == "Critical"
        assert _risk_level([self.f("Unknown")]) == "Medium"
        assert _risk_level([self.f("Unknown"), self.f("Low")]) == "Medium"

    def test_range_floor_findings_cap_at_medium(self):
        assert _risk_level([self.f("Critical", "range_floor")]) == "Medium"
        assert _risk_level([self.f("High", "range_floor"), self.f("Low", "range_floor")]) == "Medium"
        assert _risk_level([self.f("Low", "range_floor")]) == "Low"

    def test_exact_findings_still_dominate(self):
        assert _risk_level([self.f("Critical", "range_floor"), self.f("High")]) == "High"

    def test_summary_flags_range_floor_findings(self):
        from agents.cve_impact.logic import _summary

        text = _summary([{**self.f("Critical", "range_floor"), "package": "x"}], 3)
        assert "lowest version a declared range allows" in text
        assert "at most Medium" in text
        assert "lowest version" not in _summary([{**self.f("High"), "package": "x"}], 3)


class TestParseAnalysis:
    def test_parse_valid(self):
        analysis = _parse_analysis(llm_json(), URL)
        assert analysis["summary"].startswith("One high")
        assert analysis["impacts"]["CVE-2024-1"]

    def test_parse_markdown_wrapped_json(self):
        analysis = _parse_analysis(f"```json\n{llm_json()}\n```", URL)
        assert analysis is not None

    def test_optional_fields_are_coerced(self):
        analysis = _parse_analysis(json.dumps({"summary": "s", "impacts": "bad", "common_themes": None}), URL)
        assert analysis["impacts"] == {}
        assert analysis["common_themes"] == [] and analysis["recommendations"] == []

    @pytest.mark.parametrize(
        "response",
        ["{invalid", json.dumps({"impacts": {}}), json.dumps({"summary": ""}), json.dumps({"summary": 3}), json.dumps(["x"])],
    )
    def test_rejects_bad_responses(self, response):
        assert _parse_analysis(response, URL) is None
