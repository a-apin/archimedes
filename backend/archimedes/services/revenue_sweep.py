"""Platform revenue sweep: the generation-payment DCW's Gateway balance → real tokens.

Why this exists (Dan, 2026-08-21): x402 generation payments settle into the
platform DCW's balance INSIDE the Circle Gateway contract — attributed to the
address, invisible to any wallet token view. Realizing revenue is a Circle
Gateway withdrawal (burn-intent signed by the DCW via Circle's API →
attestation → on-chain gatewayMint), after which USDC appears as ordinary
tokens in the wallet (console/arcscan visible). "It's important to show we can
collect revenue into our DCW all the way."

This module is the thin platform-revenue twin of the marketplace's
settlement.py Stage A — the SAME circlekit machinery (CircleWalletSigner +
CircleTxExecutor + GatewayClient.withdraw), pointed at the revenue wallet.

Two entry points:

  * CLI (explicit operator action — works regardless of the scheduler flag)::

        python -m archimedes.services.revenue_sweep --check          # read-only
        python -m archimedes.services.revenue_sweep --sweep          # threshold-gated
        python -m archimedes.services.revenue_sweep --sweep --min 5  # override

  * Scheduler loop, wired in main.py behind ``REVENUE_SWEEP_ENABLED`` —
    default **off** (an always-on funds-moving loop is opt-in, never a
    surprise). Interval ``REVENUE_SWEEP_INTERVAL_S`` (default 3600).

Config (all env): ``GENERATION_PAYMENT_RECIPIENT`` (the DCW address — same
value the paywall pays to), ``REVENUE_WALLET_ID`` (that DCW's Circle wallet
id, needed for API signing), ``REVENUE_SWEEP_MIN_USDC`` (default "10.0" —
must stay several multiples of Circle's ~2.01 USDC withdrawal fee, same
rationale as settlement.SWEEP_WITHDRAW_THRESHOLD_USDC).

Deliberately independent of PAYMENTS_DRY_RUN / GENERATION_PAYMENTS_DRY_RUN:
those gate CHARGING USERS; this moves the platform's own already-settled
funds between the platform's own venues (Gateway balance → the same DCW's
token balance) under the accepted DCW-custodial-INTERIM posture (#958).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from decimal import Decimal

from circlekit.client import GatewayClient
from circlekit.wallets import CircleTxExecutor, CircleWalletSigner

from archimedes.marketplace.config import DEFAULT_GATEWAY_CHAIN

logger = logging.getLogger(__name__)

_USDC = Decimal(10) ** 6


def _recipient() -> str:
    return os.getenv("GENERATION_PAYMENT_RECIPIENT", "").strip()


def _wallet_id() -> str:
    return os.getenv("REVENUE_WALLET_ID", "").strip()


def _min_usdc() -> Decimal:
    raw = os.getenv("REVENUE_SWEEP_MIN_USDC", "10.0").strip() or "10.0"
    try:
        value = Decimal(raw)
    except Exception:
        logger.warning("invalid REVENUE_SWEEP_MIN_USDC=%r — using 10.0", raw)
        return Decimal("10.0")
    return value if value > 0 else Decimal("10.0")


def _client() -> GatewayClient:
    recipient, wallet_id = _recipient(), _wallet_id()
    if not recipient or not wallet_id:
        # Loud absence, not a silent no-op: a sweep that can't sign is a
        # configuration outage the operator must see (fail-soft principle —
        # this is a load-bearing credential pair for the revenue path).
        raise RuntimeError(
            "revenue sweep is not configured: GENERATION_PAYMENT_RECIPIENT and "
            "REVENUE_WALLET_ID must both be set (the paywall recipient DCW and "
            "its Circle wallet id)."
        )
    signer = CircleWalletSigner(wallet_id=wallet_id, wallet_address=recipient)
    executor = CircleTxExecutor(wallet_id=wallet_id, wallet_address=recipient)
    return GatewayClient(chain=DEFAULT_GATEWAY_CHAIN, signer=signer, tx_executor=executor)


async def check_revenue() -> dict:
    """Read-only: the revenue DCW's Gateway balance, in USDC decimal strings."""
    client = _client()
    balances = await client.get_gateway_balance()
    return {
        "recipient": _recipient(),
        "available_usdc": balances.formatted_available,
        "total_usdc": balances.formatted_total,
        "min_sweep_usdc": str(_min_usdc()),
    }


async def sweep_revenue(min_usdc: Decimal | None = None) -> dict:
    """Withdraw the full available Gateway balance to the DCW's token balance
    when it meets the threshold. Returns a structured outcome either way —
    callers log it; the scheduler loop never raises out of a tick."""
    threshold = min_usdc if min_usdc is not None else _min_usdc()
    client = _client()
    balances = await client.get_gateway_balance()
    available = Decimal(balances.available) / _USDC
    if available < threshold:
        return {
            "swept": False,
            "reason": f"available {available} USDC below threshold {threshold}",
            "available_usdc": str(available),
        }
    amount = balances.formatted_available
    result = await client.withdraw(amount=amount)
    logger.info(
        "revenue sweep: withdrew %s USDC Gateway → wallet %s; mint tx=%s",
        amount,
        _recipient(),
        getattr(result, "mint_tx_hash", result),
    )
    return {
        "swept": True,
        "amount_usdc": amount,
        "mint_tx_hash": getattr(result, "mint_tx_hash", None),
    }


def sweep_enabled() -> bool:
    """Scheduler gate — only the literal "true" enables (mirrors the repo's
    money-switch convention: an unset money switch must mean OFF)."""
    return os.getenv("REVENUE_SWEEP_ENABLED", "").strip().lower() == "true"


async def revenue_sweep_loop() -> None:
    """The opt-in scheduler loop main.py starts behind sweep_enabled()."""
    interval = int(os.getenv("REVENUE_SWEEP_INTERVAL_S", "3600"))
    logger.info("revenue sweep loop started (interval=%ds, min=%s USDC)", interval, _min_usdc())
    while True:
        try:
            outcome = await sweep_revenue()
            if outcome.get("swept"):
                logger.info("revenue sweep tick: %s", outcome)
        except Exception:
            logger.exception("revenue sweep tick failed — will retry next interval")
        await asyncio.sleep(interval)


def main() -> int:
    ap = argparse.ArgumentParser(description="Platform revenue sweep (Gateway → DCW tokens)")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="read-only balance check")
    group.add_argument("--sweep", action="store_true", help="withdraw if >= threshold")
    ap.add_argument("--min", type=Decimal, default=None, help="threshold override (USDC)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.check:
        print(asyncio.run(check_revenue()))
        return 0
    outcome = asyncio.run(sweep_revenue(args.min))
    print(outcome)
    return 0 if outcome.get("swept") or "below threshold" in str(outcome.get("reason", "")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
