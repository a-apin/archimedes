"""``GET /api/account/usage`` — the CLI's ``meter`` command backend (#1305).

Reports the caller's daily generation-quota usage against the SAME two Redis
buckets ``services/generation_quota.py`` enforces (``GenerationQuota.peek`` —
a read, never a write — added there for exactly this route; enforcement in
``enforce_generation_quota`` is untouched), plus the current price quote from
``generation_payment.quote()``. Both the enforcement path
(``POST /api/generate/start``) and this display path read the price from the
same ``quote()`` call, so the CLI's number and the API's number can never
drift apart.

Account-session-gated (Better Auth, ``require_current_user``) — mirrors
``paper_routes.py``'s per-route ``Depends`` style, not the router-level
``dependencies=[...]`` style some other routers use in ``main.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from archimedes.api.account_auth import CurrentUser, require_current_user
from archimedes.services import generation_payment
from archimedes.services.generation_quota import (
    GenerationQuota,
    client_ip,
    ip_daily_cap,
    user_daily_cap,
)

account_usage_router = APIRouter(prefix="/api/account", tags=["account"])


class DailyCapUsage(BaseModel):
    """One bucket's usage today. ``used`` is honestly ``None`` — never a
    fabricated ``0`` — when the quota backend could not be reached."""

    used: int | None = None
    cap: int
    unlimited: bool
    remaining: int | None = None
    error: str | None = None


class AccountUsageResponse(BaseModel):
    date: str  # UTC day, YYYY-MM-DD — the same day-bucket key generation_quota uses
    user_id: str
    user: DailyCapUsage
    ip: DailyCapUsage
    quote: dict


def _bucket(used: int | None, cap: int) -> DailyCapUsage:
    unlimited = cap <= 0
    if used is None:
        return DailyCapUsage(used=None, cap=cap, unlimited=unlimited, remaining=None, error="quota_backend_unavailable")
    remaining = None if unlimited else max(0, cap - used)
    return DailyCapUsage(used=used, cap=cap, unlimited=unlimited, remaining=remaining, error=None)


@account_usage_router.get("/usage", response_model=AccountUsageResponse)
async def get_account_usage(
    request: Request,
    user: CurrentUser = Depends(require_current_user),
):
    """Today's generation usage vs both daily caps, plus the live price quote."""
    quota = GenerationQuota()
    try:
        user_used = await quota.peek("user", user.id)
        ip_used = await quota.peek("ip", client_ip(request))
    finally:
        await quota.close()

    return AccountUsageResponse(
        date=datetime.now(UTC).strftime("%Y-%m-%d"),
        user_id=user.id,
        user=_bucket(user_used, user_daily_cap()),
        ip=_bucket(ip_used, ip_daily_cap()),
        quote=generation_payment.quote(),
    )
