"""Hermetic tests for the #775 secondary-source price cross-check.

Pins the load-bearing safety property: the cross-check is **asymmetric** — a
stale/missing/flaky yfinance NEVER blocks a healthy primary (only a confident
divergence between two healthy sources fails closed) — and is a no-op when the
primary is itself yfinance (not an independent source). No network: the single
yfinance read is mocked at the boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from archimedes.chain.oracle_updater import OracleUpdater
from archimedes.models.asset import AssetPrice

NOW = datetime(2026, 6, 30, tzinfo=UTC)


def _price(symbol: str = "sSPY", usd: float = 500.0, source: str = "pyth_hermes") -> AssetPrice:
    return AssetPrice(symbol=symbol, price_usd=usd, timestamp=NOW, source=source)


def _updater(band_bps: int = 5000) -> OracleUpdater:
    u = OracleUpdater()
    u._crosscheck_band_bps = band_bps
    return u


# ── no-op cases (nothing to cross-check) ──────────────────────────────────


async def test_disabled_band_is_noop():
    u = _updater(0)
    with patch.object(u, "_fetch_yfinance_single", AsyncMock(return_value=1.0)) as m:
        assert await u._cross_check_secondary(_price()) is None
    m.assert_not_called()  # disabled → never even fetches


async def test_yfinance_primary_is_noop():
    # When the primary IS yfinance there is no independent second source.
    u = _updater()
    with patch.object(u, "_fetch_yfinance_single", AsyncMock()) as m:
        assert await u._cross_check_secondary(_price(source="yfinance")) is None
    m.assert_not_called()


async def test_unmapped_symbol_is_noop():
    # A symbol with no yfinance ticker can't be cross-checked → proceed.
    u = _updater()
    with patch.object(u, "_fetch_yfinance_single", AsyncMock()) as m:
        assert await u._cross_check_secondary(_price(symbol="sNOTREAL")) is None
    m.assert_not_called()


# ── the asymmetry (the safety property) ───────────────────────────────────


async def test_asymmetric_yfinance_exception_proceeds():
    u = _updater()
    with patch.object(u, "_fetch_yfinance_single", AsyncMock(side_effect=RuntimeError("yfinance down"))):
        # A yfinance OUTAGE must NOT halt a healthy primary.
        assert await u._cross_check_secondary(_price()) is None


async def test_asymmetric_yfinance_none_proceeds():
    u = _updater()
    with patch.object(u, "_fetch_yfinance_single", AsyncMock(return_value=None)):
        assert await u._cross_check_secondary(_price()) is None


async def test_asymmetric_yfinance_nonpositive_proceeds():
    u = _updater()
    with patch.object(u, "_fetch_yfinance_single", AsyncMock(return_value=0.0)):
        assert await u._cross_check_secondary(_price()) is None


# ── the actual guardrail ──────────────────────────────────────────────────


async def test_in_band_proceeds():
    u = _updater(5000)  # 50% band
    with patch.object(u, "_fetch_yfinance_single", AsyncMock(return_value=510.0)):  # ~2% off
        assert await u._cross_check_secondary(_price(usd=500.0)) is None


async def test_out_of_band_fails_closed():
    u = _updater(1000)  # 10% band
    with patch.object(u, "_fetch_yfinance_single", AsyncMock(return_value=1000.0)):  # 2x off
        reason = await u._cross_check_secondary(_price(usd=500.0))
    assert reason is not None
    assert "diverges" in reason and "failing closed" in reason


async def test_band_boundary_inclusive_proceeds():
    # Exactly at the band is allowed (strict > check). 500 vs 550 = 909bps < 1000.
    u = _updater(1000)
    with patch.object(u, "_fetch_yfinance_single", AsyncMock(return_value=550.0)):
        assert await u._cross_check_secondary(_price(usd=500.0)) is None
