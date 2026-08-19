"""HTTP-layer tests for portfolio_routes (POST /api/portfolio/optimize).

Hermetic: the yfinance boundary (_fetch_price_histories) is mocked with
deterministic synthetic price series — no network, DB, or Redis required.
"""

from __future__ import annotations

import math
from unittest.mock import patch

import pandas as pd
import pytest
from httpx import ASGITransport, AsyncClient

from tests.auth_helpers import auth_cookies

_N_BARS = 300


def _synthetic_prices() -> dict[str, pd.Series]:
    """Three deterministic, non-degenerate price series (≥60 return bars)."""
    out: dict[str, pd.Series] = {}
    for k, sym in enumerate(["sSPY", "sGOLD", "sBTC"]):
        prices = [100.0 * (1 + 0.0004 * (k + 1)) ** i * (1 + 0.01 * math.sin(i / (3.0 + k))) for i in range(_N_BARS)]
        out[sym] = pd.Series(prices)
    return out


def _patch_prices(histories):
    return patch(
        "archimedes.services.strategy_signal_evaluator._fetch_price_histories",
        return_value=histories,
    )


async def _post_optimize(body: dict):
    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/api/portfolio/optimize", json=body, cookies=auth_cookies())


@pytest.mark.parametrize("method,expected_optimizer", [("mvo", "mvo"), ("hrp", "hrp"), ("bl", "black_litterman")])
async def test_optimize_happy_path(method, expected_optimizer):
    with _patch_prices(_synthetic_prices()):
        resp = await _post_optimize({"method": method, "risk_profile": "moderate"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["optimizer"] == expected_optimizer
    assert set(data["weights"]) <= {"sSPY", "sGOLD", "sBTC"}
    # Weights sum to the synth budget (1 - usdc_floor), which itself is in (0, 1].
    total = sum(data["weights"].values())
    assert 0.0 < total <= 1.0
    assert abs(total + data["usdc_weight"] - 1.0) < 0.01
    assert all(w >= -1e-9 for w in data["weights"].values())


async def test_optimize_default_method_is_mvo():
    with _patch_prices(_synthetic_prices()):
        resp = await _post_optimize({})
    assert resp.status_code == 200
    assert resp.json()["optimizer"] == "mvo"


async def test_optimize_unknown_method_422():
    resp = await _post_optimize({"method": "alchemy"})
    assert resp.status_code == 422
    assert "alchemy" in resp.json()["detail"]


async def test_optimize_invalid_risk_profile_422():
    resp = await _post_optimize({"method": "mvo", "risk_profile": "yolo"})
    assert resp.status_code == 422


async def test_optimize_no_price_data_503():
    with _patch_prices({}):
        resp = await _post_optimize({"method": "mvo"})
    assert resp.status_code == 503


async def test_optimize_insufficient_history_503():
    # Two symbols but only 10 bars each — below the 60-bar floor.
    short = {sym: series.iloc[:10] for sym, series in _synthetic_prices().items()}
    with _patch_prices(short):
        resp = await _post_optimize({"method": "hrp"})
    assert resp.status_code == 503


async def test_optimize_risk_profile_changes_usdc_floor():
    with _patch_prices(_synthetic_prices()):
        conservative = await _post_optimize({"method": "mvo", "risk_profile": "conservative"})
        aggressive = await _post_optimize({"method": "mvo", "risk_profile": "aggressive"})
    assert conservative.status_code == aggressive.status_code == 200
    assert conservative.json()["usdc_weight"] > aggressive.json()["usdc_weight"]
