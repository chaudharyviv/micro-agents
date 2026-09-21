"""Tests for core/osv.py."""

import pytest
import requests_mock

from core.manifests import EXACT, RANGE_FLOOR, Dependency
from core.osv import OSV_API, OSVError, build_findings, cvss3_score, fetch_details, query_batch, severity_of

DEP = Dependency("PyPI", "requests", "2.25.0", EXACT, "requirements.txt")


class TestCvss:
    @pytest.mark.parametrize(
        "vector,score",
        [
            ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", 9.8),
            ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H", 7.5),
            ("CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:C/C:L/I:L/A:N", 6.1),  # scope changed
            ("CVSS:3.0/AV:N/AC:L/PR:L/UI:N/S:C/C:H/I:H/A:H", 9.9),
            ("CVSS:3.1/AV:L/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:N", 0.0),
        ],
    )
    def test_known_scores(self, vector, score):
        assert cvss3_score(vector) == score

    def test_non_v3_or_garbage(self):
        assert cvss3_score("CVSS:4.0/AV:N/AC:L") is None
        assert cvss3_score("CVSS:3.1/AV:N") is None
        assert cvss3_score("") is None

    def test_severity_prefers_cvss_then_ghsa_label(self):
        v = {"severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"}]}
        assert severity_of(v) == ("Critical", 9.8)
        assert severity_of({"database_specific": {"severity": "MODERATE"}}) == ("Medium", None)
        assert severity_of({}) == ("Unknown", None)


class TestQueries:
    def test_query_batch_maps_results_in_order(self):
        with requests_mock.Mocker() as m:
            m.post(f"{OSV_API}/querybatch", json={"results": [{"vulns": [{"id": "A"}, {"id": "B"}]}, {}]})
            other = Dependency("npm", "lodash", "4.17.15", EXACT, "package.json")
            assert query_batch([DEP, other]) == [["A", "B"], []]
            body = m.last_request.json()
            assert body["queries"][0] == {"package": {"name": "requests", "ecosystem": "PyPI"}, "version": "2.25.0"}

    def test_query_batch_failure_raises(self):
        with requests_mock.Mocker() as m:
            m.post(f"{OSV_API}/querybatch", status_code=503)
            with pytest.raises(OSVError):
                query_batch([DEP])

    def test_query_batch_length_mismatch_raises(self):
        with requests_mock.Mocker() as m:
            m.post(f"{OSV_API}/querybatch", json={"results": []})
            with pytest.raises(OSVError):
                query_batch([DEP])

    def test_fetch_details_tolerates_individual_failures(self):
        with requests_mock.Mocker() as m:
            m.get(f"{OSV_API}/vulns/A", json={"id": "A"})
            m.get(f"{OSV_API}/vulns/B", status_code=500)
            assert fetch_details(["A", "B", "A"]) == {"A": {"id": "A"}, "B": None}


def vuln(vid, aliases=(), summary="s", severity=None, fixed=("2.32.4",), name="requests", eco="PyPI", **extra):
    v = {
        "id": vid,
        "aliases": list(aliases),
        "summary": summary,
        "affected": [
            {
                "package": {"name": name, "ecosystem": eco},
                "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}] + [{"fixed": f} for f in fixed]}],
            }
        ],
    }
    if severity:
        v["database_specific"] = {"severity": severity}
    v.update(extra)
    return v


class TestBuildFindings:
    def test_merges_aliased_advisories_and_prefers_cve_id(self):
        details = {
            "GHSA-aaaa-bbbb-cccc": vuln("GHSA-aaaa-bbbb-cccc", ["CVE-2024-1"], severity="HIGH"),
            "PYSEC-2024-9": vuln("PYSEC-2024-9", ["CVE-2024-1"]),  # no severity, same CVE
        }
        findings, missing = build_findings([DEP], [["PYSEC-2024-9", "GHSA-aaaa-bbbb-cccc"]], details)
        assert len(findings) == 1 and missing == 0
        f = findings[0]
        assert f["cve_id"] == "CVE-2024-1"
        assert f["severity"] == "High"
        assert set(f["advisory_ids"]) == {"GHSA-aaaa-bbbb-cccc", "PYSEC-2024-9"}
        assert f["fixed_in"] == ["2.32.4"]
        assert f["url"].startswith("https://osv.dev/vulnerability/")

    def test_falls_back_to_advisory_id_and_sorts_by_severity(self):
        details = {
            "GHSA-low0-0000-0000": vuln("GHSA-low0-0000-0000", severity="LOW"),
            "GHSA-crit-0000-0000": vuln("GHSA-crit-0000-0000", ["CVE-2025-5"], severity="CRITICAL"),
        }
        findings, _ = build_findings([DEP], [list(details)], details)
        assert [f["severity"] for f in findings] == ["Critical", "Low"]
        assert findings[1]["cve_id"] == "GHSA-low0-0000-0000"

    def test_fixed_versions_only_from_matching_package(self):
        v = vuln("GHSA-x", fixed=("1.0",), name="other-pkg")
        findings, _ = build_findings([DEP], [["GHSA-x"]], {"GHSA-x": v})
        assert findings[0]["fixed_in"] == []

    def test_withdrawn_skipped_and_failed_detail_kept_with_id(self):
        details = {"W": vuln("W", withdrawn="2024-01-01T00:00:00Z"), "F": None}
        findings, missing = build_findings([DEP], [["W", "F"]], details)
        assert [f["cve_id"] for f in findings] == ["F"]
        assert findings[0]["severity"] == "Unknown"
        assert missing == 1

    def test_ids_beyond_detail_cap_are_not_listed(self):
        findings, _ = build_findings([DEP], [["A", "B"]], {"A": vuln("A")})
        assert [f["cve_id"] for f in findings] == ["A"]

    def test_exact_findings_sort_before_range_floor(self):
        floor = Dependency("npm", "express", "4.17.1", RANGE_FLOOR, "package.json")
        details = {"C": vuln("C", severity="CRITICAL", name="express", eco="npm"), "L": vuln("L", severity="LOW")}
        findings, _ = build_findings([floor, DEP], [["C"], ["L"]], details)
        assert [(f["cve_id"], f["version_basis"]) for f in findings] == [("L", "exact"), ("C", "range_floor")]

    def test_version_basis_carried_through(self):
        dep = Dependency("npm", "express", "4.17.1", RANGE_FLOOR, "package.json")
        findings, _ = build_findings([dep], [["A"]], {"A": vuln("A", name="express", eco="npm")})
        assert findings[0]["version_basis"] == RANGE_FLOOR
