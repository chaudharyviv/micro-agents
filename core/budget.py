"""
Spend controls for every LLM call, enforced in one place.

Limits (all checked before an API call is made, and each attempt counts, including retries):
- per request:  a ceiling on model calls one user action can trigger (bounds agent fan-out);
                orchestrator runs get a higher ceiling since they legitimately chain agents
- per client:   model calls per UTC day for one client id (e.g. IP address)
- hourly:       model calls per UTC hour across everyone (so a burst can't drain the whole day)
- daily:        model calls and tokens per UTC day across everyone (the hard cap)

Per-client identity is best-effort: on a public host a determined caller can vary it, so the global
hourly/daily caps are the actual protection. State is in-memory by default (a process restart resets
it, and it is not shared between processes); set BUDGET_STATE_FILE to persist the global counters.
Also set a hard spending limit in the OpenAI dashboard as the backstop of last resort.

Limits are configurable through environment variables (see BudgetLimits.from_env).
"""

import json
import logging
import os
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterator, Optional

from core.errors import BudgetExceededError

logger = logging.getLogger(__name__)

_MAX_TRACKED_CLIENTS = 10_000


@dataclass(frozen=True)
class BudgetLimits:
    daily_calls: int = 1500
    daily_tokens: int = 2_000_000
    hourly_calls: int = 300
    client_daily_calls: int = 100
    request_calls: int = 12
    orchestrator_request_calls: int = 20

    @classmethod
    def from_env(cls) -> "BudgetLimits":
        """Read overrides from LLM_DAILY_CALLS, LLM_DAILY_TOKENS, LLM_HOURLY_CALLS,
        LLM_CLIENT_DAILY_CALLS, LLM_REQUEST_CALLS and LLM_ORCHESTRATOR_REQUEST_CALLS.
        Invalid values fall back to the default."""
        defaults = cls()
        values = {}
        for field, var in (
            ("daily_calls", "LLM_DAILY_CALLS"),
            ("daily_tokens", "LLM_DAILY_TOKENS"),
            ("hourly_calls", "LLM_HOURLY_CALLS"),
            ("client_daily_calls", "LLM_CLIENT_DAILY_CALLS"),
            ("request_calls", "LLM_REQUEST_CALLS"),
            ("orchestrator_request_calls", "LLM_ORCHESTRATOR_REQUEST_CALLS"),
        ):
            raw = os.getenv(var)
            try:
                values[field] = int(raw) if raw else getattr(defaults, field)
                if values[field] < 1:
                    raise ValueError
            except ValueError:
                logger.warning(f"Ignoring invalid {var}={raw!r}")
                values[field] = getattr(defaults, field)
        return cls(**values)


class _RequestScope:
    """Per-user-action counter, carried in a ContextVar so nested code shares it."""

    def __init__(self, client_id: Optional[str], max_calls: int):
        self.client_id = client_id
        self.max_calls = max_calls
        self.calls = 0


_scope: ContextVar[Optional[_RequestScope]] = ContextVar("llm_request_scope", default=None)


@contextmanager
def request_scope(client_id: Optional[str] = None, max_calls: Optional[int] = None) -> Iterator[_RequestScope]:
    """
    Bound the model calls made inside the `with` block and attribute them to `client_id`.

    Scopes don't stack: if one is already active (say the UI opened one and the orchestrator opens
    another), the outer scope is used unchanged, so inner code can't loosen or reset it.
    """
    existing = _scope.get()
    if existing is not None:
        yield existing
        return
    scope = _RequestScope(client_id, max_calls if max_calls is not None else get_budget().limits.request_calls)
    token = _scope.set(scope)
    try:
        yield scope
    finally:
        _scope.reset(token)


