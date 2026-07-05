"""Tests for GET /api/risk/cvar and GET /api/risk/greeks endpoints.

Hermetic: no DB, no Redis, no network, no .env.  The _strategy_provider
module-level singleton in risk_routes is patched at the boundary before
each test so the endpoints operate entirely on controlled mock data.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# ── Helpers ──────────────────────────────────────────────────


def _make_strategy(sid: str = "s1") -> MagicMock:
    s = MagicMock()
    s.id = sid
    s.paper_title = f"Paper {sid}"
    s.status = MagicMock()
    s.status.value = "validated"
    return s


def _make_backtest(
    sharpe: float = 1.2,
    cagr: float = 0.15,
    max_drawdown: float = 0.12,
    equity_curve: list[float] | None = None,
) -> MagicMock:
    bt = MagicMock()
    bt.sharpe_ratio = sharpe
    bt.cagr = cagr
    bt.max_drawdown = max_drawdown
    bt.equity_curve = equity_curve or []
    bt.calmar_ratio = cagr / max_drawdown if max_drawdown else None
    bt.correlation_to_spy = 0.6
    bt.win_rate = 0.55
    return bt


def _equity_from_returns(returns: list[float]) -> list[float]:
    equity = [100.0]
    for r in returns:
        equity.append(equity[-1] * (1.0 + r))
    return equity


def _alternating_returns(n: int = 252) -> list[float]:
    """Deterministic daily returns: +10bps / -8bps alternating."""
    return [0.001 if i % 2 == 0 else -0.0008 for i in range(n)]


# ── CVaR tests ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cvar_returns_200_with_empty_strategies():
    from archimedes.main import app

    mock_provider = MagicMock()
    mock_provider.list_strategies.return_value = []

    # _strategy_provider is now a lazily-cached accessor (_strategy_provider());
    # patch it with a callable returning the pre-configured mock instance.
    with patch("archimedes.api.risk_routes._strategy_provider", lambda: mock_provider):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/risk/cvar")

    assert resp.status_code == 200
    data = resp.json()
    assert "levels" in data
    # Empty-data path always returns 3 zero-filled levels (90/95/99).
    assert len(data["levels"]) == 3
    confidences = {lv["confidence"] for lv in data["levels"]}
    assert confidences == {0.90, 0.95, 0.99}
    for lv in data["levels"]:
        assert lv["var_historical"] == pytest.approx(0.0)
        assert lv["cvar_historical"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_cvar_levels_have_correct_confidence_values():
    from archimedes.main import app

    returns = _alternating_returns()
    equity = _equity_from_returns(returns)

    s1, s2 = _make_strategy("s1"), _make_strategy("s2")
    bt1 = _make_backtest(equity_curve=equity)
    bt2 = _make_backtest(equity_curve=equity)

    mock_provider = MagicMock()
    mock_provider.list_strategies.return_value = [s1, s2]
    mock_provider.get_backtest_result.side_effect = lambda sid: bt1 if sid == "s1" else bt2

    # _strategy_provider is now a lazily-cached accessor (_strategy_provider());
    # patch it with a callable returning the pre-configured mock instance.
    with patch("archimedes.api.risk_routes._strategy_provider", lambda: mock_provider):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/risk/cvar")

    assert resp.status_code == 200
    levels = resp.json()["levels"]
    assert len(levels) == 3
    conf_vals = [lv["confidence"] for lv in levels]
    assert pytest.approx(0.90) in conf_vals
    assert pytest.approx(0.95) in conf_vals
    assert pytest.approx(0.99) in conf_vals


@pytest.mark.asyncio
async def test_cvar_cvar_exceeds_var():
    """CVaR (Expected Shortfall) >= VaR by definition at every confidence level."""
    from archimedes.main import app

    returns = _alternating_returns()
    equity = _equity_from_returns(returns)

    s1 = _make_strategy("s1")
    bt1 = _make_backtest(equity_curve=equity)

    mock_provider = MagicMock()
    mock_provider.list_strategies.return_value = [s1]
    mock_provider.get_backtest_result.return_value = bt1

    # _strategy_provider is now a lazily-cached accessor (_strategy_provider());
    # patch it with a callable returning the pre-configured mock instance.
    with patch("archimedes.api.risk_routes._strategy_provider", lambda: mock_provider):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/risk/cvar")

    assert resp.status_code == 200
    for level in resp.json()["levels"]:
        assert level["cvar_historical"] >= level["var_historical"] - 1e-9, (
            f"CVaR ({level['cvar_historical']}) < VaR ({level['var_historical']}) at confidence {level['confidence']}"
        )


@pytest.mark.asyncio
async def test_cvar_single_return_emits_valid_json():
    """A portfolio with exactly one daily return used to leak a bare NaN into
    the JSON (std(ddof=1) divides by zero). The response must stay valid JSON
    and every parametric number must be finite."""
    import json
    import math

    from archimedes.main import app

    # Two equity points -> exactly one daily return, so std(ddof=1) is 0/0.
    s1 = _make_strategy("s1")
    bt1 = _make_backtest(equity_curve=[100.0, 101.0])

    mock_provider = MagicMock()
    mock_provider.list_strategies.return_value = [s1]
    mock_provider.get_backtest_result.return_value = bt1

    with patch("archimedes.api.risk_routes._strategy_provider", lambda: mock_provider):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/risk/cvar")

    assert resp.status_code == 200
    # A bare NaN token is what strict parsers (JSON.parse, Swift, Go) reject.
    assert "NaN" not in resp.text

    # Re-parse rejecting the non-standard constants to prove it's strict JSON.
    def _reject(_):
        raise AssertionError("response contains a non-finite JSON constant")

    parsed = json.loads(resp.text, parse_constant=_reject)
    for lv in parsed["levels"]:
        assert lv["sample_size"] == 1
        for key in ("var_parametric", "cvar_parametric", "var_historical", "cvar_historical"):
            assert math.isfinite(lv[key]), f"{key} is not finite: {lv[key]}"


# ── Greeks tests ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_greeks_returns_200_with_empty_strategies():
    from archimedes.main import app

    mock_provider = MagicMock()
    mock_provider.list_strategies.return_value = []

    # _strategy_provider is now a lazily-cached accessor (_strategy_provider());
    # patch it with a callable returning the pre-configured mock instance.
    with patch("archimedes.api.risk_routes._strategy_provider", lambda: mock_provider):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/risk/greeks")

    assert resp.status_code == 200
    data = resp.json()
    assert data["portfolio_delta"] == pytest.approx(0.0)
    assert data["portfolio_gamma"] == pytest.approx(0.0)
    assert data["portfolio_theta"] == pytest.approx(0.0)
    assert data["portfolio_vega"] == pytest.approx(0.0)
    assert data["strategy_count"] == 0


@pytest.mark.asyncio
async def test_greeks_delta_in_valid_range():
    """|portfolio_delta| <= 1 for any realistic strategy params (ATM call delta ∈ (0,1))."""
    from archimedes.main import app

    s1 = _make_strategy("s1")
    bt1 = _make_backtest(sharpe=1.5, cagr=0.20, max_drawdown=0.10)

    mock_provider = MagicMock()
    mock_provider.list_strategies.return_value = [s1]
    mock_provider.get_backtest_result.return_value = bt1

    # _strategy_provider is now a lazily-cached accessor (_strategy_provider());
    # patch it with a callable returning the pre-configured mock instance.
    with patch("archimedes.api.risk_routes._strategy_provider", lambda: mock_provider):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/risk/greeks")

    assert resp.status_code == 200
    delta = resp.json()["portfolio_delta"]
    assert 0.0 < delta <= 1.0, f"portfolio_delta={delta} outside (0, 1]"


@pytest.mark.asyncio
async def test_greeks_portfolio_aggregate_matches_weighted_sum():
    """Two equal-weight strategies with identical params → portfolio_delta equals the single-strategy delta."""
    from archimedes.api.risk_routes import _strategy_delta
    from archimedes.main import app

    sharpe, cagr = 1.2, 0.15
    expected = _strategy_delta(sharpe, cagr)

    s1, s2 = _make_strategy("s1"), _make_strategy("s2")
    bt1 = _make_backtest(sharpe=sharpe, cagr=cagr, max_drawdown=0.12)
    bt2 = _make_backtest(sharpe=sharpe, cagr=cagr, max_drawdown=0.12)

    mock_provider = MagicMock()
    mock_provider.list_strategies.return_value = [s1, s2]
    mock_provider.get_backtest_result.side_effect = lambda sid: bt1 if sid == "s1" else bt2

    # _strategy_provider is now a lazily-cached accessor (_strategy_provider());
    # patch it with a callable returning the pre-configured mock instance.
    with patch("archimedes.api.risk_routes._strategy_provider", lambda: mock_provider):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/risk/greeks")

    assert resp.status_code == 200
    portfolio_delta = resp.json()["portfolio_delta"]
    assert portfolio_delta == pytest.approx(expected, abs=1e-4)
