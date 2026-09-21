"""LLM interface for micro-agents."""

import os
from dotenv import load_dotenv
from openai import OpenAI, APIError as OpenAIAPIError

from core.budget import get_budget
from core.errors import BudgetExceededError, LLMOutputError, LLMUnavailableError  # noqa: F401 (re-exported)


MODEL_MAP = {
    "default": "gpt-4o-mini",
    "large": "gpt-4o-mini",
}

REQUEST_TIMEOUT_SECONDS = 60


def make_client(api_key: str) -> OpenAI:
    """
    An OpenAI client with a request timeout and the SDK's own retries turned off.

    The SDK retries failed requests by default, silently and outside our accounting. Retrying is
    done explicitly by callers, one budgeted attempt at a time.
    """
    return OpenAI(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=0)


def _total_tokens(response) -> int:
    tokens = getattr(getattr(response, "usage", None), "total_tokens", 0)
    return tokens if isinstance(tokens, int) and not isinstance(tokens, bool) else 0


def guarded_completion(client, **kwargs):
    """
    The single way this project calls the chat completions API.

    Reserves budget first (raising BudgetExceededError, with no API call made, if any limit is
    reached), then records the tokens actually used. Every attempt, including retries, is counted.
    """
    budget = get_budget()
    budget.reserve()
    response = client.chat.completions.create(**kwargs)
    budget.record_usage(_total_tokens(response))
    return response


def call_llm(
    prompt: str,
    system: str = None,
    model: str = "default",
    schema: dict = None,
    schema_name: str = "response",
    temperature: float = None,
) -> str:
    """
    Call an LLM with the given prompt.

    Uses the OpenAI API (gpt-4o-mini), retrying once on failure.

    Args:
        prompt: The user prompt to send to the LLM
        system: Optional system message to set the LLM's behavior
        model: Model key from MODEL_MAP (default: "default")
        schema: Optional JSON schema. When given, the API is asked for structured output that
                conforms to it (strict mode: see core.llm_utils.obj/arr for building compatible
                schemas), and the returned string is that JSON.
        schema_name: Name for the schema (letters, digits, underscores)
        temperature: Sampling temperature. Defaults to 0.2 with a schema (structured output should
                be steady) and 0.7 for free text.

    Returns:
        The LLM's response as a string

    Raises:
        BudgetExceededError: If a usage limit is reached (never retried; no API call is made)
        LLMOutputError: If the model refused, or its output was cut off (never retried)
        LLMUnavailableError: If the OpenAI backend fails on both attempts
    """
    load_dotenv()
    model_name = MODEL_MAP.get(model, model)
    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        raise LLMUnavailableError(
            "OpenAI backend unavailable. Ensure OPENAI_API_KEY is set in environment."
        )

    client = make_client(api_key)
    messages = [
        {"role": "system", "content": system or "You are a helpful assistant."},
        {"role": "user", "content": prompt},
    ]
    kwargs = {
        "model": model_name,
        "messages": messages,
        "temperature": temperature if temperature is not None else (0.2 if schema else 0.7),
    }
    if schema:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": schema},
        }

    last_error = None
    for attempt in range(2):
        try:
            response = guarded_completion(client, **kwargs)
            print(f"[LLM] OpenAI backend served request (model: {model_name}, attempt: {attempt + 1})")
            choice = response.choices[0]
            if schema:
                _check_structured_choice(choice)
            return choice.message.content
        except (BudgetExceededError, LLMOutputError):
            raise  # not transient: don't retry, and let the caller see why
        except (OpenAIAPIError, Exception) as e:
            last_error = e
            print(f"[LLM] OpenAI attempt {attempt + 1} failed: {e}")

    # Full exception detail is logged above for debugging; the raised message stays generic
    # since callers surface it directly to end users.
    raise LLMUnavailableError("OpenAI backend unavailable after retrying. Please try again shortly.")


def _check_structured_choice(choice) -> None:
    """A structured response can be a refusal or cut off mid-JSON; neither is usable or worth retrying."""
    if getattr(choice.message, "refusal", None):
        raise LLMOutputError("The model declined to produce this result. Try rephrasing or a different target.")
    if getattr(choice, "finish_reason", None) == "length" or not choice.message.content:
        raise LLMOutputError("The model's response was cut off before it finished. Please try again.")


if __name__ == "__main__":
    try:
        result = call_llm("Say hello in one word.")
        print(f"\nSmoke test result: {result}")
    except LLMUnavailableError as e:
        print(f"Smoke test failed: {e}")