class Budget:
    """Thread-safe usage counters plus the checks that enforce BudgetLimits."""

    def __init__(
        self,
        limits: Optional[BudgetLimits] = None,
        persist_path: Optional[str] = None,
        clock: Callable[[], float] = time.time,
    ):
        self.limits = limits or BudgetLimits.from_env()
        self._persist_path = persist_path
        self._clock = clock
        self._lock = threading.Lock()
        self._day = ""
        self._hour = ""
        self.day_calls = 0
        self.day_tokens = 0
        self.hour_calls = 0
        self._clients: OrderedDict[str, int] = OrderedDict()  # client id -> calls today
        self._load()

    # -- public API ---------------------------------------------------------------------------

    def reserve(self) -> None:
        """Count one model call, or raise BudgetExceededError if any limit is reached."""
        scope = _scope.get()
        with self._lock:
            self._roll()
            lim = self.limits
            if scope is not None and scope.calls >= scope.max_calls:
                raise BudgetExceededError(
                    f"This request reached its limit of {scope.max_calls} model calls and was stopped.",
                    "request",
                )
            client = scope.client_id if scope else None
            if client is not None and self._clients.get(client, 0) >= lim.client_daily_calls:
                raise BudgetExceededError(
                    "You've reached today's usage limit. It resets at 00:00 UTC.", "client"
                )
            if self.hour_calls >= lim.hourly_calls:
                raise BudgetExceededError(
                    "The service is at capacity for this hour. Please try again later.", "hourly"
                )
            if self.day_calls >= lim.daily_calls or self.day_tokens >= lim.daily_tokens:
                raise BudgetExceededError(
                    "The service has reached its daily usage limit. It resets at 00:00 UTC.", "daily"
                )

            self.day_calls += 1
            self.hour_calls += 1
            if scope is not None:
                scope.calls += 1
            if client is not None:
                self._clients[client] = self._clients.get(client, 0) + 1
                self._clients.move_to_end(client)
                while len(self._clients) > _MAX_TRACKED_CLIENTS:
                    self._clients.popitem(last=False)
            self._save()

    def record_usage(self, tokens: int) -> None:
        """Add the tokens a completed call actually used. A call can overshoot the token cap once."""
        if tokens > 0:
            with self._lock:
                self._roll()
                self.day_tokens += tokens
                self._save()

    def snapshot(self) -> dict:
        with self._lock:
            self._roll()
            return {
                "day_calls": self.day_calls,
                "day_tokens": self.day_tokens,
                "hour_calls": self.hour_calls,
                "clients_tracked": len(self._clients),
                "limits": self.limits,
            }

    # -- internals ----------------------------------------------------------------------------

    def _keys(self) -> tuple[str, str]:
        now = datetime.fromtimestamp(self._clock(), timezone.utc)
        return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%dT%H")

    def _roll(self) -> None:
        day, hour = self._keys()
        if day != self._day:
            self._day, self.day_calls, self.day_tokens = day, 0, 0
            self._clients.clear()
        if hour != self._hour:
            self._hour, self.hour_calls = hour, 0

    def _load(self) -> None:
        self._roll()
        if not self._persist_path:
            return
        try:
            with open(self._persist_path, encoding="utf-8") as f:
                saved = json.load(f)
            if saved.get("day") == self._day:
                self.day_calls = int(saved.get("day_calls", 0))
                self.day_tokens = int(saved.get("day_tokens", 0))
            if saved.get("hour") == self._hour:
                self.hour_calls = int(saved.get("hour_calls", 0))
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            logger.warning(f"Could not load budget state from {self._persist_path}: {e}")

    def _save(self) -> None:
        if not self._persist_path:
            return
        state = {
            "day": self._day, "day_calls": self.day_calls, "day_tokens": self.day_tokens,
            "hour": self._hour, "hour_calls": self.hour_calls,
        }
        try:
            tmp = f"{self._persist_path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f)
            os.replace(tmp, self._persist_path)
        except OSError as e:
            logger.warning(f"Could not save budget state: {e}")


_budget: Optional[Budget] = None
_budget_lock = threading.Lock()


def get_budget() -> Budget:
    """The process-wide Budget, created on first use from the environment."""
    global _budget
    with _budget_lock:
        if _budget is None:
            _budget = Budget(persist_path=os.getenv("BUDGET_STATE_FILE") or None)
        return _budget


def set_budget(budget: Optional[Budget]) -> None:
    """Replace (or with None, reset) the process-wide Budget. Mainly for tests."""
    global _budget
    with _budget_lock:
        _budget = budget
