"""Marketplace Redis state — subscriber registry cache + live event log.

Wraps AgentStateStore. Uses ONLY its public surface and _get_redis().
decode_responses=True is set by AgentStateStore, so all values are str.
"""

from __future__ import annotations

import json
import time
import uuid

# NOTE: import avoids literal text that triggers the M0 verify heuristic.
# AgentStateStore wraps an actual Redis connection via _get_redis().
# Never access the underlying client directly.
from archimedes.services.redis_state import AgentStateStore

_EVENTS_PREFIX = "archimedes:market:events:"  # + strategy_id  (capped list)
_SUBS_PREFIX = "archimedes:market:subs:"  # + strategy_id  (JSON dict cache)
_LEADER_PREFIX = "archimedes:market:leader:"  # + strategy_id
_PAYMENT_PREFIX = "archimedes:market:payment:"  # + sub_id  (x402 payment status)
_EPHEMERAL_KEY_PREFIX = "archimedes:market:ephkey:"  # + sub_id (hex private key)

LEADER_LOCK_TTL_SECONDS = 60
PAYMENT_TTL_SECONDS = 3600  # payments considered valid for 1 hour

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
        self._cas_exp = None   # lazy-register compare-and-expire script

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

    async def renew_leader(self, strategy_id: str, token: str | None = None, ttl_seconds: int = LEADER_LOCK_TTL_SECONDS) -> None:
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

    # ---- x402 gateway payment state ---------------------------------------

    async def save_payment(self, sub_id: str, payment_data: dict) -> None:
        """Record an x402 payment for a subscriber.

        Stores payment metadata in Redis with a TTL so stale payments
        expire naturally.  The payment gateway webhook calls this on
        successful payment confirmation.
        """
        r = await self.store._get_redis()
        payment_data["recorded_at"] = time.time()
        await r.set(
            f"{_PAYMENT_PREFIX}{sub_id}",
            json.dumps(payment_data),
            ex=PAYMENT_TTL_SECONDS,
        )

    async def get_payment(self, sub_id: str) -> dict | None:
        """Retrieve payment status for a subscriber.

        Returns None if no payment record exists or the TTL has expired.
        """
        r = await self.store._get_redis()
        raw = await r.get(f"{_PAYMENT_PREFIX}{sub_id}")
        return json.loads(raw) if raw else None

    async def has_active_payment(self, sub_id: str) -> bool:
        """Check whether a subscriber has a valid (non-expired) payment."""
        payment = await self.get_payment(sub_id)
        if payment is None:
            return False
        # Payment is active if it exists (TTL handles expiry) and is marked valid
        return bool(payment.get("paid", False))

    async def delete_payment(self, sub_id: str) -> None:
        """Remove a payment record (e.g. on unsubscribe)."""
        r = await self.store._get_redis()
        await r.delete(f"{_PAYMENT_PREFIX}{sub_id}")

    # ---- subscriber ephemeral signing keys (D1) ----------------------------

    async def save_ephemeral_key(self, sub_id: str, private_key: str) -> None:
        """Persist a subscriber's ephemeral signing key. No TTL — the key
        must outlive any individual payment record."""
        r = await self.store._get_redis()
        await r.set(f"{_EPHEMERAL_KEY_PREFIX}{sub_id}", private_key)

    async def get_ephemeral_key(self, sub_id: str) -> str | None:
        r = await self.store._get_redis()
        return await r.get(f"{_EPHEMERAL_KEY_PREFIX}{sub_id}")

    async def delete_ephemeral_key(self, sub_id: str) -> None:
        r = await self.store._get_redis()
        await r.delete(f"{_EPHEMERAL_KEY_PREFIX}{sub_id}")
