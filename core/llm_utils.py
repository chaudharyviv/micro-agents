"""Small helpers for working with LLM prompts and responses."""

import json
import re
import unicodedata
from typing import Any

_FENCE_RE = re.compile(r"^```[A-Za-z0-9_-]*[ \t]*\n(.*?)\n?```[ \t]*$", re.DOTALL)
_UNTRUSTED_TAG_RE = re.compile(r"<\s*/?\s*untrusted_data\b[^>]*>", re.IGNORECASE)


def neutralize_untrusted(text: str) -> str:
    """
    Remove any <untrusted_data> / </untrusted_data> tags from third-party text, so it can't close
    the block it is placed in and pose as instructions outside it.

    Invisible format characters (zero-width spaces and joiners, bidi controls) are dropped and the
    text is NFKC-normalized first, so a tag can't be disguised by splitting it with a zero-width
    character or writing it with full-width angle brackets. The result is for prompts only.
    """
    text = "".join(ch for ch in str(text) if unicodedata.category(ch) != "Cf")
    text = unicodedata.normalize("NFKC", text)
    return _UNTRUSTED_TAG_RE.sub("", text)


def wrap_untrusted(text: str) -> str:
    """
    Wrap third-party text as a delimited data block for a prompt.

    This is the only place the delimiter tags are written: prompt templates take an already-wrapped
    value, so no call site can forget to neutralize what goes inside.
    """
    return f"<untrusted_data>\n{neutralize_untrusted(text)}\n</untrusted_data>"


# --- Strict JSON schemas for structured outputs ---------------------------------------------------
#
# OpenAI's strict structured outputs require every object to list all its properties as required and
# forbid extra ones. These helpers build schemas that satisfy that, so they're written once.

STR = {"type": "string"}
INT = {"type": "integer"}
NULLABLE_STR = {"type": ["string", "null"]}


def obj(**properties: dict) -> dict:
    """A strict object schema: all given properties required, no others allowed."""
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def arr(items: dict) -> dict:
    return {"type": "array", "items": items}


def enum(*values: str) -> dict:
    return {"type": "string", "enum": list(values)}


def extract_json(response: str) -> Any:
    """
    Parse JSON from an LLM response.

    Accepts bare JSON, JSON inside a ```json fence (with or without surrounding prose), or JSON
    embedded in prose (the outermost object or array is used).

    Raises:
        ValueError: if no valid JSON can be found.
    """
    text = response.strip()

    fence = _FENCE_RE.match(text)
    if fence:
        text = fence.group(1).strip()
    else:
        embedded = re.search(r"```[A-Za-z0-9_-]*[ \t]*\n(.*?)\n?```", text, re.DOTALL)
        if embedded:
            text = embedded.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if starts:
        start = min(starts)
        closer = "}" if text[start] == "{" else "]"
        end = text.rfind(closer)
        if end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
    raise ValueError("No valid JSON found in response")
