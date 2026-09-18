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
from openai import OpenAI

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_MAX_STEPS = 4


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
    model: str = DEFAULT_MODEL,
    on_event: Optional[Callable[[str, dict], None]] = None,
) -> HarnessResult:
    """
    Run a tool-calling agent loop.

    Each step, the model sees the conversation so far (including prior tool results) and either
    calls one or more tools or returns a final answer. The harness executes each requested call,
    isolates failures per-call so one bad tool doesn't abort the run, and feeds results back for
    the next step. Stops when the model returns a plain answer (no tool calls) or `max_steps` is
    reached, in which case the model is asked for a best-effort final answer using only what it
    already gathered.

    `on_event`, if given, is called synchronously as the loop progresses - e.g. to drive a live
    UI (a Streamlit st.status block). Events: "tool_call_start" {tool, args}, "tool_call_end"
    {tool, args, output}, "step_limit_reached" {}, "final" {message}.
    """
    def emit(event: str, payload: dict) -> None:
        if on_event is not None:
            on_event(event, payload)
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OpenAI backend unavailable. Ensure OPENAI_API_KEY is set in environment.")

    client = OpenAI(api_key=api_key)
    tool_map = {t.name: t for t in tools}
    tool_schemas = [t.to_openai_schema() for t in tools]

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task},
    ]
    call_log: list = []

    for step in range(1, max_steps + 1):
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tool_schemas,
            tool_choice="auto",
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
                    "content": json.dumps(output, default=str)[:4000],
                }
            )

    emit("step_limit_reached", {})
    messages.append(
        {
            "role": "user",
            "content": "Step limit reached. Give your best final answer now, using only the tool "
            "results already gathered above - do not call any more tools.",
        }
    )
    response = client.chat.completions.create(model=model, messages=messages, temperature=0.3)
    final_message = response.choices[0].message.content or ""
    emit("final", {"message": final_message})
    return HarnessResult(final_message=final_message, tool_calls=call_log, steps_used=max_steps)
