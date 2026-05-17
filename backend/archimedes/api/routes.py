"""REST API route definitions — wired to chain services.

All endpoints return JSON matching the schemas in schemas.py.
Daniel codes the frontend fetch calls against these paths.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query

from archimedes.api.schemas import (
    AgentStatusResponse,
    AssetListResponse,
    AssetPriceHistoryResponse,
    ContractAddressesResponse,
    RegimeResponse,
    SignalResponse,
    StrategyListResponse,
    StrategyResponse,
    StrategySignalResponse,
    StrategySignalsResponse,
    SwapQuoteResponse,
    PoolListResponse,
    PoolResponse,
    TraceListResponse,
    TraceResponse,
    TracePublishRequest,
    TracePublishResponse,
    TraceVerifyResponse,
    VaultDetailResponse,
    VaultListResponse,
)

from archimedes.models.trace import ReasoningTrace
from archimedes.models.trace import DecisionType
from archimedes.services.asset_service import AssetService
from archimedes.services.vault_service import VaultService
from archimedes.services.config_service import ConfigService
from archimedes.services.strategy_provider import default_provider
from archimedes.services.strategy_architect import default_architect
from archimedes.services.strategy_guardrail import apply_guardrail
from archimedes.services.construction_trace import build_construction_trace
from archimedes.models.strategy import Strategy, StrategyStatus
from archimedes.api.architect_schemas import (
    ConstructionSelectionResponse,
    ConstructionTraceResponse,
    StrategyConstructionRequest,
    StrategyConstructionResponse,
)
from archimedes.api.vault_schemas import (
    VaultCreateRequest, VaultCreateResponse,
    VaultMetadataRequest, VaultMetadataResponse,
    SetAllocationsRequest, SetAllocationsResponse, AllocationTarget,
)
from archimedes.models.chat import VaultMetadata
from archimedes.chain.oracle_updater import OracleUpdater
from archimedes.chain.executor import chain_executor

# ═══════════════════════════════════════════════════════════════
# Router definitions
# ═══════════════════════════════════════════════════════════════

assets_router = APIRouter(prefix="/api/assets", tags=["assets"])
vaults_router = APIRouter(prefix="/api/vaults", tags=["vaults"])
strategies_router = APIRouter(prefix="/api/strategies", tags=["strategies"])
traces_router = APIRouter(prefix="/api/traces", tags=["traces"])
regime_router = APIRouter(prefix="/api/regime", tags=["regime"])
swap_router = APIRouter(prefix="/api/swap", tags=["swap"])
config_router = APIRouter(prefix="/api/config", tags=["config"])
agent_router = APIRouter(prefix="/api/agent", tags=["agent"])

# Service instances
_asset_svc = AssetService()
_vault_svc = VaultService()
_config_svc = ConfigService()
_oracle = OracleUpdater()
_strategy_provider = default_provider()
_architect = default_architect()


def _to_strategy_response(s: Strategy) -> StrategyResponse:
    """Map the shared Strategy dataclass to the frontend response shape.

    Backtest fields sourced from BACKTEST_* stub constants in strategy files
    (is_backtest_placeholder=True) until Önder's IBacktestEvaluator runs and
    populates a real BacktestResult. Stub values are clearly labelled so the UI
    can render them with an honest "estimate" disclaimer.
    """
    has_stubs = s.stub_sharpe is not None
    return StrategyResponse(
        id=s.id,
        paper_arxiv_id=s.paper_arxiv_id,
        paper_title=s.paper_title,
        paper_authors=s.paper_authors,
        methodology_summary=s.methodology_summary,
        asset_universe=s.asset_universe,
        position_sizing=s.position_sizing.value,
        rebalance_frequency=s.rebalance_frequency.value,
        status=s.status.value,
        # Paper provenance
        paper_venue=s.paper_venue,
        paper_year=s.paper_year,
        paper_doi=s.paper_doi,
        paper_citation_count=s.paper_citation_count,
        # Passport integrity
        methodology_hash=s.methodology_hash,
        extraction_llm=s.extraction_llm,
        curator_wallet=s.curator_wallet,
        curator_note=s.curator_note,
        on_chain_registration_tx=s.on_chain_registration_tx,
        # Paper claims (for delta display)
        paper_claimed_sharpe=s.paper_claimed_sharpe,
        # Backtest (stubs from strategy file; None when IBacktestEvaluator has not run)
        sharpe_ratio=s.stub_sharpe,
        cagr=s.stub_cagr,
        max_drawdown=s.stub_max_dd,
        win_rate=s.stub_win_rate,
        calmar_ratio=s.stub_calmar,
        correlation_to_spy=s.stub_corr_spy,
        is_backtest_placeholder=has_stubs,
    )


# ── Assets ────────────────────────────────────────────────────


@assets_router.get("/", response_model=AssetListResponse)
async def list_assets():
    """List all assets in the ecosystem with current prices."""
    return await _asset_svc.list_assets()


@assets_router.get("/{symbol}/history", response_model=AssetPriceHistoryResponse)
async def get_asset_price_history(
    symbol: str,
    interval: str = Query("1d", pattern="^(1h|1d|1w)$"),
    limit: int = Query(30, ge=1, le=365),
):
    """Get historical prices for an asset (for charting)."""
    # TODO: Implement with stored price history
    return AssetPriceHistoryResponse(
        symbol=symbol,
        prices=[],
        interval=interval,
    )


# ── Vaults ────────────────────────────────────────────────────


@vaults_router.get("/", response_model=VaultListResponse)
async def list_vaults(
    tier: int | None = Query(None, ge=1, le=2),
    sort_by: str = Query("aum", pattern="^(aum|return_24h|return_7d|sharpe|created_at)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List vaults for the marketplace leaderboard."""
    return await _vault_svc.list_vaults(
        tier=tier, sort_by=sort_by, order=order, limit=limit, offset=offset
    )


