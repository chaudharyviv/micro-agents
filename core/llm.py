"""LLM interface for micro-agents."""

import os
from dotenv import load_dotenv
from openai import OpenAI, APIError as OpenAIAPIError


MODEL_MAP = {
    "default": "gpt-4o-mini",
    "large": "gpt-4o-mini",
}


class LLMUnavailableError(Exception):
    """Raised when the OpenAI backend is unavailable after retrying."""

    pass


def call_llm(prompt: str, system: str = None, model: str = "default") -> str:
    """
    Call an LLM with the given prompt.

    Uses the OpenAI API (gpt-4o-mini), retrying once on failure.

    Args:
        prompt: The user prompt to send to the LLM
        system: Optional system message to set the LLM's behavior
        model: Model key from MODEL_MAP (default: "default")

    Returns:
        The LLM's response as a string

    Raises:
        LLMUnavailableError: If the OpenAI backend fails on both attempts
    """
    load_dotenv()
    model_name = MODEL_MAP.get(model, model)
    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        raise LLMUnavailableError(
            "OpenAI backend unavailable. Ensure OPENAI_API_KEY is set in environment."
        )

    client = OpenAI(api_key=api_key)
    messages = [
        {"role": "system", "content": system or "You are a helpful assistant."},
        {"role": "user", "content": prompt},
    ]

    last_error = None
    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=messages,
                temperature=0.7,
            )
            print(f"[LLM] OpenAI backend served request (model: {model_name}, attempt: {attempt + 1})")
            return response.choices[0].message.content
        except (OpenAIAPIError, Exception) as e:
            last_error = e
            print(f"[LLM] OpenAI attempt {attempt + 1} failed: {e}")

    # Full exception detail is logged above for debugging; the raised message stays generic
    # since callers surface it directly to end users.
    raise LLMUnavailableError("OpenAI backend unavailable after retrying. Please try again shortly.")


if __name__ == "__main__":
    try:
        result = call_llm("Say hello in one word.")
        print(f"\nSmoke test result: {result}")
    except LLMUnavailableError as e:
        print(f"Smoke test failed: {e}")
