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
from archimedes.services.paper_trading import advance_deployment, create_deployment, deployment_summary
from archimedes.services.strategy_dsl import DSLError

logger = logging.getLogger(__name__)

paper_router = APIRouter(prefix="/api/paper", tags=["paper"])


def _spec_for_strategy(session, strategy_id: str, caller_wallet: str | None, caller_user_id: str | None) -> dict:
    """The spec to snapshot, honoring strategy visibility (#850 rules)."""
    from archimedes.models.strategy_store import StrategyRecord
    from archimedes.services.strategy_visibility import is_strategy_visible

    row = session.query(StrategyRecord).filter_by(id=strategy_id).first()
    if row is None or not is_strategy_visible(row, caller_wallet, caller_user_id=caller_user_id):
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