@vaults_router.post("/create", response_model=VaultCreateResponse)
async def create_vault(req: VaultCreateRequest):
    """Deploy a new vault on Arc via VaultFactory.

    strategy_ids are accepted as off-chain metadata and echoed back in the
    response — they are not passed to the contract (v2 persistence hook).
    """
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


@vaults_router.get("/{address}", response_model=VaultDetailResponse)
async def get_vault_detail(address: str):
    """Get full vault detail including holdings, performance, traces."""
    detail = await _vault_svc.get_vault_detail(address)
    if detail is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Vault not found")
    return detail


# ── Vault Metadata (off-chain) ───────────────────────────────


@vaults_router.post("/metadata", response_model=VaultMetadataResponse)
async def store_vault_metadata(req: VaultMetadataRequest):
    """Store off-chain vault metadata (strategy associations, display name).

    Called by the frontend after a successful on-chain vault deployment.
    Idempotent — upserts on vault_address.
    """
    from archimedes.db import get_session

    session = get_session()
    try:
        meta = (
            session.query(VaultMetadata)
            .filter(VaultMetadata.vault_address == req.vault_address)
            .first()
        )
        if meta is None:
            meta = VaultMetadata(vault_address=req.vault_address)
            session.add(meta)

        meta.name = req.name
        meta.symbol = req.symbol
        meta.creator_address = req.creator_address or ""
        meta.set_strategy_ids(req.strategy_ids)
        session.commit()
        session.refresh(meta)
        return VaultMetadataResponse(**meta.to_dict())
    except Exception as exc:
        session.rollback()
        from fastapi import HTTPException
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
        meta = (
            session.query(VaultMetadata)
            .filter(VaultMetadata.vault_address == address)
            .first()
        )
        if meta is None:
            raise HTTPException(status_code=404, detail="No metadata for this vault")
        return VaultMetadataResponse(**meta.to_dict())
    finally:
        session.close()


