"""Swap / AMM endpoints — /api/swap/*."""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, Request, Response

from archimedes.api.limiter import limiter
from archimedes.api.schemas import PoolListResponse, PoolResponse, SwapQuoteResponse
from archimedes.services.log_scrubber import sanitize_log_value

logger = logging.getLogger(__name__)

swap_router = APIRouter(prefix="/api/swap", tags=["swap"])


def _to_token_raw(amount: float, decimals: int) -> int:
    """Convert a human amount to integer base units without float64 precision loss.

    ``int(amount * 10**decimals)`` runs the multiply in float64 (~15–16 significant
    digits), so it loses integer precision for large amounts (e.g. 1e9 tokens at 18
    decimals lands ~2e13 off) and can truncate sub-unit amounts to 0. Route through
    ``Decimal`` on the float's shortest round-trip string so the multiply is exact
    and only the final ``int()`` truncates to whole base units (#922).
    """
    return int(Decimal(str(amount)) * (Decimal(10) ** decimals))


def _known_token_meta(address: str) -> tuple[str, int]:
    """Return display symbol + decimals for known USDC/synthetic tokens."""
    from archimedes.chain.client import chain_client

    addr = address.lower()
    if addr == chain_client.settings.usdc_address.lower():
        return "USDC", 6
    for symbol, token_address in chain_client.settings.synth_addresses.items():
        if token_address and addr == token_address.lower():
            return symbol, 18
    return address[:8], 18


async def _token_decimals(address: str) -> int:
    """Fetch token decimals, falling back to known Archimedes conventions."""
    from archimedes.chain.contracts import get_contract_loader

    _, known_decimals = _known_token_meta(address)
    if known_decimals != 18:
        return known_decimals
    try:
        return await get_contract_loader().token(address).functions.decimals().call()
    except Exception:
        return known_decimals


@swap_router.get("/quote", response_model=SwapQuoteResponse)
@limiter.limit("30/minute")
async def get_swap_quote(
    request: Request,  # noqa: ARG001 — slowapi @limiter.limit inspects param name
    response: Response,  # noqa: ARG001
    token_in: str = Query(..., description="Input token address"),
    token_out: str = Query(..., description="Output token address"),
    amount_in: float = Query(..., gt=0, description="Amount of input token"),
):
    """Preview a swap via AMM router."""
    from archimedes.chain.client import chain_client
    from archimedes.chain.contracts import get_contract_loader

    try:
        loader = get_contract_loader()
        router = loader.amm_router
        decimals_in = await _token_decimals(token_in)
        decimals_out = await _token_decimals(token_out)
        amount_in_raw = _to_token_raw(amount_in, decimals_in)

        amount_out_raw = await router.functions.getAmountOut(
            chain_client.to_checksum(token_in),
            chain_client.to_checksum(token_out),
            amount_in_raw,
        ).call()

        one_unit_raw = 10**decimals_in
        spot_out_raw = await router.functions.getAmountOut(
            chain_client.to_checksum(token_in),
            chain_client.to_checksum(token_out),
            one_unit_raw,
        ).call()

        amount_out = amount_out_raw / 10**decimals_out
        spot_out = spot_out_raw / 10**decimals_out
        exec_price = amount_out / amount_in if amount_in else 0.0
        price_impact = max(((spot_out - exec_price) / spot_out) * 100, 0.0) if spot_out else 0.0

        return SwapQuoteResponse(
            token_in=token_in,
            token_out=token_out,
            amount_in=amount_in,
            amount_out=amount_out,
            price_impact_pct=price_impact,
            fee_pct=0.3,
            min_amount_out=amount_out * 0.995,
        )
    except Exception as e:
        # Don't echo the raw chain/web3 exception to the client — it leaks RPC
        # internals, contract addresses, and revert reasons (audit 2026-06-14;
        # same leak class fixed in vaults/portfolio routes #605). Log full
        # detail server-side; return a generic message.
        logger.exception("swap quote failed for %s -> %s", sanitize_log_value(token_in), sanitize_log_value(token_out))
        raise HTTPException(status_code=400, detail="Quote failed — check the token pair and amount.") from e


@swap_router.get("/pools", response_model=PoolListResponse)
async def list_swap_pools():
    """List AMM pools and reserves for the exchange UI."""
    from archimedes.chain.contracts import get_contract_loader

    loader = get_contract_loader()
    pools: list[PoolResponse] = []

    try:
        pool_addresses = await loader.amm_router.functions.getAllPools().call()
    except Exception:
        pool_addresses = []

    for pool_address in pool_addresses:
        try:
            pool = loader.amm_pool(pool_address)
            token0, token1, reserve0_raw, reserve1_raw, total_supply_raw, fee_bps = await asyncio.gather(
                pool.functions.token0().call(),
                pool.functions.token1().call(),
                pool.functions.reserve0().call(),
                pool.functions.reserve1().call(),
                pool.functions.totalSupply().call(),
                pool.functions.swapFeeBps().call(),
            )
            symbol0, decimals0 = _known_token_meta(token0)
            symbol1, decimals1 = _known_token_meta(token1)
            reserve0 = reserve0_raw / 10**decimals0
            reserve1 = reserve1_raw / 10**decimals1

            tvl = 0.0
            if symbol0 == "USDC":
                tvl += reserve0 * 2
            elif symbol1 == "USDC":
                tvl += reserve1 * 2

            pools.append(
                PoolResponse(
                    address=pool_address,
                    token0=token0,
                    token1=token1,
                    symbol0=symbol0,
                    symbol1=symbol1,
                    reserve0=reserve0,
                    reserve1=reserve1,
                    tvl_usdc=tvl,
                    fee_pct=fee_bps / 100,
                    apr_pct=None,
                    total_supply=total_supply_raw / 1e18,
                )
            )
        except Exception:
            continue

    return PoolListResponse(pools=pools, total=len(pools))
