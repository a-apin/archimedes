"""Vault endpoints — /api/vaults/* (excluding chat, which lives in chat_routes.py)."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Query, Request, Response

from archimedes.api._route_helpers import strategy_provider, vault_svc
from archimedes.api.limiter import limiter
from archimedes.chain.strategy_publisher import strategy_publisher

logger = logging.getLogger(__name__)
from archimedes.api.schemas import (
    VaultDetailResponse,
    VaultListResponse,
)
from archimedes.api.vault_schemas import (
    AllocationTarget,
    SetAllocationsRequest,
    SetAllocationsResponse,
    VaultCreateRequest,
    VaultCreateResponse,
    VaultMetadataRequest,
    VaultMetadataResponse,
)
from archimedes.chain.executor import chain_executor
from archimedes.models.chat import VaultMetadata

vaults_router = APIRouter(prefix="/api/vaults", tags=["vaults"])


async def _anchor_strategies_async(strategy_ids: list[str]) -> None:
    """Best-effort on-chain anchoring of strategy passports via StrategyRegistry.

    Fire-and-forget: failures are logged but never raised. Matches the
    trace_publisher pattern in agent_runner.py.
    """
    for sid in strategy_ids:
        try:
            passport = strategy_provider.get_strategy(sid)
            if passport is None:
                logger.debug("anchor: strategy %s not found in provider — skipping", sid)
                continue
            if not getattr(passport, "methodology_hash", None):
                logger.info("skipping anchor for %s: no methodology_hash", sid)
                continue

            paper_hashes = [p.arxiv_id for p in passport.papers if p.arxiv_id]
            regime_tag = getattr(passport, "regime_tag", None)

            await strategy_publisher.anchor(
                strategy_id=passport.id,
                methodology_hash=passport.methodology_hash,
                paper_hashes=paper_hashes,
                regime_tag=regime_tag,
                metadata_uri="",
            )
            logger.info("anchored strategy %s on-chain", sid)
        except Exception as exc:
            logger.warning("anchor failed for strategy %s (non-fatal): %s", sid, exc)


@vaults_router.get("/", response_model=VaultListResponse)
async def list_vaults(
    tier: int | None = Query(None, ge=1, le=2),
    sort_by: str = Query("aum", pattern="^(aum|return_24h|return_7d|sharpe|created_at)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List vaults for the marketplace leaderboard."""
    return await vault_svc.list_vaults(tier=tier, sort_by=sort_by, order=order, limit=limit, offset=offset)


