"""Pytest fixtures for backend unit tests.

Uses the real LocalStrategyProvider pointed at the analytics-engine/strategies/
directory so tests exercise the actual strategy files rather than mocks.
"""

# IMPORTANT: set TESTING env var BEFORE any archimedes imports so that
# the rate limiter (api/limiter.py) reads it at module init time.
import os

os.environ["TESTING"] = "1"

from pathlib import Path

import pytest
from archimedes.services.strategy_provider import LocalStrategyProvider


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Absolute path to the monorepo root (two levels up from backend/)."""
    return Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def strategies_dir(repo_root: Path) -> Path:
    return repo_root / "analytics-engine" / "strategies"


@pytest.fixture(scope="session")
def provider(strategies_dir: Path) -> LocalStrategyProvider:
    return LocalStrategyProvider(strategies_dir)
