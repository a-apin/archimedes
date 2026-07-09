"""Private metrics routes — SIWE + platform-admin-gated cost/ops dashboard (issue #830).

The public ``metrics_router`` (``/api/metrics``, ``/funnel``, ``/visitors``) is
PII-free by design and stays anonymous — it is the "agents make markets" traction
instrument. This router is the OTHER half of the split: the internal cost / ops /
infra dashboard (the ARCH-COST-DASHBOARDS content), which must NOT be public.

Gating (tightened — the Insights-page fix): a valid SIWE session
(``auth_siwe.require_verified_wallet``) is necessary but no longer sufficient.
Cost/ops data (Bedrock spend, infra spend, cost-per-user) is operationally
sensitive, not merely PII — ANY authenticated wallet is not an appropriate bar
for it. The router now also requires membership in ``PLATFORM_ADMIN_WALLETS``,
the same env-driven admin allowlist ``models/strategy_generators.wallet_can_publish``
already uses for the "publish an example strategy you didn't generate" exception.
A verified-but-non-admin wallet gets **403**; an unauthenticated request still
gets **401** (session check runs first).

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

import os
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException

from archimedes.api.auth_siwe import require_verified_wallet
from archimedes.services.user_stats import get_distinct_user_count


def _platform_admin_wallets() -> set[str]:
    """Env-driven admin allowlist, lowercased. Same parse as ``wallet_can_publish``."""
    return {w.strip().lower() for w in os.getenv("PLATFORM_ADMIN_WALLETS", "").replace(",", " ").split() if w.strip()}


def require_platform_admin(wallet: str = Depends(require_verified_wallet)) -> str:
    """FastAPI dependency: verified SIWE session AND ``PLATFORM_ADMIN_WALLETS`` membership.

    401 with no/invalid session (via ``require_verified_wallet``); 403 for a
    verified wallet that isn't a listed admin. Cost/ops data must not be
    readable by "any authenticated wallet" — only platform admins.
    """
    if wallet not in _platform_admin_wallets():
        raise HTTPException(status_code=403, detail="Admin access required.")
    return wallet


# Every route on this router requires a valid SIWE session AND platform-admin
# membership (403 for a non-admin wallet, 401 for no session at all).
metrics_private_router = APIRouter(
    prefix="/api/metrics/private",
    tags=["metrics", "private"],
    dependencies=[Depends(require_platform_admin)],
)


@metrics_private_router.get("/cost")
async def get_private_cost(wallet: str = Depends(require_platform_admin)) -> dict:
    """SIWE + platform-admin-gated cost/billing dashboard (issue #830). Anonymous
    → 401; verified-but-non-admin → 403.

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
