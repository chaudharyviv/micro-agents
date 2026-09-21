"""call_llm and the agent harness must respect the budget, and fan-out must be capped."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.budget import Budget, BudgetLimits, request_scope, set_budget
from core.errors import BudgetExceededError, LLMUnavailableError
from core.harness import Tool, run_harness
from core.llm import call_llm


def completion(content="ok", tokens=100, tool_calls=None):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=SimpleNamespace(total_tokens=tokens))


def tool_call(call_id, name, args):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(args)))


@pytest.fixture
def openai(mocker, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    client = MagicMock()
    mocker.patch("core.llm.OpenAI", return_value=client)
    mocker.patch("core.harness.make_client", return_value=client)
    return client


def use_budget(**limits):
    b = Budget(BudgetLimits(**limits), persist_path=None)
    set_budget(b)
    return b


class TestCallLlm:
    def test_refused_before_any_api_call(self, openai):
        use_budget(daily_calls=1).reserve()

        with pytest.raises(BudgetExceededError, match="daily usage limit"):
            call_llm("hi")

        openai.chat.completions.create.assert_not_called()

    def test_budget_error_is_not_retried_and_surfaces_as_llm_unavailable(self, openai):
        b = use_budget(daily_calls=1)
        openai.chat.completions.create.side_effect = Exception("transient")

        with pytest.raises(LLMUnavailableError) as e:  # what every agent already catches
            call_llm("hi")

        assert isinstance(e.value, BudgetExceededError)          # attempt 1 failed; attempt 2 was refused
        assert openai.chat.completions.create.call_count == 1
        assert b.day_calls == 1

    def test_every_attempt_counts_including_retries(self, openai):
        b = use_budget()
        openai.chat.completions.create.side_effect = Exception("down")

        with pytest.raises(LLMUnavailableError):
            call_llm("hi")

        assert b.day_calls == 2

    def test_tokens_are_recorded(self, openai):
        b = use_budget()
        openai.chat.completions.create.return_value = completion(tokens=123)

        call_llm("hi")

        assert (b.day_calls, b.day_tokens) == (1, 123)

    def test_request_scope_bounds_calls(self, openai):
        use_budget()
        openai.chat.completions.create.return_value = completion()

        with request_scope(max_calls=2):
            call_llm("a"); call_llm("b")
            with pytest.raises(BudgetExceededError, match="2 model calls"):
                call_llm("c")

        assert openai.chat.completions.create.call_count == 2


def echo_tool(counter):
    def run(input):
        counter.append(input)
        return {"echo": input}

    return Tool(
        name="echo", description="echo", run=run,
        parameters={"type": "object", "properties": {"input": {"type": "string"}}, "required": ["input"]},
    )


class TestHarnessCaps:
    def test_plain_answer(self, openai):
        openai.chat.completions.create.return_value = completion("done")

        result = run_harness("task", [], "sys")

        assert result.final_message == "done" and result.steps_used == 1 and result.tool_calls == []

    def test_disables_parallel_tool_calls_at_the_api(self, openai):
        openai.chat.completions.create.return_value = completion("done")

        run_harness("task", [echo_tool([])], "sys")

        assert openai.chat.completions.create.call_args.kwargs["parallel_tool_calls"] is False

    def test_calls_beyond_the_cap_in_one_batch_are_not_run(self, openai):
        ran = []
        batch = [tool_call(f"c{i}", "echo", {"input": f"x{i}"}) for i in range(5)]
        openai.chat.completions.create.side_effect = [
            completion(content=None, tool_calls=batch),
            completion("final report"),
        ]
        events = []

        result = run_harness("task", [echo_tool(ran)], "sys", max_tool_calls=2, on_event=lambda e, p: events.append(e))

        assert ran == ["x0", "x1"]                       # only 2 of 5 executed
        assert len(result.tool_calls) == 2
        assert result.final_message == "final report"
        assert "tool_limit_reached" in events
        assert openai.chat.completions.create.call_count == 2   # the forced final answer, no third round
        final_call_messages = openai.chat.completions.create.call_args.kwargs["messages"]
        tool_msgs = [m for m in final_call_messages if m["role"] == "tool"]
        assert len(tool_msgs) == 5                              # every call id still answered
        assert sum("Not executed" in m["content"] for m in tool_msgs) == 3
        assert "tools" not in openai.chat.completions.create.call_args.kwargs

    def test_cap_across_steps_forces_a_final_answer(self, openai):
        ran = []
        openai.chat.completions.create.side_effect = [
            completion(content=None, tool_calls=[tool_call("a", "echo", {"input": "1"})]),
            completion(content=None, tool_calls=[tool_call("b", "echo", {"input": "2"})]),
            completion("wrapped up"),
        ]

        result = run_harness("task", [echo_tool(ran)], "sys", max_steps=10, max_tool_calls=2)

        assert ran == ["1", "2"] and result.final_message == "wrapped up"
        assert openai.chat.completions.create.call_count == 3  # not 10 rounds

    def test_blocked_calls_count_toward_the_cap(self, openai):
        tool = echo_tool([])
        tool.validate = lambda args, task: False
        openai.chat.completions.create.side_effect = [
            completion(content=None, tool_calls=[tool_call(f"c{i}", "echo", {"input": "x"}) for i in range(4)]),
            completion("final"),
        ]

        result = run_harness("task", [tool], "sys", max_tool_calls=3)

        assert len(result.tool_calls) == 3

    def test_budget_exhaustion_propagates_from_the_loop(self, openai):
        use_budget(daily_calls=1)
        openai.chat.completions.create.return_value = completion(
            content=None, tool_calls=[tool_call("a", "echo", {"input": "1"})]
        )

        with pytest.raises(BudgetExceededError):
            run_harness("task", [echo_tool([])], "sys", max_steps=5, max_tool_calls=5)

        assert openai.chat.completions.create.call_count == 1

    def test_every_harness_model_call_is_counted(self, openai):
        b = use_budget()
        openai.chat.completions.create.side_effect = [
            completion(content=None, tool_calls=[tool_call("a", "echo", {"input": "1"})], tokens=10),
            completion("done", tokens=20),
        ]

        run_harness("task", [echo_tool([])], "sys")

        assert (b.day_calls, b.day_tokens) == (2, 30)
