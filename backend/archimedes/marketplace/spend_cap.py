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

Checking the cap and reserving against it happen in ONE atomic Lua script
(_CHECK_AND_RESERVE_LUA), not a read followed by a separate write — see that
script's docstring for why a two-step check-then-record is a real race under
concurrent charges (#1099 review).
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

# Lua: atomically check the wallet's rolling 24h spend window against the cap
# and, if amount_raw fits, reserve it in the SAME round-trip (single EVAL — no
# other command, including another caller's own EVAL, can interleave between
# the sum and the write). This closes a real race (#1099 review): the original
# design summed the window in one Redis round-trip (is_over_cap) and recorded
# a successful charge in a separate, later one (record_charge_usdc), with the
# async payments.charge() call in between. N concurrent charges near the cap
# could all read "under cap" before any of them had recorded anything, so all
# N would proceed and the wallet could blow through the cap by up to N times a
# single charge. Modeled directly on redis_state.py's _LEASE_ACQUIRE_LUA — same
# problem shape: check-and-claim must be one atomic primitive, not two Redis
# round-trips with caller code (here, an async HTTP/chain call) in between.
#   KEYS[1] = the wallet's sorted-set key
#   ARGV[1] = window_start (unix seconds) — prune anything at/before this
#   ARGV[2] = now (unix seconds) — the score a fresh reservation is written at
#   ARGV[3] = cap_raw (raw USDC units) — callers already checked
#             spend_cap_usdc() > 0 before invoking this script
#   ARGV[4] = amount_raw — the pending amount being checked (0 for a pure read)
#   ARGV[5] = member — f"{charge_id}:{amount_raw}", only written if ARGV[7]==1
#   ARGV[6] = ttl_seconds — refreshed on every reservation write
#   ARGV[7] = do_reserve (1/0) — 0 means read-only: never writes, regardless
#             of the verdict (is_over_cap's mode)
# Returns 1 if amount_raw fits under the cap, 0 if it would meet/exceed it.
#
# The double-count-avoidance check (does `member` already exist?) only runs
# when do_reserve==1 — i.e. only in the mode where `member` means anything.
# In read-only mode (do_reserve==0, is_over_cap's mode) member is always the
# empty string, so checking its existence would be meaningless at best; worse,
# ZSCORE key "" happening to find a real (corrupted/externally-written) empty
# member would wrongly skip adding amount_raw to the projection and undercount
# (#1099 review — Copilot). Gating it behind do_reserve==1 also drops an
# unnecessary Redis call from every read-only check.
_CHECK_AND_RESERVE_LUA = """
local key = KEYS[1]
local window_start = tonumber(ARGV[1])
local now = ARGV[2]
local cap_raw = tonumber(ARGV[3])
local amount_raw = tonumber(ARGV[4])
local member = ARGV[5]
local ttl_seconds = ARGV[6]
local do_reserve = tonumber(ARGV[7])

redis.call("ZREMRANGEBYSCORE", key, 0, window_start)
local members = redis.call("ZRANGE", key, 0, -1)
local total = 0
for _, m in ipairs(members) do
    local amt = tonumber(string.match(m, ".*:(%d+)$"))
    if amt then
        total = total + amt
    end
end

-- Default: amount_raw is new spend, not yet counted in `total`.
local projected = total + amount_raw
if do_reserve == 1 then
    -- Re-reserving the exact same (charge_id, amount_raw) as an existing
    -- entry (e.g. a caller that reserves twice for what is logically one
    -- charge) must not double-count it: it is already inside `total`, so
    -- don't add it again. Callers should still avoid reserving the same
    -- charge twice (see try_reserve_usdc's docstring on ordering relative
    -- to the idempotency claim) — this is defense in depth, not a license
    -- to skip that.
    local already_reserved = redis.call("ZSCORE", key, member)
    if already_reserved ~= false then
        projected = total
    end
end

if projected >= cap_raw then
    return 0
end
if do_reserve == 1 then
    redis.call("ZADD", key, now, member)
    redis.call("EXPIRE", key, ttl_seconds)
end
return 1
"""

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
    live rolling total, not a stale snapshot. Does NOT itself catch Redis
    errors — a connection failure propagates to the caller. is_over_cap is
    the fail-open boundary: it wraps this call in a try/except and treats
    any exception as "cap unknown", degrading to False (allow the charge)
    rather than blocking a charge decision on a Redis outage.
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


async def _atomic_check(subscriber_wallet: str, amount_raw: int, *, reserve: bool, charge_id: str | None) -> bool:
    """Shared implementation for try_reserve_usdc and is_over_cap: one atomic
    Redis round-trip (_CHECK_AND_RESERVE_LUA) that prunes the window, sums it,
    and decides whether amount_raw fits under the cap — optionally reserving
    it (ZADD) in that SAME script execution when reserve=True.

    Returns True if amount_raw fits under the cap (and was reserved, if
    reserve=True). Fails OPEN (True) on a Redis error, and short-circuits to
    True without touching Redis at all when the cap is disabled
    (spend_cap_usdc() <= 0) — see is_over_cap's docstring for the fail-open
    rationale, which applies identically here since this is its shared engine.
    A fresh Script is registered on every call rather than cached: the
    redis-py Script object binds to the client that registered it, and this
    module's tests swap in a brand new fakeredis-backed store per test — a
    module-level cached Script would silently keep talking to a prior test's
    now-discarded client. register_script itself makes no network call, so
    this costs nothing but a client-side SHA1 of a short string.
    """
    cap = spend_cap_usdc()
    if cap <= 0:
        return True
    try:
        r = await _get_store()._get_redis()
        script = r.register_script(_CHECK_AND_RESERVE_LUA)
        now = time.time()
        cap_raw = int(cap * (10**_USDC_DECIMALS))
        member = f"{charge_id}:{amount_raw}" if reserve else ""
        result = await script(
            keys=[_key(subscriber_wallet)],
            args=[now - _WINDOW_SECONDS, now, cap_raw, amount_raw, member, _KEY_TTL_SECONDS, 1 if reserve else 0],
        )
        return bool(result)
    except Exception:
        logger.exception(
            "Spend-cap check failed for wallet %s — failing open (allowing the charge)", subscriber_wallet[:10]
        )
        return True


async def try_reserve_usdc(subscriber_wallet: str, amount_raw: int, charge_id: str) -> bool:
    """Atomically check the wallet's rolling 24h window against the cap and,
    if *amount_raw* fits, reserve it in the same Redis round-trip.

    Call this immediately before payments.charge() — not earlier — and call
    release_reservation() if the charge itself then fails (see
    MarketService._charge_one). The ordering relative to the settlement-intent
    idempotency claim matters for correctness, not just style: that claim is
    what guarantees this runs at most once per logical (strategy, tick, sub,
    step) charge, so a crash-retry of an already-settled charge short-circuits
    on "already_settled" before ever reaching this call — it never tries to
    reserve the same amount a second time. Reserving earlier (e.g. ahead of
    the idempotency claim) would double-count exactly that retry, since the
    same charge_id's entry is already sitting in the window from the first
    successful reservation.

    Returns True (reserved) or False (would push the wallet at/over cap;
    nothing written). A non-positive amount_raw is always True and never
    touches Redis — there is nothing to reserve.
    """
    if amount_raw <= 0:
        return True
    return await _atomic_check(subscriber_wallet, amount_raw, reserve=True, charge_id=charge_id)


async def release_reservation(subscriber_wallet: str, charge_id: str, amount_raw: int) -> None:
    """Undo a reservation made by try_reserve_usdc, after payments.charge() fails.

    Best-effort, like this module's other write path: a failure to release
    must never raise — worst case the wallet's window overstates its spend
    until this entry ages out on its own (_WINDOW_SECONDS), which is the
    fail-safe direction (it can only make the cap MORE conservative, never
    let more spend through than intended).
    """
    if amount_raw <= 0:
        return
    try:
        r = await _get_store()._get_redis()
        await r.zrem(_key(subscriber_wallet), f"{charge_id}:{amount_raw}")
    except Exception:
        logger.exception(
            "Failed to release spend-cap reservation for wallet %s (charge %s) — the "
            "wallet's rolling window may overstate spend until this entry ages out",
            subscriber_wallet[:10],
            charge_id,
        )


async def is_over_cap(subscriber_wallet: str, additional_amount_raw: int = 0) -> bool:
    """True if the wallet's current 24h spend, plus *additional_amount_raw* (a
    hypothetical amount not yet reserved), would meet or exceed the configured
    cap.

    Pass additional_amount_raw=0 to just check current standing (e.g. before
    allowing a new subscription) without evaluating a specific pending charge.
    Read-only: built on the same atomic primitive as try_reserve_usdc, with
    reserve=False, so this never writes anything regardless of the verdict —
    both call sites now share one atomic check instead of two independently
    evolving read implementations (#1099 review).

    Fails OPEN (returns False — allow the charge) on a Redis read failure.
    This is a considered choice, not an oversight: this is an additive safety
    guard on top of the existing reactive halt-on-non-payment model
    (SubscriberLiability / MarketplaceAgent.halted), not the only backstop —
    a Redis outage should degrade to today's pre-existing behavior, not start
    blocking every charge in the system.
    """
    allowed = await _atomic_check(subscriber_wallet, additional_amount_raw, reserve=False, charge_id=None)
    return not allowed
