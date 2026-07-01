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
    svc.state.try_acquire_leader = AsyncMock(return_value=True)
    svc.state.renew_leader = AsyncMock()
    svc.state.release_leader = AsyncMock()
    svc.state.append_event = AsyncMock()
    svc.state.save_subscribers = AsyncMock()
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
async def test_tick_produces_trades_and_charges(market: MarketService):
    """The economic core: _evaluate returns weights, compute_trades returns
    non-empty trades, chargeActions is called, subscriber vault is traded."""
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

        with patch.object(market, "_charge", AsyncMock(return_value=True)) as mock_charge:
            await market.tick("strat_a")

            # _charge was called with the right action_count
            mock_charge.assert_awaited_once_with("0x" + "bb" * 32, 1)

            # execute_trades was called for publisher vault
            market.executor.execute_trades.assert_any_call("0xpublisher_vault", _dummy_trades())

            # state.save_subscribers was called
            market.state.save_subscribers.assert_awaited()


@pytest.mark.asyncio
async def test_tick_marks_subscriber_inactive_on_charge_failure(market: MarketService):
    """When chargeActions reverts, the subscriber is marked inactive and no
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

        with patch.object(market, "_charge", AsyncMock(return_value=False)):
            await market.tick("strat_b")

            # subscriber marked inactive
            assert market.publishers["strat_b"].subscribers["sub_2"].active is False

            # append_event was called with halt event
            halt_calls = [
                c for c in market.state.append_event.await_args_list
                if c[0][1].get("type") == "halt"
            ]
            assert len(halt_calls) >= 1

            # execute_trades NOT called for subscriber vault
            sub_calls = [
                c for c in market.executor.execute_trades.call_args_list
                if c[0][0] == "0xsub_vault"
            ]
            assert len(sub_calls) == 0


@pytest.mark.asyncio
async def test_tick_no_trades_skips_charge(market: MarketService):
    """When compute_trades returns empty, no charge or execution happens."""
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

        with patch.object(market, "_charge", AsyncMock()) as mock_charge:
            await market.tick("strat_c")
            mock_charge.assert_not_called()
            market.executor.execute_trades.assert_not_called()
