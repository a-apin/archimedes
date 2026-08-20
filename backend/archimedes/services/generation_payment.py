"""x402 paywall for strategy generation (Dan's directive, 2026-08-19).

Generation REQUIRES wallet connection + payment — for humans and agents alike,
no free path — while paper trading stays free. The 402 response *is* the
quote-approval flow: it carries the full payment requirements in the
``PAYMENT-REQUIRED`` header (and a human-readable quote in the JSON body), the
client signs and retries with a ``Payment-Signature`` header, and the server
verifies + settles via Circle's facilitator (the same
``marketplace.payments``/circlekit machinery the tick-charging rail uses —
this module reuses ``get_gateway_middleware``; circlekit stays imported in one
place).

FLAG-GATED: ``GENERATION_PAYMENT_REQUIRED`` (only the literal ``"true"``
enables — Dan flips it deliberately; see the flip-list, issue #834). While the
flag is off every function here is inert and /api/generate behaves exactly as
before. The account+IP daily quota stays stacked UNDERNEATH the paywall as
anti-abuse — and runs BEFORE it, so a quota-blocked caller is refused with 429
before ever being asked to pay.

``PAYMENTS_DRY_RUN`` semantics (mirrors ``main.py``): in dry-run the paywall
still quotes (402 without a payment header — so the approval UX is exercised
end to end) but accepts a presented payment WITHOUT verify/settle, loudly.
No real value can move while the custody migration (#975) is pending.

``PAYMENTS_HALT`` (#1240 kill switch) is NOT the same shape as dry-run here,
unlike on the marketplace tick-charging rail: this is a metered pay-per-call
API with no pre-existing subscriber relationship, so a halted charge REFUSES
service (503) instead of accepting an unverified header for free — comping
the paid product (and dropping the payer-binding check with it) is not what
an operator flipping a kill switch wants.

Pricing is flat (``GENERATION_PRICE_USD``, default $0.15 testnet USDC) behind
``quote()`` — the single seam #1217's measured per-generation budget replaces
later without touching the paywall flow.
"""

from __future__ import annotations

import logging
import os
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

DEFAULT_PRICE_USD = "0.15"

# The logical resource identifier bound into the 402 requirements. One flat
# resource for now — per-job binding is unnecessary while the price is flat
# (replay of a settled payment is prevented by the facilitator consuming the
# signed authorization at settle time, not by path uniqueness).
_RESOURCE_PATH = "/api/generate/start"


def payment_required() -> bool:
    """Only the literal "true" enables — mirrors EMAIL_VERIFICATION_ENFORCED."""
    return os.getenv("GENERATION_PAYMENT_REQUIRED") == "true"


def _payments_dry_run() -> bool:
    """EXACT mirror of main.py's parse — the two must never disagree."""
    return os.getenv("PAYMENTS_DRY_RUN", "true").lower() in ("1", "true", "yes")


def _payments_halted() -> bool:
    """#1240 kill switch — see marketplace.config.payments_halted's docstring.
    This is the metered-API path, the one surface the mainnet scope decision
    allows to run PAYMENTS_DRY_RUN=false on, so it needs the same no-redeploy
    stop lever as the marketplace tick-charging rail."""
    from archimedes.marketplace.config import payments_halted

    return payments_halted()


def _recipient() -> str:
    return os.getenv("GENERATION_PAYMENT_RECIPIENT", "").strip()


def _price() -> str:
    """The circlekit "$X.XXXXXX" price string. Fails SAFE to the default on a
    malformed env value (a typo must not turn the paywall free or absurd)."""
    raw = os.getenv("GENERATION_PRICE_USD", DEFAULT_PRICE_USD).strip() or DEFAULT_PRICE_USD
    try:
        value = Decimal(raw)
        if value < 0:
            raise InvalidOperation
    except InvalidOperation:
        logger.warning("invalid GENERATION_PRICE_USD=%r — using default %s", raw, DEFAULT_PRICE_USD)
        value = Decimal(DEFAULT_PRICE_USD)
    return f"${value:.6f}"


def quote() -> dict:
    """The generation price quote — public, also embedded in every 402 body.

    ``pricing_model: "flat_v1"`` names the current scheme; #1217's measured
    per-generation budget replaces the internals of this function (and bumps
    the model name) without changing the paywall flow around it.
    """
    from archimedes.marketplace.config import DEFAULT_GATEWAY_CHAIN

    return {
        "payment_required": payment_required(),
        "pricing_model": "flat_v1",
        "price": _price(),
        "asset": "USDC",
        "chain": os.getenv("GATEWAY_CHAIN", DEFAULT_GATEWAY_CHAIN).strip(),
        "recipient": _recipient() or None,
        "dry_run": _payments_dry_run(),
        "halted": _payments_halted(),
        "how": (
            "POST /api/generate/start without a Payment-Signature header returns 402 "
            "with these requirements in the PAYMENT-REQUIRED header; sign them "
            "(x402 / Circle Gateway) and retry with Payment-Signature."
        ),
    }


