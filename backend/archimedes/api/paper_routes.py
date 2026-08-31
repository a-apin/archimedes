"""Paper-trading endpoints — /api/paper/* (MVP: verdict → paper-trade).

Ownership is CANONICAL account identity (#1194): every route requires a Better
Auth session and every deployment is owned by ``owner_user_id``. Account-first
users never hold a SIWE session post-#1194, so a wallet-session gate here
would 401 exactly the users the product creates. Paper deploys are SIMULATED —
no funds move — so an account session is the correct strength of proof (the
relaxed-lookup boundary in ``wallet_routes.get_linked_wallet_address`` applies:
no fresh wallet signature is required for a non-money-moving action; the day a
paper→real promotion exists, THAT action takes the fresh-signature gate).

``owner_wallet`` is recorded as provenance only (the caller's linked wallet at
deploy time, when one exists) — it never grants access. The table shipped with
this feature, so there are no legacy wallet-owned rows and no fallback tier.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from archimedes.api.account_auth import CurrentUser, require_current_user
from archimedes.api.limiter import limiter
from archimedes.api.wallet_routes import get_linked_wallet_address
from archimedes.db import get_session, init_db
from archimedes.models.paper_store import STATUS_STOPPED, PaperDeployment
from archimedes.services.paper_trading import (
    COVERAGE_BROKEN_LOG,
    PaperTraceCoverageError,
    advance_deployment,
    create_deployment,
    deployment_summary,
)
from archimedes.services.strategy_dsl import DSLError

logger = logging.getLogger(__name__)

paper_router = APIRouter(prefix="/api/paper", tags=["paper"])


def _spec_for_strategy(session, strategy_id: str, caller_wallet: str | None, caller_user_id: str | None) -> dict:
    """The spec to snapshot, gated on OWNERSHIP of the source strategy.

    The validated DSL spec is the strategy's EXECUTABLE LOGIC — the derivation,
    not the card. Publishing a strategy puts its name, methodology and metrics
    on a public board; it does not hand over the machine-readable
    implementation for anyone to snapshot into their own ledger. So this asks
    ``is_strategy_reasoning_visible``, not ``is_strategy_visible``: curated /
    ``is_example`` house content stays available to everyone, a user's row is
    available to its owner, and ``is_published`` alone grants nothing (#1557 —
    this call site used the card predicate, so any published row's spec was
    deployable by any signed-in stranger).

    This fails CLOSED on purpose. If a marketplace/licensing flow later makes
    "paper-trade someone else's published strategy" a deliberate product
    decision, that flow reopens it explicitly — with whatever attribution or
    licensing check it decides on — rather than inheriting the permission by
    accident from a predicate that was never about reasoning.

    404 for both "no such strategy" and "not yours": existence stays private
    (#850 idiom, same as ``_owned_deployment`` below).
    """
    from archimedes.models.strategy_store import StrategyRecord
    from archimedes.services.strategy_visibility import is_strategy_reasoning_visible

    row = session.query(StrategyRecord).filter_by(id=strategy_id).first()
    if row is None or not is_strategy_reasoning_visible(row, caller_wallet, caller_user_id=caller_user_id):
        raise HTTPException(status_code=404, detail="Strategy not found")
    spec = (row.to_dict() or {}).get("strategy_spec")
    if not spec:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "This strategy has no machine-readable spec to paper-trade.",
                "reason": "no_strategy_spec",
            },
        )
    return spec


def _owned_deployment(session, deployment_id: str, user_id: str) -> PaperDeployment:
    """Fetch a deployment the caller owns — 404 for missing AND for not-yours:
    existence is private (#850 idiom). Ownership is owner_user_id only."""
    dep = session.query(PaperDeployment).filter_by(id=deployment_id).first()
    if dep is None or dep.owner_user_id != user_id:
        raise HTTPException(status_code=404, detail="Paper deployment not found")
    return dep


@paper_router.post("/deployments", status_code=201)
@limiter.limit("10/minute")
async def deploy_paper(
    request: Request,
    response: Response,  # noqa: ARG001 — slowapi header injection (#1182 invariant)
    body: dict,
    user: CurrentUser = Depends(require_current_user),
):
    strategy_id = str(body.get("strategy_id") or "").strip()
    if not strategy_id:
        raise HTTPException(status_code=422, detail="strategy_id is required")

    # Provenance only — never an access grant, may be None (account-only user).
    linked_wallet = get_linked_wallet_address(request)

    init_db()
    with get_session() as session:
        spec = _spec_for_strategy(session, strategy_id, linked_wallet, user.id)
        try:
            dep = create_deployment(
                session,
                strategy_id=strategy_id,
                spec_dict=spec,
                owner_wallet=linked_wallet,
                owner_user_id=user.id,
            )
        except DSLError as exc:
            raise HTTPException(
                status_code=422,
                detail={"message": f"Stored spec fails validation: {exc}", "reason": "invalid_strategy_spec"},
            ) from exc
        # First advance is best-effort and SYNCHRONOUS-failure-soft: the
        # deployment exists even if data is momentarily unavailable — the
        # scheduler's daily pass will backfill from deployed_at.
        try:
            advance_deployment(session, dep)
        except PaperTraceCoverageError:
            # NOT the deferred-warning path. "Deferred to the scheduler" says
            # the data was momentarily unavailable and the next pass will fix
            # it; a broken coverage identity is a bug in the trace pipeline and
            # the next pass will break the same way. Distinct ERROR with its
            # own literal. The deployment is still returned rather than 500ing:
            # it exists, its ledger is fine, and its trace_coverage on the
            # payload below is what carries the hole to the user.
            logger.error("%s for deployment %s (initial advance)", COVERAGE_BROKEN_LOG, dep.id, exc_info=True)
        except Exception as exc:
            logger.warning("paper: initial advance for %s deferred to the scheduler: %s", dep.id, exc)
        summary = deployment_summary(session, dep)
        session.commit()
    return summary


@paper_router.get("/deployments")
async def list_paper_deployments(request: Request, user: CurrentUser = Depends(require_current_user)):  # noqa: ARG001
    init_db()
    with get_session() as session:
        deps = (
            session.query(PaperDeployment)
            .filter(PaperDeployment.owner_user_id == user.id)
            .order_by(PaperDeployment.created_at.desc())
            .all()
        )
        return {"deployments": [deployment_summary(session, d) for d in deps]}


@paper_router.get("/deployments/{deployment_id}")
async def get_paper_deployment(
    request: Request,  # noqa: ARG001 — route signature parity
    deployment_id: str,
    user: CurrentUser = Depends(require_current_user),
):
    init_db()
    with get_session() as session:
        dep = _owned_deployment(session, deployment_id, user.id)
        return deployment_summary(session, dep)


@paper_router.post("/deployments/{deployment_id}/stop")
async def stop_paper_deployment(
    request: Request,  # noqa: ARG001 — route signature parity
    deployment_id: str,
    user: CurrentUser = Depends(require_current_user),
):
    init_db()
    with get_session() as session:
        dep = _owned_deployment(session, deployment_id, user.id)
        dep.status = STATUS_STOPPED
        session.commit()
        return {"deployment_id": dep.id, "status": dep.status}
