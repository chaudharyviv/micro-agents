"""Tests for core/budget.py and core/ratelimit.py."""

import json
from datetime import datetime, timezone

import pytest

from core.budget import Budget, BudgetLimits, get_budget, request_scope, set_budget
from core.errors import BudgetExceededError, LLMUnavailableError
from core.ratelimit import SlidingWindowLimiter


class Clock:
    def __init__(self, iso="2026-09-21T10:00:00"):
        self.now = datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp()

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def make(clock=None, **limits):
    return Budget(BudgetLimits(**limits), persist_path=None, clock=clock or Clock())


class TestLimits:
    def test_daily_calls(self):
        b = make(daily_calls=3)
        for _ in range(3):
            b.reserve()
        with pytest.raises(BudgetExceededError) as e:
            b.reserve()
        assert e.value.scope == "daily" and "daily usage limit" in str(e.value)

    def test_daily_tokens(self):
        b = make(daily_tokens=1000)
        b.reserve()
        b.record_usage(1200)  # a single call may overshoot; the next one is refused
        with pytest.raises(BudgetExceededError) as e:
            b.reserve()
        assert e.value.scope == "daily"

    def test_hourly_cap_leaves_the_rest_of_the_day(self):
        clock = Clock()
        b = make(clock, hourly_calls=2, daily_calls=100)
        b.reserve(); b.reserve()
        with pytest.raises(BudgetExceededError) as e:
            b.reserve()
        assert e.value.scope == "hourly"
        clock.advance(3600)  # next hour: same day, budget remains
        b.reserve()
        assert b.day_calls == 3

    def test_rollover_at_utc_midnight(self):
        clock = Clock("2026-09-21T23:30:00")
        b = make(clock, daily_calls=1, hourly_calls=10)
        b.reserve()
        with pytest.raises(BudgetExceededError):
            b.reserve()
        clock.advance(3600)  # 00:30 next day
        b.reserve()  # allowed again
        assert b.snapshot()["day_calls"] == 1

    def test_per_client_daily_limit_is_independent_per_client(self):
        b = make(client_daily_calls=2)
        with request_scope(client_id="1.1.1.1", max_calls=50):
            b.reserve(); b.reserve()
            with pytest.raises(BudgetExceededError) as e:
                b.reserve()
        assert e.value.scope == "client"
        with request_scope(client_id="2.2.2.2", max_calls=50):
            b.reserve()  # a different client is unaffected

    def test_calls_outside_a_scope_have_no_client_or_request_limit(self):
        b = make(client_daily_calls=1, request_calls=1)
        for _ in range(5):
            b.reserve()

    def test_exceeded_error_is_an_llm_unavailable_error_so_agents_surface_it(self):
        assert issubclass(BudgetExceededError, LLMUnavailableError)

    def test_refused_reservation_counts_nothing(self):
        b = make(daily_calls=1)
        b.reserve()
        for _ in range(3):
            with pytest.raises(BudgetExceededError):
                b.reserve()
        assert b.day_calls == 1


class TestRequestScope:
    def test_caps_calls_within_the_scope(self):
        b = make()
        set_budget(b)
        with request_scope(max_calls=3):
            b.reserve(); b.reserve(); b.reserve()
            with pytest.raises(BudgetExceededError) as e:
                b.reserve()
        assert e.value.scope == "request" and "3 model calls" in str(e.value)
        with request_scope(max_calls=3):
            b.reserve()  # a new scope starts fresh

    def test_default_cap_comes_from_limits(self):
        b = make(request_calls=2)
        set_budget(b)
        with request_scope():
            b.reserve(); b.reserve()
            with pytest.raises(BudgetExceededError):
                b.reserve()

    def test_inner_scope_cannot_loosen_or_reset_the_outer_one(self):
        b = make()
        set_budget(b)
        with request_scope(client_id="x", max_calls=2) as outer:
            b.reserve()
            with request_scope(client_id="y", max_calls=100) as inner:
                assert inner is outer
                b.reserve()
                with pytest.raises(BudgetExceededError):
                    b.reserve()

    def test_scope_ends_with_the_block(self):
        b = make(request_calls=1)
        set_budget(b)
        with request_scope():
            b.reserve()
        b.reserve(); b.reserve()  # no scope: unlimited per request


