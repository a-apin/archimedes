"""Unit tests for the circlekit seam (D1). circlekit is mocked at the
module boundary — no live facilitator calls in CI."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archimedes.marketplace import payments


def test_fee_to_price_basic():
    # 100 raw units = $0.0001; 3 actions = $0.0003
    assert payments.fee_to_price(3, 100) == "$0.000300"


def test_fee_to_price_zero():
    assert payments.fee_to_price(0, 100) == "$0.000000"


def test_fee_to_price_no_float_drift():
    # 1_000_000 raw = exactly $1
    assert payments.fee_to_price(1, 1_000_000) == "$1.000000"


def test_fee_to_price_negative_raises():
    with pytest.raises(ValueError):
        payments.fee_to_price(-1, 100)


@pytest.mark.asyncio
async def test_charge_zero_amount_is_paid_without_network():
    with patch.object(payments, "get_gateway_middleware") as mw:
        ok = await payments.charge(
            sub_id="0x" + "11" * 32, ephemeral_key="0x" + "22" * 32,
            strategy_id="s", tick_id="t", action_count=0, flat_fee_raw=100,
        )
    assert ok is True


@pytest.mark.asyncio
async def test_charge_success_path():
    middleware = MagicMock()
    middleware.require.return_value = {"status": 402, "headers": {}, "body": {}}
    middleware.verify = AsyncMock(return_value=MagicMock(is_valid=True))
    middleware.settle = AsyncMock()
    fake_reqs = MagicMock()
    fake_x402 = MagicMock()
    fake_x402.get_gateway_option.return_value = fake_reqs
    with (
        patch.object(payments, "get_gateway_middleware", return_value=middleware),
        patch.object(payments, "get_payment_required", return_value=fake_x402),
        patch.object(payments, "PrivateKeySigner"),
        patch.object(payments, "create_payment_header", return_value="hdr"),
    ):
        ok = await payments.charge(
            sub_id="0x" + "11" * 32, ephemeral_key="0x" + "22" * 32,
            strategy_id="s", tick_id="t", action_count=2, flat_fee_raw=100,
        )
    assert ok is True
    middleware.settle.assert_awaited_once()


@pytest.mark.asyncio
async def test_charge_verify_invalid_returns_false():
    middleware = MagicMock()
    middleware.require.return_value = {"status": 402, "headers": {}, "body": {}}
    middleware.verify = AsyncMock(
        return_value=MagicMock(is_valid=False, invalid_reason="insufficient")
    )
    middleware.settle = AsyncMock()
    fake_x402 = MagicMock()
    fake_x402.get_gateway_option.return_value = MagicMock()
    with (
        patch.object(payments, "get_gateway_middleware", return_value=middleware),
        patch.object(payments, "get_payment_required", return_value=fake_x402),
        patch.object(payments, "PrivateKeySigner"),
        patch.object(payments, "create_payment_header", return_value="hdr"),
    ):
        ok = await payments.charge(
            sub_id="0x" + "11" * 32, ephemeral_key="0x" + "22" * 32,
            strategy_id="s", tick_id="t", action_count=2, flat_fee_raw=100,
        )
    assert ok is False
    middleware.settle.assert_not_awaited()


@pytest.mark.asyncio
async def test_charge_exception_returns_false():
    with patch.object(
        payments, "get_gateway_middleware", side_effect=RuntimeError("no seller")
    ):
        ok = await payments.charge(
            sub_id="0x" + "11" * 32, ephemeral_key="0x" + "22" * 32,
            strategy_id="s", tick_id="t", action_count=2, flat_fee_raw=100,
        )
    assert ok is False
