"""HTTP-layer + unit tests for risk_routes (previously untested — issue #7 slice).

Covers the pure risk-classification helpers and the two endpoints
(/api/risk/profiles, /api/risk/portfolio). No DB, chain, or network required:
the portfolio endpoint degrades gracefully when no backtest/vault data exists,
which is exactly the path exercised here.
"""

from __future__ import annotations

import pytest
from archimedes.api.risk_routes import (
    RISK_BANDS,
    _classify_concentration,
    _classify_risk_profile,
    _derive_risk_level,
)
from httpx import ASGITransport, AsyncClient

# ── Pure helpers ─────────────────────────────────────────────


class TestClassifyRiskProfile:
    def test_lowest_band_for_tiny_drawdown(self):
        # 2% DD falls in the first band (fixed_income, max_dd 0.05).
        assert _classify_risk_profile(0.02) == RISK_BANDS[0]["label"]

    def test_picks_band_at_boundary(self):
        # Exactly the band's max_dd should still classify into that band (<=).
        assert _classify_risk_profile(RISK_BANDS[0]["max_dd"]) == RISK_BANDS[0]["label"]

    def test_overflow_is_hyper_risky(self):
        # Beyond every band → explicit hyper_risky fallback.
        assert _classify_risk_profile(0.99) == "hyper_risky"

    def test_monotonic_non_decreasing_severity(self):
        # As drawdown grows, the chosen band index must not move backwards.
        last_idx = -1
        labels = [b["label"] for b in RISK_BANDS]
        for dd in [0.0, 0.05, 0.10, 0.30, 0.50]:
            label = _classify_risk_profile(dd)
            idx = labels.index(label) if label in labels else len(labels)
            assert idx >= last_idx
            last_idx = idx


class TestClassifyConcentration:
    def test_diversified(self):
        assert _classify_concentration(0.10) == "diversified"

    def test_moderate(self):
        assert _classify_concentration(0.20) == "moderate"

    def test_concentrated(self):
        assert _classify_concentration(0.40) == "concentrated"

    def test_boundaries(self):
        # 0.15 and 0.25 are the exclusive lower edges of the next tier.
        assert _classify_concentration(0.15) == "moderate"
        assert _classify_concentration(0.25) == "concentrated"


class TestDeriveRiskLevel:
    def test_none_sharpe_is_high(self):
        assert _derive_risk_level(None) == "High"

    def test_high_sharpe_is_low_risk(self):
        assert _derive_risk_level(1.5) == "Low"

    def test_mid_sharpe_is_medium(self):
        assert _derive_risk_level(0.75) == "Medium"

    def test_low_sharpe_is_high(self):
        assert _derive_risk_level(0.2) == "High"


# ── HTTP endpoints ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_risk_profiles_endpoint_returns_all_bands():
    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/risk/profiles")
    assert resp.status_code == 200
    bands = resp.json()["bands"]
    assert len(bands) == len(RISK_BANDS)
    # Bands must be ordered by ascending max_dd (the classifier relies on it).
    dds = [b["max_dd"] for b in bands]
    assert dds == sorted(dds)


@pytest.mark.asyncio
async def test_portfolio_risk_endpoint_degrades_gracefully():
    """Returns 200 with the expected aggregate keys even with no vault data."""
    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/risk/portfolio")
    assert resp.status_code == 200
    data = resp.json()
    # Core aggregate fields should always be present (zeros when no data).
    for key in ("avg_sharpe", "worst_max_dd", "strategy_count", "strategies"):
        assert key in data, f"missing {key} in {list(data.keys())}"
    assert isinstance(data["strategies"], list)
