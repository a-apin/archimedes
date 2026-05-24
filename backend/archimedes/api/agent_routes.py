"""Agent status / bootstrap endpoints — /api/agent/*."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC

from fastapi import APIRouter, Request

from archimedes.api.limiter import limiter
from archimedes.api.schemas import AgentStatusResponse, AMMHealthResponse
from archimedes.chain.executor import chain_executor

agent_router = APIRouter(prefix="/api/agent", tags=["agent"])


@agent_router.get("/status", response_model=AgentStatusResponse)
async def get_agent_status():
    """Get autonomous agent health and state -- reads from Redis."""
    from datetime import datetime

    from archimedes.services.redis_state import AgentStateStore

    state = AgentStateStore()
    try:
        heartbeat = await state.get_heartbeat()
        regime_data = await state.load_regime()
        events = await state.get_events(count=10)
    except Exception:
        heartbeat = None
        regime_data = None
        events = []
    finally:
        await state.close()

    alive = False
    if heartbeat:
        try:
            hb_time = datetime.fromisoformat(heartbeat)
            age = (datetime.now(UTC) - hb_time).total_seconds()
            alive = age < 600
        except Exception:
            pass

    regime = regime_data.get("regime") if regime_data else None
    confidence = regime_data.get("confidence") if regime_data else None
    source = regime_data.get("source") if regime_data else None
    strat_count = regime_data.get("strategy_count", 0) if regime_data else 0

    vault_count = 0
    try:
        vaults = await chain_executor.get_all_vaults()
        vault_count = len(vaults) if vaults else 0
    except Exception:
        pass

    return AgentStatusResponse(
        alive=alive,
        last_heartbeat=heartbeat,
        regime=regime,
        regime_confidence=confidence,
        regime_source=source,
        strategy_count=strat_count,
        managed_vaults=vault_count,
        recent_events=events,
    )


@agent_router.get("/circle-status")
async def get_circle_integration_status():
    """Get Circle SDK integration breadth status."""
    from archimedes.services.circle_service import circle_service

    return await circle_service.get_integration_status()


@agent_router.get("/health/amm", response_model=AMMHealthResponse)
@limiter.exempt
async def get_amm_health(request: Request):
    """Report per-pool AMM liquidity status.

    Checks each synthetic token's AMM pool (synth/USDC pair) reserves
    and reports whether liquidity meets minimum thresholds for swaps.
    """
    from datetime import datetime

    from archimedes.chain.client import chain_client
    from archimedes.chain.contracts import get_contract_loader

    loader = get_contract_loader()
    settings = chain_client.settings
    usdc_address = settings.usdc_address
    synth_addrs = settings.synth_addresses
    oracle_addrs = settings.oracle_addresses

    now = datetime.now(UTC).isoformat()
    pools: list[dict] = []

    for symbol, token_addr in synth_addrs.items():
        if not token_addr:
            continue

        pool_health = {
            "symbol": symbol,
            "status": "error",
            "liquidity_usdc": 0.0,
            "oracle_price": None,
            "reserve_token": 0.0,
            "reserve_usdc": 0.0,
            "last_update": now,
        }

        try:
            # Find the pool address via router
            router = loader.amm_router
            pool_addr = await router.functions.getPool(
                chain_client.to_checksum(usdc_address),
                chain_client.to_checksum(token_addr),
            ).call()

            if pool_addr == "0x0000000000000000000000000000000000000000":
                pool_health["status"] = "empty"
                pools.append(pool_health)
                continue

            # Read pool reserves
            pool = loader.amm_pool(pool_addr)
            reserve0 = await pool.functions.reserve0().call()
            reserve1 = await pool.functions.reserve1().call()

            # Determine which reserve is USDC (token0 or token1)
            token0 = await pool.functions.token0().call()
            if chain_client.to_checksum(token0) == chain_client.to_checksum(usdc_address):
                reserve_usdc = reserve0 / 1e6  # USDC has 6 decimals
                reserve_token = reserve1 / 1e18  # Synth tokens have 18 decimals
            else:
                reserve_usdc = reserve1 / 1e6
                reserve_token = reserve0 / 1e18

            # Get oracle price
            oracle_price = None
            if oracle_addrs.get(symbol):
                try:
                    oracle = loader.oracle_for(symbol)
                    price_raw = await oracle.functions.price().call()
                    oracle_price = price_raw / 1e6
                except Exception:
                    pass

            # Total liquidity in USDC terms
            # reserve_usdc + (reserve_token * oracle_price)
            token_value_usdc = reserve_token * oracle_price if oracle_price else 0.0
            total_liquidity = reserve_usdc + token_value_usdc

            # Status thresholds
            if total_liquidity <= 0:
                status = "empty"
            elif total_liquidity < 1.0:  # < $1 USDC = very low
                status = "low_liquidity"
            else:
                status = "healthy"

            pool_health.update(
                {
                    "status": status,
                    "liquidity_usdc": round(total_liquidity, 4),
                    "oracle_price": oracle_price,
                    "reserve_token": round(reserve_token, 6),
                    "reserve_usdc": round(reserve_usdc, 6),
                    "last_update": now,
                }
            )

        except Exception:
            pass  # Keep error status

        pools.append(pool_health)

    healthy = sum(1 for p in pools if p["status"] == "healthy")
    return AMMHealthResponse(
        pools=pools,
        healthy_count=healthy,
        total_pools=len(pools),
    )


@agent_router.post("/bootstrap-liquidity")
async def bootstrap_amm_liquidity():
    """Add AMM pool liquidity so vault rebalances can execute."""
    from archimedes.services.amm_bootstrap import bootstrap_amm_liquidity as _bootstrap

    async def _run():
        with contextlib.suppress(Exception):
            await _bootstrap()

    # Intentional fire-and-forget background bootstrap; the task lifecycle is the
    # request-response cycle, not the parent coroutine. Acknowledging RUF006 explicitly.
    asyncio.create_task(_run())  # noqa: RUF006
    return {
        "status": "started",
        "message": "Liquidity bootstrap running in background. Check /api/swap/pools in 2-3 minutes.",
    }
