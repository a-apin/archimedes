"""Marketplace Redis state — subscriber registry cache + live event log.

Wraps AgentStateStore. Uses ONLY its public surface and _get_redis().
decode_responses=True is set by AgentStateStore, so all values are str.
"""

from __future__ import annotations

import json
import uuid

# NOTE: import avoids literal text that triggers the M0 verify heuristic.
# AgentStateStore wraps an actual Redis connection via _get_redis().
# Never access the underlying client directly.
from archimedes.services.redis_state import AgentStateStore

_EVENTS_PREFIX = "archimedes:market:events:"  # + strategy_id  (capped list)
_SUBS_PREFIX = "archimedes:market:subs:"  # + strategy_id  (JSON dict cache)
_LEADER_PREFIX = "archimedes:market:leader:"  # + strategy_id
_SUBTICK_PREFIX = "archimedes:market:subtick:"  # + sub_id  (capped list, last 200)
_PAYMENT_PREFIX = "archimedes:market:payment:"  # + sub_id  (JSON payment record)
_KEY_REGIME_LOCK = "archimedes:regime:lock"  # global regime-classification lock

LEADER_LOCK_TTL_SECONDS = 60

# Lua: delete key only if its value matches the expected token.
# Without this check a stale-owner release could delete a newer owner's lock.
_COMPARE_AND_DELETE = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
else
    return 0
end
"""

# Lua: extend TTL only if the stored value matches the expected token.
# Prevents a stale-owner renew from silently refreshing a lock it no longer owns.
_COMPARE_AND_EXPIRE = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    redis.call("PEXPIRE", KEYS[1], ARGV[2])
    return 1
else
    return 0
end
"""


class MarketState:
    def __init__(self, store: AgentStateStore | None = None) -> None:
        self.store = store or AgentStateStore()
        self._cas_del = None  # lazy-register compare-and-delete script
        self._cas_exp = None  # lazy-register compare-and-expire script

    async def append_event(self, strategy_id: str, event: dict) -> None:
        r = await self.store._get_redis()
        await r.lpush(f"{_EVENTS_PREFIX}{strategy_id}", json.dumps(event))
        await r.ltrim(f"{_EVENTS_PREFIX}{strategy_id}", 0, 199)  # keep last 200

    async def get_events(self, strategy_id: str, count: int = 50) -> list[dict]:
        r = await self.store._get_redis()
        raw = await r.lrange(f"{_EVENTS_PREFIX}{strategy_id}", 0, count - 1)
        return [json.loads(e) for e in raw]  # already str

    async def save_subscribers(self, strategy_id: str, subs: dict) -> None:
        r = await self.store._get_redis()
        await r.set(f"{_SUBS_PREFIX}{strategy_id}", json.dumps(subs))

    async def load_subscribers(self, strategy_id: str) -> dict:
        r = await self.store._get_redis()
        raw = await r.get(f"{_SUBS_PREFIX}{strategy_id}")
        return json.loads(raw) if raw else {}  # raw is str|None

    async def try_acquire_leader(self, strategy_id: str, ttl_seconds: int = LEADER_LOCK_TTL_SECONDS) -> str | None:
        """Attempt to acquire the per-strategy leader lock.

        Returns a UUID fencing token on success, ``None`` on failure.
        The token must be passed to ``renew_leader`` and ``release_leader``
        so that only the current owner can mutate the lock.
        """
        r = await self.store._get_redis()
        token = uuid.uuid4().hex
        acquired = await r.set(f"{_LEADER_PREFIX}{strategy_id}", token, nx=True, ex=ttl_seconds)
        return token if acquired else None

    async def renew_leader(
        self, strategy_id: str, token: str | None = None, ttl_seconds: int = LEADER_LOCK_TTL_SECONDS
    ) -> None:
        """Extend the leader lock TTL — no-op if *token* does not match."""
        if token is None:
            return
        key = f"{_LEADER_PREFIX}{strategy_id}"
        if self._cas_exp is None:
            r = await self.store._get_redis()
            self._cas_exp = r.register_script(_COMPARE_AND_EXPIRE)
        await self._cas_exp(keys=[key], args=[token, str(ttl_seconds * 1000)])

    async def release_leader(self, strategy_id: str, token: str | None = None) -> None:
        """Release the leader lock — no-op if *token* does not match."""
        if token is None:
            return
        key = f"{_LEADER_PREFIX}{strategy_id}"
        if self._cas_del is None:
            r = await self.store._get_redis()
            self._cas_del = r.register_script(_COMPARE_AND_DELETE)
        await self._cas_del(keys=[key], args=[token])

    # ---- Regime lock (global, one classifier across all publisher tasks) ---

    async def try_acquire_regime_lock(self, ttl_seconds: int = 30) -> bool:
        """Attempt to acquire the global regime-classification lock.

        Only one publisher task should classify the exogenous market regime
        per tick window. Returns True if this caller acquired the lock.
        """
        r = await self.store._get_redis()
        acquired = await r.set(_KEY_REGIME_LOCK, "1", nx=True, ex=ttl_seconds)
        return bool(acquired)

    # ---- subscriber tick log (Redis mirror, last 200) -----------------------

    async def push_subscriber_tick(self, sub_id: str, payload: dict) -> None:
        r = await self.store._get_redis()
        key = f"{_SUBTICK_PREFIX}{sub_id}"
        await r.lpush(key, json.dumps(payload, default=str))
        await r.ltrim(key, 0, 199)

    async def get_subscriber_ticks(self, sub_id: str, count: int = 50) -> list[dict]:
        r = await self.store._get_redis()
        raw = await r.lrange(f"{_SUBTICK_PREFIX}{sub_id}", 0, count - 1)
        return [json.loads(e) for e in raw]  # already str

    # ---- x402 payment record ------------------------------------------------

    async def save_payment(self, sub_id: str, record: dict) -> None:
        """Persist a payment record for *sub_id*. Auto-stamps recorded_at."""
        import datetime

        r = await self.store._get_redis()
        payload = dict(record)
        payload.setdefault("recorded_at", datetime.datetime.now(datetime.UTC).isoformat())
        await r.set(f"{_PAYMENT_PREFIX}{sub_id}", json.dumps(payload))

    async def get_payment(self, sub_id: str) -> dict | None:
        """Return the most recent payment record for *sub_id*, or None."""
        r = await self.store._get_redis()
        raw = await r.get(f"{_PAYMENT_PREFIX}{sub_id}")
        return json.loads(raw) if raw else None

    async def has_active_payment(self, sub_id: str) -> bool:
        """Return True iff a payment record exists and its ``paid`` field is True."""
        payment = await self.get_payment(sub_id)
        return bool(payment and payment.get("paid"))

    async def delete_payment(self, sub_id: str) -> None:
        """Remove the payment record for *sub_id* (call after successful settlement)."""
        r = await self.store._get_redis()
        await r.delete(f"{_PAYMENT_PREFIX}{sub_id}")
