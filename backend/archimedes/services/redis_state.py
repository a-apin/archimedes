"""Redis state store — persists agent state across ticks.

Stores the latest regime classification and agent heartbeat in Redis
so the API layer and frontend can read live agent state.

Two distinct signals live here, under two distinct keys (issue #659):
  - ``KEY_REGIME`` — the *exogenous* market regime from a regime detector
    (VIX / momentum / spreads). May be absent until a detector is wired.
  - ``KEY_ENSEMBLE_CONSENSUS`` — the *endogenous* strategy-ensemble consensus
    derived from ``flat_pct``. Always available once the agent ticks. This is
    "how decisive is the ensemble", NOT a market regime, and must not shadow
    the market-regime key.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import UTC, datetime

import redis.asyncio as aioredis

from archimedes.models.regime import EnsembleConsensus, RegimeClassification

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Keys
KEY_REGIME = "archimedes:regime:current"
KEY_ENSEMBLE_CONSENSUS = "archimedes:ensemble_consensus"
KEY_HEARTBEAT = "archimedes:agent:heartbeat"
KEY_LAST_REBALANCE_PREFIX = "archimedes:agent:last_rebalance:"
KEY_TRACE_PREFIX = "archimedes:trace:"
KEY_TRACE_INDEX = "archimedes:trace:index"
KEY_SIWE_NONCE_PREFIX = "archimedes:auth:nonce:"

# Runner exactly-once lease (#1043) — funds-adjacent singleton runners
# (oracle_runner, agent_runner, kb_runner) use this to make sure only ONE live
# copy performs on-chain writes at a time. `KEY_LEASE_PREFIX + runner_name` is
# the lock; `KEY_LEASE_PREFIX + runner_name + KEY_LEASE_FENCING_SUFFIX` is a
# monotonically increasing counter whose current value is folded into every
# issued token so each acquisition has a unique, ordered identity for
# audit/log correlation — independent of the SET NX itself, which is what
# actually enforces exclusivity. Deliberately a SEPARATE key namespace from
# KEY_HEARTBEAT: the heartbeat has no TTL, no owner, and is written once per
# tick by whichever process happens to be running — it cannot answer "am I
# the only one running?" and must not be repurposed for that.
KEY_LEASE_PREFIX = "archimedes:leader:"
KEY_LEASE_FENCING_SUFFIX = ":fencing"

# Lua: acquire the lease ATOMICALLY (single EVAL — no other command can
# interleave between the sub-steps). This is the exclusivity primitive.
#   KEYS[1] = lock key, KEYS[2] = fencing counter key
#   ARGV[1] = holder uuid, ARGV[2] = ttl_ms
# The whole thing runs atomically, which is what makes it correct: a naive
# client-side `SET NX` → `INCR` → `SET XX` (an earlier revision, PR #1046
# review) has a clobber race — if the lease TTL expires between the NX and
# the finalize, another runner can win the key and the finalize's `XX`
# (existence-only) write silently overwrites the NEW owner, violating
# exclusivity for a funds-adjacent singleton. Doing SET-NX + INCR + finalize
# inside ONE script removes the gap entirely: nothing can acquire the key
# between our NX win and our token write. INCR still fires only on a win, so
# a losing retry loop never burns fence numbers.
_LEASE_ACQUIRE_LUA = """
if redis.call("SET", KEYS[1], ARGV[1], "NX", "PX", ARGV[2]) then
    local fence = redis.call("INCR", KEYS[2])
    local token = ARGV[1] .. ":" .. fence
    redis.call("SET", KEYS[1], token, "PX", ARGV[2])
    return token
else
    return false
end
"""

# Lua: renew a lease's TTL — ONLY if the caller's token still matches the
# stored owner (compare-and-set). Re-issuing SET with PX (rather than
# PEXPIRE) keeps the value identical to a fresh acquire while proving
# ownership hasn't changed underneath the caller between check and renew.
_LEASE_RENEW_LUA = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    redis.call("SET", KEYS[1], ARGV[1], "PX", ARGV[2])
    return 1
else
    return 0
end
"""

# Lua: release a lease — ONLY if the caller's token still matches the stored
# owner (compare-and-delete). Without this check, a stale holder (e.g. one
# whose lease already expired and was re-acquired by someone else) could
# delete a lock it no longer owns.
_LEASE_RELEASE_LUA = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
else
    return 0
