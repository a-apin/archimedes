"""Tests for StrategyRunnerPublisher — publisher agent for subscription marketplace.

Target: backend/archimedes/chain/strategy_runner_publisher.py
Hermetic: all chain/contract/market calls mocked. No network.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from fastapi import HTTPException


# ── Fixtures ──────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def env_vars():
    """Set environment variables for publisher tests."""
    with patch.dict("os.environ", {
        "PUBLISHER_STRATEGY_ID": "test_strategy_001",
        "PUBLISHER_VAULT_ADDRESS": "0x1234567890abcdef1234567890abcdef12345678",
        "PUBLISHER_POOL_ID": "0xpool1234567890abcdef1234567890abcdef12345678",
        "CREATOR_ADDRESS": "0xcreator00000000000000000000000000000000000",
        "PLATFORM_WALLET": "0xplatform0000000000000000000000000000000000",
        "PAYMENT_SPLITTER_ADDRESS": "0xsplitter0000000000000000000000000000000000",
        "SUBSCRIPTION_MANAGER_ADDRESS": "0xsubmgr000000000000000000000000000000000000",
        "AGENT_DRY_RUN": "true",
        "AGENT_PRIVATE_KEY": "0x" + "01" * 32,
    }):
        yield


@pytest.fixture
def publisher():
    """PublisherAgent with mocked dependencies."""
    from archimedes.chain.strategy_runner_publisher import PublisherAgent

    agent = PublisherAgent()
    agent._initialized = True
    agent.vault_address = "0x1234567890abcdef1234567890abcdef12345678"
    agent.pool_id = "0xpool1234567890abcdef1234567890abcdef12345678"
    agent.loader = MagicMock()
    agent.executor = MagicMock()
    agent.redis = MagicMock()
    agent.redis.redis = MagicMock()
    agent.redis.redis.set = AsyncMock()
    agent.circle_signer = MagicMock()
    agent.circle_signer.is_configured = False
    agent.subscription_manager_address = "0xsubmgr000000000000000000000000000000000000"
    return agent


# ── Subscriber Registration Tests ─────────────────────────────


class TestSubscriberRegistration:
    """POST /subscribe endpoint tests."""

    @pytest.mark.asyncio
    async def test_subscribe_validates_sub_id_on_chain(self, publisher):
        """Reject unknown sub_id with 400."""
        from archimedes.chain.strategy_runner_publisher import SubscribeRequest

        req = SubscribeRequest(
            sub_id="0xbad00000000000000000000000000000000000000000000000000",
            webhook_url="http://subscriber:8081/events",
            ephemeral_wallet="0x0000000000000000000000000000000000000000",
        )

        mock_contract = MagicMock()
        mock_contract.functions.subscriptions.return_value.call = AsyncMock(
            side_effect=Exception("not found")
        )
        publisher.loader._contract.return_value = mock_contract

        with pytest.raises(HTTPException) as exc:
            await publisher.handle_subscribe(req)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_subscribe_rejects_unreachable_webhook(self, publisher):
        """Reject unreachable webhook_url with 400."""
        from archimedes.chain.strategy_runner_publisher import SubscribeRequest

        req = SubscribeRequest(
            sub_id="0xvalid0000000000000000000000000000000000000000000000000000",
            webhook_url="http://unreachable.example:9999/events",
            ephemeral_wallet="0x0000000000000000000000000000000000000000",
        )

        mock_contract = MagicMock()
        mock_contract.functions.subscriptions.return_value.call = AsyncMock(
            return_value=[None, None, None, None, None, True, 0]
        )
        publisher.loader._contract.return_value = mock_contract

        mock_resp = AsyncMock()
        mock_resp.status = 500

        mock_get_cm = AsyncMock()
        mock_get_cm.__aenter__ = AsyncMock(return_value=mock_resp)

        # session.get() is a regular (non-async) call — so session is a MagicMock
        mock_session = MagicMock()
        mock_session.get.return_value = mock_get_cm

        # ClientSession() is used in async with — need __aenter__
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)

        with patch("aiohttp.ClientSession", return_value=mock_cm):
            with pytest.raises(HTTPException) as exc:
                await publisher.handle_subscribe(req)
            assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_subscribe_registers_valid_subscriber(self, publisher):
        """Register a valid subscriber."""
        from archimedes.chain.strategy_runner_publisher import SubscribeRequest

        req = SubscribeRequest(
            sub_id="0xvalid0000000000000000000000000000000000000000000000000000",
            webhook_url="http://subscriber:8081/events",
            ephemeral_wallet="0x0000000000000000000000000000000000000000",
        )

        mock_contract = MagicMock()
        mock_contract.functions.subscriptions.return_value.call = AsyncMock(
            return_value=[None, None, None, None, None, True, 0]
        )
        publisher.loader._contract.return_value = mock_contract

        mock_resp = AsyncMock()
        mock_resp.status = 200

        mock_get_cm = AsyncMock()
        mock_get_cm.__aenter__ = AsyncMock(return_value=mock_resp)

        mock_session = MagicMock()
        mock_session.get.return_value = mock_get_cm

        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)

        with patch("aiohttp.ClientSession", return_value=mock_cm):
            result = await publisher.handle_subscribe(req)
            assert result["status"] == "registered"
            assert result["sub_id"] == req.sub_id
            assert req.sub_id in publisher.subscribers


# ── Halt Detection Tests ──────────────────────────────────────


class TestHaltDetection:
    """Halt check between evaluation steps."""

    @pytest.mark.asyncio
    async def test_halt_when_no_active_subscribers(self, publisher):
        """Halt fires when all subscribers inactive."""
        publisher.subscribers = {}
        halted, reason, msg = publisher._halt_check()
        assert halted
        assert reason == "no_active_subscribers"

    @pytest.mark.asyncio
    async def test_no_halt_with_active_subscribers(self, publisher):
        """No halt when active subscribers exist."""
        from archimedes.chain.strategy_runner_publisher import SubscriberInfo

        publisher.subscribers["sub1"] = SubscriberInfo(
            sub_id="sub1",
            webhook_url="http://example.com/events",
            ephemeral_wallet="0xabc",
            active=True,
        )
        halted, reason, msg = publisher._halt_check()
        assert not halted

    @pytest.mark.asyncio
    async def test_halt_when_forced(self, publisher):
        """Halt fires when FORCE_HALT env var is set."""
        with patch("archimedes.chain.strategy_runner_publisher.FORCE_HALT", True):
            halted, reason, msg = publisher._halt_check()
            assert halted
            assert reason == "forced"


# ── Charge & Rebalance Tests ──────────────────────────────────


class TestChargeAndRebalance:
    """chargeActions revert marking subscriber inactive."""

    @pytest.mark.asyncio
    async def test_charge_revert_marks_inactive(self, publisher):
        """chargeActions revert marks subscriber inactive and skips rebalance."""
        from archimedes.chain.strategy_runner_publisher import SubscriberInfo

        with patch("archimedes.chain.strategy_runner_publisher.DRY_RUN", False):
            sub_id = "sub_charge_fail"
            publisher.subscribers[sub_id] = SubscriberInfo(
                sub_id=sub_id,
                webhook_url="http://example.com/events",
                ephemeral_wallet="0xabc",
                active=True,
            )

            mock_contract = MagicMock()
            mock_contract.functions.chargeActions.return_value.build_transaction = AsyncMock(
                side_effect=Exception("insufficient balance")
            )
            publisher.loader._contract.return_value = mock_contract

            result = await publisher._charge_subscriber(sub_id, 3)
            assert not result

    @pytest.mark.asyncio
    async def test_charge_revert_non_dry_run(self, publisher):
        """chargeActions revert marks subscriber inactive (non-dry-run)."""
        from archimedes.chain.strategy_runner_publisher import SubscriberInfo

        with patch("archimedes.chain.strategy_runner_publisher.DRY_RUN", False):
            sub_id = "sub_charge_fail_real"
            publisher.subscribers[sub_id] = SubscriberInfo(
                sub_id=sub_id,
                webhook_url="http://example.com/events",
                ephemeral_wallet="0xabc",
                active=True,
            )

            mock_contract = MagicMock()
            mock_contract.functions.chargeActions.return_value.build_transaction = AsyncMock(
                side_effect=Exception("insufficient balance")
            )
            publisher.loader._contract.return_value = mock_contract

            result = await publisher._charge_subscriber(sub_id, 3)
            assert not result

    @pytest.mark.asyncio
    async def test_rebalance_payload_structure(self, publisher):
        """Rebalance payload contains required fields."""
        from archimedes.chain.strategy_runner_publisher import _rebalance_payload

        payload = _rebalance_payload(
            tick_id="pub_123_1",
            action_count=2,
            trades=[
                {"symbol": "sSPY", "direction": "BUY", "amount": 100},
                {"symbol": "sBTC", "direction": "SELL", "amount": 50},
            ],
            target_weights={"sSPY": 0.6, "USDC": 0.4},
        )
        assert payload["type"] == "rebalance"
        assert payload["action_count"] == 2
        assert len(payload["trades"]) == 2
        assert "target_weights" in payload


# ── Health Endpoint Tests ─────────────────────────────────────


class TestHealthEndpoint:
    """GET /health endpoint."""

    @pytest.mark.asyncio
    async def test_health_returns_expected_fields(self, publisher):
        """Health endpoint returns strategy, vault, subscriber count."""
        from archimedes.chain.strategy_runner_publisher import SubscriberInfo

        publisher.subscribers["sub1"] = SubscriberInfo(
            sub_id="sub1", webhook_url="http://e.com",
            ephemeral_wallet="0xabc", active=True,
        )
        publisher.subscribers["sub2"] = SubscriberInfo(
            sub_id="sub2", webhook_url="http://e2.com",
            ephemeral_wallet="0xdef", active=False,
        )

        result = await publisher.handle_health()
        assert result["status"] == "ok"
        assert result["strategy_id"] == "test_strategy_001"
        assert result["vault"] == "0x1234567890abcdef1234567890abcdef12345678"
        assert result["subscribers"] == 2
        assert result["active_subscribers"] == 1


# ── Halt Notification Tests ───────────────────────────────────


class TestHaltNotification:
    """Halt notification payload structure."""

    @pytest.mark.asyncio
    async def test_halt_payload_contains_required_fields(self, publisher):
        """Halt notification has type, step, reason, message."""
        from archimedes.chain.strategy_runner_publisher import _halt_payload

        payload = _halt_payload(
            tick_id="pub_123_1",
            step="pre_rebalance",
            reason="insufficient_balance",
            message="Subscriber sub_1 insufficient balance",
        )
        assert payload["type"] == "halt"
        assert payload["step"] == "pre_rebalance"
        assert payload["reason"] == "insufficient_balance"
        assert payload["message"] == "Subscriber sub_1 insufficient balance"

    @pytest.mark.asyncio
    async def test_evaluation_step_payload_no_charge(self, publisher):
        """Evaluation step payload is sent without any charge."""
        from archimedes.chain.strategy_runner_publisher import _eval_step_payload

        payload = _eval_step_payload(
            step="signal_collection",
            tick_id="pub_123_1",
            signals={"status": "ok", "count": 5},
        )
        assert payload["type"] == "evaluation_step"
        assert not payload["halted"]
        assert "signal_summary" in payload
        # No on-chain action fields
        assert "action_count" not in payload
