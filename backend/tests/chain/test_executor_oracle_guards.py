"""Executor SELL-sizing guards (#923).

Two failure modes fixed:
  1. `_usdc_value_to_token_raw` fell back to a 1:1 ($1) price on an oracle read
     error / missing oracle — on a high-priced synth that orders ~price× too
     many units. It must now FAIL the rebalance instead.
  2. A SELL leg with a zero/sub-cent `estimated_usdc_value` (rounded to 2dp
     upstream) sized to 0 units; it must be skipped, not submitted.

Hermetic: the loader / oracle / circle signer are mocked; no chain access.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from archimedes.chain.executor import ChainExecutor, OraclePriceUnavailableError
from archimedes.models.portfolio import TradeDirection, TradeOrder

USDC = "0x3600000000000000000000000000000000000000"
SYNTH = "0xE745C07d7d32A1Ca0d6162A1c50e876619CF7388"  # sSPY-style synth
VAULT = "0x1111111111111111111111111111111111111111"


def _executor(*, price_raw=None, oracle_raises=False) -> ChainExecutor:
    loader = MagicMock()
    price_call = MagicMock()
    price_call.call = (
        AsyncMock(side_effect=Exception("RPC down")) if oracle_raises else AsyncMock(return_value=price_raw)
    )
    oracle = MagicMock()
    oracle.functions.price = MagicMock(return_value=price_call)
    loader.oracle_for = MagicMock(return_value=oracle)
    return ChainExecutor(loader=loader)


def _patch_settings():
    settings = MagicMock()
    settings.usdc_address = USDC
    settings.synth_addresses = {"sSPY": SYNTH}
    cc = MagicMock()
    cc.settings = settings
    return patch("archimedes.chain.executor.chain_client", cc)


@pytest.mark.asyncio
async def test_valid_oracle_price_sizes_correctly():
    executor = _executor(price_raw=600_000_000)  # $600.00, 6 decimals
    with _patch_settings():
        # $600 of a $600 synth → 1.0 unit → 1e18 raw (18 decimals).
        raw = await executor._usdc_value_to_token_raw(SYNTH, 600.0)
    assert raw == 10**18


@pytest.mark.asyncio
async def test_oracle_read_error_raises_not_1to1():
    executor = _executor(oracle_raises=True)
    with _patch_settings(), pytest.raises(OraclePriceUnavailableError):
        # Old behavior returned int(60000 * 1e18) = 60000 units of a $600 asset.
        await executor._usdc_value_to_token_raw(SYNTH, 60_000.0)


@pytest.mark.asyncio
async def test_non_positive_oracle_price_raises():
    executor = _executor(price_raw=0)
    with _patch_settings(), pytest.raises(OraclePriceUnavailableError):
        await executor._usdc_value_to_token_raw(SYNTH, 100.0)


@pytest.mark.asyncio
async def test_missing_oracle_raises_not_1to1():
    executor = _executor(price_raw=600_000_000)
    unknown = "0x9999999999999999999999999999999999999999"  # not in synth_addresses
    with _patch_settings(), pytest.raises(OraclePriceUnavailableError):
        await executor._usdc_value_to_token_raw(unknown, 60_000.0)


@pytest.mark.asyncio
async def test_zero_value_sell_leg_is_skipped_not_sized():
    executor = _executor(price_raw=600_000_000)
    executor._validate_trade_liquidity = AsyncMock()  # no-op liquidity preflight
    executor._confirm_receipt = AsyncMock(return_value="0xconfirmed")
    sizing_spy = AsyncMock(wraps=executor._usdc_value_to_token_raw)
    executor._usdc_value_to_token_raw = sizing_spy

    sell_zero = TradeOrder(
        symbol="sSPY",
        token_address=SYNTH,
        direction=TradeDirection.SELL,
        amount=0.0,
        estimated_usdc_value=0.0,  # sub-cent leg rounded to 0.00
    )

    with _patch_settings(), patch("archimedes.chain.executor.circle_signer") as cs:
        cs.is_configured = True
        cs.execute_contract = AsyncMock(return_value="0xhash")
        await executor.execute_trades(VAULT, [sell_zero])

    # The zero-value SELL leg is skipped before sizing — never sent as 0 units.
    sizing_spy.assert_not_called()
