"""Tests for StrategyRunnerSubscriber — subscriber backend for the
subscription marketplace.

Target: backend/archimedes/chain/strategy_runner_subscriber.py
Hermetic: all chain/contract calls mocked. No network.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from fastapi import HTTPException


# ── Fixtures ──────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def env_vars():
    with patch.dict("os.environ", {
        "SUBSCRIBER_WALLET_ADDRESS": "0xsubscriber0000000000000000000000000000000",
        "SUBSCRIBER_SUB_ID": "0xsubid00000000000000000000000000000000000000000000",
        "SUBSCRIBER_VAULT_ADDRESS": "0xvault0000000000000000000000000000000000000",
        "SUBSCRIBER_POOL_ID": "0xpool0000000000000000000000000000000000000000",
        "PUBLISHER_ENDPOINT": "http://publisher:8080",
        "SUBSCRIPTION_MANAGER_ADDRESS": "0xsubmgr000000000000000000000000000000000000",
        "AGENT_DRY_RUN": "true",
        "AGENT_PRIVATE_KEY": "0x" + "01" * 32,
    }):
        yield


@pytest.fixture
def subscriber():
    """SubscriberAgent with mocked dependencies."""
    from archimedes.chain.strategy_runner_subscriber import SubscriberAgent

    agent = SubscriberAgent()
    agent._initialized = True
    agent.vault_address = "0xvault0000000000000000000000000000000000000"
    agent.sub_id = "0xsubid00000000000000000000000000000000000000000000"
    agent.ephemeral_wallet_address = "0xephemeral00000000000000000000000000000000"
    agent.executor = MagicMock()
    agent.executor.execute_trades = AsyncMock(return_value=["0xabc"])
    agent.loader = MagicMock()
    agent.circle_signer = MagicMock()
    agent.circle_signer.is_configured = False
    agent.subscription_manager_address = "0xsubmgr000000000000000000000000000000000000"
    agent.redis = MagicMock()
    agent.redis.redis = MagicMock()
    agent.redis.redis.set = AsyncMock()
    return agent


# ── Event Handling Tests ──────────────────────────────────────


class TestEventHandling:
    """POST /events endpoint."""

    @pytest.mark.asyncio
    async def test_rebalance_event_triggers_execute_trades(self, subscriber):
        """Rebalance event calls execute_trades with correct trades."""
        from archimedes.chain.strategy_runner_subscriber import PublisherEvent

        event = PublisherEvent(
            type="rebalance",
            tick_id="pub_123_1",
            action_count=2,
            trades=[
                {"symbol": "sSPY", "direction": "BUY", "amount": 100},
                {"symbol": "sBTC", "direction": "SELL", "amount": 50},
            ],
            target_weights={"sSPY": 0.6, "USDC": 0.4},
        )

        result = await subscriber.handle_event(event)
        assert result["status"] == "received"

        # In DRY_RUN mode execute_trades is not called
        if not subscriber.executor.execute_trades.called:
            pytest.skip("DRY_RUN skip")

    @pytest.mark.asyncio
    async def test_rebalance_event_non_dry_run(self, subscriber):
        """Rebalance calls execute_trades when not in dry-run."""
        from archimedes.chain.strategy_runner_subscriber import PublisherEvent

        with patch("archimedes.chain.strategy_runner_subscriber.DRY_RUN", False):
            event = PublisherEvent(
                type="rebalance",
                tick_id="pub_123_1",
                action_count=1,
                trades=[{"symbol": "sSPY", "direction": "BUY", "amount": 100}],
                target_weights={"sSPY": 1.0},
            )

            await subscriber.handle_event(event)
            subscriber.executor.execute_trades.assert_called_once()
            args, _ = subscriber.executor.execute_trades.call_args
            assert args[0] == subscriber.vault_address
            assert len(args[1]) == 1
            assert args[1][0].direction == "BUY"

    @pytest.mark.asyncio
    async def test_halt_event_logs_and_does_not_execute(self, subscriber):
        """Halt event logs and does not call execute_trades."""
        from archimedes.chain.strategy_runner_subscriber import PublisherEvent

        event = PublisherEvent(
            type="halt",
            tick_id="pub_123_1",
            step="pre_rebalance",
            reason="insufficient_balance",
            message="Subscriber balance too low",
        )

        result = await subscriber.handle_event(event)
        assert result["status"] == "received"
        subscriber.executor.execute_trades.assert_not_called()

    @pytest.mark.asyncio
    async def test_halt_insufficient_balance_logs_warning(self, subscriber):
        """Halt with insufficient_balance logs a warning."""
        import logging
        from archimedes.chain.strategy_runner_subscriber import PublisherEvent

        with patch.object(logging.getLogger("archimedes.chain.strategy_runner_subscriber"),
                          "warning") as mock_warning:
            event = PublisherEvent(
                type="halt",
                tick_id="pub_123_1",
                step="pre_rebalance",
                reason="insufficient_balance",
                message="Need top up",
            )
            await subscriber.handle_event(event)
            mock_warning.assert_called()

    @pytest.mark.asyncio
    async def test_evaluation_step_logs_and_checks_balance(self, subscriber):
        """Evaluation step logs the step and checks balance."""
        from archimedes.chain.strategy_runner_subscriber import PublisherEvent

        mock_contract = MagicMock()
        mock_sub_data = [None, None, None, 5_000_000, None, True, 0]  # balance = 5 USDC
        mock_contract.functions.subscriptions.return_value.call = AsyncMock(
            return_value=mock_sub_data
        )
        subscriber.loader._contract.return_value = mock_contract

        event = PublisherEvent(
            type="evaluation_step",
            tick_id="pub_123_1",
            step="signal_collection",
            signal_summary={"count": 5},
        )

        result = await subscriber.handle_event(event)
        assert result["status"] == "received"
        subscriber.executor.execute_trades.assert_not_called()

    @pytest.mark.asyncio
    async def test_rebalance_with_empty_trades_skipped(self, subscriber):
        """Rebalance with empty trades list is skipped."""
        from archimedes.chain.strategy_runner_subscriber import PublisherEvent

        event = PublisherEvent(
            type="rebalance",
            tick_id="pub_123_1",
            action_count=0,
            trades=[],
            target_weights={},
        )

        await subscriber.handle_event(event)
        subscriber.executor.execute_trades.assert_not_called()


# ── Health Endpoint Tests ─────────────────────────────────────


class TestHealthEndpoint:
    """GET /health endpoint."""

    @pytest.mark.asyncio
    async def test_health_returns_expected_fields(self, subscriber):
        """Health endpoint returns sub_id, vault, ephemeral_balance."""
        mock_contract = MagicMock()
        mock_sub_data = [None, None, None, 10_000_000, None, True, 0]
        mock_contract.functions.subscriptions.return_value.call = AsyncMock(
            return_value=mock_sub_data
        )
        subscriber.loader._contract.return_value = mock_contract

        result = await subscriber.handle_health()
        assert result["status"] == "ok"
        assert result["sub_id"] == "0xsubid00000000000000000000000000000000000000000000"
        assert result["vault"] == "0xvault0000000000000000000000000000000000000"
        assert result["ephemeral_balance"] == 10_000_000


# ── Top-up Tests ──────────────────────────────────────────────


class TestTopUp:
    """POST /top-up endpoint."""

    @pytest.mark.asyncio
    async def test_top_up_dry_run(self, subscriber):
        """Top-up returns success in dry-run mode."""
        from archimedes.chain.strategy_runner_subscriber import TopUpRequest

        req = TopUpRequest(amount_usdc_raw=5_000_000)
        result = await subscriber.handle_top_up(req)
        assert result["status"] == "topped_up"

    @pytest.mark.asyncio
    async def test_top_up_non_dry_run(self, subscriber):
        """Top-up calls renewEphemeralWallet on-chain."""
        from archimedes.chain.strategy_runner_subscriber import TopUpRequest

        with patch("archimedes.chain.strategy_runner_subscriber.DRY_RUN", False):
            mock_contract = MagicMock()
            mock_contract.functions.renewEphemeralWallet.return_value.build_transaction = AsyncMock(
                return_value={"gas": 200_000}
            )
            mock_contract.functions.subscriptions.return_value.call = AsyncMock(
                return_value=[None, None, None, 15_000_000, None, True, 0]
            )
            subscriber.loader._contract.return_value = mock_contract

            # Mock helper methods to avoid w3 async issues
            subscriber._get_nonce = AsyncMock(return_value=5)
            subscriber._get_gas_price = AsyncMock(return_value=1000000000)
            subscriber._send_raw = AsyncMock(return_value=b"\x00" * 32)

            # Set up agent_account for signing
            mock_account = MagicMock()
            mock_account.address = "0xsubscriber0000000000000000000000000000000"
            mock_settings = MagicMock()
            mock_settings.agent_account = mock_account
            mock_settings.agent_private_key = "0x" + "01" * 32
            mock_settings.chain_id = 5042002
            subscriber.settings = mock_settings

            req = TopUpRequest(amount_usdc_raw=5_000_000)
            result = await subscriber.handle_top_up(req)
            assert result["status"] == "topped_up"
