"""Proposals API — read endpoint for the strategy_proposals episodic table.

Exposes ``GET /api/proposals/`` with filtering and pagination.
Write path lives in ``services/strategy_memory.py`` and is called from
the generation pipeline / fusion / architect code paths.

Owner-scoped (privacy fix, `dbrowneup/proposals-owner-scope`): every
``StrategyProposal`` row carries a user's private ``intent`` (their
strategic brief), full ``strategy_spec``, and ``rigor_verdict`` — including
rejected candidates. Both endpoints below previously had no auth at all and
returned EVERY user's rows. They now require a verified SIWE session
(``require_verified_wallet``, the same dependency other owner-scoped routes
use — see ``strategies_routes.py`` / ``vaults_routes.py``) and filter to the
caller's own wallet in the DB query. A legacy row with ``owner_wallet IS
NULL`` (written before this column existed) is returned to NO ONE.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from archimedes.api.auth_siwe import require_verified_wallet

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
    wallet: str = Depends(require_verified_wallet),
):
    """List episodic strategy proposals with filtering and pagination.

    Every fusion / architect / agent generation writes a proposal row here.
    The endpoint is read-only; writes happen via ``strategy_memory.persist_proposal``.

    Owner-scoped: 401 without a verified SIWE session; scoped to the caller's
    own ``owner_wallet`` (case-insensitive) otherwise. The DB query does the
    filtering — see ``strategy_memory.query_proposals``.
    """
    from archimedes.services.strategy_memory import query_proposals

    proposals, total = query_proposals(
        verdict=verdict,
        agent=agent,
        since=since,
        limit=limit,
        offset=offset,
        owner_wallet=wallet,
    )
    return {
        "proposals": proposals,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@proposals_router.get("/{generation_id}/siblings")
async def get_proposal_siblings(generation_id: str, wallet: str = Depends(require_verified_wallet)):
    """Get all proposals from the same generation — 'considered alternatives'.

    Owner-scoped: 401 without a verified SIWE session; a generation owned by
    another wallet (or a legacy NULL-owner generation) returns an empty
    sibling list rather than 403/404 — existence of another user's generation
    stays unconfirmed, mirroring the 404-not-403 pattern used elsewhere in
    this codebase for owner-gated resources.
    """
    from archimedes.services.strategy_memory import get_siblings

    siblings = get_siblings(generation_id, owner_wallet=wallet)
    return {
        "generation_id": generation_id,
        "siblings": siblings,
        "count": len(siblings),
    }
