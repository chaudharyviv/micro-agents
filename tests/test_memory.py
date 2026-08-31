"""Tests for memory system."""

import json
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch

from core.memory import (
    store_result,
    retrieve_result,
    get_history,
    clear_memory,
    get_stats,
)


@pytest.fixture
def temp_memory_dir(tmp_path):
    """Create a temporary memory directory for tests."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()

    # Patch the memory directory
    with patch("core.memory.MEMORY_DIR", memory_dir):
        with patch("core.memory.ANALYSES_FILE", memory_dir / "analyses.jsonl"):
            yield memory_dir


def test_store_and_retrieve_result(temp_memory_dir):
    """Test storing and retrieving a result."""
    result = {"title": "Test Result", "data": "test_data"}

    result_id = store_result("test_agent", "https://github.com/test/repo", result)

    assert result_id is not None
    retrieved = retrieve_result(result_id)
    assert retrieved == result


def test_retrieve_nonexistent_result(temp_memory_dir):
    """Test retrieving a result that doesn't exist."""
    retrieved = retrieve_result("nonexistent_id")

    assert retrieved is None


def test_get_history_all(temp_memory_dir):
    """Test getting all history."""
    result1 = {"data": "result1"}
    result2 = {"data": "result2"}

    store_result("agent1", "https://github.com/test/repo1", result1)
    store_result("agent2", "https://github.com/test/repo2", result2)

    history = get_history()

    assert len(history) >= 2
    assert history[0]["agent"] in ["agent1", "agent2"]


def test_get_history_filter_by_agent(temp_memory_dir):
    """Test filtering history by agent name."""
    store_result("blog_scout", "https://github.com/test/repo", {"data": "result1"})
    store_result("cve_impact", "https://github.com/test/repo", {"data": "result2"})

    history = get_history(agent_name="blog_scout")

    assert len(history) == 1
    assert history[0]["agent"] == "blog_scout"


def test_get_history_filter_by_repo(temp_memory_dir):
    """Test filtering history by repo URL."""
    repo_url = "https://github.com/test/repo"
    store_result("agent1", repo_url, {"data": "result1"})
    store_result("agent2", "https://github.com/other/repo", {"data": "result2"})

    history = get_history(repo_url=repo_url)

    assert len(history) == 1
    assert history[0]["repo_url"] == repo_url


def test_get_history_limit(temp_memory_dir):
    """Test limiting history results."""
    for i in range(5):
        store_result("test_agent", f"https://github.com/test/repo{i}", {"data": f"result{i}"})

    history = get_history(limit=2)

    assert len(history) <= 2


def test_clear_memory(temp_memory_dir):
    """Test clearing memory."""
    store_result("test_agent", "https://github.com/test/repo", {"data": "result"})

    history = get_history()
    assert len(history) > 0

    clear_memory()

    history = get_history()
    assert len(history) == 0


def test_get_stats(temp_memory_dir):
    """Test getting memory statistics."""
    store_result("blog_scout", "https://github.com/test/repo1", {"data": "result1"})
    store_result("cve_impact", "https://github.com/test/repo2", {"data": "result2"})
    store_result("blog_scout", "https://github.com/test/repo1", {"data": "result3"})

    stats = get_stats()

    assert stats["total_results"] >= 3
    assert stats["agents"]["blog_scout"] == 2
    assert stats["agents"]["cve_impact"] == 1
    assert len(stats["repos"]) >= 2


def test_get_stats_empty(temp_memory_dir):
    """Test getting stats when memory is empty."""
    stats = get_stats()

    assert stats["total_results"] == 0
    assert len(stats["agents"]) == 0
    assert len(stats["repos"]) == 0