@vaults_router.post("/{address}/derive-allocations", response_model=SetAllocationsResponse)
async def derive_vault_allocations(address: str, req: SetAllocationsRequest):
    """Derive target allocations from selected strategies.

    Evaluates strategies against live market data, aggregates their signals,
    maps symbol weights to on-chain token addresses + BPS.
    Returns the data needed for a setTargetAllocations() on-chain tx.
    Does NOT execute any on-chain transaction — the UI submits via user wallet.
    """
    from archimedes.chain.client import chain_client
    from archimedes.services.strategy_signal_evaluator import strategy_evaluator

    strategies = _strategy_provider.list_strategies()

    # Filter to selected strategies if IDs provided
    if req.strategy_ids:
        strategies = [s for s in strategies if s.id in req.strategy_ids]

    if not strategies:
        # Default: equal-weight across all available synths
        usdc_floor_bps = int(req.usdc_floor_pct * 100)
        synth_budget_bps = 10000 - usdc_floor_bps
        synth_addrs = {k: v for k, v in chain_client.settings.synth_addresses.items() if v}
        per_synth = synth_budget_bps // max(len(synth_addrs), 1)
        allocations = [
            AllocationTarget(symbol=sym, token_address=addr, weight_bps=per_synth)
            for sym, addr in synth_addrs.items()
        ]
        allocations.append(AllocationTarget(
            symbol="USDC",
            token_address=chain_client.settings.usdc_address,
            weight_bps=usdc_floor_bps,
        ))
        return SetAllocationsResponse(
            allocations=allocations,
            total_bps=sum(a.weight_bps for a in allocations),
            strategy_count=0,
        )

    # Evaluate strategies against live data
    synth_assets = [sym for sym, addr in chain_client.settings.synth_addresses.items() if addr]
    all_signals = await asyncio.to_thread(
        strategy_evaluator.evaluate_strategies, strategies, synth_assets,
    )
    usdc_floor = req.usdc_floor_pct / 100.0
    target_weights = strategy_evaluator.aggregate_signals(all_signals, usdc_floor=usdc_floor)

    # Map symbol weights → on-chain token addresses + BPS
    allocations: list[AllocationTarget] = []

    symbol_to_addr = {"USDC": chain_client.settings.usdc_address}
    symbol_to_addr.update(chain_client.settings.synth_addresses)

    for symbol, weight in target_weights.items():
        token_address = symbol_to_addr.get(symbol)
        if not token_address:
            continue
        weight_bps = int(round(weight * 10000))
        if weight_bps > 0:
            allocations.append(AllocationTarget(
                symbol=symbol,
                token_address=token_address,
                weight_bps=weight_bps,
            ))

    # Normalize to exactly 10000 BPS
    total = sum(a.weight_bps for a in allocations)
    if total > 0 and total != 10000:
        scale = 10000 / total
        for a in allocations:
            a.weight_bps = int(round(a.weight_bps * scale))
        # Drop any entries that rounded to zero
        allocations = [a for a in allocations if a.weight_bps > 0]
        # Fix rounding residue — apply to largest entry to avoid zeroing
        total = sum(a.weight_bps for a in allocations)
        if total != 10000 and allocations:
            largest = max(allocations, key=lambda a: a.weight_bps)
            largest.weight_bps += (10000 - total)

    return SetAllocationsResponse(
        allocations=allocations,
        total_bps=sum(a.weight_bps for a in allocations),
        strategy_count=len(strategies),
    )


# ── Strategies ────────────────────────────────────────────────


