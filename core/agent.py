"""Agent loop for micro-agents."""

from core.llm import call_llm
from typing import Any, Callable, Dict, List, Optional


def dummy_tool() -> str:
    """Dummy tool for testing agent loop."""
    return "dummy_tool executed successfully"


def build_plan_prompt(task: str, history: List[tuple]) -> str:
    """
    Build a ReAct-style prompt for the agent to plan its next action.

    Args:
        task: The original task for the agent to complete
        history: List of (plan, tool_result) tuples from previous steps

    Returns:
        A formatted prompt string for the LLM
    """
    prompt = f"Task: {task}\n\n"

    if history:
        prompt += "Previous observations:\n"
        for i, (plan, result) in enumerate(history, 1):
            prompt += f"Step {i}:\n"
            prompt += f"  Plan: {plan}\n"
            prompt += f"  Result: {result}\n"
        prompt += "\n"

    prompt += (
        "Based on the task and any previous observations, decide your next action:\n"
        "1. Call a tool using format: [TOOL: tool_name(arg1, arg2, ...)]\n"
        "2. Or provide your final response using: [ANSWER: your response here]\n\n"
        "What is your next action?"
    )
    return prompt


def parse_tool_call(response: str) -> Optional[Dict[str, Any]]:
    """
    Parse tool call from LLM response using simple string parsing.

    Looks for patterns like:
      [TOOL: tool_name(...)]
      [TOOL: tool_name(arg1, arg2)]

    Args:
        response: The LLM response string

    Returns:
        Dict with keys 'tool' and 'args' if tool call found, None otherwise
    """
    response = response.strip()

    # Check for tool call pattern: [TOOL: tool_name(...)]
    if "[TOOL:" in response:
        start = response.find("[TOOL:") + 6
        end = response.find("]", start)

        if end == -1:
            return None

        tool_section = response[start:end].strip()

        # Extract tool name and arguments
        paren_start = tool_section.find("(")
        if paren_start == -1:
            # No arguments
            tool_name = tool_section.strip()
            return {"tool": tool_name, "args": {}}

        tool_name = tool_section[:paren_start].strip()
        args_str = tool_section[paren_start + 1 : -1].strip()

        # Simple argument parsing: split by comma (basic approach)
        args = {}
        if args_str:
            # For simplicity, store raw argument string
            args = {"_raw": args_str}

        return {"tool": tool_name, "args": args}

    return None


def summarize_history(history: List[tuple]) -> str:
    """
    Summarize the agent's action history into a readable response.

    Args:
        history: List of (plan, tool_result) tuples

    Returns:
        A summary string of findings
    """
    if not history:
        return "No actions were taken."

    summary = "Summary of findings:\n"
    for i, (plan, result) in enumerate(history, 1):
        summary += f"{i}. {result}\n"

    return summary


def run_agent(
    task: str, tools: Dict[str, Callable], max_steps: int = 4, verbose: bool = False
) -> str:
    """
    Run a minimal ReAct-style agent loop.

    The agent:
    1. Plans its next action using the LLM
    2. Parses the plan for tool calls or final answers
    3. Executes the requested tool
    4. Incorporates results into history
    5. Repeats up to max_steps times

    Args:
        task: The task for the agent to complete
        tools: Dict mapping tool names to callable functions
        max_steps: Maximum number of loop iterations (default: 4)
        verbose: If True, print step-by-step execution (default: False)

    Returns:
        The final answer as a string
    """
    history = []
    system_prompt = (
        "You are a helpful agent. You have tools available to help you complete tasks. "
        "When you want to use a tool, format your response as [TOOL: tool_name()] or [TOOL: tool_name(args)]. "
        "When you have your final answer, format it as [ANSWER: your answer]. "
        "Be concise and direct."
    )

    for step in range(max_steps):
        if verbose:
            print(f"\n[Step {step + 1}/{max_steps}]")

        # Build prompt with task and history
        prompt = build_plan_prompt(task, history)

        if verbose:
            print(f"Prompt: {prompt}")

        # Get LLM response
        try:
            response = call_llm(prompt, system=system_prompt)
        except Exception as e:
            if verbose:
                print(f"LLM call failed: {e}")
            # Return best-effort summary on LLM failure
            return summarize_history(history) if history else "Task could not be completed."

        if verbose:
            print(f"LLM response: {response}")

        # Check for final answer
        if "[ANSWER:" in response:
            answer_start = response.find("[ANSWER:") + 8
            answer_end = response.find("]", answer_start)
            if answer_end != -1:
                final_answer = response[answer_start:answer_end].strip()
                if verbose:
                    print(f"Final answer: {final_answer}")
                return final_answer

        # Parse tool call
        tool_call = parse_tool_call(response)

        if tool_call:
            tool_name = tool_call["tool"]
            tool_args = tool_call["args"]

            if verbose:
                print(f"Tool call: {tool_name} with args: {tool_args}")

            # Check if tool exists
            if tool_name not in tools:
                if verbose:
                    print(f"Tool '{tool_name}' not found. Available: {list(tools.keys())}")
                history.append((response, f"Error: Tool '{tool_name}' not found."))
                continue

            # Execute tool
            try:
                tool_func = tools[tool_name]
                if tool_args and "_raw" in tool_args:
                    # For simple arg parsing, just call with no args for now
                    # In production, would need more sophisticated parsing
                    result = tool_func()
                else:
                    result = tool_func()

                if verbose:
                    print(f"Tool result: {result}")

                history.append((response, str(result)))
            except Exception as e:
                error_msg = f"Error executing '{tool_name}': {str(e)}"
                if verbose:
                    print(error_msg)
                history.append((response, error_msg))
        else:
            # No valid action found, add to history and continue
            if verbose:
                print("No valid action found in response")
            history.append((response, "No valid action parsed"))

    # Max steps reached
    if verbose:
        print(f"\nReached max_steps ({max_steps})")

    return summarize_history(history)


if __name__ == "__main__":
    # Smoke test: run agent with dummy tool
    print("=== Agent Smoke Test ===\n")

    tools = {"dummy": dummy_tool}

    task = "Test the agent loop"
    print(f"Task: {task}")

    try:
        result = run_agent(task, tools, max_steps=2, verbose=True)
        print(f"\nFinal result:\n{result}")
    except Exception as e:
        print(f"Error: {e}")
