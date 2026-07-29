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


# ─── Quant-lane synthetic-data fixtures ──────────────────────────────
# Reusable deterministic factories for rigor / optimizer / fusion / backtester
# tests. The underlying functions live in quant_factories.py and can also be
# imported directly when a test needs custom parameters.

from tests import quant_factories  # noqa: E402


@pytest.fixture
def synthetic_returns():
    """Factory fixture: build a daily return series with a target Sharpe.

    Usage:  rets = synthetic_returns(annual_sharpe=1.2, n=504, seed=7)
    """
    return quant_factories.make_returns


@pytest.fixture
def price_panel():
    """Factory fixture: build (close_panel, volume_panel) DataFrames.

    Usage:  close, vol = price_panel(["SPY", "AGG"], n=300, correlation=0.4)
    """
    return quant_factories.make_price_panel


@pytest.fixture
def returns_matrix():
    """Factory fixture: build a {strategy_id: daily returns} matrix.

    Usage:  m = returns_matrix(n_strategies=8, n=504)
    """
    return quant_factories.make_returns_matrix


@pytest.fixture
def regime_shift_returns():
    """Factory fixture: (market, strategy) returns with a mid-series vol regime shift."""
    return quant_factories.make_regime_shift_returns


@pytest.fixture(autouse=True)
def _legacy_siwe_test_adapter(monkeypatch):
    """Map legacy SIWE test cookies onto canonical test users.

    Production never enables this adapter. Existing route tests keep exercising
    ownership with cryptographically signed fixture cookies while dedicated
    account-auth tests cover Better Auth session resolution directly.
    """
    from datetime import UTC, datetime, timedelta

    from archimedes.api import (
        account_auth,
        auth_siwe,
        generate_routes,
        proposals_routes,
        strategies_routes,
        wallet_routes,
    )

    def legacy_wallet(request):
        token = request.cookies.get(auth_siwe._COOKIE_NAME)
        return auth_siwe._verify_session(token) if token else None

    async def legacy_session(request):
        wallet = legacy_wallet(request)
        if wallet is None:
            return None
        return {
            "user": {
                "id": f"legacy-test:{wallet}",
                "name": "Legacy test user",
                "email": f"{wallet[2:10]}@legacy.test",
                "emailVerified": True,
            },
            "session": {"expiresAt": (datetime.now(UTC) + timedelta(hours=1)).isoformat()},
        }

    monkeypatch.setattr(account_auth, "_SESSION_COOKIE_FRAGMENT", "session")
    monkeypatch.setattr(account_auth, "_fetch_session", legacy_session)
    monkeypatch.setattr(wallet_routes, "get_linked_wallet_address", legacy_wallet)
    monkeypatch.setattr(generate_routes, "get_linked_wallet_address", legacy_wallet)
    monkeypatch.setattr(proposals_routes, "get_linked_wallet_address", legacy_wallet)
    monkeypatch.setattr(strategies_routes, "get_verified_wallet", legacy_wallet)


@pytest.fixture(autouse=True)
def _clear_rigor_cache():
    """Reset the process-level live-rigor-gate cache (services/rigor_cache.py)
    around every test.

    That cache is a module-global dict keyed on a data-version token
    (strategy ids + a fingerprint of their returns). Several test files reuse
    the SAME real curated strategy ids (via the session-scoped ``provider`` /
    ``default_provider()``) with the SAME synthetic returns fixtures across
    different test functions — without this reset, a cache entry populated by
    one test could be silently served to a later, unrelated test in the same
    pytest process, making that test's assertions (e.g. call-count spies on
    ``run_rigor_gate``) order-dependent on whatever ran before it. Clearing
    before AND after keeps every test's view of the cache empty regardless of
    suite order, matching the hermetic-test mandate (no hidden environmental
    state leaking between tests).
    """
    from archimedes.services import rigor_cache

    rigor_cache.clear()
    yield
    rigor_cache.clear()
