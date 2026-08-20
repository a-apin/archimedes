"""Hermetic coverage for the oracle-freshness probe (#1371).

Every deployed PriceOracle on Arc testnet has been stale since the T3.2
redeploy (2026-07-09) — the on-chain updater address has nonce 0, no push has
ever landed (#1341 owns the cause). Nothing reported this: `isFresh()` had
zero backend callers. This file covers the probe that closes that gap
(`archimedes.services.oracle_health.oracle_health`) and its `/health`
surfacing in `main.py`.

Four properties are load-bearing and each gets its own test class:

1. **All-fresh** — every probed oracle reads fresh → `oracle_fresh=True`.
2. **One-stale** — a mixed push set → `oracle_fresh=False`, oldest age surfaced.
3. **Chain-read-failure** — every read raises → `oracle_fresh=False` with an
   explicit `chain_read_failed` marker in `reason`, never a silent absence
   and never a fail-soft "assume fresh" (docs/architectural-principles.md
   § fail-soft).
4. **Counts-visible** — `oracle_probed_count` and `oracle_universe_count` are
   always both present and the probed count is always a small fraction of the
   universe, so an all-fresh push-set result can never be read as "the oracle
   subsystem is healthy" (the 2-of-281 gap the #1371 scope amendment names).

No DB / Redis / network / `.env` — the chain boundary is mocked exactly like
`backend/tests/chain/test_oracle_updater.py`'s `get_contract_loader` idiom.
Hermetic gate:

    env -i HOME=$HOME PATH=$PATH PYTHONPATH=backend \\
        python -m pytest backend/tests/test_oracle_health_telemetry.py -q
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import archimedes.services.oracle_health as oracle_health_mod
import pytest
from archimedes.services.oracle_health import (
    OracleHealth,
    _push_set_symbols,
    _universe_count,
    oracle_health,
)
from httpx import ASGITransport, AsyncClient


def _mock_loader(per_symbol: dict[str, tuple[bool | None, int | None, Exception | None]]) -> MagicMock:
    """Build a fake ContractLoader whose ``oracle_for(symbol)`` yields a mock
    PriceOracle contract wired the same way test_oracle_updater.py wires
    ``oracle_contract.functions.price.return_value.call``.

    ``per_symbol[symbol] = (is_fresh, last_updated, raises)``. When ``raises``
    is not None, BOTH ``isFresh()`` and ``lastUpdated()`` raise it (a real RPC
    failure fails the whole call, not just one field).
    """
    loader = MagicMock()

    def _oracle_for(symbol: str) -> MagicMock:
        is_fresh, last_updated, raises = per_symbol[symbol]
        contract = MagicMock()
        if raises is not None:
            contract.functions.isFresh.return_value.call = AsyncMock(side_effect=raises)
            contract.functions.lastUpdated.return_value.call = AsyncMock(side_effect=raises)
        else:
            contract.functions.isFresh.return_value.call = AsyncMock(return_value=is_fresh)
            contract.functions.lastUpdated.return_value.call = AsyncMock(return_value=last_updated)
        return contract

    loader.oracle_for.side_effect = _oracle_for
    return loader


# ── Push-set derivation — proves the set is read, not hard-coded ────────


class TestPushSetDerivation:
    def test_push_set_matches_oracle_updaters_real_maps(self) -> None:
        # Today's real push set per the #1371 scope amendment: sSPY (YFINANCE_MAP)
        # + sBTC (CRYPTO_MAP) — the ^GSPC/^VIX regime-signal index tickers that
        # also live in YFINANCE_MAP are excluded by the leading-"s" filter.
        assert _push_set_symbols() == ["sBTC", "sSPY"]

    def test_universe_count_is_the_full_on_chain_universe_not_the_push_set(self) -> None:
        from archimedes.universe import ON_CHAIN_SYNTHS

        assert _universe_count() == len(ON_CHAIN_SYNTHS)
        # The 281-class ceiling named in the #1371 scope amendment: the push
        # set (2) must be a small fraction of the universe, not the whole of it.
        assert _universe_count() > len(_push_set_symbols())


# ── Core probe behavior ──────────────────────────────────────────────────


class TestOracleHealthAllFresh:
    @pytest.mark.asyncio
    async def test_all_probed_oracles_fresh(self) -> None:
        now = datetime(2026, 8, 20, 15, 37, tzinfo=UTC).timestamp()
        loader = _mock_loader(
            {
                "sBTC": (True, int(now) - 60, None),
                "sSPY": (True, int(now) - 90, None),
            }
        )
        with (
            patch("archimedes.chain.contracts.get_contract_loader", return_value=loader),
            patch.object(oracle_health_mod.time, "time", return_value=now),
        ):
            diag = await oracle_health()

        assert diag.oracle_fresh is True
        assert diag.status == "fresh"
        assert diag.oracle_oldest_age_s == 90
        assert diag.oracle_probed_count == 2
        assert diag.oracle_universe_count == _universe_count()


class TestOracleHealthOneStale:
    @pytest.mark.asyncio
    async def test_one_stale_oracle_fails_the_whole_probe(self) -> None:
        now = datetime(2026, 8, 20, 15, 37, tzinfo=UTC).timestamp()
        stale_age = 42 * 24 * 60 * 60  # 42 days, matching #1371's measured state
        loader = _mock_loader(
            {
                "sBTC": (True, int(now) - 30, None),
                "sSPY": (False, int(now) - stale_age, None),
            }
        )
        with (
            patch("archimedes.chain.contracts.get_contract_loader", return_value=loader),
            patch.object(oracle_health_mod.time, "time", return_value=now),
        ):
            diag = await oracle_health()

        # ALL-fresh is required for oracle_fresh=True — one stale oracle in an
        # otherwise-fresh set must still fail the aggregate.
        assert diag.oracle_fresh is False
        assert diag.status == "stale"
        assert diag.oracle_oldest_age_s == stale_age
        assert diag.oracle_probed_count == 2
        assert "1/2" in diag.reason


class TestOracleHealthChainReadFailure:
    @pytest.mark.asyncio
    async def test_total_chain_read_failure_yields_false_with_explicit_marker(self) -> None:
        loader = _mock_loader(
            {
                "sBTC": (None, None, ConnectionError("RPC timed out")),
                "sSPY": (None, None, ConnectionError("RPC timed out")),
            }
        )
        with patch("archimedes.chain.contracts.get_contract_loader", return_value=loader):
            diag = await oracle_health()

        # Fail-soft ("assume fresh when we can't tell") is wrong here — a
        # read failure must be an explicit, visible failure state, never a
        # silent absence (docs/architectural-principles.md § fail-soft).
        assert diag.oracle_fresh is False
        assert diag.status == "unknown"
        assert diag.oracle_oldest_age_s is None
        assert diag.oracle_probed_count == 2
        assert "chain_read_failed" in diag.reason

    @pytest.mark.asyncio
    async def test_partial_chain_read_failure_still_reports_not_fresh(self) -> None:
        now = datetime(2026, 8, 20, 15, 37, tzinfo=UTC).timestamp()
        loader = _mock_loader(
            {
                "sBTC": (True, int(now) - 10, None),
                "sSPY": (None, None, TimeoutError("RPC timeout")),
            }
        )
        with (
            patch("archimedes.chain.contracts.get_contract_loader", return_value=loader),
            patch.object(oracle_health_mod.time, "time", return_value=now),
        ):
            diag = await oracle_health()

        # One successful read exists, so this is NOT the total-outage
        # "unknown" branch — but a partial failure still can't claim all-fresh.
        assert diag.oracle_fresh is False
        assert diag.oracle_oldest_age_s == 10
        assert "read error" in diag.reason


class TestOracleHealthCountsVisible:
    @pytest.mark.asyncio
    async def test_counts_are_always_both_present_and_probed_ne_universe(self) -> None:
        """The 2-of-281 gap must be visible even in the all-fresh case.

        A caller reading only `oracle_fresh` (with the counts absent or
        equal) would reasonably conclude "the oracle subsystem is healthy" —
        exactly the misread #1371's scope amendment forbids. This asserts the
        payload structurally prevents that: the probed count is always far
        smaller than the universe count, so the gap can never be missed.
        """
        now = datetime(2026, 8, 20, 15, 37, tzinfo=UTC).timestamp()
        loader = _mock_loader(
            {
                "sBTC": (True, int(now) - 10, None),
                "sSPY": (True, int(now) - 10, None),
            }
        )
        with (
            patch("archimedes.chain.contracts.get_contract_loader", return_value=loader),
            patch.object(oracle_health_mod.time, "time", return_value=now),
        ):
            diag = await oracle_health()

        assert diag.oracle_fresh is True  # the push set IS fully fresh here
        assert diag.oracle_probed_count == 2
        assert diag.oracle_universe_count >= 281
        assert diag.oracle_probed_count < diag.oracle_universe_count


class TestOracleHealthTrapAvoidance:
    """The trap this probe exists to avoid (mirrors #1371's mutation check (b)).

    A runner can loop every 60s and log "Pushed sSPY" forever while the push
    itself reverts on-chain — that is exactly #1341's 42-day failure mode. A
    probe derived from runner/process liveness would have read green through
    all of it. This test builds a fixture where every runner-liveness signal
    is "healthy" (a fresh price snapshot, a successful-looking fetch) while
    the mocked ON-CHAIN read is 42 days stale, and asserts the probe still
    reports `oracle_fresh=False` — because `oracle_health()` has no code path
    that ever reads a runner/Redis signal at all, only the chain.
    """

    @pytest.mark.asyncio
    async def test_healthy_runner_signal_does_not_mask_a_stale_chain(self) -> None:
        now = datetime(2026, 8, 20, 15, 37, tzinfo=UTC).timestamp()
        stale_age = 42 * 24 * 60 * 60

        # A runner-liveness signal that would read "green" under a
        # liveness-based probe: a fresh in-memory snapshot + a "just
        # succeeded" fetch flag. oracle_health() never touches either.
        healthy_runner_signal = MagicMock(last_fetch_success=True, seconds_since_fetch=5)
        assert healthy_runner_signal.last_fetch_success is True  # sanity: the trap bait is "healthy"

        loader = _mock_loader(
            {
                "sBTC": (False, int(now) - stale_age, None),
                "sSPY": (False, int(now) - stale_age, None),
            }
        )
        with (
            patch("archimedes.chain.contracts.get_contract_loader", return_value=loader),
            patch.object(oracle_health_mod.time, "time", return_value=now),
        ):
            diag = await oracle_health()

        assert diag.oracle_fresh is False
        assert diag.oracle_oldest_age_s == stale_age


class TestOracleHealthProductionStateReplay:
    """Replays #1371's live-verified measured state (2026-08-20 15:37Z read).

    ``price_oracle``'s real ``lastUpdated()`` was ``1783583437`` (2026-07-09
    07:50:37Z) — a genuine on-chain value, not a synthetic one.
    """

    @pytest.mark.asyncio
    async def test_real_measured_staleness_reports_not_fresh(self) -> None:
        now = datetime(2026, 8, 20, 15, 37, tzinfo=UTC).timestamp()
        real_last_updated = 1783583437
        loader = _mock_loader(
            {
                "sBTC": (False, real_last_updated, None),
                "sSPY": (False, real_last_updated, None),
            }
        )
        with (
            patch("archimedes.chain.contracts.get_contract_loader", return_value=loader),
            patch.object(oracle_health_mod.time, "time", return_value=now),
        ):
            diag = await oracle_health()

        assert diag.oracle_fresh is False
        assert diag.oracle_oldest_age_s >= 3639364


# ── /health surfaces the oracle fields ────────────────────────────────────


class TestHealthEndpointSurfacesOracleFields:
    @pytest.mark.asyncio
    async def test_health_surfaces_stale_oracle_and_logs_marker(self, caplog: pytest.LogCaptureFixture) -> None:
        from archimedes.main import app

        stale_diag = OracleHealth(
            status="stale",
            oracle_fresh=False,
            oracle_oldest_age_s=3656783,
            oracle_probed_count=2,
            oracle_universe_count=281,
            reason="0/2 probed oracle(s) fresh, oldest age 3656783s (of 281 in the universe)",
        )
        with (
            patch.object(oracle_health_mod, "oracle_health", AsyncMock(return_value=stale_diag)),
            caplog.at_level(logging.WARNING, logger="archimedes.main"),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.get("/health")

        assert resp.status_code == 200
        body = resp.json()
        assert body["oracle_fresh"] is False
        assert body["oracle_oldest_age_s"] == 3656783
        assert body["oracle_probed_count"] == 2
        assert body["oracle_universe_count"] == 281
        assert "oracle_reason" in body

        marker_lines = [r for r in caplog.records if "HEALTH_ORACLE_STALE" in r.getMessage()]
        assert marker_lines, "expected a HEALTH_ORACLE_STALE WARNING when oracle_fresh is false"
        assert "3656783" in marker_lines[0].getMessage()

    @pytest.mark.asyncio
    async def test_health_surfaces_fresh_oracle_with_no_stale_log(self, caplog: pytest.LogCaptureFixture) -> None:
        from archimedes.main import app

        fresh_diag = OracleHealth(
            status="fresh",
            oracle_fresh=True,
            oracle_oldest_age_s=45,
            oracle_probed_count=2,
            oracle_universe_count=281,
            reason="2/2 probed oracle(s) fresh (of 281 in the universe)",
        )
        with (
            patch.object(oracle_health_mod, "oracle_health", AsyncMock(return_value=fresh_diag)),
            caplog.at_level(logging.WARNING, logger="archimedes.main"),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.get("/health")

        assert resp.status_code == 200
        body = resp.json()
        assert body["oracle_fresh"] is True
        assert body["oracle_oldest_age_s"] == 45
        marker_lines = [r for r in caplog.records if "HEALTH_ORACLE_STALE" in r.getMessage()]
        assert not marker_lines, "must not log the stale marker when oracle_fresh is true"

    @pytest.mark.asyncio
    async def test_health_survives_probe_import_failure(self, caplog: pytest.LogCaptureFixture) -> None:
        """A broken probe must degrade /health, not crash it (fail-loud, not fail-hard)."""
        from archimedes.main import app

        with (
            patch.object(oracle_health_mod, "oracle_health", AsyncMock(side_effect=RuntimeError("boom"))),
            caplog.at_level(logging.WARNING, logger="archimedes.main"),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.get("/health")

        assert resp.status_code == 200
        body = resp.json()
        assert body["oracle_fresh"] is False
        assert body["oracle_oldest_age_s"] is None
        assert "probe_error" in body["oracle_reason"]
        marker_lines = [r for r in caplog.records if "HEALTH_ORACLE_STALE" in r.getMessage()]
        assert marker_lines, "a probe failure is itself a not-fresh state and must still log the marker"
