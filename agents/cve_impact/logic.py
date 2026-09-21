"""Core logic for CVE Impact agent.

Vulnerabilities come from OSV.dev, looked up for dependencies parsed out of the repo's manifests.
Vulnerability IDs, versions, severities, fixed versions and the overall risk level are computed in
code from that data. The LLM only adds prose (summary, per-finding impact, themes) about findings
it is handed, and its output is discarded if it mentions any ID OSV did not return.
"""

import json
import logging
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.github_tool import fetch_repo, GitHubAPIError, MANIFEST_FILES
from core.llm import call_llm, LLMUnavailableError
from core.llm_utils import extract_json, wrap_untrusted
from core.manifests import PARSERS, parse_manifests
from core.osv import (
    MAX_DETAIL_FETCH,
    SEVERITY_ORDER,
    OSVError,
    build_findings,
    fetch_details,
    query_batch,
)
from agents.cve_impact.prompts import (
    RESPONSE_SCHEMA,
    SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MAX_DEPS = 200
MAX_FINDINGS_FOR_LLM = 15

_VULN_ID_RE = re.compile(
    r"\b(?:CVE-\d{4}-\d{4,}|GHSA(?:-[a-z0-9]{4}){3}|PYSEC-\d{4}-\d+|GO-\d{4}-\d+)\b", re.IGNORECASE
)


def analyze_cve_impact(repo_url: str) -> dict:
    """
    Analyze known vulnerabilities in a GitHub repository's declared dependencies.

    Args:
        repo_url: GitHub repository URL (e.g., https://github.com/user/repo)

    Returns:
        A dict with:
        - summary: overview of security posture
        - risk_level: Critical/High/Medium/Low, or Unknown if no dependency could be checked.
          Findings at declared range floors (not exact pins) can raise it to at most Medium.
        - cve_analysis: list of findings, each with cve_id, advisory_ids, package, ecosystem,
          version, version_basis ("exact" or "range_floor"), severity, cvss_score, description,
          impact, remediation, fixed_in, url
        - common_themes, remediation_priority, recommendations
        - dependencies_checked: number of dependencies looked up
        - dependencies_unchecked: list of {name, source, reason} that could not be looked up
        - manifests_not_analyzed: manifest files present in the repo but not parsed
        - data_source: "OSV.dev"; analysis_source: "osv+llm" or "osv" (LLM prose unavailable)
        - repo_url: original URL

        On error, returns a dict with error_message and repo_url.
    """
    logger.info(f"Analyzing CVE impact for: {repo_url}")

    try:
        repo_data = fetch_repo(repo_url)
        logger.info(f"Fetched repo: {repo_data.get('name', 'Unknown')}")
    except GitHubAPIError as e:
        logger.error(f"Failed to fetch repo: {e}")
        return {"error_message": str(e), "repo_url": repo_url}
    except Exception as e:
        logger.error(f"Unexpected error fetching repo: {e}")
        return {"error_message": "Failed to fetch repository. Please try again.", "repo_url": repo_url}

    manifests = repo_data.get("manifests", {})
    deps, unparsed = parse_manifests(manifests)

    covered = {m for m in manifests if m in PARSERS and m not in unparsed}
    present = {f["name"] for f in repo_data.get("files", []) if f["name"] in MANIFEST_FILES}
    not_analyzed = sorted((present | set(manifests)) - covered)

    seen = set()
    checkable, unchecked = [], []
    for d in deps:
        if d.version is None:
            unchecked.append({"name": d.name, "source": d.source, "reason": d.reason})
            continue
        key = (d.ecosystem, d.name, d.version)
        if key in seen:
            continue
        seen.add(key)
        if len(checkable) < MAX_DEPS:
            checkable.append(d)
        else:
            unchecked.append({"name": d.name, "source": d.source, "reason": f"over the {MAX_DEPS}-dependency limit"})
    logger.info(f"Parsed {len(deps)} dependencies; {len(checkable)} checkable")

    coverage = {
        "dependencies_checked": len(checkable),
        "dependencies_unchecked": unchecked,
        "manifests_not_analyzed": not_analyzed,
        "data_source": "OSV.dev",
        "repo_url": repo_url,
    }

    if not checkable:
        if not present:
            reason = "no dependency manifests found in the repository root"
        elif not_analyzed:
            reason = f"the manifests present ({', '.join(not_analyzed)}) are not supported or could not be parsed"
        else:
            reason = "the dependencies declared could not be resolved to specific versions"
        return {
            "summary": f"No dependencies could be checked: {reason}. This is not evidence the project is free of vulnerabilities.",
            "risk_level": "Unknown",
            "cve_analysis": [],
            "common_themes": [],
            "remediation_priority": [],
            "recommendations": [],
            "analysis_source": "osv",
            **coverage,
        }

    try:
        ids_per_dep = query_batch(checkable)
    except OSVError as e:
        return {"error_message": f"{e}. Please try again later.", "repo_url": repo_url}

    all_ids = list(dict.fromkeys(i for ids in ids_per_dep for i in ids))
    details = fetch_details(all_ids[:MAX_DETAIL_FETCH])
    findings, no_details = build_findings(checkable, ids_per_dep, details)
    logger.info(f"{len(findings)} findings from {len(all_ids)} advisories")

    for f in findings:
        f["impact"] = ""
        f["remediation"] = _remediation(f)

    risk_level = _risk_level(findings)
    summary = _summary(findings, len(checkable))
    themes: list = []
    recommendations: list = []
    analysis_source = "osv"

    if findings:
        prose = _explain_findings(repo_url, repo_data, findings, len(checkable))
        if prose:
            analysis_source = "osv+llm"
            summary = prose["summary"]
            themes = prose.get("common_themes", [])
            recommendations = prose.get("recommendations", [])
            for f in findings:
                f["impact"] = prose.get("impacts", {}).get(f["cve_id"], "")

    result = {
        "summary": summary,
        "risk_level": risk_level,
        "cve_analysis": findings,
        "common_themes": themes,
        "remediation_priority": _remediation_priority(findings),
        "recommendations": recommendations,
        "analysis_source": analysis_source,
        **coverage,
    }
    if len(all_ids) > MAX_DETAIL_FETCH or no_details:
        result["advisories_not_detailed"] = max(len(all_ids) - MAX_DETAIL_FETCH, 0) + no_details
    return result


def _top_severity(findings: list[dict]) -> str:
    """Highest severity among findings, with Unknown counted as Medium. Low when there are none."""
    if not findings:
        return "Low"
    top = max(findings, key=lambda f: SEVERITY_ORDER[f["severity"]])["severity"]
    return "Medium" if top == "Unknown" else top


def _risk_level(findings: list[dict]) -> str:
    """
    Overall risk from the findings.

    Findings at exact pinned versions count at full severity. Findings that exist only at a declared
    range floor (the installed version may be newer and unaffected) can raise the level to at most
    Medium, so a library that merely allows an old version of a dependency isn't rated Critical.
    """
    exact = [f for f in findings if f["version_basis"] == "exact"]
    floor = [f for f in findings if f["version_basis"] != "exact"]
    floor_top = _top_severity(floor)
    if floor and SEVERITY_ORDER[floor_top] > SEVERITY_ORDER["Medium"]:
        floor_top = "Medium"
    return max(_top_severity(exact), floor_top if floor else "Low", key=lambda s: SEVERITY_ORDER[s])


def _summary(findings: list[dict], checked: int) -> str:
    if not findings:
        return f"No known vulnerabilities found in the {checked} dependencies checked against OSV.dev."
    counts: dict[str, int] = {}
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    breakdown = ", ".join(f"{n} {s}" for s, n in sorted(counts.items(), key=lambda kv: -SEVERITY_ORDER[kv[0]]))
    packages = len({f["package"] for f in findings})
    noun = "vulnerability" if len(findings) == 1 else "vulnerabilities"
    text = f"Found {len(findings)} known {noun} ({breakdown}) across {packages} of {checked} dependencies checked against OSV.dev."
    floor = sum(1 for f in findings if f["version_basis"] != "exact")
    if floor:
        text += (
            f" {floor} of these were checked at the lowest version a declared range allows, so the installed "
            "version may be newer and unaffected; they raise the risk level to at most Medium."
        )
    return text


def _remediation(finding: dict) -> str:
    if finding["fixed_in"]:
        return f"Upgrade {finding['package']} to a fixed version (fixed in: {', '.join(finding['fixed_in'][:4])})."
    return "OSV lists no fixed version; review the advisory for mitigations or alternatives."


def _remediation_priority(findings: list[dict]) -> list[dict]:
    """One upgrade action per package, ordered by the worst severity that package carries."""
    by_pkg: dict[str, list[dict]] = {}
    for f in findings:  # findings are already severity-sorted, so first seen = worst
        by_pkg.setdefault(f["package"], []).append(f)
    actions = []
    for pkg, fs in list(by_pkg.items())[:10]:
        actions.append(
            {
                "rank": len(actions) + 1,
                "action": f"Upgrade {pkg} {fs[0]['version']}: {len(fs)} known vulnerabilities, worst is {fs[0]['severity']}",
                "fixes": [f["cve_id"] for f in fs],
            }
        )
    return actions


def _known_ids(findings: list[dict]) -> set[str]:
    ids = set()
    for f in findings:
        ids.add(f["cve_id"].upper())
        ids.update(i.upper() for i in f["advisory_ids"])
    return ids


def _explain_findings(repo_url: str, repo_data: dict, findings: list[dict], checked: int) -> Optional[dict]:
    """Ask the LLM for prose about the findings. Returns None if it fails or strays from OSV's IDs."""
    payload = [
        {k: f[k] for k in ("cve_id", "package", "version", "version_basis", "severity", "description", "fixed_in")}
        for f in findings[:MAX_FINDINGS_FOR_LLM]
    ]
    prompt = USER_PROMPT_TEMPLATE.format(
        repo_url=repo_url,
        repo_name=repo_data.get("name", "Unknown"),
        repo_description=repo_data.get("description", ""),
        repo_language=repo_data.get("language", "Unknown"),
        checked_count=checked,
        findings=wrap_untrusted("Findings (JSON):\n" + json.dumps(payload, indent=2)),
    )

    prose = None
    for attempt in range(2):
        try:
            response = call_llm(
                prompt if attempt == 0 else prompt + "\n\nYour previous response was not valid JSON. Respond with ONLY a JSON object.",
                system=SYSTEM_PROMPT,
                schema=RESPONSE_SCHEMA,
                schema_name="cve_impact_prose",
            )
        except LLMUnavailableError as e:
            logger.warning(f"LLM unavailable, returning OSV data without prose: {e}")
            return None
        except Exception as e:
            logger.warning(f"LLM error, returning OSV data without prose: {e}")
            return None
        prose = _parse_analysis(response, repo_url)
        if prose is not None:
            break
    if prose is None:
        return None

    strings = [prose["summary"]]
    strings += [v for v in prose.get("impacts", {}).values() if isinstance(v, str)]
    strings += [s for s in prose.get("common_themes", []) + prose.get("recommendations", []) if isinstance(s, str)]
    known = _known_ids(findings)
    stray = {m.upper() for s in strings for m in _VULN_ID_RE.findall(s)} - known
    if stray:
        logger.warning(f"Discarding LLM prose: it mentioned IDs OSV did not return: {sorted(stray)}")
        return None
    return prose


def _parse_analysis(response: str, repo_url: str) -> Optional[dict]:
    """
    Parse the LLM's JSON prose response.

    Args:
        response: LLM's JSON response (may be wrapped in markdown code blocks)
        repo_url: Original repository URL for context

    Returns:
        Parsed dict with at least a string "summary", or None if parsing or validation fails
    """
    try:
        analysis = extract_json(response)

        if not isinstance(analysis, dict):
            logger.error("Response is not a JSON object")
            return None

        if not isinstance(analysis.get("summary"), str) or not analysis["summary"].strip():
            logger.warning("Analysis missing summary")
            return None

        # Coerce optional fields to the shapes callers rely on. impacts arrives as [{cve_id, impact}]
        # (a dict is accepted too) and is normalized to {cve_id: impact}.
        impacts = analysis.get("impacts")
        if isinstance(impacts, list):
            impacts = {
                e["cve_id"]: e["impact"]
                for e in impacts
                if isinstance(e, dict) and isinstance(e.get("cve_id"), str) and isinstance(e.get("impact"), str)
            }
        analysis["impacts"] = impacts if isinstance(impacts, dict) else {}
        for key in ("common_themes", "recommendations"):
            if not isinstance(analysis.get(key), list):
                analysis[key] = []

        return analysis

    except ValueError as e:
        logger.error(f"JSON parse error: {e}")
        return None
    except Exception as e:
        logger.error(f"Parse error: {e}")
        return None


if __name__ == "__main__":
    print("=== CVE Impact Smoke Test ===\n")

    repo_url = "https://github.com/anthropics/anthropic-sdk-python"
    print(f"Analyzing CVE impact for: {repo_url}\n")

    try:
        analysis = analyze_cve_impact(repo_url)

        if "error_message" in analysis:
            print(f"Error: {analysis['error_message']}")
        else:
            print(f"Summary: {analysis.get('summary', 'N/A')}")
            print(f"Risk Level: {analysis.get('risk_level', 'N/A')}")
            print(f"Dependencies checked: {analysis.get('dependencies_checked', 0)}")

            cves = analysis.get("cve_analysis", [])
            if cves:
                print(f"\nFindings: {len(cves)}")
                for cve in cves[:3]:
                    print(f"  - {cve.get('cve_id', 'N/A')} ({cve.get('package')}): {cve.get('severity', 'N/A')}")

    except Exception as e:
        print(f"Error: {e}")