class TestConfigAndPersistence:
    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("LLM_DAILY_CALLS", "42")
        monkeypatch.setenv("LLM_REQUEST_CALLS", "not-a-number")
        monkeypatch.setenv("LLM_HOURLY_CALLS", "0")
        limits = BudgetLimits.from_env()
        assert limits.daily_calls == 42
        assert limits.request_calls == BudgetLimits().request_calls  # invalid -> default
        assert limits.hourly_calls == BudgetLimits().hourly_calls    # non-positive -> default

    def test_get_budget_uses_state_file_env(self, monkeypatch, tmp_path):
        path = tmp_path / "state.json"
        monkeypatch.setenv("BUDGET_STATE_FILE", str(path))
        set_budget(None)
        get_budget().reserve()
        assert json.loads(path.read_text())["day_calls"] == 1

    def test_state_survives_a_restart_within_the_same_day(self, tmp_path):
        path = str(tmp_path / "s.json")
        clock = Clock()
        first = Budget(BudgetLimits(daily_calls=3), persist_path=path, clock=clock)
        first.reserve(); first.reserve(); first.record_usage(500)

        second = Budget(BudgetLimits(daily_calls=3), persist_path=path, clock=clock)  # "restart"
        assert (second.day_calls, second.day_tokens) == (2, 500)
        second.reserve()
        with pytest.raises(BudgetExceededError):
            second.reserve()

    def test_stale_state_from_yesterday_is_ignored(self, tmp_path):
        path = str(tmp_path / "s.json")
        clock = Clock("2026-09-21T10:00:00")
        Budget(BudgetLimits(), persist_path=path, clock=clock).reserve()
        clock.advance(86400)
        assert Budget(BudgetLimits(), persist_path=path, clock=clock).day_calls == 0

    def test_corrupt_or_unwritable_state_is_not_fatal(self, tmp_path):
        bad = tmp_path / "s.json"
        bad.write_text("{not json")
        b = Budget(BudgetLimits(), persist_path=str(bad))
        b.reserve()
        Budget(BudgetLimits(), persist_path=str(tmp_path / "no" / "such" / "dir" / "s.json")).reserve()


class TestSlidingWindowLimiter:
    def test_allows_up_to_limit_then_refuses(self):
        clock = Clock()
        lim = SlidingWindowLimiter(3, 60, clock=clock)
        assert [lim.allow("a") for _ in range(4)] == [True, True, True, False]
        assert lim.allow("b")  # other keys are independent

    def test_window_slides(self):
        clock = Clock()
        lim = SlidingWindowLimiter(2, 60, clock=clock)
        lim.allow("a"); clock.advance(30); lim.allow("a")
        assert not lim.allow("a")
        clock.advance(31)  # the first event has aged out
        assert lim.allow("a")
        assert not lim.allow("a")

    def test_refused_events_are_not_recorded(self):
        clock = Clock()
        lim = SlidingWindowLimiter(1, 60, clock=clock)
        lim.allow("a")
        for _ in range(5):
            assert not lim.allow("a")
        clock.advance(61)
        assert lim.allow("a")  # hammering didn't extend the block

    def test_retry_after(self):
        clock = Clock()
        lim = SlidingWindowLimiter(1, 60, clock=clock)
        assert lim.retry_after("a") == 0
        lim.allow("a")
        clock.advance(10)
        assert 49 <= lim.retry_after("a") <= 51
        clock.advance(60)
        assert lim.retry_after("a") == 0

    def test_memory_is_bounded(self):
        lim = SlidingWindowLimiter(1, 60, max_keys=3)
        for i in range(10):
            lim.allow(f"k{i}")
        assert len(lim._events) == 3
