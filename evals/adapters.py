"""How each agent is called from an eval case. Imports are lazy so listing or validating cases needs no keys."""

from typing import Any


def call_agent(agent: str, value: Any) -> Any:
    """Run `agent` on a case's input and return its raw output."""
    if agent == "blog_scout":
        from agents.blog_scout.logic import scout_blog_ideas

        return scout_blog_ideas(value)
    if agent == "repo_onboarding":
        from agents.repo_onboarding.logic import generate_onboarding_guide

        return generate_onboarding_guide(value)
    if agent == "cve_impact":
        from agents.cve_impact.logic import analyze_cve_impact

        return analyze_cve_impact(value)
    if agent == "security_audit":
        from agents.security_audit.logic import generate_security_audit

        return generate_security_audit(value)
    if agent == "issue_fix_planner":
        from agents.issue_fix_planner.logic import run_issue_fix_planner

        return run_issue_fix_planner(value)
    if agent == "do_i_care":
        from agents.do_i_care.logic import run_do_i_care

        return run_do_i_care(value)
    if agent == "opportunity_scout":
        from agents.opportunity_scout.logic import run_opportunity_scout

        return run_opportunity_scout(value)
    if agent == "orchestrator":
        from agents.orchestrator.logic import run_orchestrator

        return run_orchestrator(value)
    raise ValueError(f"unknown agent {agent!r}")