def _quote_402(detail_reason: str, message: str) -> HTTPException:
    """A 402 carrying BOTH the machine quote (PAYMENT-REQUIRED header, built by
    the same circlekit middleware the settle path uses) and a human/agent
    readable body. Raising-site helper so every 402 is shaped identically."""
    from archimedes.marketplace.payments import get_gateway_middleware

    headers = {}
    try:
        required = get_gateway_middleware(_recipient()).require(_price(), _RESOURCE_PATH)
        headers = required.get("headers") or {}
    except Exception:
        # Requirements are computed locally; a failure here is a config bug.
        # Still 402 (honest: payment IS required) with the JSON quote only.
        logger.exception("could not build PAYMENT-REQUIRED header")
    return HTTPException(
        status_code=402,
        detail={"reason": detail_reason, "message": message, "quote": quote()},
        headers=headers,
    )


async def enforce_generation_payment(request: Request, linked_wallet: str):
    """The paywall. Returns a settled ``PaymentInfo`` (or ``None`` when the
    flag is off / dry-run accepted). Raises HTTPException 402/503 otherwise.

    Fail-closed configuration: flag ON with no recipient configured is an
    OUTAGE (503), never a free pass — the flip-list documents that
    ``GENERATION_PAYMENT_RECIPIENT`` must be set before the flag.
    """
    if not payment_required():
        return None

    if not _recipient():
        logger.error("GENERATION_PAYMENT_REQUIRED=true but GENERATION_PAYMENT_RECIPIENT is unset — refusing (503)")
        raise HTTPException(
            status_code=503,
            detail={
                "reason": "payment_config_missing",
                "message": "Generation payments are enabled but not fully configured. Please try again later.",
            },
        )

    header = request.headers.get("Payment-Signature")
    if not header:
        raise _quote_402(
            "payment_required",
            "Generation requires payment. Sign the PAYMENT-REQUIRED requirements with your linked wallet "
            "and retry with a Payment-Signature header.",
        )

    if _payments_dry_run():
        # No real value may move while PAYMENTS_DRY_RUN holds (#975 custody
        # migration pending). Accept the presented payment WITHOUT verify or
        # settle — loudly, so a log reader can never mistake this for revenue.
        logger.warning(
            "PAYMENTS_DRY_RUN — generation payment header accepted UNVERIFIED and UNSETTLED (no value moved)"
        )
        return None

    if _payments_halted():
        # #1240 kill switch — but on THIS surface (unlike the marketplace
        # tick-charging rail) halting must REFUSE service, not give the paid
        # product away. This is a metered pay-per-call API with no pre-existing
        # subscription relationship, so "no real charge" here doesn't mean
        # "let the tick continue as if it had paid" — it means "we cannot take
        # payment right now." Accepting ANY Payment-Signature value unverified
        # (the marketplace rail's own "treated as no-op" shape) would have
        # served the paid product for free AND dropped the payer-binding check
        # above's guarantee for the whole halt window — the honest reading of
        # a kill switch on a paid surface is to refuse, not to comp it.
        logger.warning("PAYMENTS_HALT active — refusing generation payment (service unavailable, not free)")
        raise HTTPException(
            status_code=503,
            detail={
                "reason": "payments_halted",
                "message": "Generation payments are temporarily halted by an operator kill switch. "
                "Please try again later.",
            },
        )

    from circlekit.x402 import decode_payment_header

    from archimedes.marketplace.payments import get_gateway_middleware

    # Bind the payment to the caller's PROVEN wallet before spending a
    # facilitator round-trip: the payer inside the signed authorization must be
    # the wallet this account linked (the wallet-connection precondition and
    # the payment are one identity, not two).
    try:
        payload = decode_payment_header(header)
        payer = str(((payload.get("payload") or {}).get("authorization") or {}).get("from", ""))
    except Exception:
        raise _quote_402("payment_malformed", "The Payment-Signature header could not be decoded.") from None
    if not payer or payer.lower() != linked_wallet.lower():
        raise _quote_402(
            "payer_mismatch",
            "The payment must be signed by your linked wallet "
            f"({linked_wallet[:10]}…). Link that wallet or sign with it and retry.",
        )

    middleware = get_gateway_middleware(_recipient())
    price = _price()
    try:
        verify_result = await middleware.verify(header, price)
    except ValueError as exc:
        raise _quote_402("payment_invalid", f"Payment could not be verified: {exc}") from exc
    if not verify_result.is_valid:
        reason = getattr(verify_result, "invalid_reason", None) or "verification failed"
        raise _quote_402("payment_invalid", f"Payment verification failed: {reason}")

    try:
        payment = await middleware.settle(header, price)
    except ValueError as exc:
        # Verified but not settled — the caller keeps their funds; honest 402.
        raise _quote_402("payment_settle_failed", f"Payment could not be settled: {exc}") from exc

    logger.info(
        "generation payment settled: payer=%s amount=%s tx=%s",
        payment.payer,
        payment.amount,
        payment.transaction,
    )
    return payment
