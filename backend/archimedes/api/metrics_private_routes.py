"""Private metrics routes — SIWE-gated cost/ops dashboard (issue #830).

The public ``metrics_router`` (``/api/metrics``, ``/funnel``, ``/visitors``) is
PII-free by design and stays anonymous — it is the "agents make markets" traction
instrument. This router is the OTHER half of the split: the internal cost / ops /
infra dashboard (the ARCH-COST-DASHBOARDS content), which must NOT be public.

Gating reuses the existing SIWE session gate — the same mechanism ``user_routes``
uses to protect PII (``auth_siwe.require_verified_wallet``). No new auth system:
a request without a valid ``archimedes_session`` cookie gets **401**.

Claim integrity (issue #830, denominator honesty updated by #1028 AC1): the
private numbers here are recomputed against the honest instruments — distinct
HUMAN WALLETS (``wallet_identities``, not the ``user_profiles`` row count,
which undercounted — see ``services/user_stats.py``) and strategy generations
— NEVER the cumulative request tallies. ``$/user`` / ``$/gen`` figures are
derived from true users or generations, not from ``human_count``.

Today's cost fields are DRAFT/illustrative placeholders (the live Bedrock/infra
billing wiring — AWS Cost Explorer + Bedrock token metering — is roadmap work);
they are labelled ``draft`` so a reader can't mistake them for metered live spend.
Real distinct users are read live so the per-user denominators are honest.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from archimedes.api.auth_siwe import require_verified_wallet
from archimedes.services.user_stats import get_distinct_user_count

# Every route on this router requires a valid SIWE session (401 otherwise). The
# router-level dependency is the same gate user_routes uses for PII.
metrics_private_router = APIRouter(
    prefix="/api/metrics/private",
    tags=["metrics", "private"],
    dependencies=[Depends(require_verified_wallet)],
)


@metrics_private_router.get("/cost")
async def get_private_cost(wallet: str = Depends(require_verified_wallet)) -> dict:
    """SIWE-gated cost/billing dashboard (issue #830). Anonymous → 401.

    Cost fields are DRAFT placeholders until the live billing wiring lands
    (roadmap: AWS Cost Explorer + Bedrock token metering). ``real_users`` is read
    live so any per-user figure a consumer computes is anchored to the honest
    distinct-user denominator, never the request counter.
    """
    real_users = get_distinct_user_count()
    return {
        "source": "draft",  # NOT live-metered spend — placeholders pending billing wiring.
        "real_users": real_users,
        "bedrock_monthly_usd": None,
        "bedrock_daily_usd": None,
        "infra_monthly_usd": None,
        "cost_per_user_usd": None,
        "cost_per_generation_usd": None,
        "note": (
            "Draft placeholders. Per-user / per-generation figures must be derived from "
            "real_users (distinct wallets) or strategy generations — never the cumulative "
            "request tallies on /api/metrics (issue #830)."
        ),
        "authenticated_wallet": wallet,
        "timestamp": datetime.now(UTC).isoformat(),
    }
