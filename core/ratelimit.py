"""Process-wide sliding-window rate limiter, keyed by an arbitrary client id."""

import threading
import time
from collections import OrderedDict, deque
from typing import Callable


class SlidingWindowLimiter:
    """
    Allow at most `limit` events per `window_seconds` for each key.

    Thread-safe (Streamlit runs sessions on separate threads) and memory-bounded: at most `max_keys`
    keys are tracked, evicting the least recently seen. Unlike state kept in a browser session, it
    isn't reset by reloading the page.
    """

    def __init__(
        self,
        limit: int,
        window_seconds: float,
        max_keys: int = 10_000,
        clock: Callable[[], float] = time.time,
    ):
        self.limit = limit
        self.window = window_seconds
        self.max_keys = max_keys
        self._clock = clock
        self._events: OrderedDict[str, deque] = OrderedDict()
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque:
        events = self._events.get(key)
        if events is None:
            events = self._events[key] = deque()
        while events and events[0] <= now - self.window:
            events.popleft()
        return events

    def allow(self, key: str) -> bool:
        """Record an event for `key` and return True, or return False (recording nothing) if over the limit."""
        now = self._clock()
        with self._lock:
            events = self._prune(key, now)
            self._events.move_to_end(key)
            allowed = len(events) < self.limit
            if allowed:
                events.append(now)
            while len(self._events) > self.max_keys:
                self._events.popitem(last=False)
            return allowed

    def retry_after(self, key: str) -> int:
        """Seconds until `key` may next be allowed (0 if it already can be)."""
        now = self._clock()
        with self._lock:
            events = self._prune(key, now)
            if len(events) < self.limit:
                return 0
            return max(int(events[0] + self.window - now) + 1, 1)
