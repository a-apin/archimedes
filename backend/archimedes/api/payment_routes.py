"""``GET /api/payments/receipts`` — the CALLER's own settled generation-payment
receipts (Dan's directive, 2026-08-21: "we must provide people with their
receipts").

Reads the durable ``payment_receipts`` table written at settle time by
``generate_routes.start_generation`` (fail-safe there: a write failure never
fails or delays the paid generation, so this list can in principle be
incomplete for a given user during a genuine DB outage — an honest gap, not a
fabricated empty result masquerading as "no payments").

Account-session-gated (Better Auth, ``require_current_user``) — mirrors
``account_usage_routes.py``'s per-route ``Depends`` style. Owner-scoped by
construction: the query is always filtered to the CALLER's own ``user.id``,
so one account can never see another's charges. No pagination yet — capped at
``PaymentReceiptRecord.MAX_RECEIPTS`` (200), noted in the response docstring.

**Honesty note** (mirrors ``models/payment_receipt.py``): ``settlement_ref``
is a Circle facilitator reference id, NOT an on-chain transaction hash. It is
returned as a plain string; nothing here (or in the UI) may render it as an
arcscan link.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from archimedes.api.account_auth import CurrentUser, require_current_user

payment_router = APIRouter(prefix="/api/payments", tags=["payments"])


class PaymentReceiptResponse(BaseModel):
    id: int
    created_at: str | None
    price_usd: str
    amount_base_units: int
    payer_wallet: str
    # Circle facilitator reference id — NOT an on-chain tx hash. Never link
    # this to a block explorer (see module docstring's honesty note).
    settlement_ref: str | None
    job_id: str | None
    network: str


@payment_router.get("/receipts", response_model=list[PaymentReceiptResponse])
async def list_receipts(user: CurrentUser = Depends(require_current_user)) -> list[PaymentReceiptResponse]:
    """The caller's own settled generation-payment receipts, newest-first.

    Capped at 200 rows — no pagination yet. If a payer somehow has more than
    200 settled generation payments, only the most recent 200 are returned.
    """
    from archimedes.db import get_session
    from archimedes.models.payment_receipt import MAX_RECEIPTS, list_payment_receipts

    with get_session() as session:
        rows = list_payment_receipts(session, user.id, limit=MAX_RECEIPTS)
    return [PaymentReceiptResponse(**row) for row in rows]