@strategies_router.get("/", response_model=StrategyListResponse)
async def list_strategies(
    status: str | None = Query(None, pattern="^(candidate|validated|live|retired)$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List strategies in the library. Backed by LocalStrategyProvider."""
    status_filter = StrategyStatus(status) if status else None
    strategies = _strategy_provider.list_strategies(status=status_filter)
    total = len(strategies)
    window = strategies[offset : offset + limit]
    return StrategyListResponse(
        strategies=[_to_strategy_response(s) for s in window],
        total=total,
    )


@strategies_router.get("/signals", response_model=StrategySignalsResponse)
async def get_strategy_signals():
    """Evaluate all strategies against live market data and return signals.

    This is the intelligence layer surfaced as an API — the same evaluation
    the agent runner performs each tick, but on-demand for the frontend.
    """
    from datetime import datetime, timezone

    from archimedes.services.strategy_signal_evaluator import strategy_evaluator
    from archimedes.services.redis_state import AgentStateStore

    strategies = _strategy_provider.list_strategies()
    from archimedes.chain.client import chain_client
    synth_assets = [sym for sym, addr in chain_client.settings.synth_addresses.items() if addr]

    all_signals = await asyncio.to_thread(
        strategy_evaluator.evaluate_strategies, strategies, synth_assets,
    )

    target_weights = strategy_evaluator.aggregate_signals(all_signals, usdc_floor=0.20)

    flat_count = sum(1 for ss in all_signals for s in ss.signals if s.signal.value == "flat")
    total_count = sum(len(ss.signals) for ss in all_signals)
    flat_pct = flat_count / total_count if total_count > 0 else 0

    if flat_pct > 0.6:
        regime = "risk_off"
    elif flat_pct > 0.3:
        regime = "transition"
    else:
        regime = "risk_on"

    strat_responses = []
    for ss in all_signals:
        strat_responses.append(StrategySignalResponse(
            strategy_id=ss.strategy_id,
            paper_title=ss.paper_title,
            signals=[
                SignalResponse(
                    asset=s.asset,
                    signal=s.signal.value,
                    weight=s.weight,
                    reason=s.reason,
                    strategy_name=s.strategy_name,
                )
                for s in ss.signals
            ],
        ))

    return StrategySignalsResponse(
        strategy_count=len(all_signals),
        regime=regime,
        confidence=round(1.0 - flat_pct, 2),
        target_weights=target_weights,
        strategies=strat_responses,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@strategies_router.get("/{strategy_id}", response_model=StrategyResponse)
async def get_strategy(strategy_id: str):
    """Get a single strategy by ID. Backed by LocalStrategyProvider."""
    strategy = _strategy_provider.get_strategy(strategy_id)
    if strategy is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Strategy not found")
    return _to_strategy_response(strategy)


@strategies_router.post("/construct", response_model=StrategyConstructionResponse)
async def construct_strategy(req: StrategyConstructionRequest):
    """Interactive strategy architect — the 'design me a portfolio' path.

    User intent + risk profile → Claude selects/weights paper-grounded
    strategies → deterministic guardrail → hashed reasoning trace. The
    trace_hash is the same artifact Chuan/Marten's ITracePublisher anchors
    on-chain. This is the interactive counterpart to Chuan's autonomous
    IAgentOrchestrator; they share the provider + guardrail.
    """
    # propose() may issue a blocking Claude call — keep the event loop free.
    try:
        proposal = await asyncio.to_thread(
            _architect.propose,
            req.intent,
            req.risk_profile,
            req.capital_usdc,
            req.regime,
        )
    except Exception as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail=f"LLM backend unavailable: {exc}") from exc
    guardrail = apply_guardrail(proposal)
    trace = build_construction_trace(proposal, guardrail)

    by_id = {s.strategy_id: s for s in proposal.selected}
    selected = []
    for sid, weight in sorted(guardrail.strategy_weights.items()):
        sel = by_id.get(sid)
        strat = _strategy_provider.get_strategy(sid)
        selected.append(
            ConstructionSelectionResponse(
                strategy_id=sid,
                paper_title=strat.paper_title if strat else "",
                weight=weight,
                rationale=sel.rationale if sel else "",
                paper_citation=sel.paper_citation if sel else "",
            )
        )

    return StrategyConstructionResponse(
        intent=proposal.intent,
        risk_profile=proposal.risk_profile,
        capital_usdc=proposal.capital_usdc,
        regime=proposal.regime,
        model_id=proposal.model_id,
        selected=selected,
        usyc_weight=guardrail.usyc_weight,
        overall_reasoning=proposal.overall_reasoning,
        risk_notes=proposal.risk_notes,
        guardrail_notes=guardrail.adjustments,
        trace=ConstructionTraceResponse(
            id=trace.id,
            decision_type=trace.decision_type.value,
            trigger=trace.trigger,
            timestamp=trace.timestamp.isoformat(),
            trace_hash=trace.trace_hash,
            arc_tx_hash=trace.arc_tx_hash,
            is_anchored=trace.is_anchored,
        ),
    )


# ── Reasoning Traces ──────────────────────────────────────────


@traces_router.get("/", response_model=TraceListResponse)
async def list_traces(
    vault_address: str | None = None,
    decision_type: str | None = Query(
        None, pattern="^(construction|rebalance|rotation|regime_change|skip)$"
    ),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List reasoning traces — merges on-chain IDs with off-chain metadata."""
    from archimedes.services.redis_state import AgentStateStore

    state = AgentStateStore()
    try:
        # Get off-chain traces from Redis (enriched data)
        off_chain_traces, total = await state.list_traces(
            vault_address=vault_address,
            decision_type=decision_type,
            limit=limit,
            offset=offset,
        )

        if off_chain_traces:
            traces = []
            for t in off_chain_traces:
                traces.append(TraceResponse(
                    id=t.get("id", ""),
                    vault_address=t.get("vault_address", ""),
                    decision_type=t.get("decision_type", "unknown"),
                    trigger=t.get("trigger", "unknown"),
                    timestamp=t.get("timestamp", ""),
                    reasoning=t.get("reasoning", ""),
                    confidence=t.get("confidence", 0.0),
                    trace_hash=t.get("trace_hash", ""),
                    arc_tx_hash=t.get("arc_tx_hash"),
                    is_verified=t.get("is_verified", False),
                    regime_at_decision=t.get("market_context", {}).get("regime"),
                    trades_executed=t.get("trades_executed", []),
                    strategies_referenced=t.get("strategies_referenced", []),
                    # Commit-reveal temporal binding
                    commit_tx_hash=t.get("commit_tx_hash"),
                    commit_block_number=t.get("commit_block_number"),
                    reveal_tx_hash=t.get("reveal_tx_hash"),
                    reveal_block_number=t.get("reveal_block_number"),
                    trade_tx_hash=t.get("trade_tx_hash"),
                    trade_block_number=t.get("trade_block_number"),
                    temporal_binding_valid=t.get("temporal_binding_valid"),
                ))
            return TraceListResponse(traces=traces, total=total)

        # Fallback: read on-chain traces directly if no off-chain data yet
        from archimedes.chain.trace_publisher import trace_publisher

        traces: list[TraceResponse] = []
        try:
            total_count = await trace_publisher.get_total_trace_count()
            start = max(1, total_count - offset - limit + 1)
            end = max(1, total_count - offset)

            for trace_id in range(end, start - 1, -1):
                detail = await trace_publisher.get_trace_by_id(trace_id)
                if detail is None:
                    continue

                if vault_address and detail["vault"].lower() != vault_address.lower():
                    continue

                from datetime import datetime, timezone

                traces.append(
                    TraceResponse(
                        id=str(trace_id),
                        vault_address=detail["vault"],
                        decision_type="rebalance",
                        trigger="on-chain",
                        timestamp=datetime.fromtimestamp(
                            detail["timestamp"], tz=timezone.utc
                        ).isoformat(),
                        reasoning="On-chain trace (off-chain metadata not available)",
                        confidence=0.0,
                        trace_hash=detail["trace_hash"],
                        is_verified=True,
                    )
                )
        except Exception:
            pass

        return TraceListResponse(traces=traces, total=len(traces))
    finally:
        await state.close()


@traces_router.get("/{trace_id}", response_model=TraceResponse)
async def get_trace(trace_id: str):
    """Get a single reasoning trace by ID (on-chain or off-chain hash)."""
    from archimedes.services.redis_state import AgentStateStore
    from archimedes.chain.trace_publisher import trace_publisher
    from fastapi import HTTPException
    from datetime import datetime, timezone

    state = AgentStateStore()
    try:
        # Try off-chain first (trace_id may be a hash)
        off_chain = await state.get_trace(trace_id)
        if off_chain:
            return TraceResponse(
                id=off_chain.get("id", trace_id),
                vault_address=off_chain.get("vault_address", ""),
                decision_type=off_chain.get("decision_type", "unknown"),
                trigger=off_chain.get("trigger", "unknown"),
                timestamp=off_chain.get("timestamp", ""),
                reasoning=off_chain.get("reasoning", ""),
                confidence=off_chain.get("confidence", 0.0),
                trace_hash=off_chain.get("trace_hash", ""),
                arc_tx_hash=off_chain.get("arc_tx_hash"),
                is_verified=off_chain.get("is_verified", False),
                regime_at_decision=off_chain.get("market_context", {}).get("regime"),
                trades_executed=off_chain.get("trades_executed", []),
                strategies_referenced=off_chain.get("strategies_referenced", []),
                # Commit-reveal temporal binding
                commit_tx_hash=off_chain.get("commit_tx_hash"),
                commit_block_number=off_chain.get("commit_block_number"),
                reveal_tx_hash=off_chain.get("reveal_tx_hash"),
                reveal_block_number=off_chain.get("reveal_block_number"),
                trade_tx_hash=off_chain.get("trade_tx_hash"),
                trade_block_number=off_chain.get("trade_block_number"),
                temporal_binding_valid=off_chain.get("temporal_binding_valid"),
            )

        # Try on-chain numeric ID
        try:
            int_id = int(trace_id)
        except ValueError:
            raise HTTPException(status_code=404, detail="Trace not found")

        detail = await trace_publisher.get_trace_by_id(int_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="Trace not found")

        return TraceResponse(
            id=trace_id,
            vault_address=detail["vault"],
            decision_type="rebalance",
            trigger="on-chain",
            timestamp=datetime.fromtimestamp(
                detail["timestamp"], tz=timezone.utc
            ).isoformat(),
            reasoning="On-chain trace (off-chain metadata not available)",
            confidence=0.0,
            trace_hash=detail["trace_hash"],
            is_verified=True,
        )
    finally:
        await state.close()


@traces_router.post("/publish", response_model=TracePublishResponse)
async def publish_trace(req: TracePublishRequest):
    """Publish a reasoning trace: compute hash, anchor on Arc, persist off-chain.

    This is the primary endpoint for the Reasoning page's "Publish" flow
    and for the agent runner's autonomous rebalance traces.
    """
    import uuid
    from datetime import datetime, timezone

    from archimedes.models.trace import DecisionType, ReasoningTrace
    from archimedes.chain.trace_publisher import trace_publisher
    from archimedes.services.redis_state import AgentStateStore
    from fastapi import HTTPException

    # Validate decision_type
    try:
        dt = DecisionType(req.decision_type)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid decision_type: {req.decision_type}. "
                   f"Must be one of: construction, rebalance, rotation, regime_change, skip",
        )

    # Build the trace
    trace = ReasoningTrace(
        id=str(uuid.uuid4()),
        vault_address=req.vault_address,
        decision_type=dt,
        trigger=req.trigger,
        timestamp=datetime.now(timezone.utc),
        market_context=req.market_context,
        portfolio_before=req.portfolio_before,
        portfolio_after=req.portfolio_after,
        reasoning=req.reasoning,
        confidence=req.confidence,
        trades_executed=req.trades_executed,
        strategies_referenced=req.strategies_referenced,
    )

    # Compute keccak256 hash
    trace.compute_hash()

    # Persist off-chain to Redis
    off_chain_data = {
        "id": trace.id,
        "vault_address": trace.vault_address,
        "decision_type": trace.decision_type.value,
        "trigger": trace.trigger,
        "timestamp": trace.timestamp.isoformat(),
        "market_context": trace.market_context,
        "portfolio_before": trace.portfolio_before,
        "portfolio_after": trace.portfolio_after,
        "reasoning": trace.reasoning,
        "confidence": trace.confidence,
        "trades_executed": trace.trades_executed,
        "strategies_referenced": trace.strategies_referenced,
        "trace_hash": trace.trace_hash,
        "arc_tx_hash": None,
        "is_verified": False,
    }

    # Publish on-chain
    arc_tx_hash = None
    try:
        arc_tx_hash = await trace_publisher.publish(trace)
        if arc_tx_hash:
            off_chain_data["arc_tx_hash"] = arc_tx_hash
            off_chain_data["is_verified"] = True
    except Exception as e:
        # On-chain publish failed — still persist off-chain
        import logging
        logging.getLogger(__name__).error(f"On-chain publish failed: {e}")

    # Save to Redis regardless
    state = AgentStateStore()
    try:
        await state.save_trace(off_chain_data)
    finally:
        await state.close()

    return TracePublishResponse(
        id=trace.id,
        trace_hash=trace.trace_hash,
        arc_tx_hash=arc_tx_hash,
        is_anchored=arc_tx_hash is not None,
        timestamp=trace.timestamp.isoformat(),
        vault_address=trace.vault_address,
        decision_type=trace.decision_type.value,
    )


@traces_router.get("/{trace_id}/verify", response_model=TraceVerifyResponse)
async def verify_trace(trace_id: str):
    """Verify a reasoning trace against its on-chain anchor.

    Recomputes the keccak256 hash from off-chain data and compares
    with the hash stored in ReasoningTraceRegistry on Arc.
    """
    from archimedes.chain.trace_publisher import trace_publisher
    from archimedes.services.redis_state import AgentStateStore
    from fastapi import HTTPException

    state = AgentStateStore()
    try:
        # Load off-chain trace data
        off_chain = await state.get_trace(trace_id)
        if not off_chain:
            # Try numeric ID → get hash from on-chain
            try:
                int_id = int(trace_id)
            except ValueError:
                raise HTTPException(status_code=404, detail="Trace not found")

            detail = await trace_publisher.get_trace_by_id(int_id)
            if not detail:
                raise HTTPException(status_code=404, detail="Trace not found")

            return TraceVerifyResponse(
                trace_id=int_id,
                trace_hash=detail["trace_hash"],
                is_verified=True,  # It's on-chain, so the hash is anchored
                agent=detail["agent"],
                vault=detail["vault"],
                on_chain_timestamp=detail["timestamp"],
                details="Hash is anchored on-chain (no off-chain data to recompute against)",
            )

        # We have off-chain data — verify against on-chain
        trace_hash = off_chain.get("trace_hash", "")
        is_verified = False
        agent = ""
        vault = off_chain.get("vault_address", "")
        on_chain_ts = 0
        details = ""

        if not off_chain.get("arc_tx_hash"):
            details = "Trace was not published on-chain — cannot verify"
        else:
            # Search on-chain for this hash — filter by vault first
            try:
                vault_traces = await trace_publisher.get_traces_by_vault(vault)
                on_chain_count = await trace_publisher.get_total_trace_count()
                is_verified = False
                agent = ""
                on_chain_ts = 0

                for tid in vault_traces if vault_traces else range(1, on_chain_count + 1):
                    detail = await trace_publisher.get_trace_by_id(tid)
                    if detail and detail["trace_hash"] == trace_hash.removeprefix("0x"):
                        is_verified = True
                        agent = detail["agent"]
                        on_chain_ts = detail["timestamp"]
                        break
                details = (
                    "Hash verified on-chain ✓"
                    if is_verified
                    else "Hash not found on-chain"
                )
            except Exception as e:
                details = f"Verification failed: {e}"

        return TraceVerifyResponse(
            trace_id=int(trace_id) if trace_id.isdigit() else 0,
            trace_hash=trace_hash,
            is_verified=is_verified,
            agent=agent,
            vault=vault,
            on_chain_timestamp=on_chain_ts,
            details=details,
            # Temporal binding verification
            commit_block_number=off_chain.get("commit_block_number"),
            trade_block_number=off_chain.get("trade_block_number"),
            reveal_block_number=off_chain.get("reveal_block_number"),
            temporal_binding_valid=off_chain.get("temporal_binding_valid"),
        )
    finally:
        await state.close()


# ── Regime ────────────────────────────────────────────────────


@traces_router.get("/{trace_id}/canonical")
async def get_trace_canonical(trace_id: str):
    """Get the canonical JSON used to compute the trace hash.

    Third parties can use this to verify: contract.verifyTrace(id, canonicalBytes)
    where canonicalBytes = utf8Bytes of this response.
    """
    from archimedes.services.redis_state import AgentStateStore
    from fastapi import HTTPException
    from fastapi.responses import PlainTextResponse

    state = AgentStateStore()
    try:
        off_chain = await state.get_trace(trace_id)
        if not off_chain:
            raise HTTPException(status_code=404, detail="Trace not found")

        # Reconstruct a ReasoningTrace from the stored data
        trace = ReasoningTrace(
            id=off_chain["id"],
            vault_address=off_chain["vault_address"],
            decision_type=DecisionType(off_chain["decision_type"]),
            trigger=off_chain["trigger"],
            timestamp=off_chain["timestamp"],
            market_context=off_chain.get("market_context", {}),
            portfolio_before=off_chain.get("portfolio_before", {}),
            portfolio_after=off_chain.get("portfolio_after", {}),
            reasoning=off_chain.get("reasoning", ""),
            confidence=off_chain.get("confidence", 0.0),
            trades_executed=off_chain.get("trades_executed", []),
            strategies_referenced=off_chain.get("strategies_referenced", []),
        )
        return PlainTextResponse(trace.canonical_json(), media_type="application/json")
    finally:
        await state.close()


@regime_router.get("/current", response_model=RegimeResponse)
async def get_current_regime():
    """Get current market regime — reads live state from Redis (agent writes it)."""
    from archimedes.services.redis_state import AgentStateStore

    state = AgentStateStore()
    try:
        data = await state.load_regime()
    finally:
        await state.close()

    from archimedes.api.schemas import RegimeSignalsResponse

    if data:
        return RegimeResponse(
            regime=data.get("regime", "unknown"),
            confidence=data.get("confidence", 0.0),
            timestamp=data.get("timestamp", ""),
            regime_changed=False,
            signals=RegimeSignalsResponse(
                vix_level=0.0,
                sp500_above_ma50=True,
                sp500_above_ma200=True,
            ),
        )

    return RegimeResponse(
        regime="unknown",
        confidence=0.0,
        timestamp="",
        regime_changed=False,
        signals=RegimeSignalsResponse(
            vix_level=0.0,
            sp500_above_ma50=True,
            sp500_above_ma200=True,
        ),
    )


# ── Swap ──────────────────────────────────────────────────────


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
async def get_swap_quote(
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
        amount_in_raw = int(amount_in * 10**decimals_in)

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
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"Quote failed: {str(e)}")


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

            # Hackathon display estimate: USDC side if present, otherwise count the pair conservatively.
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


# ── Config ────────────────────────────────────────────────────


@config_router.get("/contracts", response_model=ContractAddressesResponse)
async def get_contract_addresses():
    """Get all deployed contract addresses."""
    return await _config_svc.get_contract_addresses()


# ── Agent Status ─────────────────────────────────────────────


@agent_router.get("/status", response_model=AgentStatusResponse)
async def get_agent_status():
    """Get autonomous agent health and state — reads from Redis."""
    from datetime import datetime, timezone

    from archimedes.services.redis_state import AgentStateStore

    state = AgentStateStore()
    try:
        heartbeat = await state.get_heartbeat()
        regime_data = await state.load_regime()
        events = await state.get_events(count=10)

        alive = False
        if heartbeat:
            try:
                hb_time = datetime.fromisoformat(heartbeat)
                age = (datetime.now(timezone.utc) - hb_time).total_seconds()
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
    finally:
        await state.close()
