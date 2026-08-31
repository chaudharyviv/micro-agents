"""LLM interface for micro-agents."""

import os
from dotenv import load_dotenv
from groq import Groq as GroqClient
from groq import APIError as GroqAPIError
try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


MODEL_MAP = {
    "default": "openai/gpt-oss-20b",
    "large": "openai/gpt-oss-120b",
}


class LLMUnavailableError(Exception):
    """Raised when both primary and fallback LLM backends are unavailable."""

    pass


def call_llm(prompt: str, system: str = None, model: str = "default") -> str:
    """
    Call an LLM with the given prompt.

    Attempts Groq API first, then falls back to Hugging Face Inference API.

    Args:
        prompt: The user prompt to send to the LLM
        system: Optional system message to set the LLM's behavior
        model: Model key from MODEL_MAP (default: "default")

    Returns:
        The LLM's response as a string

    Raises:
        LLMUnavailableError: If both Groq and HuggingFace backends fail
    """
    load_dotenv()
    model_name = MODEL_MAP.get(model, model)
    groq_key = os.getenv("GROQ_API_KEY")
    hf_key = os.getenv("HF_TOKEN")

    # Try Groq first
    if groq_key:
        try:
            client = GroqClient(api_key=groq_key)
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": system or "You are a helpful assistant."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
            )
            print(f"[LLM] Groq backend served request (model: {model_name})")
            return response.choices[0].message.content
        except (GroqAPIError, Exception) as e:
            print(f"[LLM] Groq failed: {e}, attempting HuggingFace fallback...")

    # Try HuggingFace Inference API via OpenAI-compatible endpoint
    if hf_key and OpenAI:
        try:
            client = OpenAI(
                api_key=hf_key,
                base_url="https://router.huggingface.co/v1",
            )
            # Try multiple models for compatibility
            hf_models = [
                "meta-llama/Llama-2-7b-chat-hf",
                "mistralai/Mistral-7B-Instruct-v0.2",
                "NousResearch/Nous-Hermes-2-Mixtral-8x7B-DPO",
            ]

            for model_id in hf_models:
                try:
                    response = client.chat.completions.create(
                        model=model_id,
                        messages=[
                            {"role": "system", "content": system or "You are a helpful assistant."},
                            {"role": "user", "content": prompt},
                        ],
                        max_tokens=512,
                    )
                    print(f"[LLM] HuggingFace backend served request (model: {model_id})")
                    return response.choices[0].message.content
                except Exception:
                    continue

            # All models failed
            raise Exception("No HuggingFace models available")
        except Exception as e:
            print(f"[LLM] HuggingFace failed: {e}")

    raise LLMUnavailableError(
        "Both Groq and HuggingFace backends unavailable. "
        "Ensure GROQ_API_KEY or HF_TOKEN is set in environment."
    )


if __name__ == "__main__":
    try:
        result = call_llm("Say hello in one word.")
        print(f"\nSmoke test result: {result}")
    except LLMUnavailableError as e:
        print(f"Smoke test failed: {e}")
