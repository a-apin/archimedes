"""Laws of the platform revenue sweep (services/revenue_sweep.py).

Hermetic: circlekit's GatewayClient is replaced at the module boundary — no
Circle API, no chain. The guard demonstrations (repo rule 4): the
disabled-by-default scheduler gate, the loud-unconfigured failure, the
threshold refusing a below-minimum sweep, and the fee reserve keeping the
request affordable are each shown rejecting their inputs, not assumed.

Note on the fake: withdrawal semantics use ``tests.gateway_fake``, which
enforces Circle's real ``amount + fee <= available`` rule. The bare
``AsyncMock`` this suite used to withdraw against accepted any argument, and
so certified the full-balance request that failed on the first production
sweep (2026-08-26). ``_mock_client`` remains only for the paths that never
reach ``withdraw``.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from archimedes.services import revenue_sweep as rs
from tests.gateway_fake import FakeGatewayClient

RECIPIENT = "0xffa7abba5f17cb8471ebf150bf808bd6fb8856c1"
WALLET_ID = "af3e1cf6-76a3-55db-911a-b356860058e4"


def _balances(available_units: int):
    b = MagicMock()
    b.available = available_units
    b.formatted_available = f"{available_units / 10**6:.6f}"
    b.formatted_total = b.formatted_available
    return b


def _mock_client(available_units: int):
    client = MagicMock()
    client.get_gateway_balance = AsyncMock(return_value=_balances(available_units))
    client.withdraw = AsyncMock(return_value=MagicMock(mint_tx_hash="0xmint"))
    return client


class TestSchedulerGate:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("REVENUE_SWEEP_ENABLED", raising=False)
        assert rs.sweep_enabled() is False

    def test_only_literal_true_arms(self, monkeypatch):
        for bad in ("1", "yes", "TRUE ", "on", ""):
            monkeypatch.setenv("REVENUE_SWEEP_ENABLED", bad)
            # "TRUE " strips+lowers to "true" — that one IS armed; the rest not.
            expected = bad.strip().lower() == "true"
            assert rs.sweep_enabled() is expected
        monkeypatch.setenv("REVENUE_SWEEP_ENABLED", "true")
        assert rs.sweep_enabled() is True


class TestConfigurationIsLoud:
    async def test_unconfigured_raises_not_noop(self, monkeypatch):
        monkeypatch.delenv("REVENUE_WALLET_ID", raising=False)
        monkeypatch.setenv("GENERATION_PAYMENT_RECIPIENT", RECIPIENT)
        with pytest.raises(RuntimeError, match="not configured"):
            await rs.check_revenue()


class TestSweep:
    async def test_below_threshold_refuses(self, monkeypatch):
        monkeypatch.setenv("GENERATION_PAYMENT_RECIPIENT", RECIPIENT)
        monkeypatch.setenv("REVENUE_WALLET_ID", WALLET_ID)
        client = _mock_client(4_000_000)  # $4 < default $10
        with patch.object(rs, "_client", return_value=client):
            out = await rs.sweep_revenue()
        assert out["swept"] is False
        client.withdraw.assert_not_awaited()

    async def test_at_threshold_withdraws_available_less_fee_reserve(self, monkeypatch):
        """The amount requested must leave room for Circle's fee.

        This is the guard demonstration for the prod bug of 2026-08-26: the
        fake enforces Circle's real rule (amount + fee <= available), so the
        old ``amount = balances.formatted_available`` cannot pass it.
        """
        monkeypatch.setenv("GENERATION_PAYMENT_RECIPIENT", RECIPIENT)
        monkeypatch.setenv("REVENUE_WALLET_ID", WALLET_ID)
        monkeypatch.delenv("GATEWAY_WITHDRAW_FEE_RESERVE_USDC", raising=False)
        client = FakeGatewayClient(12_500_000)  # $12.50
        with patch.object(rs, "_client", return_value=client):
            out = await rs.sweep_revenue()

        assert out["swept"] is True and out["mint_tx_hash"] == "0xmint"
        # $12.50 available - $0.05 reserve
        assert client.last_withdraw["amount"] == "12.450000"
        assert out["amount_usdc"] == "12.450000"
        assert out["available_usdc"] == "12.500000"
        assert out["fee_reserve_usdc"] == "0.050000"

    async def test_requesting_the_whole_balance_is_what_circle_rejects(self, monkeypatch):
        """Pin the failure mode itself, so the fake is proven to have teeth.

        If this test ever stops raising, the fake has gone slack and the guard
        demonstration above is worthless.
        """
        client = FakeGatewayClient(36_000_000)
        with pytest.raises(ValueError, match="Insufficient balance for depositor"):
            await client.withdraw(amount="36.000000", max_fee=2_010_000)

    async def test_reserve_bounds_what_circle_may_charge(self, monkeypatch):
        """max_fee is pinned to the reserve, so an outsized fee fails loudly
        instead of being quietly paid out of revenue."""
        monkeypatch.setenv("GENERATION_PAYMENT_RECIPIENT", RECIPIENT)
        monkeypatch.setenv("REVENUE_WALLET_ID", WALLET_ID)
        monkeypatch.delenv("GATEWAY_WITHDRAW_FEE_RESERVE_USDC", raising=False)
        # A wildly out-of-band fee — under circlekit's 2.01 default this would
        # silently take 5.6% of a $36 sweep.
        client = FakeGatewayClient(36_000_000, fee_raw=2_000_000)
        with patch.object(rs, "_client", return_value=client):
            with pytest.raises(ValueError, match="exceeds maxFee"):
                await rs.sweep_revenue()
        assert client.last_withdraw["max_fee"] == 50_000

    async def test_balance_under_the_reserve_is_not_swept(self, monkeypatch):
        monkeypatch.setenv("GENERATION_PAYMENT_RECIPIENT", RECIPIENT)
        monkeypatch.setenv("REVENUE_WALLET_ID", WALLET_ID)
        monkeypatch.setenv("GATEWAY_WITHDRAW_FEE_RESERVE_USDC", "0.05")
        client = FakeGatewayClient(40_000)  # $0.04 — under the reserve
        with patch.object(rs, "_client", return_value=client):
            out = await rs.sweep_revenue(Decimal("0.01"))  # threshold cleared
        assert out["swept"] is False
        assert "does not cover" in out["reason"]
        assert client.withdraw_calls == []

    async def test_min_override(self, monkeypatch):
        monkeypatch.setenv("GENERATION_PAYMENT_RECIPIENT", RECIPIENT)
        monkeypatch.setenv("REVENUE_WALLET_ID", WALLET_ID)
        client = FakeGatewayClient(4_000_000)
        with patch.object(rs, "_client", return_value=client):
            out = await rs.sweep_revenue(Decimal("2"))
        assert out["swept"] is True
        assert client.last_withdraw["amount"] == "3.950000"

    async def test_check_is_read_only(self, monkeypatch):
        monkeypatch.setenv("GENERATION_PAYMENT_RECIPIENT", RECIPIENT)
        monkeypatch.setenv("REVENUE_WALLET_ID", WALLET_ID)
        client = _mock_client(12_500_000)
        with patch.object(rs, "_client", return_value=client):
            out = await rs.check_revenue()
        assert out["available_usdc"] == "12.500000"
        client.withdraw.assert_not_awaited()


def test_threshold_parse_defensive(monkeypatch):
    monkeypatch.setenv("REVENUE_SWEEP_MIN_USDC", "garbage")
    assert rs._min_usdc() == Decimal("10.0")
    monkeypatch.setenv("REVENUE_SWEEP_MIN_USDC", "-3")
    assert rs._min_usdc() == Decimal("10.0")
    monkeypatch.setenv("REVENUE_SWEEP_MIN_USDC", "5.5")
    assert rs._min_usdc() == Decimal("5.5")
