"""OSV.dev client: look up known vulnerabilities for parsed dependencies.

Every vulnerability ID surfaced to users comes from an OSV response, never from an LLM.
"""

import logging
import math
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import requests

from core.manifests import Dependency

logger = logging.getLogger(__name__)

OSV_API = "https://api.osv.dev/v1"
_TIMEOUT = 15
MAX_DETAIL_FETCH = 60  # advisory detail lookups per analysis

SEVERITY_ORDER = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1, "Unknown": 2}
_GHSA_LABELS = {"CRITICAL": "Critical", "HIGH": "High", "MODERATE": "Medium", "MEDIUM": "Medium", "LOW": "Low"}


class OSVError(Exception):
    """Raised when OSV.dev can't be queried; callers must not treat this as 'no vulnerabilities'."""


def query_batch(deps: list[Dependency]) -> list[list[str]]:
    """Return, for each dependency, the IDs of advisories affecting that exact version."""
    queries = [
        {"package": {"name": d.name, "ecosystem": d.ecosystem}, "version": d.version} for d in deps
    ]
    try:
        resp = requests.post(f"{OSV_API}/querybatch", json={"queries": queries}, timeout=_TIMEOUT)
        resp.raise_for_status()
        results = resp.json()["results"]
    except Exception as e:
        logger.error(f"OSV batch query failed: {e}")
        raise OSVError("Could not reach the OSV.dev vulnerability database") from e
    if len(results) != len(deps):
        raise OSVError("OSV.dev returned a malformed batch response")
    return [[v["id"] for v in r.get("vulns", [])] for r in results]


def _get_vuln(vuln_id: str) -> Optional[dict]:
    try:
        resp = requests.get(f"{OSV_API}/vulns/{vuln_id}", timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f"OSV detail lookup failed for {vuln_id}: {e}")
        return None


def fetch_details(ids: list[str]) -> dict[str, Optional[dict]]:
    """Fetch advisory records concurrently. A failed lookup maps to None."""
    unique = list(dict.fromkeys(ids))
    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(zip(unique, pool.map(_get_vuln, unique)))


# --- Severity -------------------------------------------------------------------------------

_CVSS3_WEIGHTS = {
    "AV": {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.2},
    "AC": {"L": 0.77, "H": 0.44},
    "UI": {"N": 0.85, "R": 0.62},
    "CIA": {"H": 0.56, "L": 0.22, "N": 0.0},
}


def _roundup(x: float) -> float:
    i = round(x * 100000)
    return i / 100000.0 if i % 10000 == 0 else (math.floor(i / 10000) + 1) / 10.0


def cvss3_score(vector: str) -> Optional[float]:
    """CVSS v3.x base score from a vector string, or None if it isn't a valid v3 vector."""
    if not vector.startswith(("CVSS:3.0/", "CVSS:3.1/")):
        return None
    try:
        m = dict(p.split(":") for p in vector.split("/")[1:])
        changed = m["S"] == "C"
        pr = {"N": 0.85, "L": 0.68 if changed else 0.62, "H": 0.5 if changed else 0.27}[m["PR"]]
        c, i, a = (_CVSS3_WEIGHTS["CIA"][m[k]] for k in ("C", "I", "A"))
        iss = 1 - (1 - c) * (1 - i) * (1 - a)
        impact = 7.52 * (iss - 0.029) - 3.25 * (iss - 0.02) ** 15 if changed else 6.42 * iss
        expl = 8.22 * _CVSS3_WEIGHTS["AV"][m["AV"]] * _CVSS3_WEIGHTS["AC"][m["AC"]] * pr * _CVSS3_WEIGHTS["UI"][m["UI"]]
        if impact <= 0:
            return 0.0
        return _roundup(min((impact + expl) * (1.08 if changed else 1), 10))
    except (KeyError, ValueError):
        return None


def _band(score: float) -> str:
    if score >= 9.0:
        return "Critical"
    if score >= 7.0:
        return "High"
    if score >= 4.0:
        return "Medium"
    return "Low"


def severity_of(vuln: dict) -> tuple[str, Optional[float]]:
    """(label, cvss_score) for an advisory. Label is 'Unknown' if OSV gives nothing we can read."""
    score = None
    for s in vuln.get("severity", []):
        if s.get("type") == "CVSS_V3":
            score = cvss3_score(s.get("score", ""))
            if score is not None:
                break
    if score is not None and score > 0:
        return _band(score), score
    label = _GHSA_LABELS.get(str(vuln.get("database_specific", {}).get("severity", "")).upper())
    return (label or "Unknown"), score


# --- Findings -------------------------------------------------------------------------------

def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _fixed_versions(vuln: dict, dep: Dependency) -> list[str]:
    fixed = []
    for aff in vuln.get("affected", []):
        pkg = aff.get("package", {})
        if pkg.get("ecosystem") != dep.ecosystem or _norm(pkg.get("name", "")) != _norm(dep.name):
            continue
        for rng in aff.get("ranges", []):
            for ev in rng.get("events", []):
                if "fixed" in ev and ev["fixed"] not in fixed:
                    fixed.append(ev["fixed"])
    return fixed


def build_findings(deps: list[Dependency], ids_per_dep: list[list[str]], details: dict[str, Optional[dict]]) -> tuple[list[dict], int]:
    """
    Turn OSV results into de-duplicated, severity-sorted findings.

    Advisories that alias the same CVE for the same package (e.g. a GHSA and a PYSEC record) are
    merged. Returns (findings, advisories_without_details).
    """
    merged: dict[tuple, dict] = {}
    for dep, ids in zip(deps, ids_per_dep):
        for vid in ids:
            if vid not in details:
                continue  # beyond the detail-fetch cap; counted by the caller
            vuln = details[vid]
            if vuln is None:
                vuln = {"id": vid}
            if vuln.get("withdrawn"):
                continue
            all_ids = [vid] + [a for a in vuln.get("aliases", []) if a != vid]
            cves = sorted(i for i in all_ids if i.startswith("CVE-"))
            primary = cves[0] if cves else vid
            key = (dep.ecosystem, _norm(dep.name), dep.version, primary)

            severity, score = severity_of(vuln)
            summary = vuln.get("summary") or (vuln.get("details", "").strip().splitlines() or [""])[0]
            finding = {
                "cve_id": primary,
                "advisory_ids": [i for i in all_ids if i != primary],
                "package": dep.name,
                "ecosystem": dep.ecosystem,
                "version": dep.version,
                "version_basis": dep.basis,
                "severity": severity,
                "cvss_score": score,
                "description": summary[:300] if summary else "No description provided by OSV.",
                "fixed_in": _fixed_versions(vuln, dep),
                "url": f"https://osv.dev/vulnerability/{vid}",
                "source": dep.source,
            }
            existing = merged.get(key)
            if existing is None:
                merged[key] = finding
            else:  # same CVE seen under another advisory ID: keep the richer record
                existing["advisory_ids"] = sorted(set(existing["advisory_ids"]) | set(finding["advisory_ids"]) | ({vid} - {primary}))
                if existing["severity"] == "Unknown" and severity != "Unknown":
                    existing["severity"], existing["cvss_score"] = severity, score
                if not existing["fixed_in"]:
                    existing["fixed_in"] = finding["fixed_in"]

    # Confirmed (exact-pin) findings first, then by severity
    findings = sorted(
        merged.values(),
        key=lambda f: (f["version_basis"] != "exact", -SEVERITY_ORDER[f["severity"]], f["package"], f["cve_id"]),
    )
    missing = sum(1 for v in details.values() if v is None)
    return findings, missing
