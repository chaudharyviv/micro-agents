"""Tests for the in-process agent memory (session memory + shared result cache)."""

import time

from core.memory import (
    ResultCache,
    SessionMemory,
    is_error,
    normalize_input,
    run_with_memory,
    summarize,
)


def test_normalize_input_ignores_case_slash_and_git_suffix():
    assert normalize_input(" https://GitHub.com/a/b.git/ ") == normalize_input("https://github.com/a/b")


def test_is_error_detects_both_error_shapes():
    assert is_error({"status": "error"})
    assert is_error({"error_message": "boom"})
    assert not is_error({"status": "success"})
    assert not is_error(["a", "b"])


def test_summarize_truncates_long_output():
    assert len(summarize({"x": "y" * 1000}, max_chars=50)) == 50
    assert summarize("short") == "short"


def test_session_memory_records_most_recent_first_and_caps_entries():
    mem = SessionMemory(max_entries=3)
    for i in range(5):
        mem.record("agent", f"input-{i}", {"n": i})
    recent = mem.recent()
    assert len(mem) == 3
    assert [e["input"] for e in recent] == ["input-4", "input-3", "input-2"]
    assert all("age_seconds" in e for e in recent)


def test_session_memory_has_input_and_clear():
    mem = SessionMemory()
    mem.record("repo_onboarding", "https://github.com/a/b", {"ok": 1})
    assert mem.has_input("https://GITHUB.com/a/b/")
    assert not mem.has_input("https://github.com/x/y")
    mem.clear()
    assert len(mem) == 0


def test_result_cache_hit_miss_and_stats():
    cache = ResultCache()
    assert cache.get("cve_impact", "log4j") is None
    cache.put("cve_impact", "Log4j", {"risk": "high"})
    assert cache.get("cve_impact", " log4j ") == {"risk": "high"}
    assert (cache.hits, cache.misses) == (1, 1)


def test_result_cache_expires_after_ttl():
    cache = ResultCache(ttl_seconds=0)
    cache.put("cve_impact", "log4j", {"risk": "high"})
    time.sleep(0.01)
    assert cache.get("cve_impact", "log4j") is None


def test_result_cache_evicts_least_recently_used():
    cache = ResultCache(max_entries=2)
    cache.put("a", "1", "one")
    cache.put("a", "2", "two")
    cache.get("a", "1")  # touch "1" so "2" becomes the eviction candidate
    cache.put("a", "3", "three")
    assert cache.get("a", "2") is None
    assert cache.get("a", "1") == "one"
    assert len(cache) == 2


def test_run_with_memory_caches_only_cacheable_agents_and_skips_errors():
    cache, session, calls = ResultCache(), SessionMemory(), []

    def fn(x):
        calls.append(x)
        return {"echo": x}

    assert run_with_memory("repo_onboarding", "u", fn, cache, session) == ({"echo": "u"}, False)
    assert run_with_memory("repo_onboarding", "u", fn, cache, session) == ({"echo": "u"}, True)
    assert calls == ["u"]
    assert [e["cached"] for e in session.recent()] == [True, False]

    run_with_memory("blog_scout", "topic", fn, cache, session)
    run_with_memory("blog_scout", "topic", fn, cache, session)
    assert calls.count("topic") == 2  # blog_scout is not cacheable

    err = lambda x: {"error_message": "nope"}
    run_with_memory("security_audit", "bad", err, cache, session)
    assert cache.get("security_audit", "bad") is None


def test_run_with_memory_before_run_can_block_execution_but_not_cache_hits():
    cache, calls = ResultCache(), []

    def fn(x):
        calls.append(x)
        return {"ok": True}

    assert run_with_memory("cve_impact", "x", fn, cache, before_run=lambda: False) == (None, False)
    assert calls == []
    run_with_memory("cve_impact", "x", fn, cache)
    # Cache hit must not consult before_run (a rate limiter shouldn't charge for free results).
    assert run_with_memory("cve_impact", "x", fn, cache, before_run=lambda: False) == ({"ok": True}, True)
