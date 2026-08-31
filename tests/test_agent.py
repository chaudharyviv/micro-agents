"""Tests for core/agent.py agent loop."""

import json
import pytest
from unittest.mock import MagicMock

from core.agent import Tool, run_agent, _parse_action


class TestAgentLoop:
    """Tests for the agent loop."""

    def test_agent_tool_call_and_final_answer(self, mocker):
        """Test agent calls a tool, then returns a final answer."""
        # Mock call_llm to return a tool call, then a final answer
        responses = [
            json.dumps(
                {"action": "use_tool", "tool_name": "get_time", "tool_args": {}}
            ),
            json.dumps(
                {
                    "action": "answer",
                    "result": "The current time is 14:30 UTC",
                }
            ),
        ]

        mock_llm = mocker.patch("core.agent.call_llm", side_effect=responses)

        # Create a simple tool
        def get_time():
            return "14:30 UTC"

        tool = Tool(name="get_time", description="Returns current time", func=get_time)

        result = run_agent("What is the current time?", tools=[tool])

        assert result == "The current time is 14:30 UTC"
        assert mock_llm.call_count == 2

    def test_agent_max_steps_reached(self, mocker):
        """Test agent returns best-effort response when max_steps is reached."""
        # Mock call_llm to always return a tool call (never final answer)
        response = json.dumps(
            {"action": "use_tool", "tool_name": "search", "tool_args": {"q": "test"}}
        )
        mock_llm = mocker.patch("core.agent.call_llm", return_value=response)

        def search(q):
            return f"Results for: {q}"

        tool = Tool(
            name="search", description="Search the web", func=search
        )

        result = run_agent("Find information about X", tools=[tool], max_steps=2)

        # Should have called LLM max_steps times
        assert mock_llm.call_count == 2
        # Should return a non-empty best-effort response
        assert len(result) > 0
        assert "Results for" in result

    def test_agent_malformed_json_retry(self, mocker):
        """Test agent retries once on malformed JSON, then continues."""
        # First response is malformed JSON, second is valid tool call, third is final answer
        responses = [
            "{invalid json}",  # Malformed
            json.dumps(
                {"action": "use_tool", "tool_name": "add", "tool_args": {"a": 1, "b": 2}}
            ),
            json.dumps(
                {"action": "answer", "result": "1 + 2 = 3"}
            ),
        ]

        mock_llm = mocker.patch("core.agent.call_llm", side_effect=responses)

        def add(a, b):
            return a + b

        tool = Tool(name="add", description="Add two numbers", func=add)

        result = run_agent("What is 1 + 2?", tools=[tool])

        assert result == "1 + 2 = 3"
        # Should be called 3 times: initial, retry, and final answer
        assert mock_llm.call_count == 3

    def test_agent_tool_execution_failure(self, mocker):
        """Test agent handles tool execution errors gracefully."""
        responses = [
            json.dumps(
                {"action": "use_tool", "tool_name": "divide", "tool_args": {"a": 10, "b": 0}}
            ),
            json.dumps(
                {"action": "answer", "result": "Cannot divide by zero"}
            ),
        ]

        mock_llm = mocker.patch("core.agent.call_llm", side_effect=responses)

        def divide(a, b):
            if b == 0:
                raise ValueError("Cannot divide by zero")
            return a / b

        tool = Tool(name="divide", description="Divide two numbers", func=divide)

        result = run_agent("Divide 10 by 0", tools=[tool])

        # Should still return the final answer even though tool failed
        assert result == "Cannot divide by zero"
        assert mock_llm.call_count == 2

    def test_agent_unknown_tool(self, mocker):
        """Test agent handles calls to non-existent tools."""
        responses = [
            json.dumps(
                {
                    "action": "use_tool",
                    "tool_name": "nonexistent",
                    "tool_args": {},
                }
            ),
            json.dumps(
                {"action": "answer", "result": "Tool not found"}
            ),
        ]

        mock_llm = mocker.patch("core.agent.call_llm", side_effect=responses)

        def dummy():
            return "dummy"

        tool = Tool(name="dummy", description="A dummy tool", func=dummy)

        result = run_agent("Call a tool that doesn't exist", tools=[tool])

        assert "Tool not found" in result or "not found" in result.lower()

    def test_agent_multiple_tool_calls(self, mocker):
        """Test agent can call multiple tools in sequence."""
        responses = [
            json.dumps(
                {"action": "use_tool", "tool_name": "get_name", "tool_args": {}}
            ),
            json.dumps(
                {"action": "use_tool", "tool_name": "get_age", "tool_args": {}}
            ),
            json.dumps(
                {"action": "answer", "result": "Name: Alice, Age: 30"}
            ),
        ]

        mock_llm = mocker.patch("core.agent.call_llm", side_effect=responses)

        def get_name():
            return "Alice"

        def get_age():
            return 30

        tools = [
            Tool(name="get_name", description="Get name", func=get_name),
            Tool(name="get_age", description="Get age", func=get_age),
        ]

        result = run_agent("Tell me about Alice", tools=tools)

        assert "Name: Alice" in result
        assert "Age: 30" in result
        assert mock_llm.call_count == 3


class TestActionParsing:
    """Tests for action parsing."""

    def test_parse_valid_tool_call(self):
        """Test parsing a valid tool_call action."""
        response = json.dumps(
            {"action": "use_tool", "tool_name": "search", "tool_args": {"q": "python"}}
        )
        action, error = _parse_action(response)

        assert error is None
        assert action["action"] == "tool_call"
        assert action["tool"] == "search"
        assert action["args"] == {"q": "python"}

    def test_parse_valid_final_answer(self):
        """Test parsing a valid final_answer action."""
        response = json.dumps(
            {"action": "answer", "result": "The answer is 42"}
        )
        action, error = _parse_action(response)

        assert error is None
        assert action["action"] == "final_answer"
        assert action["output"] == "The answer is 42"

    def test_parse_invalid_json(self):
        """Test parsing invalid JSON."""
        response = "{not valid json"
        action, error = _parse_action(response)

        assert action is None
        assert error is not None
        assert "Invalid JSON" in error

    def test_parse_missing_tool_name(self):
        """Test parsing tool_call without tool_name."""
        response = json.dumps(
            {"action": "use_tool", "tool_args": {}}
        )
        action, error = _parse_action(response)

        assert action is None
        assert error is not None
        assert "tool_name" in error

    def test_parse_missing_result(self):
        """Test parsing answer without result."""
        response = json.dumps(
            {"action": "answer"}
        )
        action, error = _parse_action(response)

        assert action is None
        assert error is not None
        assert "result" in error

    def test_parse_unknown_action(self):
        """Test parsing unknown action type."""
        response = json.dumps(
            {"action": "unknown"}
        )
        action, error = _parse_action(response)

        assert action is None
        assert error is not None
        assert "Unknown action" in error


class TestToolDataclass:
    """Tests for Tool dataclass."""

    def test_tool_creation(self):
        """Test creating a Tool."""
        def my_func(x):
            return x * 2

        tool = Tool(
            name="double",
            description="Double a number",
            func=my_func,
        )

        assert tool.name == "double"
        assert tool.description == "Double a number"
        assert tool.func(5) == 10

    def test_tool_with_lambda(self):
        """Test Tool with lambda function."""
        tool = Tool(
            name="add_one",
            description="Add one to a number",
            func=lambda x: x + 1,
        )

        assert tool.func(5) == 6
