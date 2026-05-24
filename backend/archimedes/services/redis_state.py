"""Redis state store — persists agent state across ticks.

Stores the latest regime classification and agent heartbeat in Redis
so the API layer and frontend can read live agent state.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime

import redis.asyncio as aioredis

from archimedes.models.regime import RegimeClassification

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Keys
KEY_REGIME = "archimedes:regime:current"
KEY_HEARTBEAT = "archimedes:agent:heartbeat"
KEY_LAST_REBALANCE_PREFIX = "archimedes:agent:last_rebalance:"
KEY_TRACE_PREFIX = "archimedes:trace:"
KEY_TRACE_INDEX = "archimedes:trace:index"


class AgentStateStore:
    """Thin wrapper over Redis for agent state."""

    def __init__(self, url: str | None = None) -> None:
        self._url = url or REDIS_URL
        self._redis: aioredis.Redis | None = None

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

    async def save_regime_from_values(
        self,
        regime: str,
        flat_pct: float,
        all_signals: list,
    ) -> None:
        """Save regime derived from strategy signal consensus."""
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
        data = {
            "regime": regime,
            "confidence": round(1.0 - flat_pct, 2),
            "flat_pct": round(flat_pct, 2),
            "strategy_count": len(all_signals),
            "signals": signal_summary,
            "timestamp": datetime.now(UTC).isoformat(),
            "source": "strategy_consensus",
        }
        await r.set(KEY_REGIME, json.dumps(data))
        logger.debug("Saved strategy-derived regime to Redis: %s", regime)

    async def load_regime(self) -> dict | None:
        r = await self._get_redis()
        raw = await r.get(KEY_REGIME)
        if raw:
            return json.loads(raw)
        return None

    # ─── Heartbeat ────────────────────────────────────────────────

    async def save_heartbeat(self) -> None:
        r = await self._get_redis()
        await r.set(KEY_HEARTBEAT, datetime.now(UTC).isoformat())

    async def get_heartbeat(self) -> str | None:
        r = await self._get_redis()
        raw = await r.get(KEY_HEARTBEAT)
        return raw

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
        return [json.loads(e) for e in raw]

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
        return [json.loads(e) for e in raw]

    # ─── Reasoning Trace Persistence ────────────────────────────

    async def save_trace(self, trace_data: dict) -> None:
        """Store off-chain reasoning trace data keyed by trace_hash.

        Also maintains secondary index by trace UUID for lookup.
        """
        r = await self._get_redis()
        trace_hash = trace_data.get("trace_hash", "")
        trace_id = trace_data.get("id", "")
        if not trace_hash:
            logger.warning("Cannot save trace without trace_hash")
            return

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

    async def get_trace(self, trace_id_or_hash: str) -> dict | None:
        """Get off-chain trace data by hash or UUID."""
        r = await self._get_redis()

        # Try direct hash lookup
        raw = await r.get(f"{KEY_TRACE_PREFIX}{trace_id_or_hash}")
        if raw:
            return json.loads(raw)

        # Try UUID → hash → data
        hash_val = await r.get(f"{KEY_TRACE_PREFIX}id:{trace_id_or_hash}")
        if hash_val:
            raw = await r.get(f"{KEY_TRACE_PREFIX}{hash_val}")
            if raw:
                return json.loads(raw)

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
            data = json.loads(raw)

            # Apply filters
            if vault_address and data.get("vault_address", "").lower() != vault_address.lower():
                continue
            if decision_type and data.get("decision_type") != decision_type:
                continue

            traces.append(data)

        total = len(traces)
        window = traces[offset : offset + limit]
        return window, total

    async def get_last_trace(self, vault_address: str) -> dict | None:
        """Get the most recent trace for a specific vault."""
        traces, _ = await self.list_traces(vault_address=vault_address, limit=1)
        return traces[0] if traces else None

    async def get_trace_count(self) -> int:
        """Total number of stored off-chain traces."""
        r = await self._get_redis()
        return await r.zcard(KEY_TRACE_INDEX)

    # ─── Lifecycle ────────────────────────────────────────────────

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()
            self._redis = None
