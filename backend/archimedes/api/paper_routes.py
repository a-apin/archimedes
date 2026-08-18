"""Paper-trading endpoints — /api/paper/* (MVP: verdict → paper-trade).

Ownership follows the SIWE model live on main today (verified wallet), with
the Better Auth user id column already carried for the #1194 transition —
this router only needs its auth dependency swapped when that lands.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, Response

from archimedes.api.auth_siwe import get_verified_wallet
from archimedes.api.limiter import limiter
from archimedes.db import get_session, init_db
from archimedes.models.paper_store import STATUS_STOPPED, PaperDeployment
from archimedes.services.paper_trading import advance_deployment, create_deployment, deployment_summary
from archimedes.services.strategy_dsl import DSLError

logger = logging.getLogger(__name__)

paper_router = APIRouter(prefix="/api/paper", tags=["paper"])


def _spec_for_strategy(session, strategy_id: str, caller: str | None) -> dict:
    """The spec to snapshot, honoring strategy visibility (#850 rules)."""
    from archimedes.models.strategy_store import StrategyRecord
    from archimedes.services.strategy_visibility import is_strategy_visible

    row = session.query(StrategyRecord).filter_by(id=strategy_id).first()
    if row is None or not is_strategy_visible(row, caller):
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


@paper_router.post("/deployments", status_code=201)
@limiter.limit("10/minute")
async def deploy_paper(
    request: Request,
    response: Response,  # noqa: ARG001 — slowapi header injection (#1182 invariant)
    body: dict,
):
    wallet = get_verified_wallet(request)
    if not wallet:
        raise HTTPException(status_code=401, detail="Connect a wallet to paper-trade")
    strategy_id = str(body.get("strategy_id") or "").strip()
    if not strategy_id:
        raise HTTPException(status_code=422, detail="strategy_id is required")

    init_db()
    with get_session() as session:
        spec = _spec_for_strategy(session, strategy_id, wallet)
        try:
            dep = create_deployment(session, strategy_id=strategy_id, spec_dict=spec, owner_wallet=wallet)
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
async def list_paper_deployments(request: Request):
    wallet = get_verified_wallet(request)
    if not wallet:
        raise HTTPException(status_code=401, detail="Connect a wallet to view paper trades")
    init_db()
    with get_session() as session:
        deps = (
            session.query(PaperDeployment)
            .filter(PaperDeployment.owner_wallet == wallet.lower())
            .order_by(PaperDeployment.created_at.desc())
            .all()
        )
        return {"deployments": [deployment_summary(session, d) for d in deps]}


@paper_router.get("/deployments/{deployment_id}")
async def get_paper_deployment(request: Request, deployment_id: str):
    wallet = get_verified_wallet(request)
    init_db()
    with get_session() as session:
        dep = session.query(PaperDeployment).filter_by(id=deployment_id).first()
        # 404 for missing AND for not-yours: existence is private (#850 idiom).
        if dep is None or not wallet or dep.owner_wallet != wallet.lower():
            raise HTTPException(status_code=404, detail="Paper deployment not found")
        return deployment_summary(session, dep)


@paper_router.post("/deployments/{deployment_id}/stop")
async def stop_paper_deployment(request: Request, deployment_id: str):
    wallet = get_verified_wallet(request)
    init_db()
    with get_session() as session:
        dep = session.query(PaperDeployment).filter_by(id=deployment_id).first()
        if dep is None or not wallet or dep.owner_wallet != wallet.lower():
            raise HTTPException(status_code=404, detail="Paper deployment not found")
        dep.status = STATUS_STOPPED
        session.commit()
        return {"deployment_id": dep.id, "status": dep.status}
