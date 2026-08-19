"""Proposals API — read endpoint for the strategy_proposals episodic table.

Exposes ``GET /api/proposals/`` with filtering and pagination.
Write path lives in ``services/strategy_memory.py`` and is called from
the generation pipeline / fusion / architect code paths.

Owner-scoped (privacy fix, `dbrowneup/proposals-owner-scope`): every
``StrategyProposal`` row carries a user's private ``intent`` (their
strategic brief), full ``strategy_spec``, and ``rigor_verdict`` — including
rejected candidates. Both endpoints below previously had no auth at all and
returned EVERY user's rows. They now require Better Auth account and filter by
canonical user ID. Verified linked wallet is used only for legacy compatibility.
Unowned legacy rows are returned to no one.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from archimedes.api.account_auth import CurrentUser, require_current_user
from archimedes.api.wallet_routes import get_linked_wallet_address

proposals_router = APIRouter(prefix="/api/proposals", tags=["proposals"])


@proposals_router.get("/")
async def list_proposals(
    request: Request,
    verdict: str | None = Query(
        None, description="Filter by verdict: rigor_pass | rigor_fail | user_rejected | pending"
    ),
    agent: str | None = Query(None, description="Filter by agent: fusion | architect | agent"),
    since: str | None = Query(None, description="ISO datetime lower bound"),
    limit: int = Query(50, ge=1, le=200, description="Page size"),
    offset: int = Query(0, ge=0, description="Offset"),
    user: CurrentUser = Depends(require_current_user),
):
    """List episodic strategy proposals with filtering and pagination.

    Every fusion / architect / agent generation writes a proposal row here.
    The endpoint is read-only; writes happen via ``strategy_memory.persist_proposal``.

    Owner-scoped: 401 without account session; canonical user ID is primary and
    linked wallet is legacy fallback. DB query does
    filtering — see ``strategy_memory.query_proposals``.
    """
    from archimedes.services.strategy_memory import query_proposals

    proposals, total = query_proposals(
        verdict=verdict,
        agent=agent,
        since=since,
        limit=limit,
        offset=offset,
        owner_user_id=user.id,
        owner_wallet=get_linked_wallet_address(request),
    )
    return {
        "proposals": proposals,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@proposals_router.get("/{generation_id}/siblings")
async def get_proposal_siblings(
    generation_id: str,
    request: Request,
    user: CurrentUser = Depends(require_current_user),
):
    """Get all proposals from the same generation — 'considered alternatives'.

    Owner-scoped: 401 without account session; generation owned by
    another user returns HTTP 200 with an
    empty sibling list — never a 403 or 404. Returning an empty list rather than
    an error keeps the existence of another user's generation unconfirmed, the
    same non-disclosure goal as the 404-not-403 convention used elsewhere for
    owner-gated resources.
    """
    from archimedes.services.strategy_memory import get_siblings

    siblings = get_siblings(
        generation_id,
        owner_user_id=user.id,
        owner_wallet=get_linked_wallet_address(request),
    )
    return {
        "generation_id": generation_id,
        "siblings": siblings,
        "count": len(siblings),
    }
