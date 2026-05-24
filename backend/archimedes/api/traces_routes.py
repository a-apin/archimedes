"""Reasoning trace endpoints — /api/traces/*."""

from __future__ import annotations

from datetime import UTC

from fastapi import APIRouter, Query, Request

from archimedes.api.limiter import limiter
from archimedes.api.schemas import (
    TraceListResponse,
    TracePublishRequest,
    TracePublishResponse,
    TraceResponse,
    TraceVerifyResponse,
)
from archimedes.models.trace import DecisionType, ReasoningTrace

traces_router = APIRouter(prefix="/api/traces", tags=["traces"])


@traces_router.get("/", response_model=TraceListResponse)
async def list_traces(
    vault_address: str | None = None,
    decision_type: str | None = Query(None, pattern="^(construction|rebalance|rotation|regime_change|skip)$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List reasoning traces -- merges on-chain IDs with off-chain metadata."""
    from archimedes.services.redis_state import AgentStateStore

    state = AgentStateStore()
    try:
        off_chain_traces, total = await state.list_traces(
            vault_address=vault_address,
            decision_type=decision_type,
            limit=limit,
            offset=offset,
        )

        if off_chain_traces:
            traces = []
            for t in off_chain_traces:
                if t.get("trigger") == "empty_vault":
                    continue
                traces.append(
                    TraceResponse(
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
                        commit_tx_hash=t.get("commit_tx_hash"),
                        commit_block_number=t.get("commit_block_number"),
                        reveal_tx_hash=t.get("reveal_tx_hash"),
                        reveal_block_number=t.get("reveal_block_number"),
                        trade_tx_hash=t.get("trade_tx_hash"),
                        trade_block_number=t.get("trade_block_number"),
                        temporal_binding_valid=t.get("temporal_binding_valid"),
                    )
                )
            return TraceListResponse(traces=traces, total=total)

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

                from datetime import datetime

                traces.append(
                    TraceResponse(
                        id=str(trace_id),
                        vault_address=detail["vault"],
                        decision_type="rebalance",
                        trigger="on-chain",
                        timestamp=datetime.fromtimestamp(detail["timestamp"], tz=UTC).isoformat(),
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
    from datetime import datetime

    from fastapi import HTTPException

    from archimedes.chain.trace_publisher import trace_publisher
    from archimedes.services.redis_state import AgentStateStore

    state = AgentStateStore()
    try:
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
                commit_tx_hash=off_chain.get("commit_tx_hash"),
                commit_block_number=off_chain.get("commit_block_number"),
                reveal_tx_hash=off_chain.get("reveal_tx_hash"),
                reveal_block_number=off_chain.get("reveal_block_number"),
                trade_tx_hash=off_chain.get("trade_tx_hash"),
                trade_block_number=off_chain.get("trade_block_number"),
                temporal_binding_valid=off_chain.get("temporal_binding_valid"),
            )

        try:
            int_id = int(trace_id)
        except ValueError:
            raise HTTPException(status_code=404, detail="Trace not found") from None

        detail = await trace_publisher.get_trace_by_id(int_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="Trace not found")

        return TraceResponse(
            id=trace_id,
            vault_address=detail["vault"],
            decision_type="rebalance",
            trigger="on-chain",
            timestamp=datetime.fromtimestamp(detail["timestamp"], tz=UTC).isoformat(),
            reasoning="On-chain trace (off-chain metadata not available)",
            confidence=0.0,
            trace_hash=detail["trace_hash"],
            is_verified=True,
        )
    finally:
        await state.close()


@traces_router.post("/publish", response_model=TracePublishResponse)
async def publish_trace(req: TracePublishRequest):
    """Publish a reasoning trace: compute hash, anchor on Arc, persist off-chain."""
    import uuid
    from datetime import datetime

    from fastapi import HTTPException

    from archimedes.chain.trace_publisher import trace_publisher
    from archimedes.services.redis_state import AgentStateStore

    try:
        dt = DecisionType(req.decision_type)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid decision_type: {req.decision_type}. "
            f"Must be one of: construction, rebalance, rotation, regime_change, skip",
        ) from None

    trace = ReasoningTrace(
        id=str(uuid.uuid4()),
        vault_address=req.vault_address,
        decision_type=dt,
        trigger=req.trigger,
        timestamp=datetime.now(UTC),
        market_context=req.market_context,
        portfolio_before=req.portfolio_before,
        portfolio_after=req.portfolio_after,
        reasoning=req.reasoning,
        confidence=req.confidence,
        trades_executed=req.trades_executed,
        strategies_referenced=req.strategies_referenced,
    )

    trace.compute_hash()

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

    arc_tx_hash = None
    try:
        arc_tx_hash = await trace_publisher.publish(trace)
        if arc_tx_hash:
            off_chain_data["arc_tx_hash"] = arc_tx_hash
            off_chain_data["is_verified"] = True
    except Exception as e:
        import logging

        logging.getLogger(__name__).error(f"On-chain publish failed: {e}")

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
@limiter.exempt
async def verify_trace(trace_id: str, request: Request):
    """Verify a reasoning trace against its on-chain anchor."""
    from fastapi import HTTPException

    from archimedes.chain.trace_publisher import trace_publisher
    from archimedes.services.redis_state import AgentStateStore

    state = AgentStateStore()
    try:
        off_chain = await state.get_trace(trace_id)
        if not off_chain:
            try:
                int_id = int(trace_id)
            except ValueError:
                raise HTTPException(status_code=404, detail="Trace not found") from None

            detail = await trace_publisher.get_trace_by_id(int_id)
            if not detail:
                raise HTTPException(status_code=404, detail="Trace not found")

            return TraceVerifyResponse(
                trace_id=int_id,
                trace_hash=detail["trace_hash"],
                is_verified=True,
                agent=detail["agent"],
                vault=detail["vault"],
                on_chain_timestamp=detail["timestamp"],
                details="Hash is anchored on-chain (no off-chain data to recompute against)",
            )

        trace_hash = off_chain.get("trace_hash", "")
        is_verified = False
        agent = ""
        vault = off_chain.get("vault_address", "")
        on_chain_ts = 0
        details = ""

        if not off_chain.get("arc_tx_hash"):
            details = "Trace was not published on-chain -- cannot verify"
        else:
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
                details = "Hash verified on-chain ✓" if is_verified else "Hash not found on-chain"
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
            commit_block_number=off_chain.get("commit_block_number"),
            trade_block_number=off_chain.get("trade_block_number"),
            reveal_block_number=off_chain.get("reveal_block_number"),
            temporal_binding_valid=off_chain.get("temporal_binding_valid"),
        )
    finally:
        await state.close()


@traces_router.get("/{trace_id}/canonical")
async def get_trace_canonical(trace_id: str):
    """Get the canonical JSON used to compute the trace hash."""
    from fastapi import HTTPException
    from fastapi.responses import PlainTextResponse

    from archimedes.services.redis_state import AgentStateStore

    state = AgentStateStore()
    try:
        off_chain = await state.get_trace(trace_id)
        if not off_chain:
            raise HTTPException(status_code=404, detail="Trace not found")

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
