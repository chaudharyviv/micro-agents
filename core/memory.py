"""
Agent memory: in-process only, bounded, and safe on shared/ephemeral hosts (e.g. Streamlit Cloud).

Two tiers, deliberately kept separate:
- SessionMemory: one visitor's own run history (short-term memory). Never shared between users.
- ResultCache: a shared, TTL'd, size-capped cache of results derived purely from *public* data
  (a GitHub repo/issue URL), so repeat runs are instant and don't burn GitHub/OpenAI quota.

Nothing touches the filesystem: the host's disk is shared by all visitors and wiped on restart.
"""

import json
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Optional

# Only agents whose output depends solely on a public URL are safe to share across visitors.
CACHEABLE_AGENTS = {"repo_onboarding", "cve_impact", "security_audit", "issue_fix_planner"}


def normalize_input(text: str) -> str:
    """Canonical form for cache/recall matching: trimmed, lower-cased, no trailing slash or .git."""
    text = (text or "").strip().lower().rstrip("/")
    return text[:-4] if text.endswith(".git") else text


def is_error(output: Any) -> bool:
    """True if a specialist's output is an error result (these are never cached)."""
    return isinstance(output, dict) and (output.get("status") == "error" or "error_message" in output)


def summarize(output: Any, max_chars: int = 400) -> str:
    """Compact text form of an output, for recall and display."""
    text = output if isinstance(output, str) else json.dumps(output, default=str)
    return text if len(text) <= max_chars else text[: max_chars - 3] + "..."


class SessionMemory:
    """Short-term memory: the most recent agent runs for a single user session."""

    def __init__(self, max_entries: int = 20):
        self.max_entries = max_entries
        self._entries: list[dict] = []

    def record(self, agent: str, input: str, output: Any, cached: bool = False) -> None:
        self._entries.append(
            {
                "agent": agent,
                "input": (input or "").strip(),
                "summary": summarize(output),
                "cached": cached,
                "timestamp": time.time(),
            }
        )
        del self._entries[: -self.max_entries]

    def recent(self, limit: int = 10) -> list[dict]:
        """Most recent runs first, each with its age in seconds."""
        now = time.time()
        return [{**e, "age_seconds": int(now - e["timestamp"])} for e in reversed(self._entries[-limit:])]

    def has_input(self, input: str) -> bool:
        """True if this exact (normalized) input was already run earlier in this session."""
        target = normalize_input(input)
        return any(normalize_input(e["input"]) == target for e in self._entries)

    def inputs(self) -> list[str]:
        """Every input recorded this session. Each was either typed by the user or passed validation."""
        return [e["input"] for e in self._entries]

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


class ResultCache:
    """Shared LRU cache with a TTL; thread-safe since Streamlit serves sessions on separate threads."""

    def __init__(self, ttl_seconds: int = 3600, max_entries: int = 100):
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.hits = 0
        self.misses = 0
        self._data: OrderedDict[tuple[str, str], tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, agent: str, input: str) -> Optional[Any]:
        key = (agent, normalize_input(input))
        with self._lock:
            entry = self._data.get(key)
            if entry is None or time.time() - entry[0] > self.ttl_seconds:
                self._data.pop(key, None)
                self.misses += 1
                return None
            self._data.move_to_end(key)
            self.hits += 1
            return entry[1]

    def put(self, agent: str, input: str, output: Any) -> None:
        key = (agent, normalize_input(input))
        with self._lock:
            self._data[key] = (time.time(), output)
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)


def run_with_memory(
    agent: str,
    input: str,
    fn: Callable[[str], Any],
    cache: Optional[ResultCache] = None,
    session: Optional[SessionMemory] = None,
    before_run: Optional[Callable[[], bool]] = None,
) -> tuple[Any, bool]:
    """
    Run `fn(input)` for an agent, consulting/updating memory around it.

    On a cache hit the agent isn't run at all. Otherwise `before_run` (e.g. a rate-limit check) is
    called first; if it returns False nothing runs and (None, False) is returned. Successful
    results from cacheable agents are cached; every run is recorded to session memory.

    Returns (output, cache_hit).
    """
    cacheable = cache is not None and agent in CACHEABLE_AGENTS

    if cacheable:
        cached = cache.get(agent, input)
        if cached is not None:
            if session is not None:
                session.record(agent, input, cached, cached=True)
            return cached, True

    if before_run is not None and not before_run():
        return None, False

    output = fn(input)

    if cacheable and not is_error(output):
        cache.put(agent, input, output)
    if session is not None:
        session.record(agent, input, output)
    return output, False
