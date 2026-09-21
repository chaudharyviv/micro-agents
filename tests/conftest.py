"""Shared test fixtures."""

import pytest

from core.budget import Budget, BudgetLimits, set_budget


@pytest.fixture(autouse=True)
def fresh_budget():
    """Every test gets its own in-memory Budget (default limits, no state file), then it is discarded."""
    budget = Budget(BudgetLimits(), persist_path=None)
    set_budget(budget)
    yield budget
    set_budget(None)
