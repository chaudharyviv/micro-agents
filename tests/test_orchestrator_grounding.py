"""The orchestrator must only run specialists on targets the user themselves named."""

import pytest

from agents.orchestrator import logic
from agents.orchestrator.logic import MAX_TOOL_CALLS, build_tools, run_orchestrator
from core.budget import _scope
from core.errors import BudgetExceededError
from core.harness import HarnessResult
from core.memory import SessionMemory

REPO_TASK = "Review https://github.com/anthropics/anthropic-sdk-python for security"


def validator(name, session=None):
    tool = next(t for t in build_tools(session=session) if t.name == name)
    return lambda spec_input, task: tool.validate({"input": spec_input}, task)


class TestRepoTargets:
    @pytest.mark.parametrize("tool", ["repo_onboarding", "cve_impact", "security_audit"])
    @pytest.mark.parametrize(
        "spec_input",
        [
            "https://github.com/anthropics/anthropic-sdk-python",
            "https://github.com/Anthropics/Anthropic-SDK-Python",
            "https://github.com/anthropics/anthropic-sdk-python.git",
            "https://github.com/anthropics/anthropic-sdk-python/",
            "https://github.com/anthropics/anthropic-sdk-python/tree/main",
            "github.com/anthropics/anthropic-sdk-python",
        ],
    )
    def test_same_repo_in_any_spelling_is_allowed(self, tool, spec_input):
        assert validator(tool)(spec_input, REPO_TASK)

    @pytest.mark.parametrize(
        "spec_input",
        [
            "https://github.com/other/thing",                                  # the old check let this through
            "https://github.com/anthropics/anthropic-sdk-python-evil",
            "https://github.com/anthropics/anthropic-sdk",
            "https://github.com/evil/anthropic-sdk-python",
            "https://github.com/anthropics/anthropic-sdk-python2",
            "not a url",
            "",
        ],
    )
    def test_any_other_repo_is_blocked(self, spec_input):
        assert not validator("security_audit")(spec_input, REPO_TASK)

    def test_the_old_fuzzy_check_did_pass_the_lookalike(self):
        """Documents why repo/issue/user targets no longer use word overlap: https, github and com
        are shared with any GitHub URL, which alone clears the old 60% threshold."""
        assert logic._text_overlaps_task("https://github.com/other/thing", REPO_TASK)
        assert not validator("security_audit")("https://github.com/other/thing", REPO_TASK)

    def test_non_string_input_is_blocked(self):
        tool = next(t for t in build_tools() if t.name == "cve_impact")
        assert not tool.validate({"input": ["https://github.com/anthropics/anthropic-sdk-python"]}, REPO_TASK)
        assert not tool.validate({}, REPO_TASK)


class TestIssueTargets:
    TASK = "Plan a fix for https://github.com/o/r/issues/12"

    def test_exact_issue_allowed(self):
        assert validator("issue_fix_planner")("https://github.com/o/r/issues/12", self.TASK)

    @pytest.mark.parametrize(
        "spec_input",
        ["https://github.com/o/r/issues/13", "https://github.com/o/r/issues/123", "https://github.com/x/r/issues/12", "https://github.com/o/r"],
    )
    def test_other_issues_blocked(self, spec_input):
        assert not validator("issue_fix_planner")(spec_input, self.TASK)


class TestUserTargets:
    TASK = "What should GitHub user torvalds work on to advance their career?"

    @pytest.mark.parametrize("spec_input", ["torvalds", "@torvalds", "Torvalds", "https://github.com/torvalds"])
    def test_named_user_allowed(self, spec_input):
        assert validator("opportunity_scout")(spec_input, self.TASK)

    @pytest.mark.parametrize("spec_input", ["gvanrossum", "what", "github", "career", "torvalds2", "tor", "should work"])
    def test_words_and_other_users_blocked(self, spec_input):
        assert not validator("opportunity_scout")(spec_input, self.TASK)


class TestFreeTextTargets:
    def test_topic_from_the_task_allowed(self):
        assert validator("blog_scout")("Rust programming", "Suggest blog post ideas about Rust programming")

    def test_unrelated_topic_blocked(self):
        assert not validator("blog_scout")("cryptocurrency mining pools", "Suggest blog post ideas about Rust programming")

    def test_trending_is_always_fine(self):
        assert validator("blog_scout")("trending", "give me blog ideas")


class TestSessionInputs:
    def test_earlier_repo_can_be_reused_by_another_specialist(self):
        session = SessionMemory()
        session.record("repo_onboarding", "https://github.com/a/b", {})

        check = validator("security_audit", session)

        assert check("https://github.com/a/b", "now run a security audit on that repo")
        assert not check("https://github.com/c/d", "now run a security audit on that repo")

    def test_without_a_session_nothing_carries_over(self):
        assert not validator("security_audit")("https://github.com/a/b", "audit that repo")

    def test_a_recorded_repo_does_not_ground_a_random_username(self):
        session = SessionMemory()
        session.record("repo_onboarding", "https://github.com/a/b", {})

        assert not validator("opportunity_scout", session)("someone-else", "profile my account")

    def test_text_agent_can_rerun_an_exact_earlier_input(self):
        session = SessionMemory()
        session.record("blog_scout", "quantum computing", [])

        assert validator("blog_scout", session)("quantum computing", "do it again")


class TestRunOrchestratorLimits:
    def test_passes_the_tool_call_cap_and_runs_inside_a_bounded_scope(self, mocker):
        seen = {}

        def fake_harness(**kwargs):
            seen.update(kwargs)
            seen["scope"] = _scope.get()
            return HarnessResult(final_message="report", tool_calls=[{"tool": "cve_impact", "args": {"input": "u"}, "output": {}}], steps_used=1)

        mocker.patch("agents.orchestrator.logic.run_harness", side_effect=fake_harness)

        result = run_orchestrator(REPO_TASK)

        assert result["status"] == "success"
        assert seen["max_tool_calls"] == MAX_TOOL_CALLS == 3
        assert seen["max_steps"] == 3
        assert seen["scope"] is not None and seen["scope"].max_calls == 20
        assert _scope.get() is None  # scope closed afterwards

    def test_reuses_a_scope_opened_by_the_caller(self, mocker):
        from core.budget import request_scope

        seen = {}
        mocker.patch(
            "agents.orchestrator.logic.run_harness",
            side_effect=lambda **kw: seen.update(scope=_scope.get()) or HarnessResult("r", [{"tool": "x", "args": {}, "output": {}}], 1),
        )

        with request_scope(client_id="1.2.3.4", max_calls=5) as outer:
            run_orchestrator(REPO_TASK)

        assert seen["scope"] is outer and seen["scope"].max_calls == 5

    def test_budget_exhaustion_is_a_clear_error(self, mocker):
        mocker.patch(
            "agents.orchestrator.logic.run_harness",
            side_effect=BudgetExceededError("The service has reached its daily usage limit. It resets at 00:00 UTC.", "daily"),
        )

        result = run_orchestrator(REPO_TASK)

        assert result["status"] == "error"
        assert "daily usage limit" in result["error_message"]

    def test_all_blocked_message_says_how_to_name_targets(self, mocker):
        blocked = {"tool": "security_audit", "args": {"input": "https://github.com/other/thing"}, "output": {"error": "Call to 'security_audit' blocked: input not grounded in the task."}}
        mocker.patch("agents.orchestrator.logic.run_harness", return_value=HarnessResult("r", [blocked], 1))

        result = run_orchestrator(REPO_TASK)

        assert result["status"] == "error"
        assert "@name" in result["error_message"]
