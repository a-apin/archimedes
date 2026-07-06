"""Tests for POST /api/portfolio/parameter-sweep and POST /api/portfolio/scenario-analysis.

Hermetic: no DB, no Redis, no live data feeds.  sensitivity_sweep is mocked at
the service module boundary (where portfolio_routes imports it at call time),
so the endpoint logic is exercised without running actual backtests.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

# ── Shared fixtures ───────────────────────────────────────────


def _sweep_payload(
    param1_name: str = "rebalance_days",
    param1_range: list[float] | None = None,
    param2_name: str = "tx_cost_bps",
    param2_range: list[float] | None = None,
) -> dict:
    return {
        "strategy_id": "tsmom",
        "weights": {"SPY": 0.5, "QQQ": 0.5},
        "param1_name": param1_name,
        "param1_range": param1_range or [10, 20, 30],
        "param2_name": param2_name,
        "param2_range": param2_range or [5, 10],
        "metric": "sharpe_ratio",
    }


def _mock_sweep_result(p1_range: list[int], p2_range: list[int], p1_name: str, p2_name: str) -> dict:
    """Return a minimal sensitivity_sweep result for the given grid dimensions."""
    grid = [
        {"params": {p1_name: p1, p2_name: p2}, "metric_value": 1.0 + 0.1 * i}
        for i, (p1, p2) in enumerate([(p1, p2) for p1 in p1_range for p2 in p2_range])
    ]
    return {
        "grid": grid,
        "metric": "sharpe_ratio",
        "metric_mean": 1.1,
        "metric_std": 0.05,
        "metric_range": [1.0, 1.2],
        "sensitivity_ratio": 0.2,
        "best_params": {p1_name: p1_range[0], p2_name: p2_range[0]},
        "worst_params": {p1_name: p1_range[-1], p2_name: p2_range[-1]},
    }


# ── Parameter-sweep tests ─────────────────────────────────────


@pytest.mark.asyncio
async def test_parameter_sweep_returns_422_on_invalid_param():
    """Invalid param name (not in allowed set) → 422 before reaching sensitivity_sweep."""
    from archimedes.main import app

    payload = _sweep_payload(param1_name="lookback_days")  # not allowed

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/portfolio/parameter-sweep", json=payload)

    assert resp.status_code == 422
    assert "lookback_days" in resp.text or "param1_name" in resp.text


@pytest.mark.asyncio
async def test_parameter_sweep_rejects_oversized_range():
    """Audit 2026-06-14: an unbounded range schedules one backtest per Cartesian
    cell → compute-amplification DoS. Each range is capped at 25 at the schema
    layer, so an oversized range is rejected (422) before any backtest runs."""
    from archimedes.main import app

    payload = _sweep_payload(param1_range=[float(i) for i in range(200)])  # 200 > 25

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/portfolio/parameter-sweep", json=payload)

    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_parameter_sweep_rejects_empty_range():
    """min_length=1 keeps an empty range from yielding zero cells / odd grids."""
    from archimedes.main import app

    # Build the payload directly — _sweep_payload coalesces a falsy [] to its
    # default, which would defeat the empty-range check.
    payload = {
        "strategy_id": "tsmom",
        "weights": {"SPY": 0.5, "QQQ": 0.5},
        "param1_name": "rebalance_days",
        "param1_range": [10, 20, 30],
        "param2_name": "tx_cost_bps",
        "param2_range": [],  # empty → must be rejected by min_length=1
        "metric": "sharpe_ratio",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/portfolio/parameter-sweep", json=payload)

    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_parameter_sweep_grid_shape():
    """Grid dimensions must match len(param1_range) × len(param2_range)."""
    from archimedes.main import app

    p1_range = [10, 20, 30]
    p2_range = [5, 10]
    payload = _sweep_payload(param1_range=p1_range, param2_range=p2_range)

    mock_result = _mock_sweep_result(p1_range, p2_range, "rebalance_days", "tx_cost_bps")

    with patch("archimedes.services.portfolio_backtester.sensitivity_sweep", return_value=mock_result):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/portfolio/parameter-sweep", json=payload)

    assert resp.status_code == 200
    data = resp.json()
    grid = data["grid_2d"]
    assert len(grid) == len(p1_range), f"Expected {len(p1_range)} rows, got {len(grid)}"
    assert all(len(row) == len(p2_range) for row in grid), f"Expected {len(p2_range)} cols per row"


@pytest.mark.asyncio
async def test_parameter_sweep_response_has_required_fields():
    """Response must carry grid_2d, rows, cols, sensitivity_ratio, best_params, worst_params."""
    from archimedes.main import app

    p1_range = [10, 20]
    p2_range = [5, 10]
    payload = _sweep_payload(param1_range=p1_range, param2_range=p2_range)

    mock_result = _mock_sweep_result(p1_range, p2_range, "rebalance_days", "tx_cost_bps")

    with patch("archimedes.services.portfolio_backtester.sensitivity_sweep", return_value=mock_result):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/portfolio/parameter-sweep", json=payload)

    assert resp.status_code == 200
    data = resp.json()
    for key in ("grid_2d", "rows", "cols", "metric_mean", "metric_std", "sensitivity_ratio", "best_params"):
        assert key in data, f"Missing key '{key}' in response"


@pytest.mark.asyncio
async def test_parameter_sweep_second_invalid_param_returns_422():
    """param2_name validation also fires correctly."""
    from archimedes.main import app

    payload = _sweep_payload(param2_name="sma_window")  # not allowed

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/portfolio/parameter-sweep", json=payload)

    assert resp.status_code == 422


# ── Scenario-analysis tests ───────────────────────────────────


@pytest.mark.asyncio
async def test_scenario_analysis_returns_predefined_scenarios():
    """Without custom scenarios, the endpoint uses all 6 predefined scenarios."""
    from archimedes.main import app

    payload = {
        "weights": {"SPY": 0.6, "TLT": 0.3, "GLD": 0.1},
        "portfolio_value": 10000.0,
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/portfolio/scenario-analysis", json=payload)

    assert resp.status_code == 200
    data = resp.json()
    assert "scenarios" in data
    # 6 predefined scenarios must all be present
    assert len(data["scenarios"]) == 6


@pytest.mark.asyncio
async def test_scenario_analysis_equity_crash_is_negative():
    """The 2008-style equity crash scenario must produce a negative portfolio impact."""
    from archimedes.main import app

    payload = {
        "weights": {"SPY": 1.0},
        "portfolio_value": 10000.0,
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/portfolio/scenario-analysis", json=payload)

    assert resp.status_code == 200
    scenarios_by_name = {s["scenario_name"]: s for s in resp.json()["scenarios"]}
    crash = scenarios_by_name.get("Equity Crash 2008-style")
    assert crash is not None, "Expected 'Equity Crash 2008-style' scenario in response"
    assert crash["impact_pct"] < 0, f"SPY-only portfolio should lose value in 2008 crash; got {crash['impact_pct']}"


@pytest.mark.asyncio
async def test_scenario_analysis_custom_scenario():
    """A custom scenario supplied by the caller is applied instead of predefined ones."""
    from archimedes.main import app

    payload = {
        "weights": {"SPY": 0.5, "QQQ": 0.5},
        "portfolio_value": 20000.0,
        "scenarios": [
            {
                "name": "Custom SPY shock",
                "shocks": [{"asset": "SPY", "shock": -0.10}],
            }
        ],
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/portfolio/scenario-analysis", json=payload)

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["scenarios"]) == 1
    sc = data["scenarios"][0]
    assert sc["scenario_name"] == "Custom SPY shock"
    # SPY 50% × -10% shock → portfolio impact ≈ -5%; QQQ 50% unshocked → total ≈ -5% of 20k = -$1000
    assert sc["impact_dollars"] == pytest.approx(-1000.0, abs=1.0)


@pytest.mark.asyncio
async def test_scenario_analysis_stress_amplification_applied():
    """stress_adjusted_pct must equal impact_pct * 1.2 for every scenario."""
    from archimedes.main import app

    payload = {
        "weights": {"SPY": 1.0},
        "portfolio_value": 10000.0,
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/portfolio/scenario-analysis", json=payload)

    assert resp.status_code == 200
    for sc in resp.json()["scenarios"]:
        assert sc["stress_adjusted_pct"] == pytest.approx(sc["impact_pct"] * 1.2, rel=1e-4), (
            f"Stress amplification wrong for '{sc['scenario_name']}'"
        )


@pytest.mark.asyncio
async def test_scenario_analysis_rejects_oversized_weights():
    """#917: >100 weights is rejected at validation (422) before any compute."""
    from archimedes.main import app

    payload = {
        "weights": {f"SYM{i}": 0.01 for i in range(101)},
        "portfolio_value": 10000.0,
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/portfolio/scenario-analysis", json=payload)

    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_scenario_analysis_rejects_oversized_scenario_list():
    """#917: >10 scenarios is rejected at validation (422) before the O(w×s) loop."""
    from archimedes.main import app

    payload = {
        "weights": {"SPY": 1.0},
        "portfolio_value": 10000.0,
        "scenarios": [{"name": f"s{i}", "shocks": [{"asset": "SPY", "shock": -0.01}]} for i in range(11)],
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/portfolio/scenario-analysis", json=payload)

    assert resp.status_code == 422, resp.text
