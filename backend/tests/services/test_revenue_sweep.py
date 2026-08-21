"""Laws of the platform revenue sweep (services/revenue_sweep.py).

Hermetic: circlekit's GatewayClient is mocked at the module boundary — no
Circle API, no chain. The guard demonstrations (repo rule 4): the
disabled-by-default scheduler gate, the loud-unconfigured failure, and the
threshold refusing a below-minimum sweep are each shown rejecting their
inputs, not assumed.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from archimedes.services import revenue_sweep as rs

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

    async def test_at_threshold_withdraws_full_available(self, monkeypatch):
        monkeypatch.setenv("GENERATION_PAYMENT_RECIPIENT", RECIPIENT)
        monkeypatch.setenv("REVENUE_WALLET_ID", WALLET_ID)
        client = _mock_client(12_500_000)  # $12.50
        with patch.object(rs, "_client", return_value=client):
            out = await rs.sweep_revenue()
        assert out["swept"] is True and out["mint_tx_hash"] == "0xmint"
        client.withdraw.assert_awaited_once_with(amount="12.500000")

    async def test_min_override(self, monkeypatch):
        monkeypatch.setenv("GENERATION_PAYMENT_RECIPIENT", RECIPIENT)
        monkeypatch.setenv("REVENUE_WALLET_ID", WALLET_ID)
        client = _mock_client(4_000_000)
        with patch.object(rs, "_client", return_value=client):
            out = await rs.sweep_revenue(Decimal("2"))
        assert out["swept"] is True

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
