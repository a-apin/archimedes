"""Proposals API — read endpoint for the strategy_proposals episodic table.

Exposes ``GET /api/strategies/proposals`` with filtering and pagination.
Write path lives in ``services/strategy_memory.py`` and is called from
the generation pipeline / fusion / architect code paths.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

proposals_router = APIRouter(prefix="/api/proposals", tags=["proposals"])


@proposals_router.get("/")
async def list_proposals(
    verdict: str | None = Query(
        None, description="Filter by verdict: rigor_pass | rigor_fail | user_rejected | pending"
    ),
    agent: str | None = Query(None, description="Filter by agent: fusion | architect | agent"),
    since: str | None = Query(None, description="ISO datetime lower bound"),
    limit: int = Query(50, ge=1, le=200, description="Page size"),
    offset: int = Query(0, ge=0, description="Offset"),
):
    """List episodic strategy proposals with filtering and pagination.

    Every fusion / architect / agent generation writes a proposal row here.
    The endpoint is read-only; writes happen via ``strategy_memory.persist_proposal``.
    """
    from archimedes.services.strategy_memory import query_proposals

    proposals, total = query_proposals(
        verdict=verdict,
        agent=agent,
        since=since,
        limit=limit,
        offset=offset,
    )
    return {
        "proposals": proposals,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@proposals_router.get("/{generation_id}/siblings")
async def get_proposal_siblings(generation_id: str):
    """Get all proposals from the same generation — 'considered alternatives'."""
    from archimedes.services.strategy_memory import get_siblings

    siblings = get_siblings(generation_id)
    return {
        "generation_id": generation_id,
        "siblings": siblings,
        "count": len(siblings),
    }
