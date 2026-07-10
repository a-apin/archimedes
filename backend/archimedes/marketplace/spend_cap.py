"""Per-subscriber-wallet rolling 24h spend cap for marketplace nanopayments (#713).

Tracks USDC actually charged per subscriber wallet (not per sub_id — a wallet can
run several subscriptions, and the cap is meant to bound one person's total
exposure, not let it multiply per subscription) in a trailing 24h window, and
lets callers refuse a charge (or a new subscription) that would push the wallet
over a configured ceiling.

Kept deliberately separate from payments.py (settlement) and service.py (custody,
liability, halting): this is a pure "would this push the wallet over its own
declared limit" check, backed by its own Redis key. Issue #975's non-custodial
fee-custody migration will rework payments.py/wallet_provisioner.py/marketplace_routes.py
for custody reasons; this module doesn't touch custody at all, so it shouldn't
need rework alongside that migration.

Storage: one Redis sorted set per wallet, score = charge unix timestamp, member =
f"{charge_id}:{amount_raw}" (amount_raw is 6-decimal raw USDC, matching the rest
of this codebase's convention — see payments.fee_to_price). charge_id only needs
to make the member unique per entry; it is never read back.
"""

from __future__ import annotations

import logging
import os
import time
from decimal import Decimal

from archimedes.services.redis_state import AgentStateStore

logger = logging.getLogger(__name__)

_SPEND_PREFIX = "archimedes:market:spend:"  # + subscriber_wallet (lowercased)
_WINDOW_SECONDS = 24 * 60 * 60
_KEY_TTL_SECONDS = _WINDOW_SECONDS + 3600  # small margin past the window so an
# inactive wallet's key disappears on its own instead of living forever with
# nothing left in range after every member has aged out.
_USDC_DECIMALS = 6

_store: AgentStateStore | None = None


def _get_store() -> AgentStateStore:
    global _store
    if _store is None:
        _store = AgentStateStore()
    return _store


def spend_cap_usdc() -> Decimal:
    """The configured per-wallet 24h USDC spend cap.

    Default (50) is NOT grounded in any specific risk analysis — today's
    FLAT_FEE_PER_ACTION (100 raw units = $0.0001/action) makes any USDC-scale
    cap enormously permissive relative to current testnet fee levels. Treat
    this default as a placeholder to be revisited once real pricing is live
    (PAYMENTS_DRY_RUN=false), not a considered number.

    A cap of 0 (or unset — the default keeps it non-zero) disables the check
    entirely, matching this codebase's general fail-open-when-unconfigured
    convention for opt-in safety features.
    """
    raw = os.getenv("MARKETPLACE_SPEND_CAP_USDC", "50")
    try:
        return Decimal(raw)
    except Exception:
        logger.warning("MARKETPLACE_SPEND_CAP_USDC=%r is not a valid number — treating as disabled", raw)
        return Decimal(0)


def _key(subscriber_wallet: str) -> str:
    return f"{_SPEND_PREFIX}{subscriber_wallet.lower()}"


def _raw_to_usdc(amount_raw: int | str) -> Decimal:
    return Decimal(amount_raw) / (Decimal(10) ** _USDC_DECIMALS)


async def get_24h_spend_usdc(subscriber_wallet: str) -> Decimal:
    """Sum of USDC actually charged to *subscriber_wallet* in the trailing 24h.

    Prunes expired entries first (ZREMRANGEBYSCORE), so this is always the
    live rolling total, not a stale snapshot. Never raises — a Redis failure
    here should not itself block or corrupt a charge decision; callers treat
    an exception as "cap unknown" (see is_over_cap).
    """
    r = await _get_store()._get_redis()
    key = _key(subscriber_wallet)
    now = time.time()
    await r.zremrangebyscore(key, 0, now - _WINDOW_SECONDS)
    members = await r.zrange(key, 0, -1)
    total = Decimal(0)
    for member in members:
        try:
            total += _raw_to_usdc(member.rsplit(":", 1)[-1])
        except Exception:
            continue  # malformed member (should not happen) — skip, don't fail the whole read
    return total


async def record_charge_usdc(subscriber_wallet: str, charge_id: str, amount_raw: int) -> None:
    """Record a SUCCESSFUL charge against the wallet's rolling window.

    Call this only after payments.charge() returns True — this function does
    not charge anything itself, it only tracks what was already charged.
    Best-effort: a failure to record must never undo or fail an already-settled
    charge, so exceptions are logged and swallowed, never raised.
    """
    if amount_raw <= 0:
        return
    try:
        r = await _get_store()._get_redis()
        key = _key(subscriber_wallet)
        now = time.time()
        await r.zadd(key, {f"{charge_id}:{amount_raw}": now})
        await r.expire(key, _KEY_TTL_SECONDS)
    except Exception:
        logger.exception(
            "Failed to record spend-cap charge for wallet %s (charge %s, amount %d raw) — "
            "the charge itself already succeeded and is unaffected; only this window's "
            "bookkeeping is stale until the next successful record",
            subscriber_wallet[:10],
            charge_id,
            amount_raw,
        )


async def is_over_cap(subscriber_wallet: str, additional_amount_raw: int = 0) -> bool:
    """True if the wallet's current 24h spend, plus *additional_amount_raw* (a
    pending charge not yet recorded), would meet or exceed the configured cap.

    Pass additional_amount_raw=0 to just check current standing (e.g. before
    allowing a new subscription) without evaluating a specific pending charge.

    Fails OPEN (returns False — allow the charge) on a Redis read failure.
    This is a considered choice, not an oversight: this is an additive safety
    guard on top of the existing reactive halt-on-non-payment model
    (SubscriberLiability / MarketplaceAgent.halted), not the only backstop —
    a Redis outage should degrade to today's pre-existing behavior, not start
    blocking every charge in the system.
    """
    cap = spend_cap_usdc()
    if cap <= 0:
        return False
    try:
        current = await get_24h_spend_usdc(subscriber_wallet)
    except Exception:
        logger.exception("Spend-cap check failed for wallet %s — failing open (allowing the charge)", subscriber_wallet[:10])
        return False
    additional = _raw_to_usdc(additional_amount_raw) if additional_amount_raw else Decimal(0)
    return (current + additional) >= cap
