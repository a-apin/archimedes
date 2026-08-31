"""Private metrics routes — account + admin-linked-wallet cost/ops dashboard.

The public ``metrics_router`` (``/api/metrics``, ``/funnel``, ``/visitors``) is
PII-free by design and stays anonymous — it is the "agents make markets" traction
instrument. This router is the OTHER half of the split: the internal cost / ops /
infra dashboard (the ARCH-COST-DASHBOARDS content), which must NOT be public.

Gating requires Better Auth account, verified linked wallet, and membership in
``PLATFORM_ADMIN_WALLETS``.
Cost/ops data (Bedrock spend, infra spend, cost-per-user) is operationally
sensitive, not merely PII — any linked wallet is not an appropriate bar
for it. The router now also requires membership in ``PLATFORM_ADMIN_WALLETS``,
the same env-driven admin allowlist ``models/strategy_generators.wallet_can_publish``
already uses for the "publish an example strategy you didn't generate" exception.
A verified-but-non-admin wallet gets **403**; an unauthenticated request still
gets **401** (session check runs first).

Account denominator comes from canonical Better Auth users, never cumulative
request tallies or optional linked-wallet/profile counts.

The account-level AWS cost fields are DRAFT/illustrative placeholders (the live
Bedrock/infra billing wiring — AWS Cost Explorer + Bedrock token metering — is
roadmap work); they are labelled ``draft`` so a reader can't mistake them for
metered live spend. ``cost_per_generation_usd`` is the exception as of #1217:
it is derived from real ``generation_costs`` measurements priced against the
``GENERATION_COST_RATE_CARD`` env rate card (``services/generation_cost_rollup``),
and carries its own provenance rather than sharing the ``draft`` label.
Real distinct users are read live so the per-user denominators are honest.

Owner directive (2026-08-20, SUPERSEDES issue #1028 D8 "public Insights page"):
``/app/insights`` moved from the public app surface to ADMIN-ONLY. It remains
the owner traction dashboard and gained new current-schema engagement/adoption
tiles (``GET /whoami``, ``GET /engagement`` below); the public aggregate
endpoints on ``metrics_router`` (``/api/metrics``, ``/funnel``, ``/visitors``)
are UNCHANGED and stay public — only the Insights *page* and these new
per-entity admin endpoints are gated. ``/whoami`` is a server-truth gate probe:
the frontend calls it before rendering anything Insights-shaped so a
non-admin/anonymous visitor gets the exact "page does not exist" treatment
(``ui/src/routes.js``'s not-found handling), not a page that discloses "you
need admin access" — which would itself advertise the page's existence.
This closes only that one disclosure vector (the denial screen's wording),
not concealment of the endpoint or route in general — a non-admin's browser
still calls this probe on every app page and still ships the gated route
and component in its main JS bundle; see ``ui/src/adminProbe.js``'s "Scope
of 'does not advertise the page exists'" note (round 2, 2026-08-20).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException

from archimedes.api.wallet_routes import require_linked_wallet
from archimedes.models.telemetry import (
    WalletConnectionOut,
    WalletConnectionsResponse,
    WalletIdentityOut,
    WalletsResponse,
)
from archimedes.services.engagement_metrics import get_engagement_snapshot
from archimedes.services.generation_cost_rollup import get_measured_generation_cost
from archimedes.services.identity_metrics import (
    count_human_wallets,
    list_human_wallets,
    list_wallet_connections,
)
from archimedes.services.user_stats import get_distinct_user_count


def _platform_admin_wallets() -> set[str]:
    """Env-driven admin allowlist, lowercased. Same parse as ``wallet_can_publish``."""
    return {w.strip().lower() for w in os.getenv("PLATFORM_ADMIN_WALLETS", "").replace(",", " ").split() if w.strip()}


def require_platform_admin(wallet: str = Depends(require_linked_wallet)) -> str:
    """Require account-linked wallet listed in ``PLATFORM_ADMIN_WALLETS``.

    401 with no account/link; 403 for a
    verified wallet that isn't a listed admin. Cost/ops data must not be
    readable by any linked wallet — only platform admins.
    """
    if wallet not in _platform_admin_wallets():
        raise HTTPException(status_code=403, detail="Admin access required.")
    return wallet


# Every route requires account-linked wallet and platform-admin
# membership (403 for a non-admin wallet, 401 for no session at all).
metrics_private_router = APIRouter(
    prefix="/api/metrics/private",
    tags=["metrics", "private"],
    dependencies=[Depends(require_platform_admin)],
)


@metrics_private_router.get("/whoami")
async def get_whoami(wallet: str = Depends(require_platform_admin)) -> dict:
    """Admin-gate probe for the frontend — the server-truth half of the gate.

    The UI calls this on entry to the Insights page (and to decide whether to
    render the Ops nav item) BEFORE rendering anything Insights-shaped.
    Anonymous → 401; verified-but-non-admin → 403; admin → 200
    ``{admin: true, wallet}``. There is no "admin: false" 200 response —
    non-admin is always a 4xx, so the frontend's error branch is the only
    path that ever needs to fall back to the not-found treatment, and a
    network/parse failure degrades the same way (fail closed, not open).
    """
    return {"admin": True, "wallet": wallet}


@metrics_private_router.get("/engagement")
async def get_engagement(wallet: str = Depends(require_platform_admin)) -> dict:
    """Dashboard v2 — current-schema engagement/adoption tiles (admin-only).

    See ``services/engagement_metrics.py`` for the per-tile query docs and
    what is/isn't joinable today; the PR that introduced this endpoint carries
    the Phase 2 list of metrics deferred pending schema-relations work.
    """
    snapshot = get_engagement_snapshot()
    snapshot["authenticated_wallet"] = wallet
    return snapshot


@metrics_private_router.get("/cost")
async def get_private_cost(wallet: str = Depends(require_platform_admin)) -> dict:
    """Account + admin-linked-wallet cost dashboard. Anonymous
    → 401; verified-but-non-admin → 403.

    **Two provenances on one payload, and they are labelled separately (#1217).**
    The account-level AWS spend fields (``bedrock_*``, ``infra_monthly_usd``,
    ``cost_per_user_usd``) are still DRAFT placeholders pending the live billing
    wiring (roadmap: AWS Cost Explorer + Bedrock token metering) — that is what
    the top-level ``source: "draft"`` describes, and it is deliberately
    unchanged. ``cost_per_generation_usd`` is no longer one of them: it is now
    derived from the ``generation_costs`` measurements this platform actually
    recorded, priced against the ``GENERATION_COST_RATE_CARD`` environment rate
    card, with the full distribution and the N-scaling breakdown under
    ``generation_cost``.

    It stays ``None`` — never ``0`` — when no rate card is configured or no
    measured run was priceable, and ``generation_cost.rate_card_configured`` /
    ``.unpriceable_reasons`` say which. A null here means "not measured or not
    priceable", exactly as before; what changed is that it can now also be a
    real, measured number.

    ``real_users`` is read live so any per-user figure a consumer computes is
    anchored to the honest distinct-user denominator, never the request counter.

    This is the ONLY surface carrying the priced figure, and it is admin-gated:
    the public ``/api/metrics`` family stays aggregate, PII-free and unpriced.
    """
    real_users = get_distinct_user_count()
    generation_cost = get_measured_generation_cost()
    measured = generation_cost.get("cost_per_generation_usd") or {}
    return {
        # Describes the AWS-billing placeholders below, NOT generation_cost —
        # which carries its own provenance (rate_card_configured / jobs_priced /
        # unavailable) precisely so the two are never read as one claim.
        "source": "draft",
        "real_users": real_users,
        "bedrock_monthly_usd": None,
        "bedrock_daily_usd": None,
        "infra_monthly_usd": None,
        "cost_per_user_usd": None,
        # The mean of the priced runs, or None. Kept flat for the existing
        # consumer contract; read `generation_cost` for the distribution, the
        # LLM-vs-compute split, and how many runs could not be priced.
        "cost_per_generation_usd": measured.get("mean"),
        "generation_cost": generation_cost,
        "note": (
            "source='draft' applies to the bedrock_*/infra/cost_per_user placeholders only. "
            "cost_per_generation_usd is measured: generation_costs rows priced against the "
            "GENERATION_COST_RATE_CARD env rate card (null, never 0, when unset or unpriceable). "
            "Per-user figures must be derived from real_users (canonical accounts) — never the "
            "cumulative request tallies on /api/metrics (issue #830)."
        ),
        "authenticated_wallet": wallet,
        "timestamp": datetime.now(UTC).isoformat(),
    }


@metrics_private_router.get("/wallets", response_model=WalletsResponse)
async def get_wallets() -> WalletsResponse:
    """Enumerate legacy verified human wallets, separate from account count.

    ADMIN-ONLY, moved here from the public metrics router (#1366): this returns
    the full per-wallet roster — wallet addresses are pseudonymous but
    permanently linkable to on-chain activity, so an open enumeration endpoint
    let anyone list every user of the platform and trace their chain history.
    The public metrics surface is aggregate and PII-free BY DESIGN (module
    docstring above); a per-identity roster belongs behind the same admin gate
    as the rest of the ops dashboard. Anonymous → 401; verified non-admin → 403
    (router-level dependency).

    ``real_users`` field is retained for response compatibility but means wallet
    count on this endpoint only (#1028 AC1). Fail-safe: empty list / zero on DB
    error.
    """
    return WalletsResponse(
        real_users=count_human_wallets(),
        wallets=[WalletIdentityOut(**row) for row in list_human_wallets()],
        timestamp=datetime.now(UTC).isoformat(),
    )


@metrics_private_router.get("/wallets/connections", response_model=WalletConnectionsResponse)
async def get_wallet_connections() -> WalletConnectionsResponse:
    """ "Which wallets connected, and when" — issue #1028 AC2.

    ADMIN-ONLY, moved here from the public metrics router for the same reason
    as ``/wallets`` above (#1366): a per-wallet first-connection ledger is
    identity data, not aggregate traction.

    ``SELECT wallet, min(occurred_at) FROM identity_events WHERE event_type =
    'auth_verified' GROUP BY wallet`` — a query that was impossible before the
    ledger (the SIWE-verify path used to discard the wallet into a stateless
    cookie with no durable write). Fail-safe: an empty list on any DB error.
    """
    connections = list_wallet_connections()
    return WalletConnectionsResponse(
        count=len(connections),
        connections=[WalletConnectionOut(**row) for row in connections],
        timestamp=datetime.now(UTC).isoformat(),
    )
