"""x402 micropayment seam over circle-titanoboa-sdk (circlekit).

This module is the ONLY place circlekit is imported. The SDK is
pre-1.0; keeping the import surface here gives API drift a one-file
blast radius.

Flow per charge (all in-process, no HTTP between publisher/subscriber):
  1. middleware.require(price, path)      -> 402 requirements (publisher side)
  2. create_payment_header(signer, reqs)  -> EIP-712 signature with the
     subscriber's ephemeral key (subscriber side, same process)
  3. middleware.settle(header, price)     -> Circle facilitator verifies and
     records the micropayment. Circle batches and settles on-chain later;
     we do NOT run any settlement logic.
"""

from __future__ import annotations

import logging
import os
from decimal import Decimal

from circlekit import PrivateKeySigner, create_gateway_middleware
from circlekit.server import GatewayMiddleware
from circlekit.x402 import create_payment_header, get_payment_required

logger = logging.getLogger(__name__)

_USDC_DECIMALS = 6

_middleware: GatewayMiddleware | None = None


def get_gateway_middleware() -> GatewayMiddleware:
    """Lazy process-wide middleware singleton (config read at first use)."""
    global _middleware
    if _middleware is None:
        seller = os.getenv("GATEWAY_SELLER_ADDRESS", "").strip()
        chain = os.getenv("GATEWAY_CHAIN", "arcTestnet").strip()
        if not seller or int(seller, 16) == 0:
            raise RuntimeError(
                "GATEWAY_SELLER_ADDRESS is not configured — refusing to "
                "charge into the zero address."
            )
        _middleware = create_gateway_middleware(
            seller_address=seller,
            chain=chain,
            description="Archimedes copy-trading tick charge",
        )
    return _middleware


def fee_to_price(action_count: int, flat_fee_raw: int) -> str:
    """Convert action_count x flat fee (raw 6-decimal USDC units) to the
    "$X.XXXXXX" price string circlekit expects. Uses Decimal — no floats."""
    if action_count < 0 or flat_fee_raw < 0:
        raise ValueError("action_count and flat_fee_raw must be >= 0")
    total_raw = action_count * flat_fee_raw
    usd = Decimal(total_raw) / (Decimal(10) ** _USDC_DECIMALS)
    return f"${usd:.6f}"


async def charge(
    sub_id: str,
    ephemeral_key: str,
    strategy_id: str,
    tick_id: str,
    action_count: int,
    flat_fee_raw: int,
) -> bool:
    """Charge one subscriber for one tick. Returns True iff the micropayment
    was verified AND settled by Circle's facilitator. Never raises: every
    failure mode is logged and returned as False (the caller's existing
    halt path handles unpaid subscribers)."""
    try:
        middleware = get_gateway_middleware()
        price = fee_to_price(action_count, flat_fee_raw)

        # Zero-amount tick: nothing to charge, treat as paid.
        if price == "$0.000000":
            return True

        # 1. Publisher side: build 402 requirements. `path` is a logical
        # resource identifier only — no HTTP route exists or is needed.
        path = f"/charge/{strategy_id}/{tick_id}/{sub_id}"
        required = middleware.require(price, path)
        x402 = get_payment_required(
            required["headers"].get("PAYMENT-REQUIRED"),
            required["body"],
        )
        requirements = x402.get_gateway_option()
        if requirements is None:
            logger.error("[%s] no gateway payment option in 402 body", tick_id)
            return False

        # 2. Subscriber side (same process): sign with the ephemeral key.
        signer = PrivateKeySigner(ephemeral_key)
        header = create_payment_header(signer=signer, requirements=requirements)

        # 3. Verify + settle via Circle's facilitator.
        verify_result = await middleware.verify(header, price)
        if not verify_result.is_valid:
            logger.warning(
                "[%s] payment verify failed for sub %s: %s",
                tick_id, sub_id, getattr(verify_result, "invalid_reason", "unknown"),
            )
            return False

        await middleware.settle(header, price)  # raises ValueError on failure
        logger.info("[%s] charged sub %s %s", tick_id, sub_id, price)
        return True

    except Exception as exc:  # noqa: BLE001 — bool contract, never raise
        logger.warning("[%s] charge failed for sub %s: %s", tick_id, sub_id, exc)
        return False
