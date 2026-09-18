"""Tests for how the orchestrator wires memory into its harness tools (no API calls)."""

from unittest.mock import patch

from agents.orchestrator import logic
from core.memory import ResultCache, SessionMemory

URL = "https://github.com/acme/widgets"


def _tools_by_name(tools):
    return {t.name: t for t in tools}


def test_recall_tool_only_exists_when_session_memory_is_given():
    assert "recall_memory" not in _tools_by_name(logic.build_tools())
    assert "recall_memory" in _tools_by_name(logic.build_tools(session=SessionMemory()))


def test_recall_tool_returns_recent_runs_or_a_note_when_empty():
    session = SessionMemory()
    recall = _tools_by_name(logic.build_tools(session=session))["recall_memory"]
    assert recall.run()["runs"] == []

    session.record("repo_onboarding", URL, {"overview": "widgets"})
    runs = recall.run()["runs"]
    assert runs[0]["agent"] == "repo_onboarding" and runs[0]["input"] == URL
    assert set(runs[0]) == {"agent", "input", "summary", "age_seconds"}


def test_followup_may_reuse_an_input_from_earlier_in_the_session_but_not_a_new_one():
    session = SessionMemory()
    session.record("repo_onboarding", URL, {"ok": 1})
    tool = _tools_by_name(logic.build_tools(session=session))["security_audit"]

    assert tool.validate({"input": URL}, "do the same again for the security side")
    assert not tool.validate({"input": "https://github.com/evil/other"}, "do the same again")


def test_without_session_input_must_appear_in_the_task_text():
    tool = _tools_by_name(logic.build_tools())["security_audit"]
    assert tool.validate({"input": URL}, f"audit {URL}")
    assert not tool.validate({"input": URL}, "audit something else")


def test_specialist_tool_caches_records_and_reports_memory_hit():
    calls, events = [], []
    session, cache = SessionMemory(), ResultCache()
    spec = {**logic.SPECIALISTS["repo_onboarding"], "run": lambda x: calls.append(x) or {"guide": x}}

    with patch.dict(logic.SPECIALISTS, {"repo_onboarding": spec}):
        tool = _tools_by_name(logic.build_tools(cache, session, lambda e, p: events.append(e)))["repo_onboarding"]
        assert tool.run(URL) == {"guide": URL}
        assert tool.run(URL) == {"guide": URL}

    assert calls == [URL]
    assert events == ["memory_hit"]
    assert len(session) == 2