end
"""


def safe_json_loads(raw, *, context: str):
    """``json.loads`` that degrades gracefully on malformed data (#919).

    Public helper (promoted from ``_safe_json_loads`` in the #1107 review so
    cross-module consumers — e.g. ``marketplace/state.py`` — depend on a
    stable name rather than a private internal).

    A truncated, partial, or externally-tampered Redis value must not crash a
    read path with a 500. Logs the decode failure and returns ``None`` so the
    caller can skip the bad entry or return a null/empty response.
    """
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("Malformed JSON in Redis (%s) — dropping value: %s", context, exc)
        return None


class AgentStateStore:
    """Thin wrapper over Redis for agent state."""

    def __init__(self, url: str | None = None) -> None:
        self._url = url or REDIS_URL
        self._redis: aioredis.Redis | None = None
        self._lease_acquire_script = None  # lazy-registered atomic acquire+fence Lua script
        self._lease_renew_script = None  # lazy-registered compare-and-set Lua script
        self._lease_release_script = None  # lazy-registered compare-and-delete Lua script

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(self._url, decode_responses=True)
        return self._redis

    # ─── Regime ───────────────────────────────────────────────────

    async def save_regime(self, classification: RegimeClassification) -> None:
        r = await self._get_redis()
        data = {
            "regime": classification.regime.value,
            "confidence": classification.confidence,
            "vix": classification.signals.vix_level,
            "sp500_above_ma50": classification.signals.sp500_above_ma50,
            "sp500_above_ma200": classification.signals.sp500_above_ma200,
            "regime_changed": classification.regime_changed,
            "timestamp": classification.timestamp.isoformat(),
        }
        await r.set(KEY_REGIME, json.dumps(data))
        logger.debug("Saved regime to Redis: %s", classification.regime.value)

    async def save_ensemble_consensus(
        self,
        consensus: EnsembleConsensus,
        all_signals: list,
    ) -> None:
        """Persist the strategy-ensemble consensus under its own Redis key.

        This is the endogenous "how decisive is the ensemble" signal — derived
        from ``flat_pct`` — and is stored under ``KEY_ENSEMBLE_CONSENSUS`` so it
        does NOT shadow the exogenous market regime at ``KEY_REGIME`` (#659).
        """
        r = await self._get_redis()
        signal_summary = {}
        for ss in all_signals:
            for s in ss.signals:
                signal_summary[s.asset] = {
                    "signal": s.signal.value,
                    "weight": s.weight,
                    "reason": s.reason,
                    "strategy": ss.paper_title[:40],
                }
        # Dynamic confidence from signal weights + dispersion (matches
        # _compute_confidence in agent_runner — same formula, different caller).
        # This is the ensemble's *decisiveness*, not a market-regime confidence.
        flat_pct = consensus.flat_pct
        if all_signals:
            directional = [s for ss in all_signals for s in ss.signals if s.signal.value != "flat"]
            vote_ratio = 1.0 - flat_pct
            avg_strength = sum(abs(s.weight) for s in directional) / max(len(directional), 1) if directional else 0.0
            avg_strength = min(avg_strength, 1.0)
            all_weights = [s.weight for ss in all_signals for s in ss.signals]
            if len(all_weights) >= 2:
                mean_w = sum(all_weights) / len(all_weights)
                variance = sum((w - mean_w) ** 2 for w in all_weights) / len(all_weights)
                dispersion_penalty = min(variance**0.5 * 2, 0.3)
            else:
                dispersion_penalty = 0.0
            dyn_confidence = max(0.05, min(0.99, vote_ratio * (0.5 + 0.5 * avg_strength) - dispersion_penalty))
        else:
            dyn_confidence = 0.5
        data = {
            "label": consensus.label.value,
            "confidence": round(dyn_confidence, 4),
            "flat_pct": round(flat_pct, 2),
            "strategy_count": consensus.signal_count or len(all_signals),
            "signals": signal_summary,
            "timestamp": datetime.now(UTC).isoformat(),
            "source": "strategy_consensus",
        }
        await r.set(KEY_ENSEMBLE_CONSENSUS, json.dumps(data))
        logger.debug("Saved ensemble consensus to Redis: %s", consensus.label.value)

    async def load_regime(self) -> dict | None:
        """Load the exogenous market regime (may be None until a detector writes it)."""
        r = await self._get_redis()
        raw = await r.get(KEY_REGIME)
        if raw:
            return safe_json_loads(raw, context=KEY_REGIME)
        return None

    async def load_ensemble_consensus(self) -> dict | None:
        """Load the endogenous strategy-ensemble consensus (#659)."""
        r = await self._get_redis()
        raw = await r.get(KEY_ENSEMBLE_CONSENSUS)
        if raw:
            return safe_json_loads(raw, context=KEY_ENSEMBLE_CONSENSUS)
        return None

    # ─── Heartbeat ────────────────────────────────────────────────

    async def save_heartbeat(self) -> None:
        r = await self._get_redis()
        await r.set(KEY_HEARTBEAT, datetime.now(UTC).isoformat())

    async def get_heartbeat(self) -> str | None:
        r = await self._get_redis()
        return await r.get(KEY_HEARTBEAT)

    # ─── Runner exactly-once lease (#1043) ─────────────────────────
    #
    # A real mutual-exclusion primitive — owner token + TTL + compare-and-set
    # renew/release — for funds-adjacent singleton runners. NOT a repurposing
    # of save_heartbeat/get_heartbeat above (those stay untouched: no TTL, no
    # owner, once-per-tick, and cannot prove exclusivity).

    async def acquire_lease(self, runner_name: str, ttl_ms: int) -> str | None:
        """Attempt to acquire the exactly-once lease for *runner_name*.

        Returns a fencing token ``"<uuid4>:<fence>"`` on success, or ``None``
        if another live copy already holds the lease. ``fence`` is a
        monotonically increasing per-runner counter folded into the token so
        every acquisition WIN gets a unique, ordered identity — useful for
        audit/log correlation, independent of the ``SET NX`` that actually
        enforces exclusivity.

        The acquire runs as a SINGLE atomic Lua script (``_LEASE_ACQUIRE_LUA``):
        ``SET NX`` (the exclusivity check) + ``INCR`` (fence, only on a win) +
        the final token write, with no window for another process to interleave.
        This is deliberately NOT a client-side ``SET NX`` → ``INCR`` → ``SET XX``
        sequence: that has a clobber race (if the TTL lapses between the NX and
        the finalize, the existence-only ``XX`` write can overwrite a lease a
        DIFFERENT runner just won — fatal for a funds-adjacent singleton). The
        atomic script closes that gap and still only consumes a fence on a win,
        so a losing retry loop never burns fence numbers.

        The returned token must be passed to ``renew_lease`` / ``release_lease``
        so only the current owner can mutate the lease.
        """
        r = await self._get_redis()
        key = f"{KEY_LEASE_PREFIX}{runner_name}"
        fencing_key = f"{KEY_LEASE_PREFIX}{runner_name}{KEY_LEASE_FENCING_SUFFIX}"
        if self._lease_acquire_script is None:
            self._lease_acquire_script = r.register_script(_LEASE_ACQUIRE_LUA)

        holder_uuid = str(uuid.uuid4())
        token = await self._lease_acquire_script(keys=[key, fencing_key], args=[holder_uuid, str(ttl_ms)])
        return token if token else None

    async def renew_lease(self, runner_name: str, token: str, ttl_ms: int) -> bool:
        """Extend the lease TTL — ONLY if *token* still matches the stored owner.

        Returns ``False`` (never raises for a lost lease) when the token no
        longer matches — e.g. the lease expired and another copy acquired it.
        Callers MUST treat a ``False`` return as "lease lost" and fail
        closed: skip the on-chain write this cycle and keep retrying to
        re-acquire. See ``chain/oracle_runner.py`` and ``chain/agent_runner.py``.
        """
        r = await self._get_redis()
        key = f"{KEY_LEASE_PREFIX}{runner_name}"
        if self._lease_renew_script is None:
            self._lease_renew_script = r.register_script(_LEASE_RENEW_LUA)
        result = await self._lease_renew_script(keys=[key], args=[token, str(ttl_ms)])
        return bool(result)

    async def release_lease(self, runner_name: str, token: str) -> None:
        """Release the lease — a no-op if *token* no longer matches the owner.

        Best-effort: safe to call on shutdown even if the lease already
        expired or was reclaimed by another copy (the compare-and-delete
        check makes that a no-op rather than deleting someone else's lease).
        """
        r = await self._get_redis()
        key = f"{KEY_LEASE_PREFIX}{runner_name}"
        if self._lease_release_script is None:
            self._lease_release_script = r.register_script(_LEASE_RELEASE_LUA)
        await self._lease_release_script(keys=[key], args=[token])

    # ─── Last rebalance per vault ─────────────────────────────────

    async def save_last_rebalance(self, vault_address: str) -> None:
        r = await self._get_redis()
        key = f"{KEY_LAST_REBALANCE_PREFIX}{vault_address.lower()}"
        await r.set(key, datetime.now(UTC).isoformat())

    async def get_last_rebalance(self, vault_address: str) -> datetime | None:
        r = await self._get_redis()
        key = f"{KEY_LAST_REBALANCE_PREFIX}{vault_address.lower()}"
        raw = await r.get(key)
        if raw:
            return datetime.fromisoformat(raw)
        return None

    # ─── Events ──────────────────────────────────────────────────

    async def save_event(self, event_type: str, data: dict) -> None:
        """Append an event to the agent event log (capped list)."""
        r = await self._get_redis()
        entry = json.dumps(
            {
                "type": event_type,
                "data": data,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )
        await r.lpush("archimedes:agent:events", entry)
        await r.ltrim("archimedes:agent:events", 0, 99)  # keep last 100

    async def get_events(self, count: int = 20) -> list[dict]:
        r = await self._get_redis()
        raw = await r.lrange("archimedes:agent:events", 0, count - 1)
        return [d for e in raw if (d := safe_json_loads(e, context="agent:events")) is not None]

    # ─── Vault Monitoring ─────────────────────────────────────────

    async def save_vault_snapshot(self, vault_address: str, metrics: dict) -> None:
        """Save a vault metrics snapshot. Keeps last 288 (= 24h at 5min)."""
        r = await self._get_redis()
        key = f"archimedes:vault:snapshots:{vault_address.lower()}"
        entry = json.dumps(
            {
                **metrics,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )
        await r.lpush(key, entry)
        await r.ltrim(key, 0, 287)

    async def get_vault_snapshots(self, vault_address: str, count: int = 50) -> list[dict]:
        r = await self._get_redis()
        key = f"archimedes:vault:snapshots:{vault_address.lower()}"
        raw = await r.lrange(key, 0, count - 1)
        return [d for e in raw if (d := safe_json_loads(e, context="vault:snapshots")) is not None]

    # ─── Reasoning Trace Persistence ────────────────────────────

    async def save_trace(self, trace_data: dict) -> None:
        """Store off-chain reasoning trace data keyed by trace_hash.

        Also maintains secondary index by trace UUID for lookup.

        Stamps ownership on the way in (#1556). This is the single write choke
        point for traces — ``publish_trace``, the agent runner's three persist
        sites and the generation-trace writer all land here — so stamping HERE
        is what makes "every persisted trace knows who owns it" true by
        construction rather than by five call sites remembering. The read gate
        (``services.trace_visibility``) then needs no database round-trip for
        anything published after this change, which is why a Postgres outage
        cannot downgrade a private trace to a public one.

        A caller that already knows the owner (the generation path, whose trace
        has no vault at all) sets ``owner_user_id``/``owner_wallet`` itself;
        the presence of either key suppresses the lookup, including when the
        value is ``None`` — "this writer resolved the owner and there isn't
        one" must not be overwritten by a vault guess.
        """
        r = await self._get_redis()
        trace_hash = trace_data.get("trace_hash", "")
        trace_id = trace_data.get("id", "")
        if not trace_hash:
            logger.warning("Cannot save trace without trace_hash")
            return

        if "owner_user_id" not in trace_data and "owner_wallet" not in trace_data:
            trace_data = {**trace_data, **self._resolve_trace_owner(trace_data.get("vault_address", ""))}

        # Store full trace data by hash
        key = f"{KEY_TRACE_PREFIX}{trace_hash}"
        await r.set(key, json.dumps(trace_data, default=str))

        # Secondary index by UUID
        if trace_id:
            await r.set(f"{KEY_TRACE_PREFIX}id:{trace_id}", trace_hash)

        # Add to sorted set by timestamp for listing
        ts = trace_data.get("timestamp", "")
        score = 0
        if ts:
            try:
                dt = datetime.fromisoformat(ts)
                score = dt.timestamp()
            except (ValueError, TypeError):
                score = datetime.now(UTC).timestamp()
        else:
            score = datetime.now(UTC).timestamp()

        await r.zadd(KEY_TRACE_INDEX, {trace_hash: score})
        logger.debug("Saved trace %s to Redis", trace_hash[:16])

    @staticmethod
    def _resolve_trace_owner(vault_address: str) -> dict:
        """``{"owner_user_id": …, "owner_wallet": …}`` for a vault (#1556).

        Fail-soft by design — a trace must still persist when the identity
        database is unreachable, and an unstamped row is not a leak: the read
        gate falls back to looking the vault owner up itself, and to the
        house-vault allowlist below that.
        """
        try:
            from archimedes.services.trace_visibility import resolve_vault_owners

            owner_user_id, owner_wallet = resolve_vault_owners({str(vault_address or "")}).get(
                str(vault_address or "").strip().lower(), (None, None)
            )
        except Exception:
            logger.warning("save_trace: owner stamp lookup failed — persisting unstamped", exc_info=True)
            return {}
        return {"owner_user_id": owner_user_id, "owner_wallet": owner_wallet}

    async def get_trace(self, trace_id_or_hash: str) -> dict | None:
        """Get off-chain trace data by hash or UUID."""
        r = await self._get_redis()

        # Try direct hash lookup
        raw = await r.get(f"{KEY_TRACE_PREFIX}{trace_id_or_hash}")
        if raw:
            parsed = safe_json_loads(raw, context="trace:hash")
            if parsed is not None:
                return parsed

        # Try UUID → hash → data
        hash_val = await r.get(f"{KEY_TRACE_PREFIX}id:{trace_id_or_hash}")
        if hash_val:
            raw = await r.get(f"{KEY_TRACE_PREFIX}{hash_val}")
            if raw:
                parsed = safe_json_loads(raw, context="trace:uuid")
                if parsed is not None:
                    return parsed

        return None

    async def list_traces(
        self,
        vault_address: str | None = None,
        decision_type: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """List traces from index, optionally filtered. Returns (traces, total)."""
        r = await self._get_redis()

        # Get all trace hashes sorted by timestamp (newest first)
        all_hashes = await r.zrevrange(KEY_TRACE_INDEX, 0, -1)
        len(all_hashes)

        # Load and filter
        traces: list[dict] = []
        for h in all_hashes:
            raw = await r.get(f"{KEY_TRACE_PREFIX}{h}")
            if not raw:
                continue
            data = _safe_json_loads(raw, context="trace:index")
            if data is None:
                continue

            # Apply filters
            if vault_address and data.get("vault_address", "").lower() != vault_address.lower():
                continue
            if decision_type and data.get("decision_type") != decision_type:
                continue

            traces.append(data)

        total = len(traces)
        window = traces[offset : offset + limit]
        return window, total

    async def list_recent_traces(self, limit: int = 200) -> list[dict]:
        """Newest-first window of persisted traces, bounded AT THE INDEX.

        Deliberately not ``list_traces(limit=…)``: that one loads every trace in
        the index and only windows afterwards, which is acceptable for a
        user-facing page but not for something the agent runs on every tick.
        Here the ``zrevrange`` bound is applied first, so the cost is O(limit)
        regardless of how much history has accumulated (#1276).
        """
        r = await self._get_redis()
        hashes = await r.zrevrange(KEY_TRACE_INDEX, 0, max(int(limit), 1) - 1)

        traces: list[dict] = []
        for h in hashes:
            raw = await r.get(f"{KEY_TRACE_PREFIX}{h}")
            if not raw:
                continue
            data = safe_json_loads(raw, context="trace:recent")
            if data is not None:
                traces.append(data)
        return traces

    async def get_last_trace(self, vault_address: str) -> dict | None:
        """Get the most recent trace for a specific vault."""
        traces, _ = await self.list_traces(vault_address=vault_address, limit=1)
        return traces[0] if traces else None

    async def get_trace_count(self) -> int:
        """Total number of stored off-chain traces."""
        r = await self._get_redis()
        return await r.zcard(KEY_TRACE_INDEX)

    # ─── SIWE Nonces ────────────────────────────────────────────────

    async def save_nonce(self, nonce: str, ttl_seconds: int) -> None:
        """Store a SIWE challenge nonce with a Redis-managed expiry.

        Using SETEX means Redis itself evicts expired nonces -- no manual
        sweep needed. Shared across workers so /nonce on one worker and
        /verify on another see the same pending-nonce set.
        """
        r = await self._get_redis()
        await r.setex(f"{KEY_SIWE_NONCE_PREFIX}{nonce}", ttl_seconds, "1")

    async def pop_nonce(self, nonce: str) -> bool:
        """Atomically read-and-delete a pending nonce. Returns True if it existed.

        GETDEL makes the nonce single-use: a second pop for the same value
        returns False, matching the "Nonce not found or already used" check.
        """
        r = await self._get_redis()
        return await r.getdel(f"{KEY_SIWE_NONCE_PREFIX}{nonce}") is not None

    # ─── Lifecycle ────────────────────────────────────────────────

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()
            self._redis = None


# Backwards-compat alias — prefer safe_json_loads (public) going forward.
_safe_json_loads = safe_json_loads
