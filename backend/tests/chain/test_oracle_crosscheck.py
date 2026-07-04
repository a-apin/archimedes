"""Hermetic tests for the #775 secondary-source price cross-check.

Pins the load-bearing safety property: the cross-check is **asymmetric** — a
stale/missing/flaky yfinance NEVER blocks a healthy primary (only a confident
divergence between two healthy sources fails closed) — and is a no-op when the
primary is itself yfinance (not an independent source). No network: the single
yfinance read is mocked at the boundary.

Staleness fixtures (``_fresh_ts`` / ``_stale_ts`` / ``_boundary_ts``) compute bar
timestamps relative to wall-clock ``datetime.now(UTC)`` at call time (no frozen
clock) since ``_cross_check_secondary`` itself calls ``datetime.now(UTC)`` when
computing bar age — the offsets are large enough (minutes vs. days vs. the exact
cap) that this is stable regardless of test run time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from archimedes.chain.oracle_updater import DEFAULT_CROSSCHECK_MAX_STALENESS_SECONDS, OracleUpdater
from archimedes.models.asset import AssetPrice

NOW = datetime(2026, 6, 30, tzinfo=UTC)


def _price(symbol: str = "sSPY", usd: float = 500.0, source: str = "pyth_hermes") -> AssetPrice:
    return AssetPrice(symbol=symbol, price_usd=usd, timestamp=NOW, source=source)


def _updater(band_bps: int = 5000) -> OracleUpdater:
    u = OracleUpdater()
    u._crosscheck_band_bps = band_bps
    return u


def _fresh_ts() -> datetime:
    return datetime.now(UTC) - timedelta(minutes=5)


def _stale_ts() -> datetime:
    return datetime.now(UTC) - timedelta(days=5)


def _boundary_ts() -> datetime:
    # Just inside the cap (not exactly at it): the check computes age at
    # assertion time, a fraction of a second after this fixture is built, so an
    # exact-cap fixture would nondeterministically tip over the strict `>`
    # threshold under real wall-clock elapsed time. One second of slack keeps
    # this deterministically on the "not stale" side while still exercising the
    # boundary (mirrors the band-boundary test's "inclusive" framing above).
    return datetime.now(UTC) - timedelta(seconds=DEFAULT_CROSSCHECK_MAX_STALENESS_SECONDS - 1)


# ── no-op cases (nothing to cross-check) ──────────────────────────────────


async def test_disabled_band_is_noop():
    u = _updater(0)
    with patch.object(u, "_fetch_yfinance_single", AsyncMock(return_value=(1.0, _fresh_ts()))) as m:
        assert await u._cross_check_secondary(_price()) is None
    m.assert_not_called()  # disabled → never even fetches


async def test_yfinance_primary_is_noop():
    # When the primary IS yfinance there is no independent second source.
    u = _updater()
    with patch.object(u, "_fetch_yfinance_single", AsyncMock()) as m:
        assert await u._cross_check_secondary(_price(source="yfinance")) is None
    m.assert_not_called()


async def test_admin_source_is_noop():
    # An admin pin (ADMIN_PRICES_JSON last-resort operator override) must NOT be
    # second-guessed by the secondary guardrail — the whole point is to override
    # upstream. yfinance must not even be fetched.
    u = _updater()
    with patch.object(u, "_fetch_yfinance_single", AsyncMock()) as m:
        assert await u._cross_check_secondary(_price(source="admin")) is None
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
    with patch.object(u, "_fetch_yfinance_single", AsyncMock(return_value=(0.0, _fresh_ts()))):
        assert await u._cross_check_secondary(_price()) is None


# ── the actual guardrail ──────────────────────────────────────────────────


async def test_in_band_proceeds():
    u = _updater(5000)  # 50% band
    with patch.object(u, "_fetch_yfinance_single", AsyncMock(return_value=(510.0, _fresh_ts()))):  # ~2% off
        assert await u._cross_check_secondary(_price(usd=500.0)) is None


async def test_out_of_band_fails_closed():
    u = _updater(1000)  # 10% band
    with patch.object(u, "_fetch_yfinance_single", AsyncMock(return_value=(1000.0, _fresh_ts()))):  # 2x off
        reason = await u._cross_check_secondary(_price(usd=500.0))
    assert reason is not None
    assert "diverges" in reason and "failing closed" in reason


async def test_band_boundary_inclusive_proceeds():
    # Exactly at the band is allowed (strict > check). 500 vs 550 = 909bps < 1000.
    u = _updater(1000)
    with patch.object(u, "_fetch_yfinance_single", AsyncMock(return_value=(550.0, _fresh_ts()))):
        assert await u._cross_check_secondary(_price(usd=500.0)) is None


# ── secondary staleness window (#775 Phase 2) ─────────────────────────────


async def test_fresh_bar_in_band_proceeds():
    # Regression check: fresh bar timestamp + in-band deviation still proceeds
    # exactly like the pre-staleness-gate behavior.
    u = _updater(5000)
    with patch.object(u, "_fetch_yfinance_single", AsyncMock(return_value=(510.0, _fresh_ts()))):
        assert await u._cross_check_secondary(_price(usd=500.0)) is None


async def test_stale_bar_in_band_logs_stale_not_ok(caplog):
    # A stale secondary is fail-open (like a missing one), but the caller can't
    # observe a distinguishing string from a bare None return — this exercises
    # the log path instead by asserting the outcome (proceeds) while confirming
    # via caplog that the reasoning says "stale", not a normal pass-through.
    import logging

    u = _updater(5000)
    with (
        caplog.at_level(logging.INFO, logger="archimedes.chain.oracle_updater"),
        patch.object(u, "_fetch_yfinance_single", AsyncMock(return_value=(510.0, _stale_ts()))),
    ):
        assert await u._cross_check_secondary(_price(usd=500.0)) is None
    assert any("stale" in rec.message for rec in caplog.records)
    assert not any("cross-check OK" in rec.message for rec in caplog.records)


async def test_stale_bar_out_of_band_still_proceeds():
    # KEY regression proof: staleness must short-circuit BEFORE the divergence
    # check, so a stale-and-wildly-different reading does NOT incorrectly fail
    # closed. Without the staleness gate this divergence (2x) would fail closed
    # under a 1000bps band.
    u = _updater(1000)
    with patch.object(u, "_fetch_yfinance_single", AsyncMock(return_value=(1000.0, _stale_ts()))):
        assert await u._cross_check_secondary(_price(usd=500.0)) is None


async def test_staleness_boundary_at_cap_treated_as_ok_not_stale(caplog):
    # Mirrors the band-boundary convention above (strict `>` check): at/under
    # the staleness cap is NOT stale, so it falls through to the normal
    # in-band-proceeds path (not the staleness-proceeds path) and logs the
    # ordinary "cross-check OK" line rather than a staleness reason.
    import logging

    u = _updater(5000)
    with (
        caplog.at_level(logging.DEBUG, logger="archimedes.chain.oracle_updater"),
        patch.object(u, "_fetch_yfinance_single", AsyncMock(return_value=(510.0, _boundary_ts()))),
    ):
        assert await u._cross_check_secondary(_price(usd=500.0)) is None
    assert any("cross-check OK" in rec.message for rec in caplog.records)
    assert not any("stale" in rec.message for rec in caplog.records)
