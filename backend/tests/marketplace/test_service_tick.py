"""Tests for MarketService.tick() — non-dry-run engine path."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archimedes.marketplace.service import MarketService, Publisher, Subscriber


@pytest.fixture
def market():
    svc = MarketService(interval_seconds=9999, dry_run=False)
    # Mock the heavy dependencies
    svc.executor = MagicMock()
    svc.executor.read_portfolio = AsyncMock(return_value={"usdc": 10000})
    svc.executor.execute_trades = AsyncMock()
    svc.signer = MagicMock()
    svc.signer.is_configured = False
    # Mock the chain contract loader
    svc.loader = MagicMock()
    # Mock state
    svc.state = MagicMock()
    svc.state.try_acquire_leader = AsyncMock(return_value="test-token")
    svc.state.renew_leader = AsyncMock()
    svc.state.release_leader = AsyncMock()
    svc.state.append_event = AsyncMock()
    svc.state.save_subscribers = AsyncMock()
    svc.state.load_subscribers = AsyncMock(return_value={})
    return svc


def _dummy_trades():
    from archimedes.models.portfolio import TradeDirection, TradeOrder
    return [
        TradeOrder(
            symbol="ETH",
            token_address="",
            direction=TradeDirection.BUY,
            amount=100.0,
            estimated_usdc_value=100.0,
        ),
    ]


@pytest.mark.asyncio
async def test_tick_produces_trades_and_verifies_payment(market: MarketService):
    """The economic core: _evaluate returns weights, compute_trades returns
    non-empty trades, payment is verified, subscriber vault is traded."""
    with (
        patch.object(market, "_evaluate", AsyncMock(return_value={"ETH": 0.5})),
        patch("archimedes.marketplace.service.compute_trades", return_value=_dummy_trades()),
    ):
        market.publishers["strat_a"] = Publisher(
            strategy_id="strat_a",
            pool_id="0x" + "aa" * 32,
            vault_address="0xpublisher_vault",
            creator_wallet="0xpublisher",
        )
        market.publishers["strat_a"].subscribers["sub_1"] = Subscriber(
            sub_id="0x" + "bb" * 32,
            pool_id="0x" + "cc" * 32,
            vault_address="0xsub_vault",
            ephemeral_wallet="0xephemeral",
            subscriber_wallet="0xsubscriber",
            active=True,
        )

        with patch.object(market, "_verify_payment", AsyncMock(return_value=True)) as mock_verify:
            await market.tick("strat_a")

            # _verify_payment was called with the right sub_id
            mock_verify.assert_awaited_once_with("0x" + "bb" * 32)

            # execute_trades was called for publisher vault
            market.executor.execute_trades.assert_any_call("0xpublisher_vault", _dummy_trades())

            # state.save_subscribers was called
            market.state.save_subscribers.assert_awaited()


@pytest.mark.asyncio
async def test_tick_marks_subscriber_inactive_on_payment_failure(market: MarketService):
    """When payment verification fails, the subscriber is marked inactive and no
    trades are executed for them."""
    with (
        patch.object(market, "_evaluate", AsyncMock(return_value={"ETH": 0.5})),
        patch("archimedes.marketplace.service.compute_trades", return_value=_dummy_trades()),
    ):
        market.publishers["strat_b"] = Publisher(
            strategy_id="strat_b",
            pool_id="0x" + "dd" * 32,
            vault_address="0xpublisher_vault",
            creator_wallet="0xpublisher",
        )
        market.publishers["strat_b"].subscribers["sub_2"] = Subscriber(
            sub_id="0x" + "ee" * 32,
            pool_id="0x" + "ff" * 32,
            vault_address="0xsub_vault",
            ephemeral_wallet="0xephemeral",
            subscriber_wallet="0xsubscriber",
            active=True,
        )

        with patch.object(market, "_verify_payment", AsyncMock(return_value=False)):
            await market.tick("strat_b")

            # subscriber marked inactive
            assert market.publishers["strat_b"].subscribers["sub_2"].active is False

            # append_event was called with halt event
            halt_calls = [
                c for c in market.state.append_event.await_args_list
                if c[0][1].get("type") == "halt"
            ]
            assert len(halt_calls) >= 1

            # verify halt event has the new reason
            assert halt_calls[0][0][1].get("reason") == "payment_required"

            # execute_trades NOT called for subscriber vault
            sub_calls = [
                c for c in market.executor.execute_trades.call_args_list
                if c[0][0] == "0xsub_vault"
            ]
            assert len(sub_calls) == 0


@pytest.mark.asyncio
async def test_tick_no_trades_skips_payment(market: MarketService):
    """When compute_trades returns empty, no payment verification or execution happens."""
    with (
        patch.object(market, "_evaluate", AsyncMock(return_value={})),
        patch("archimedes.marketplace.service.compute_trades", return_value=[]),
    ):
        market.publishers["strat_c"] = Publisher(
            strategy_id="strat_c",
            pool_id="0x" + "11" * 32,
            vault_address="0xpublisher_vault",
            creator_wallet="0xpublisher",
        )

        with patch.object(market, "_verify_payment", AsyncMock()) as mock_verify:
            await market.tick("strat_c")
            mock_verify.assert_not_called()
            market.executor.execute_trades.assert_not_called()


# ── H3 — Liability ledger (charge-succeeds / mirror-fails) ─────────────


@pytest.mark.asyncio
async def test_tick_records_liability_when_mirror_fails(market: MarketService):
    """When payment succeeds but the subscriber mirror trade fails, a liability
    is recorded for that subscriber."""
    with (
        patch.object(market, "_evaluate", AsyncMock(return_value={"ETH": 0.5})),
        patch("archimedes.marketplace.service.compute_trades", return_value=_dummy_trades()),
    ):
        market.publishers["strat_d"] = Publisher(
            strategy_id="strat_d",
            pool_id="0x" + "11" * 32,
            vault_address="0xpublisher_vault",
            creator_wallet="0xpublisher",
        )
        market.publishers["strat_d"].subscribers["sub_3"] = Subscriber(
            sub_id="0x" + "22" * 32,
            pool_id="0x" + "33" * 32,
            vault_address="0xsub_vault",
            ephemeral_wallet="0xephemeral",
            subscriber_wallet="0xsubscriber",
            active=True,
        )

        # Payment succeeds but mirror (apply) fails
        with (
            patch.object(market, "_verify_payment", AsyncMock(return_value=True)),
            patch.object(market, "_record_liability", AsyncMock()) as mock_record,
            patch.object(market, "_apply_to_subscriber", AsyncMock(return_value=False)),
        ):
            await market.tick("strat_d")

            # Liability was recorded for the subscriber
            mock_record.assert_awaited_once()
            args = mock_record.await_args.args
            assert args[0].sub_id == "0x" + "22" * 32
            assert args[1] == "strat_d"
            assert args[2] is not None  # tick_id
            assert args[3] == len(_dummy_trades())  # action_count


@pytest.mark.asyncio
async def test_tick_no_liability_when_mirror_succeeds(market: MarketService):
    """When mirror succeeds, no liability is recorded."""
    with (
        patch.object(market, "_evaluate", AsyncMock(return_value={"ETH": 0.5})),
        patch("archimedes.marketplace.service.compute_trades", return_value=_dummy_trades()),
    ):
        market.publishers["strat_e"] = Publisher(
            strategy_id="strat_e",
            pool_id="0x" + "44" * 32,
            vault_address="0xpublisher_vault",
            creator_wallet="0xpublisher",
        )
        market.publishers["strat_e"].subscribers["sub_4"] = Subscriber(
            sub_id="0x" + "55" * 32,
            pool_id="0x" + "66" * 32,
            vault_address="0xsub_vault",
            ephemeral_wallet="0xephemeral",
            subscriber_wallet="0xsubscriber",
            active=True,
        )

        with (
            patch.object(market, "_verify_payment", AsyncMock(return_value=True)),
            patch.object(market, "_record_liability", AsyncMock()) as mock_record,
            patch.object(market, "_apply_to_subscriber", AsyncMock(return_value=True)),
        ):
            await market.tick("strat_e")

            mock_record.assert_not_called()


@pytest.mark.asyncio
async def test_tick_no_liability_when_payment_fails(market: MarketService):
    """When payment fails, the subscriber is skipped and no liability is recorded
    (the charge itself didn't succeed, so no liability arises)."""
    with (
        patch.object(market, "_evaluate", AsyncMock(return_value={"ETH": 0.5})),
        patch("archimedes.marketplace.service.compute_trades", return_value=_dummy_trades()),
    ):
        market.publishers["strat_f"] = Publisher(
            strategy_id="strat_f",
            pool_id="0x" + "77" * 32,
            vault_address="0xpublisher_vault",
            creator_wallet="0xpublisher",
        )
        market.publishers["strat_f"].subscribers["sub_5"] = Subscriber(
            sub_id="0x" + "88" * 32,
            pool_id="0x" + "99" * 32,
            vault_address="0xsub_vault",
            ephemeral_wallet="0xephemeral",
            subscriber_wallet="0xsubscriber",
            active=True,
        )

        with (
            patch.object(market, "_verify_payment", AsyncMock(return_value=False)),
            patch.object(market, "_record_liability", AsyncMock()) as mock_record,
        ):
            await market.tick("strat_f")

            mock_record.assert_not_called()


@pytest.mark.asyncio
async def test_record_liability_best_effort(market: MarketService):
    """``_record_liability`` is best-effort: a DB failure logs but does not raise
    and does not emit an event (since the DB write failed first)."""
    sub = Subscriber(
        sub_id="0x" + "aa" * 32,
        pool_id="0x" + "bb" * 32,
        vault_address="0xsub_vault",
        ephemeral_wallet="0xephemeral",
        subscriber_wallet="0xsubscriber",
        active=True,
    )

    # Count events before
    before_count = market.state.append_event.await_count

    with patch("archimedes.marketplace.service.get_session", side_effect=RuntimeError("DB down")):
        # Must not raise despite DB failure
        await market._record_liability(sub, "strat_g", "tick_1", 3)

    # No new event was emitted (DB failure was caught before append_event)
    assert market.state.append_event.await_count == before_count


# ── TASK 17 — Leader lock: per-strategy isolation + explicit release + renewal ─


@pytest.mark.asyncio
async def test_tick_releases_leader_lock_in_finally(market: MarketService):
    """Tick releases the per-strategy leader lock in ``finally`` after a
    successful run (C3, explicit release)."""
    market.publishers["strat_release"] = Publisher(
        strategy_id="strat_release",
        pool_id="0x" + "aa" * 32,
        vault_address="0xpub",
        creator_wallet="0xcreator",
    )
    with (
        patch.object(market, "_evaluate", AsyncMock(return_value={"ETH": 0.5})),
        patch("archimedes.marketplace.service.compute_trades", return_value=[]),
    ):
        await market.tick("strat_release", leader_token="t1")

    # release_leader was called exactly once with the strategy_id and token
    market.state.release_leader.assert_awaited_once_with("strat_release", token="t1")


@pytest.mark.asyncio
async def test_tick_releases_leader_lock_on_exception(market: MarketService):
    """Tick releases the per-strategy lock even when tick fails midway, so
    the next interval re-acquires cleanly regardless of TTL."""
    market.publishers["strat_err"] = Publisher(
        strategy_id="strat_err",
        pool_id="0x" + "bb" * 32,
        vault_address="0xpub",
        creator_wallet="0xcreator",
    )
    with patch.object(market, "_evaluate", AsyncMock(side_effect=RuntimeError("boom"))):
        with pytest.raises(RuntimeError):
            await market.tick("strat_err", leader_token="t2")

    # release_leader was still called with the token
    market.state.release_leader.assert_awaited_once_with("strat_err", token="t2")


@pytest.mark.asyncio
async def test_tick_renews_leader_at_midpoint(market: MarketService):
    """renew_leader is called exactly once per tick, midway through the
    subscriber list (crash-safety insurance)."""
    market.publishers["strat_renew"] = Publisher(
        strategy_id="strat_renew",
        pool_id="0x" + "cc" * 32,
        vault_address="0xpub",
        creator_wallet="0xcreator",
    )
    market.publishers["strat_renew"].subscribers["sub_1"] = Subscriber(
        sub_id="0x" + "11" * 32,
        pool_id="0x" + "22" * 32,
        vault_address="0xsub_vault",
        ephemeral_wallet="0xephemeral",
        subscriber_wallet="0xsub",
        active=True,
    )
    market.publishers["strat_renew"].subscribers["sub_2"] = Subscriber(
        sub_id="0x" + "33" * 32,
        pool_id="0x" + "44" * 32,
        vault_address="0xsub_vault",
        ephemeral_wallet="0xephemeral",
        subscriber_wallet="0xsub",
        active=True,
    )

    with (
        patch.object(market, "_evaluate", AsyncMock(return_value={"ETH": 0.5})),
        patch("archimedes.marketplace.service.compute_trades", return_value=_dummy_trades()),
        patch.object(market, "_verify_payment", AsyncMock(return_value=True)),
        patch.object(market, "_apply_to_subscriber", AsyncMock(return_value=True)),
    ):
        await market.tick("strat_renew", leader_token="t3")

    # renew_leader was called exactly once with the strategy_id and token
    market.state.renew_leader.assert_awaited_once_with("strat_renew", token="t3")


@pytest.mark.asyncio
async def test_two_strategies_tick_independently(market: MarketService):
    """Two strategies both complete their tick bodies — the tick() method
    is not gated by a global lock (C2; per-strategy lock acquisition lives
    in _run_loop, not tick())."""
    market.publishers["strat_a"] = Publisher(
        strategy_id="strat_a",
        pool_id="0x" + "aa" * 32,
        vault_address="0xpub_a",
        creator_wallet="0xcreator",
    )
    market.publishers["strat_b"] = Publisher(
        strategy_id="strat_b",
        pool_id="0x" + "bb" * 32,
        vault_address="0xpub_b",
        creator_wallet="0xcreator",
    )

    with (
        patch.object(market, "_evaluate", AsyncMock(return_value={})),
        patch("archimedes.marketplace.service.compute_trades", return_value=[]),
    ):
        await market.tick("strat_a")
        await market.tick("strat_b")

    # Both tick bodies executed fully, both released locks independently
    assert market.state.release_leader.await_count == 2


@pytest.mark.asyncio
async def test_run_loop_uses_per_strategy_key(market: MarketService):
    """_run_loop acquires the per-strategy lock on each iteration."""
    market.state.try_acquire_leader = AsyncMock(return_value="loop-token")

    # Make the publisher retired so the loop exits after one iteration
    pub = Publisher(
        strategy_id="strat_loop",
        pool_id="0x" + "dd" * 32,
        vault_address="0xpub",
        creator_wallet="0xcreator",
    )
    pub.retired = True  # signal exit after first iteration
    market.publishers["strat_loop"] = pub

    with patch.object(market, "tick", AsyncMock()):
        await market._run_loop("strat_loop")

    # try_acquire_leader was called with the per-strategy key
    market.state.try_acquire_leader.assert_awaited_with("strat_loop")
