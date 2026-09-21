"""
Minimal tool-calling agent harness.

"Agent = Model + Harness": the model supplies judgment, the harness supplies
everything else - a tool registry, an execution loop, and guardrails (step
limits, per-tool error isolation, optional per-call validation). The model
decides which tools to call and when it has enough to answer; this module
never hardcodes that sequence.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Callable, Optional

from dotenv import load_dotenv

from core.llm import guarded_completion, make_client
from core.llm_utils import wrap_untrusted

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_MAX_STEPS = 4
DEFAULT_MAX_TOOL_CALLS = 4
MAX_TOOL_RESULT_CHARS = 4000


@dataclass
class Tool:
    """A single capability the model can invoke, described as an OpenAI function schema."""

    name: str
    description: str
    parameters: dict  # JSON schema for the tool's arguments
    run: Callable[..., object]
    validate: Optional[Callable[[dict, str], bool]] = None
    # validate(args, task) -> False blocks execution without spending a tool call on the model's
    # behalf; used for guardrails like "the extracted input must actually appear in the task text".

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _tool_message_content(output) -> str:
    """
    A tool's output as the model sees it: wrapped as untrusted data (tool results carry text fetched
    from repos, issues and the web), and cut at MAX_TOOL_RESULT_CHARS with a visible marker so the
    model knows the JSON was truncated rather than reading a silently broken document.
    """
    text = json.dumps(output, default=str)
    if len(text) > MAX_TOOL_RESULT_CHARS:
        text = text[:MAX_TOOL_RESULT_CHARS] + f"\n... [truncated: {len(text) - MAX_TOOL_RESULT_CHARS} more characters]"
    return wrap_untrusted(text)


@dataclass
class HarnessResult:
    final_message: str
    tool_calls: list = field(default_factory=list)  # [{"tool", "args", "output"}]
    steps_used: int = 0


def run_harness(
    task: str,
    tools: list[Tool],
    system_prompt: str,
    max_steps: int = DEFAULT_MAX_STEPS,
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
    model: str = DEFAULT_MODEL,
    on_event: Optional[Callable[[str, dict], None]] = None,
) -> HarnessResult:
    """
    Run a tool-calling agent loop.

    Each step, the model sees the conversation so far (including prior tool results) and either
    calls one or more tools or returns a final answer. The harness executes each requested call,
    isolates failures per-call so one bad tool doesn't abort the run, and feeds results back for
    the next step. Stops when the model returns a plain answer (no tool calls), or when `max_steps`
    or `max_tool_calls` is reached, in which case the model is asked for a best-effort final answer
    using only what it already gathered.

    `max_tool_calls` caps the total number of tool calls attempted across the whole run, however
    many the model requests per step: calls past the cap are not executed (the model is told so).
    Every model call goes through core.llm.guarded_completion, so spend limits apply, and a
    BudgetExceededError propagates to the caller.

    `on_event`, if given, is called synchronously as the loop progresses - e.g. to drive a live
    UI (a Streamlit st.status block). Events: "tool_call_start" {tool, args}, "tool_call_end"
    {tool, args, output}, "step_limit_reached" {}, "tool_limit_reached" {}, "final" {message}.
    """
    def emit(event: str, payload: dict) -> None:
        if on_event is not None:
            on_event(event, payload)
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OpenAI backend unavailable. Ensure OPENAI_API_KEY is set in environment.")

    client = make_client(api_key)
    tool_map = {t.name: t for t in tools}
    tool_schemas = [t.to_openai_schema() for t in tools]

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task},
    ]
    call_log: list = []

    tool_limit_hit = False
    steps_taken = 0
    for step in range(1, max_steps + 1):
        if len(call_log) >= max_tool_calls:
            tool_limit_hit = True
            break
        steps_taken = step
        response = guarded_completion(
            client,
            model=model,
            messages=messages,
            tools=tool_schemas,
            tool_choice="auto",
            parallel_tool_calls=False,
            temperature=0.3,
        )
        msg = response.choices[0].message

        if not msg.tool_calls:
            emit("final", {"message": msg.content or ""})
            return HarnessResult(final_message=msg.content or "", tool_calls=call_log, steps_used=step)

        messages.append(
            {
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in msg.tool_calls
                ],
            }
        )

        for tc in msg.tool_calls:
            name = tc.function.name

            if len(call_log) >= max_tool_calls:
                # Still answer the call so the message history stays valid, but don't run it.
                logger.warning(f"Skipped call to '{name}': tool call limit ({max_tool_calls}) reached")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps({"error": f"Not executed: the limit of {max_tool_calls} tool calls was reached."}),
                    }
                )
                continue

            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            emit("tool_call_start", {"tool": name, "args": args})

            tool = tool_map.get(name)
            if tool is None:
                output = {"error": f"Unknown tool '{name}'"}
            elif tool.validate is not None and not tool.validate(args, task):
                logger.warning(f"Blocked call to '{name}': failed validation for args {args}")
                output = {"error": f"Call to '{name}' blocked: input not grounded in the task."}
            else:
                try:
                    output = tool.run(**args)
                except Exception as e:
                    logger.error(f"Tool '{name}' failed: {e}")
                    output = {"error": f"'{name}' failed due to an internal error."}

            call_log.append({"tool": name, "args": args, "output": output})
            emit("tool_call_end", {"tool": name, "args": args, "output": output})
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": _tool_message_content(output),
                }
            )

    emit("tool_limit_reached" if tool_limit_hit or len(call_log) >= max_tool_calls else "step_limit_reached", {})
    messages.append(
        {
            "role": "user",
            "content": "Tool or step limit reached. Give your best final answer now, using only the tool "
            "results already gathered above - do not call any more tools.",
        }
    )
    response = guarded_completion(client, model=model, messages=messages, temperature=0.3)
    final_message = response.choices[0].message.content or ""
    emit("final", {"message": final_message})
    return HarnessResult(final_message=final_message, tool_calls=call_log, steps_used=steps_taken)
