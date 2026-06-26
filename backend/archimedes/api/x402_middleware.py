"""Generic x402 paywall — a reusable FastAPI dependency.

This is the first slice of the optional-publish nanopayment marketplace
(issue #713 / roadmap T1.2). It implements the seller side of the x402 open
payment standard (HTTP `402 Payment Required`) backed by Circle's Gateway
facilitator for gasless, sub-cent USDC settlement on Arc.

Design: GENERIC + opt-in per route. `x402_paywall(price_usdc, recipient)`
returns a FastAPI dependency you attach to ANY route with a configurable price
and recipient address. In THIS PR it is wired to exactly one endpoint
(`POST /api/strategies/construct`), but the factory shape is deliberate — a
sibling use-case (charging per *generation*, 100% to the platform to cover
inference cost) will reuse it on the generate route in a follow-up.

Flow (per `submodules/context-arc/docs/.../nanopayments/concepts/x402.md`):
  1. No `PAYMENT-SIGNATURE` header  → 402 + a `PAYMENT-REQUIRED` header naming
     Arc testnet, the recipient `RevenueSplit` address, and the USDC price.
  2. With `PAYMENT-SIGNATURE`       → settle via Circle Gateway
     (`POST {CIRCLE_GW_BASE}/x402/settle`); gate the handler on `success: true`.
  3. Per-payer spend cap            → 429 when a payer wallet exceeds the daily
     USDC cap (24h sliding window in Redis).

Feature flag: `ARCHIMEDES_X402_ENABLED=1` arms the paywall. Default OFF → the
dependency is a no-op passthrough, so attaching it to a route never changes
behavior until the flag is set.

The Circle facilitator base URL is imported from `circle_service.CIRCLE_GW_BASE`
(not redeclared) per the spec's anti-goal on duplicating that constant.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable

import aiohttp
from fastapi import HTTPException, Request

from archimedes.services.circle_service import CIRCLE_GW_BASE

logger = logging.getLogger(__name__)

# ── Config (env-driven; all non-secret) ──────────────────────────────────────

#: Master feature flag. When unset/0, every x402 dependency is a no-op.
FLAG_ENV = "ARCHIMEDES_X402_ENABLED"

#: Deployed RevenueSplit address (the default recipient). Non-secret; lives in
#: .env.example mirroring STRATEGY_REGISTRY_ADDRESS.
REVENUE_SPLIT_ADDRESS_ENV = "REVENUE_SPLIT_ADDRESS"

#: Per-payer daily spend cap in USDC (testnet default 0.10).
MAX_USDC_PER_DAY_ENV = "ARCHIMEDES_X402_MAX_USDC_PER_DAY"
DEFAULT_MAX_USDC_PER_DAY = 0.10

#: Arc testnet chain id (CAIP-2 `eip155:5042002`); matches chain/client.py.
ARC_CHAIN_ID = 5042002
ARC_NETWORK_CAIP2 = f"eip155:{ARC_CHAIN_ID}"

#: USDC token address on Arc (matches chain/client.py default).
ARC_USDC_ADDRESS = "0x3600000000000000000000000000000000000000"

#: 24h sliding-window length for the spend cap, in seconds.
SPEND_WINDOW_SECONDS = 24 * 60 * 60


def x402_enabled() -> bool:
    """True when the paywall is armed (`ARCHIMEDES_X402_ENABLED=1`)."""
    return os.getenv(FLAG_ENV, "").strip() in {"1", "true", "True", "yes"}


def _max_usdc_per_day() -> float:
    raw = os.getenv(MAX_USDC_PER_DAY_ENV, "").strip()
    if not raw:
        return DEFAULT_MAX_USDC_PER_DAY
    try:
        return float(raw)
    except ValueError:
        logger.warning("Invalid %s=%r; falling back to %s", MAX_USDC_PER_DAY_ENV, raw, DEFAULT_MAX_USDC_PER_DAY)
        return DEFAULT_MAX_USDC_PER_DAY


# ── Per-payer spend cap (Redis, 24h sliding window) ───────────────────────────


class SpendCapStore:
    """Thin Redis wrapper for the per-payer daily USDC spend cap.

    Keyed on the *payer wallet address* from the verified payment payload (there
    is no embedded wallet per user yet — that's downstream of Dan's passkey fix).
    Uses a sorted set per payer scored by unix-ts; entries older than 24h are
    trimmed on each read. Mirrors the `AgentStateStore` shape in
    `services/redis_state.py` (lazy `aioredis.from_url`), and is mocked at this
    boundary in tests so the suite stays hermetic (no live Redis).
    """

    KEY_PREFIX = "archimedes:x402:spend:"

    def __init__(self, url: str | None = None) -> None:
        self._url = url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self._redis = None

    async def _get_redis(self):
        if self._redis is None:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(self._url, decode_responses=True)
        return self._redis

    async def amount_spent(self, payer: str, *, now: float | None = None) -> float:
        """Total USDC spent by `payer` inside the trailing 24h window."""
        r = await self._get_redis()
        key = f"{self.KEY_PREFIX}{payer.lower()}"
        now = time.time() if now is None else now
        cutoff = now - SPEND_WINDOW_SECONDS
        await r.zremrangebyscore(key, 0, cutoff)
        members = await r.zrange(key, 0, -1)
        total = 0.0
        for m in members:
            # member format: "<unix_ts>:<amount_usdc>"
            try:
                total += float(str(m).split(":", 1)[1])
            except (IndexError, ValueError):
                continue
        return total

    async def record(self, payer: str, amount_usdc: float, *, now: float | None = None) -> None:
        """Record a `payer` charge of `amount_usdc` at `now`."""
        r = await self._get_redis()
        key = f"{self.KEY_PREFIX}{payer.lower()}"
        now = time.time() if now is None else now
        member = f"{now}:{amount_usdc}"
        await r.zadd(key, {member: now})
        await r.expire(key, SPEND_WINDOW_SECONDS)


#: Module-level default store. Tests patch `SpendCapStore` methods at this
#: boundary (per CLAUDE.md "Testing conventions" §"Mock at boundaries").
_spend_store = SpendCapStore()


# ── Circle Gateway facilitator settlement ─────────────────────────────────────


async def _settle_with_facilitator(payment_payload: dict, requirements: dict) -> dict:
    """Settle an x402 payment via Circle Gateway.

    POSTs to `{CIRCLE_GW_BASE}/x402/settle` (see the Gateway OpenAPI
    `/v1/x402/settle`). Returns the parsed settlement result
    (`{success, payer, transaction, network, errorReason?}`). Raises on a
    non-200 facilitator response so the caller maps it to a 402.

    Mocked at the `aiohttp` boundary in tests — no live Circle call.
    """
    url = f"{CIRCLE_GW_BASE}/x402/settle"
    body = {"paymentPayload": payment_payload, "paymentRequirements": requirements}
    async with aiohttp.ClientSession() as session, session.post(url, json=body) as resp:
        if resp.status != 200:
            text = await resp.text()
            logger.warning("x402 facilitator non-200 (%s): %s", resp.status, text[:200])
            raise HTTPException(status_code=402, detail="Payment settlement failed")
        return await resp.json()


# ── Payment requirements / header helpers ─────────────────────────────────────


def _recipient_address(explicit: str | None) -> str:
    return explicit or os.getenv(REVENUE_SPLIT_ADDRESS_ENV, "").strip()


def _build_requirements(price_usdc: float, recipient: str, resource_path: str) -> dict:
    """Build the x402 `paymentRequirements` block (exact scheme, Arc testnet)."""
    # USDC has 6 decimals; amount is the integer base-unit string.
    amount_base_units = str(int(round(price_usdc * 1_000_000)))
    return {
        "scheme": "exact",
        "network": ARC_NETWORK_CAIP2,
        "asset": ARC_USDC_ADDRESS,
        "amount": amount_base_units,
        "maxTimeoutSeconds": 604900,
        "payTo": recipient,
        "resource": resource_path,
    }


def _payment_required_header(price_usdc: float, recipient: str, resource_path: str) -> str:
    """Base64-encoded `PAYMENT-REQUIRED` header value (x402 v2)."""
    payment_required = {
        "x402Version": 2,
        "resource": {
            "url": resource_path,
            "description": "Archimedes strategy construction",
            "mimeType": "application/json",
        },
        "accepts": [_build_requirements(price_usdc, recipient, resource_path)],
    }
    return base64.b64encode(json.dumps(payment_required).encode()).decode()


def _decode_payment_signature(raw: str) -> dict:
    """Decode the base64 `PAYMENT-SIGNATURE` header into the payment payload."""
    try:
        return json.loads(base64.b64decode(raw).decode())
    except Exception as exc:
        # Any decode failure (bad base64 / non-JSON) → 402, never a 500.
        raise HTTPException(status_code=402, detail="Malformed PAYMENT-SIGNATURE") from exc


def _payer_from_payload(payload: dict, settlement: dict) -> str:
    """Best-effort payer wallet extraction (settlement result, then payload)."""
    payer = settlement.get("payer")
    if payer:
        return str(payer)
    # Fall back to the EIP-3009 `from` field in the authorization.
    auth = (payload.get("payload") or {}).get("authorization") or {}
    return str(auth.get("from", "")) or "unknown"


# ── The reusable dependency factory ───────────────────────────────────────────


def x402_paywall(
    price_usdc: float,
    *,
    recipient: str | None = None,
    resource_path: str = "",
) -> Callable[[Request], Awaitable[dict | None]]:
    """Build a FastAPI dependency that paywalls a route via x402.

    Args:
        price_usdc:   Price per request in USDC (e.g. 0.001 for a sub-cent slice).
        recipient:    Destination address for settlement. Defaults to the deployed
                      `RevenueSplit` (env `REVENUE_SPLIT_ADDRESS`). A follow-up
                      generation paywall passes the platform address here for the
                      100%-to-platform case.
        resource_path: The route path, surfaced in the PAYMENT-REQUIRED header.

    Returns:
        An async dependency. When the flag is OFF it is a no-op (returns None).
        When ON it enforces 402 / settle / 429 and returns the payment context
        dict on success (so handlers can read `payer` / `transaction` if needed).

    Usage:
        @router.post("/construct", ...)
        async def construct(..., _pay: dict | None = Depends(x402_paywall(0.001))):
            ...
    """

    async def _dependency(request: Request) -> dict | None:
        # Flag OFF → no-op passthrough. Attaching this never changes behavior
        # until the operator arms the paywall.
        if not x402_enabled():
            return None

        to = _recipient_address(recipient)
        if not to:
            # Misconfigured (flag on but no recipient) — fail loud, don't silently
            # let a paid route through for free.
            raise HTTPException(status_code=503, detail="x402 enabled but REVENUE_SPLIT_ADDRESS is unset")

        path = resource_path or request.url.path
        sig = request.headers.get("PAYMENT-SIGNATURE")

        # 1. No payment → 402 with the requirements.
        if not sig:
            raise HTTPException(
                status_code=402,
                detail="Payment required",
                headers={"PAYMENT-REQUIRED": _payment_required_header(price_usdc, to, path)},
            )

        # 2. Settle via Circle Gateway facilitator.
        payload = _decode_payment_signature(sig)
        requirements = _build_requirements(price_usdc, to, path)
        settlement = await _settle_with_facilitator(payload, requirements)
        if not settlement.get("success"):
            reason = settlement.get("errorReason", "settlement_failed")
            raise HTTPException(status_code=402, detail=f"Payment not settled: {reason}")

        payer = _payer_from_payload(payload, settlement)

        # 3. Per-payer daily spend cap → 429.
        cap = _max_usdc_per_day()
        spent = await _spend_store.amount_spent(payer)
        if spent + price_usdc > cap:
            reset_in = SPEND_WINDOW_SECONDS
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "Daily spend cap exceeded",
                    "payer": payer,
                    "spent_usdc": round(spent, 6),
                    "cap_usdc": cap,
                    "resets_in_seconds": reset_in,
                },
            )
        await _spend_store.record(payer, price_usdc)

        return {
            "payer": payer,
            "amount_usdc": price_usdc,
            "transaction": settlement.get("transaction", ""),
            "network": settlement.get("network", ARC_NETWORK_CAIP2),
        }

    return _dependency
