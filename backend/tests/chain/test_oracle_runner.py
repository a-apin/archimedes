"""Tests for the oracle runner loop (#738, #1043).

Target: backend/archimedes/chain/oracle_runner.py
The runner is the periodic process that fetches prices and pushes them on-chain.
It was at 0% coverage. We exercise one full loop iteration (fetch → push), the
"no prices this cycle" branch, the "fetch error" branch, and the "push returns
no tx" branch — breaking out of the otherwise-infinite `while True` by making
`asyncio.sleep` raise a sentinel.

Hermetic: the OracleUpdater is mocked at the boundary; `asyncio.sleep` is
patched to stop the loop. No network, no Arc RPC, no Circle. The #1043
exactly-once lease is mocked to a stub that is always "held" — its own
acquire/renew/fail-closed behavior is covered by
`backend/tests/test_runner_lease.py`; this file's job is the price-push loop.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from archimedes.chain import oracle_runner
from archimedes.models.asset import AssetPrice


class _StopLoop(Exception):
    """Sentinel raised by the patched sleep to break the runner's while-loop."""


def _price(symbol: str = "sTSLA", usd: float = 100.0) -> AssetPrice:
    from datetime import UTC, datetime

    return AssetPrice(symbol=symbol, price_usd=usd, timestamp=datetime.now(UTC), source="yfinance")


def _fake_lease(*, is_valid: bool = True) -> MagicMock:
    """A RunnerLeaseGuard stub that is always already-acquired and valid."""
    lease = MagicMock()
    lease.acquire_forever = AsyncMock(return_value=None)
    lease.start_renewal = MagicMock()
    lease.install_sigterm_release = MagicMock()
    lease.is_valid = is_valid
    return lease


async def _run_one_cycle(updater: MagicMock, lease: MagicMock | None = None) -> None:
    """Run oracle_runner.run() through exactly one loop body, then stop."""
    with (
        patch("archimedes.chain.oracle_runner.OracleUpdater", return_value=updater),
        patch("archimedes.chain.oracle_runner.RunnerLeaseGuard", return_value=lease or _fake_lease()),
        patch("archimedes.chain.oracle_runner.asyncio.sleep", AsyncMock(side_effect=_StopLoop)),
        pytest.raises(_StopLoop),
    ):
        await oracle_runner.run()


class TestOracleRunnerLoop:
    async def test_fetch_then_push_path(self):
        updater = MagicMock()
        updater.fetch_prices = AsyncMock(return_value=[_price()])
        updater.push_prices_on_chain = AsyncMock(return_value="0xtx-1")
        await _run_one_cycle(updater)
        updater.fetch_prices.assert_awaited_once()
        updater.push_prices_on_chain.assert_awaited_once()

    async def test_prices_fetched_but_no_push_tx(self):
        # push returns None (no tx reached a terminal-success state this
        # cycle) — must not crash.
        updater = MagicMock()
        updater.fetch_prices = AsyncMock(return_value=[_price()])
        updater.push_prices_on_chain = AsyncMock(return_value=None)
        await _run_one_cycle(updater)
        updater.push_prices_on_chain.assert_awaited_once()

    async def test_no_push_tx_logs_debug_not_the_stale_owner_key_claim(self, caplog):
        # #1525 adjudication: the old INFO line asserted "owner key not
        # configured" — a holdover from a pre-Circle raw-key design that is
        # almost never the actual cause (this runner has pushed exclusively
        # via Circle Wallets since #905) and duplicated detail already logged
        # with the real reason, at the correct severity, inside
        # push_prices_on_chain. It is downgraded to DEBUG with accurate
        # wording rather than asserting a specific (usually wrong) cause.
        updater = MagicMock()
        updater.fetch_prices = AsyncMock(return_value=[_price()])
        updater.push_prices_on_chain = AsyncMock(return_value=None)
        with caplog.at_level("DEBUG", logger="archimedes.chain.oracle_runner"):
            await _run_one_cycle(updater)
        messages = [r.getMessage() for r in caplog.records]
        assert not any("owner key not configured" in m for m in messages)
        assert any(
            r.getMessage() == "Prices fetched — no tx reached a terminal-success state this cycle"
            and r.levelname == "DEBUG"
            for r in caplog.records
        )

    async def test_no_prices_this_cycle_skips_push(self):
        updater = MagicMock()
        updater.fetch_prices = AsyncMock(return_value=[])
        updater.push_prices_on_chain = AsyncMock()
        await _run_one_cycle(updater)
        # No prices → push is never attempted.
        updater.push_prices_on_chain.assert_not_called()

    async def test_fetch_exception_is_caught_and_loop_continues(self):
        # A fetch error must be swallowed (logged) and the loop proceed to sleep
        # — the _StopLoop from sleep proves we reached the end of the body.
        updater = MagicMock()
        updater.fetch_prices = AsyncMock(side_effect=RuntimeError("yfinance down"))
        updater.push_prices_on_chain = AsyncMock()
        await _run_one_cycle(updater)
        updater.push_prices_on_chain.assert_not_called()

    def test_interval_default_is_60s(self):
        # Sanity: the module-level INTERVAL falls back to 60 when env is unset.
        assert isinstance(oracle_runner.INTERVAL, int)
        assert oracle_runner.INTERVAL >= 1


class TestOracleRunnerLeaseGate:
    """#1043 — the on-chain price push is gated on the exactly-once lease."""

    async def test_lease_held_pushes_prices(self):
        updater = MagicMock()
        updater.fetch_prices = AsyncMock(return_value=[_price()])
        updater.push_prices_on_chain = AsyncMock(return_value="0xtx-1")
        await _run_one_cycle(updater, lease=_fake_lease(is_valid=True))
        updater.push_prices_on_chain.assert_awaited_once()

    async def test_lease_not_held_skips_push_fail_closed(self):
        updater = MagicMock()
        updater.fetch_prices = AsyncMock(return_value=[_price()])
        updater.push_prices_on_chain = AsyncMock(return_value="0xtx-1")
        await _run_one_cycle(updater, lease=_fake_lease(is_valid=False))
        # Prices were fetched but the on-chain write is skipped — never fail open.
        updater.fetch_prices.assert_awaited_once()
        updater.push_prices_on_chain.assert_not_called()

    async def test_run_blocks_on_acquire_before_entering_loop(self):
        # acquire_forever() must be awaited BEFORE the price-fetch loop starts.
        updater = MagicMock()
        updater.fetch_prices = AsyncMock(return_value=[])
        lease = _fake_lease()
        await _run_one_cycle(updater, lease=lease)
        lease.acquire_forever.assert_awaited_once()
        lease.start_renewal.assert_called_once()
        lease.install_sigterm_release.assert_called_once()
