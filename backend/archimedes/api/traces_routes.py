"""Reasoning trace endpoints — /api/traces/*."""

from __future__ import annotations

import logging
from datetime import UTC

from fastapi import APIRouter, Depends, Query, Request

from archimedes.api.auth_guard import require_internal_agent_key
from archimedes.api.limiter import limiter
from archimedes.api.schemas import (
    TraceListResponse,
    TracePublishRequest,
    TracePublishResponse,
    TraceResponse,
    TraceVerifyResponse,
)
from archimedes.models.trace import DecisionType, ReasoningTrace

logger = logging.getLogger(__name__)

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
        try:
            off_chain_traces, total = await state.list_traces(
                vault_address=vault_address,
                decision_type=decision_type,
                limit=limit,
                offset=offset,
            )
        except Exception:
            logger.warning("list_traces: Redis unavailable — falling back to on-chain-only listing", exc_info=True)
            off_chain_traces, total = [], 0

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
                        temporal_binding_source=t.get("temporal_binding_source", "none"),
                    )
                )
            return TraceListResponse(traces=traces, total=total)

        from archimedes.chain.trace_publisher import trace_publisher

        traces: list[TraceResponse] = []
        # Real registry size (#1356): read once up front so it survives even
        # if a later per-trace fetch in the loop fails partway through. The
        # old code returned `total=len(traces)` here, which is the CURRENT
        # PAGE size (capped at `limit`), not the registry size — pagination
        # reported a smaller universe than actually exists. `total_count` is
        # the value this route promises callers via `start`/`end` below; it
        # must be the same value in the response.
        total_count = 0
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
                        # "unknown", not "rebalance" (#1356): the on-chain
                        # anchor does not record which decision type
                        # produced it, so asserting "rebalance" for every
                        # trace on this path is an invented fact. "unknown"
                        # is the same default the off-chain path already
                        # uses when its own data lacks the field.
                        decision_type="unknown",
                        trigger="on-chain",
                        timestamp=datetime.fromtimestamp(detail["timestamp"], tz=UTC).isoformat(),
                        reasoning="On-chain trace (off-chain metadata not available)",
                        confidence=0.0,
                        trace_hash=detail["trace_hash"],
                        is_verified=True,
                    )
                )
        except Exception:
            logger.debug("on-chain trace listing failed", exc_info=True)

        return TraceListResponse(traces=traces, total=total_count)
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
        try:
            off_chain = await state.get_trace(trace_id)
        except Exception:
            logger.warning("get_trace: Redis unavailable — falling back to on-chain-only lookup", exc_info=True)
            off_chain = None
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
                temporal_binding_source=off_chain.get("temporal_binding_source", "none"),
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
            # "unknown", not "rebalance" — see the identical note on the list
            # route above (#1356). The on-chain anchor doesn't record which
            # decision type produced it.
            decision_type="unknown",
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
async def publish_trace(req: TracePublishRequest, _: None = Depends(require_internal_agent_key)):
    """Publish a reasoning trace: compute hash, anchor on Arc, persist off-chain.

    Internal-only: requires X-Internal-Agent-Key header.
    """
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
        logging.getLogger(__name__).error(f"On-chain publish failed: {e}")

    state = AgentStateStore()
    try:
        await state.save_trace(off_chain_data)
    except Exception:
        # WRITE path: unlike the read endpoints' graceful degradation above,
        # a failed off-chain persist must NOT return 200 — the caller would
        # get is_anchored=True for a trace whose full reasoning body was never
        # stored, i.e. an on-chain anchor that can never be re-verified against
        # its off-chain content (claim-integrity). Fail loudly and retryably;
        # include the anchor tx so the caller knows the on-chain half landed.
        logger.error("publish_trace: failed to persist off-chain trace data (Redis unavailable?)", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail=(
                f"Trace anchored on-chain (tx {arc_tx_hash}) but off-chain persistence failed — retry publish."
                if arc_tx_hash
                else "Off-chain trace persistence failed (no on-chain anchor was recorded either) — retry publish."
            ),
        ) from None
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
async def verify_trace(trace_id: str, request: Request):  # noqa: ARG001 — slowapi @limiter.exempt inspects param name
    """Verify a reasoning trace against its on-chain anchor."""
    from fastapi import HTTPException

    from archimedes.chain.trace_publisher import trace_publisher
    from archimedes.services.redis_state import AgentStateStore

    state = AgentStateStore()
    try:
        try:
            off_chain = await state.get_trace(trace_id)
        except Exception:
            # A Redis outage is a loud absence, not a verification result
            # (CLAUDE.md § fail-soft) — mirrors get_trace_canonical's 503
            # below. Previously this exception was swallowed and fell
            # through to the "no off-chain data" branch, which returns
            # is_verified=True with zero hashes compared — an outage was
            # silently upgrading every trace to a green check (#1359).
            logger.warning("verify_trace: Redis unavailable — cannot verify without the store", exc_info=True)
            raise HTTPException(
                status_code=503, detail="Trace store temporarily unavailable — retry verification."
            ) from None
        if not off_chain:
            # The store IS reachable and simply has no record for this id —
            # the only case allowed to report anchored_only.
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
                verification_mode="anchored_only",
                agent=detail["agent"],
                vault=detail["vault"],
                on_chain_timestamp=detail["timestamp"],
                details="Hash is anchored on-chain — no off-chain trace body was stored, so no hashes were compared",
            )

        trace_hash = off_chain.get("trace_hash", "")
        is_verified = False
        verification_mode = "failed"
        agent = ""
        vault = off_chain.get("vault_address", "")
        on_chain_ts = 0
        details = ""

        arc_tx_hash = off_chain.get("arc_tx_hash")
        if not arc_tx_hash:
            details = "Trace was not published on-chain -- cannot verify"
        else:
            try:
                # O(1): fetch the receipt for the cached arc_tx_hash and decode
                # the TracePublished event directly. Replaces the prior O(N)
                # getTracesByVault → getTraceById scan that 504'd on vaults with
                # 40+ traces.
                detail = await trace_publisher.get_trace_by_tx_hash(arc_tx_hash)
                if detail is None:
                    details = "On-chain receipt not found for cached arc_tx_hash"
                else:
                    expected = trace_hash.removeprefix("0x").lower()
                    on_chain = detail["trace_hash"].removeprefix("0x").lower()
                    if expected and expected == on_chain:
                        is_verified = True
                        verification_mode = "hash_matched"
                        agent = detail["agent"]
                        on_chain_ts = detail["timestamp"]
                        # Keep vault as recorded off-chain; surface the on-chain
                        # vault when off-chain didn't record one.
                        if not vault:
                            vault = detail["vault"]
                        details = "Hash verified on-chain ✓"
                    else:
                        details = "Hash mismatch: on-chain trace does not match off-chain hash"
            except Exception as e:
                details = f"Verification failed: {e}"

        return TraceVerifyResponse(
            trace_id=int(trace_id) if trace_id.isdigit() else 0,
            trace_hash=trace_hash,
            is_verified=is_verified,
            verification_mode=verification_mode,
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
        try:
            off_chain = await state.get_trace(trace_id)
        except Exception:
            # 503, not 404: "store unavailable" must stay distinguishable from
            # "trace doesn't exist" — a canonical-JSON consumer (hash
            # re-verification) should retry, not conclude the trace is gone.
            logger.warning("get_trace_canonical: Redis unavailable", exc_info=True)
            raise HTTPException(status_code=503, detail="Trace store temporarily unavailable — retry.") from None
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
            # Hashed field (#903): without it the rebuilt canonical bytes can
            # never match the committed hash for paper-grounded decisions.
            consulted_paper_hashes=off_chain.get("consulted_paper_hashes", []),
        )
        return PlainTextResponse(trace.canonical_json(), media_type="application/json")
    finally:
        await state.close()
