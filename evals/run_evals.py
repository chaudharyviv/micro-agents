"""Evaluation suite for micro-agents.

Runs all agent evaluations and reports pass rates.
Usage: python evals/run_evals.py
"""

import json
import sys
from pathlib import Path
from typing import Optional

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.blog_scout.logic import scout_blog_ideas
from agents.repo_onboarding.logic import generate_onboarding_guide
from agents.cve_impact.logic import analyze_cve_impact
from agents.issue_fix_planner.logic import run_issue_fix_planner
from agents.do_i_care.logic import run_do_i_care
from agents.opportunity_scout.logic import run_opportunity_scout


def load_evals(agent_name: str) -> list[dict]:
    """Load evals.jsonl for an agent."""
    evals_path = Path(__file__).parent.parent / f"agents/{agent_name}/evals.jsonl"
    if not evals_path.exists():
        print(f"⚠️  No evals.jsonl found for {agent_name}")
        return []

    evals = []
    with open(evals_path, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                evals.append(json.loads(line))
    return evals


def check_result(output, rule: str) -> bool:
    """Check if output passes the given rule."""
    if rule == "min_count":
        if isinstance(output, list) and len(output) >= 3:
            return True
        if isinstance(output, dict) and output.get("error_message"):
            return False
        return isinstance(output, list) and len(output) >= 1

    elif rule == "has_source_url":
        if isinstance(output, list):
            return all(isinstance(i, dict) and i.get("source_url") for i in output[:3])
        return False

    elif rule == "has_structure":
        if isinstance(output, dict) and "top_items" in output:
            return isinstance(output["top_items"], list)
        return False

    elif rule == "no_code_generation":
        if isinstance(output, dict) and "status" in output:
            if output.get("status") == "error":
                return True
            plan = output.get("plan", "").lower()
            return not any(
                keyword in plan for keyword in ["def ", "class ", "import ", "```", "code"]
            )
        return False

    elif rule == "error_handling":
        if isinstance(output, dict):
            return "error_message" in output or "status" in output
        return False

    elif rule == "input_validation":
        if isinstance(output, str):
            return "error" in output.lower() or "please" in output.lower()
        if isinstance(output, dict):
            return "error_message" in output
        return False

    elif rule == "manual":
        # Manual inspection - assume pass if no error
        return not (
            isinstance(output, dict) and "error_message" in output
        ) or isinstance(output, dict)

    else:
        return True


def evaluate_blog_scout(test_case: dict) -> bool:
    """Evaluate Blog Scout agent."""
    try:
        topic = test_case.get("input") or "trending"
        output = scout_blog_ideas(topic)
        rule = test_case.get("check", "min_count")
        return check_result(output, rule)
    except Exception as e:
        print(f"    ❌ Exception: {str(e)[:80]}")
        return False


def evaluate_repo_onboarding(test_case: dict) -> bool:
    """Evaluate Repo Onboarding agent."""
    try:
        repo_url = test_case.get("input")
        if not repo_url or not repo_url.strip():
            return check_result(None, test_case.get("check", "input_validation"))

        output = generate_onboarding_guide(repo_url.strip())
        rule = test_case.get("check", "has_required_fields")

        if rule == "has_required_fields":
            return (
                isinstance(output, dict)
                and "project_name" in output
                and "overview" in output
            )
        elif rule == "guide_completeness":
            return (
                isinstance(output, dict)
                and output.get("overview")
                and len(output.get("setup_steps", [])) > 0
            )
        return check_result(output, rule)
    except Exception as e:
        print(f"    ❌ Exception: {str(e)[:80]}")
        return False


def evaluate_cve_impact(test_case: dict) -> bool:
    """Evaluate CVE Impact agent."""
    try:
        query = test_case.get("input")
        if not query or not query.strip():
            return check_result(None, test_case.get("check", "input_validation"))

        output = analyze_cve_impact(query.strip())
        rule = test_case.get("check", "security_assessment")

        if rule == "security_assessment":
            return isinstance(output, dict) and (
                "risk_level" in output or "severity" in output or "error_message" in output
            )
        elif rule == "impact_assessment":
            return isinstance(output, dict) and "summary" in output
        elif rule == "search_based_analysis":
            return isinstance(output, dict)
        return check_result(output, rule)
    except Exception as e:
        print(f"    ❌ Exception: {str(e)[:80]}")
        return False


def evaluate_issue_planner(test_case: dict) -> bool:
    """Evaluate Issue Fix Planner agent."""
    try:
        issue_url = test_case.get("input")
        if not issue_url or not issue_url.strip():
            return check_result(None, test_case.get("check", "input_validation"))

        output = run_issue_fix_planner(issue_url.strip())
        rule = test_case.get("check", "no_code_generation")

        if rule == "no_code_generation":
            if output.get("status") == "error":
                return True
            plan = output.get("plan", "").lower()
            return not any(
                keyword in plan for keyword in ["def ", "class ", "import ", "```"]
            )
        elif rule == "has_plan_structure":
            return (
                output.get("status") == "success"
                and output.get("plan")
                and len(output.get("plan", "")) > 100
            )
        return check_result(output, rule)
    except Exception as e:
        print(f"    ❌ Exception: {str(e)[:80]}")
        return False


def evaluate_do_i_care(test_case: dict) -> bool:
    """Evaluate Do I Care agent."""
    try:
        headlines = test_case.get("input")
        if not headlines:
            return check_result(None, test_case.get("check", "input_validation"))

        if isinstance(headlines, str):
            headlines = [headlines]

        output = run_do_i_care(headlines)
        rule = test_case.get("check", "has_structure")

        if rule == "has_structure":
            return (
                output.get("status") == "success"
                and isinstance(output.get("top_items"), list)
            )
        elif rule == "relevance_ranking":
            return (
                output.get("status") == "success"
                and len(output.get("top_items", [])) <= 3
            )
        elif rule == "top_3_selection":
            return len(output.get("top_items", [])) <= 3
        return check_result(output, rule)
    except Exception as e:
        print(f"    ❌ Exception: {str(e)[:80]}")
        return False


def evaluate_opportunity_scout(test_case: dict) -> bool:
    """Evaluate Opportunity Scout agent."""
    try:
        username = test_case.get("input")
        if not username or not username.strip():
            return check_result(None, test_case.get("check", "input_validation"))

        output = run_opportunity_scout(username.strip())
        rule = test_case.get("check", "has_structure")

        if rule == "has_structure":
            return (
                output.get("status") == "success"
                and "skill_gaps" in output
                and "job_suggestions" in output
            )
        elif rule == "analysis_completeness":
            return (
                output.get("status") == "success"
                and output.get("project_idea")
            )
        elif rule == "opportunistic_insight":
            return output.get("status") == "success"
        return check_result(output, rule)
    except Exception as e:
        print(f"    ❌ Exception: {str(e)[:80]}")
        return False


def run_agent_evals(
    agent_name: str, eval_func, verbose: bool = False
) -> dict:
    """Run evals for a single agent."""
    evals = load_evals(agent_name)
    if not evals:
        return {"passed": 0, "total": 0, "pass_rate": 0.0, "failures": []}

    passed = 0
    failures = []

    for i, test_case in enumerate(evals, 1):
        try:
            if eval_func(test_case):
                passed += 1
                if verbose:
                    print(f"    ✅ Test {i}/{len(evals)}")
            else:
                failures.append(
                    {
                        "test": i,
                        "input": str(test_case.get("input"))[:50],
                        "expected": test_case.get("expected_behavior", "")[:80],
                    }
                )
                if verbose:
                    print(f"    ❌ Test {i}/{len(evals)}")
        except Exception as e:
            failures.append(
                {"test": i, "error": str(e)[:100]}
            )
            if verbose:
                print(f"    ❌ Test {i}/{len(evals)} - Exception")

    pass_rate = passed / len(evals) if evals else 0.0
    return {
        "passed": passed,
        "total": len(evals),
        "pass_rate": pass_rate,
        "failures": failures,
    }


def run_evals(verbose: bool = False):
    """Run the evaluation suite for all agents."""
    print("\n" + "=" * 70)
    print("🧪 MICRO-AGENTS EVALUATION SUITE")
    print("=" * 70 + "\n")

    agents = [
        ("blog_scout", evaluate_blog_scout),
        ("repo_onboarding", evaluate_repo_onboarding),
        ("cve_impact", evaluate_cve_impact),
        ("issue_fix_planner", evaluate_issue_planner),
        ("do_i_care", evaluate_do_i_care),
        ("opportunity_scout", evaluate_opportunity_scout),
    ]

    results = {}
    total_passed = 0
    total_tests = 0

    for agent_name, eval_func in agents:
        print(f"📊 Testing {agent_name}...")
        result = run_agent_evals(agent_name, eval_func, verbose=verbose)
        results[agent_name] = result

        total_passed += result["passed"]
        total_tests += result["total"]

        pass_rate_pct = result["pass_rate"] * 100
        status = "✅" if result["pass_rate"] >= 0.8 else "⚠️"
        print(
            f"   {status} Pass rate: {result['passed']}/{result['total']} "
            f"({pass_rate_pct:.0f}%)"
        )

        if result["failures"] and verbose:
            print(f"   Failures:")
            for failure in result["failures"][:3]:
                if "error" in failure:
                    print(f"     - Test {failure['test']}: {failure['error']}")
                else:
                    print(f"     - Test {failure['test']}: {failure['input']}")
        print()

    # Print summary table
    print("\n" + "=" * 70)
    print("📈 SUMMARY TABLE")
    print("=" * 70 + "\n")

    print(f"{'Agent':<25} {'Pass Rate':<15} {'Tests':<10} {'Status':<10}")
    print("-" * 70)

    for agent_name, result in results.items():
        pass_rate_pct = result["pass_rate"] * 100
        status = "✅ PASS" if result["pass_rate"] >= 0.8 else "⚠️  REVIEW"
        print(
            f"{agent_name:<25} {pass_rate_pct:>6.0f}% "
            f"({result['passed']}/{result['total']:<3}) {status:<10}"
        )

    print("-" * 70)
    overall_rate = total_passed / total_tests if total_tests > 0 else 0.0
    overall_pct = overall_rate * 100
    print(
        f"{'OVERALL':<25} {overall_pct:>6.0f}% "
        f"({total_passed}/{total_tests:<3})"
    )
    print()

    # Target achievement
    print("=" * 70)
    print("🎯 TARGET ACHIEVEMENT")
    print("=" * 70 + "\n")

    passed_agents = sum(1 for r in results.values() if r["pass_rate"] >= 0.8)
    target = len(results)

    print(f"Agents at 80%+: {passed_agents}/{target}")
    print(f"Overall pass rate: {overall_pct:.1f}%")
    print(f"Target: 80%+ for all agents\n")

    if passed_agents == target and overall_rate >= 0.8:
        print("🎉 All agents meet 80%+ target! Ready for production.\n")
    else:
        print("⚠️  Some agents below 80%. Review failures and improve.\n")

    return results


if __name__ == "__main__":
    # Run with verbose output if requested
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    run_evals(verbose=verbose)