@vaults_router.post("/create", response_model=VaultCreateResponse)
@limiter.limit("5/minute")
async def create_vault(req: VaultCreateRequest, request: Request, response: Response):  # noqa: ARG001 — slowapi @limiter.limit inspects param name
    """Deploy a new vault on Arc via VaultFactory."""
    from fastapi import HTTPException

    try:
        vault_address = await chain_executor.create_vault(
            name=req.name,
            symbol=req.symbol,
            management_fee_bps=req.management_fee_bps,
            performance_fee_bps=req.performance_fee_bps,
            agent_assisted=req.agent_assisted,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Vault deployment failed: {exc}") from exc

    return VaultCreateResponse(vault_address=vault_address, strategy_ids=req.strategy_ids)


@vaults_router.get("/{address}/health")
async def get_vault_health(address: str):
    """Get vault health snapshot including live Sharpe drift vs backtest baseline."""
    from archimedes.services.vault_monitor import vault_monitor

    return await vault_monitor.get_vault_health(address)


@vaults_router.get("/{address}", response_model=VaultDetailResponse)
async def get_vault_detail(address: str):
    """Get full vault detail including holdings, performance, traces."""
    from fastapi import HTTPException

    detail = await vault_svc.get_vault_detail(address)
    if detail is None:
        raise HTTPException(status_code=404, detail="Vault not found")
    return detail


# ── Vault Metadata (off-chain) ───────────────────────────────


@vaults_router.post("/metadata", response_model=VaultMetadataResponse)
@limiter.limit("10/minute")
async def store_vault_metadata(req: VaultMetadataRequest, request: Request, response: Response):  # noqa: ARG001 — slowapi @limiter.limit inspects param name
    """Store off-chain vault metadata (strategy associations, display name)."""
    from fastapi import HTTPException

    from archimedes.db import get_session

    session = get_session()
    try:
        meta = session.query(VaultMetadata).filter(VaultMetadata.vault_address == req.vault_address).first()
        if meta is None:
            meta = VaultMetadata(vault_address=req.vault_address)
            session.add(meta)

        meta.name = req.name
        meta.symbol = req.symbol
        meta.creator_address = req.creator_address or ""
        meta.set_strategy_ids(req.strategy_ids)
        session.commit()
        session.refresh(meta)

        # Fire-and-forget on-chain strategy anchoring (best-effort, non-fatal)
        if req.strategy_ids:
            asyncio.create_task(_anchor_strategies_async(req.strategy_ids))

        return VaultMetadataResponse(**meta.to_dict())
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        session.close()


@vaults_router.get("/{address}/metadata", response_model=VaultMetadataResponse)
async def get_vault_metadata(address: str):
    """Get off-chain vault metadata (strategy associations, display name)."""
    from fastapi import HTTPException

    from archimedes.db import get_session

    session = get_session()
    try:
        meta = session.query(VaultMetadata).filter(VaultMetadata.vault_address == address).first()
        if meta is None:
            raise HTTPException(status_code=404, detail="No metadata for this vault")
        return VaultMetadataResponse(**meta.to_dict())
    finally:
        session.close()


@vaults_router.post("/{address}/derive-allocations", response_model=SetAllocationsResponse)
@limiter.limit("20/minute")
async def derive_vault_allocations(address: str, req: SetAllocationsRequest, request: Request, response: Response):  # noqa: ARG001 — slowapi @limiter.limit inspects param name; path param routes the request
    """Derive target allocations from selected strategies."""
    from archimedes.chain.client import chain_client
    from archimedes.services.strategy_signal_evaluator import strategy_evaluator

    strategies = strategy_provider.list_strategies()

    if req.strategy_ids:
        strategies = [s for s in strategies if s.id in req.strategy_ids]

    if not strategies:
        usdc_floor_bps = int(req.usdc_floor_pct * 100)
        synth_budget_bps = 10000 - usdc_floor_bps
        synth_addrs = {k: v for k, v in chain_client.settings.synth_addresses.items() if v}
        per_synth = synth_budget_bps // max(len(synth_addrs), 1)
        allocations = [
            AllocationTarget(symbol=sym, token_address=addr, weight_bps=per_synth) for sym, addr in synth_addrs.items()
        ]
        allocations.append(
            AllocationTarget(
                symbol="USDC",
                token_address=chain_client.settings.usdc_address,
                weight_bps=usdc_floor_bps,
            )
        )
        return SetAllocationsResponse(
            allocations=allocations,
            total_bps=sum(a.weight_bps for a in allocations),
            strategy_count=0,
        )

    synth_assets = [sym for sym, addr in chain_client.settings.synth_addresses.items() if addr]
    all_signals = await asyncio.to_thread(
        strategy_evaluator.evaluate_strategies,
        strategies,
        synth_assets,
    )
    usdc_floor = req.usdc_floor_pct / 100.0
    target_weights = strategy_evaluator.aggregate_signals(all_signals, usdc_floor=usdc_floor)

    allocations: list[AllocationTarget] = []

    symbol_to_addr = {"USDC": chain_client.settings.usdc_address}
    symbol_to_addr.update(chain_client.settings.synth_addresses)

    for symbol, weight in target_weights.items():
        token_address = symbol_to_addr.get(symbol)
        if not token_address:
            continue
        weight_bps = int(round(weight * 10000))
        if weight_bps > 0:
            allocations.append(
                AllocationTarget(
                    symbol=symbol,
                    token_address=token_address,
                    weight_bps=weight_bps,
                )
            )

    total = sum(a.weight_bps for a in allocations)
    if total > 0 and total != 10000:
        scale = 10000 / total
        for a in allocations:
            a.weight_bps = int(round(a.weight_bps * scale))
        allocations = [a for a in allocations if a.weight_bps > 0]
        total = sum(a.weight_bps for a in allocations)
        if total != 10000 and allocations:
            largest = max(allocations, key=lambda a: a.weight_bps)
            largest.weight_bps += 10000 - total

    return SetAllocationsResponse(
        allocations=allocations,
        total_bps=sum(a.weight_bps for a in allocations),
        strategy_count=len(strategies),
    )
