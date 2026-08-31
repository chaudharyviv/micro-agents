"""Simple memory system for storing and retrieving agent results."""

import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# Memory storage directory
MEMORY_DIR = Path(__file__).parent.parent / "memory"
MEMORY_DIR.mkdir(exist_ok=True)

# Database files
ANALYSES_FILE = MEMORY_DIR / "analyses.jsonl"
HISTORY_FILE = MEMORY_DIR / "history.json"


def store_result(agent_name: str, repo_url: str, result: dict) -> str:
    """
    Store an agent result to memory.

    Args:
        agent_name: Name of the agent (e.g., "blog_scout", "repo_onboarding")
        repo_url: Repository URL analyzed (if applicable)
        result: The result dict to store

    Returns:
        ID of the stored result
    """
    try:
        # Create a record with metadata
        record = {
            "id": f"{agent_name}_{int(datetime.now().timestamp()*1000)}",
            "agent": agent_name,
            "repo_url": repo_url,
            "timestamp": datetime.now().isoformat(),
            "result": result,
        }

        # Append to JSONL file
        with open(ANALYSES_FILE, "a") as f:
            f.write(json.dumps(record) + "\n")

        logger.info(f"Stored result: {record['id']}")
        return record["id"]

    except Exception as e:
        logger.error(f"Failed to store result: {e}")
        return None


def retrieve_result(result_id: str) -> Optional[dict]:
    """
    Retrieve a stored result by ID.

    Args:
        result_id: ID of the result to retrieve

    Returns:
        The result dict, or None if not found
    """
    try:
        if not ANALYSES_FILE.exists():
            return None

        with open(ANALYSES_FILE, "r") as f:
            for line in f:
                record = json.loads(line)
                if record["id"] == result_id:
                    return record["result"]

        logger.warning(f"Result not found: {result_id}")
        return None

    except Exception as e:
        logger.error(f"Failed to retrieve result: {e}")
        return None


def get_history(agent_name: str = None, repo_url: str = None, limit: int = 10) -> list[dict]:
    """
    Get history of stored results.

    Args:
        agent_name: Filter by agent name (optional)
        repo_url: Filter by repo URL (optional)
        limit: Maximum number of results to return

    Returns:
        List of result records
    """
    try:
        if not ANALYSES_FILE.exists():
            return []

        results = []
        with open(ANALYSES_FILE, "r") as f:
            for line in f:
                record = json.loads(line)

                # Apply filters
                if agent_name and record["agent"] != agent_name:
                    continue
                if repo_url and record["repo_url"] != repo_url:
                    continue

                results.append(record)

        # Return most recent first
        results.sort(key=lambda r: r["timestamp"], reverse=True)
        return results[:limit]

    except Exception as e:
        logger.error(f"Failed to get history: {e}")
        return []


def clear_memory():
    """Clear all stored results."""
    try:
        if ANALYSES_FILE.exists():
            ANALYSES_FILE.unlink()
        logger.info("Memory cleared")
    except Exception as e:
        logger.error(f"Failed to clear memory: {e}")


def get_stats() -> dict:
    """Get statistics about stored results."""
    try:
        if not ANALYSES_FILE.exists():
            return {
                "total_results": 0,
                "agents": {},
                "repos": [],
            }

        agents = {}
        repos = set()
        total = 0

        with open(ANALYSES_FILE, "r") as f:
            for line in f:
                record = json.loads(line)
                total += 1

                agent = record.get("agent", "unknown")
                agents[agent] = agents.get(agent, 0) + 1

                repo = record.get("repo_url", "unknown")
                if repo:
                    repos.add(repo)

        return {
            "total_results": total,
            "agents": agents,
            "repos": list(repos),
        }

    except Exception as e:
        logger.error(f"Failed to get stats: {e}")
        return {}
