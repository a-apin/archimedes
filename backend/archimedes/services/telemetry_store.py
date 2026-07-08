"""Telemetry store — atomic human/agent request counters in Redis (issue #428).

Backs the human-vs-agent traction counter. Uses Redis ``INCR`` (atomic across
Uvicorn workers) on two cumulative keys so the count is correct under
concurrent traffic without a read-modify-write race.

Design notes:
  - Reuses the same Redis client/config pattern as ``services/redis_state.py``
    (``redis.asyncio.from_url`` with ``decode_responses=True`` and the
    ``REDIS_URL`` env default) so there is one connection convention in the
    codebase.
  - **Fail-safe by construction.** Every method swallows Redis errors and logs
    at ``debug``; a Redis outage must never turn a request into a 5xx. The
    increment path returns silently; the read path returns zeros.
  - ``total`` is derived (humans + agents) on read rather than stored, so a
    crash between two INCRs can never desync a stored total from its parts.

**Redis's role, demoted (D8, issue #1028):** these counters are the fast LIVE
tally, not the durable record. ``api/metrics_routes.get_metrics`` reconciles
every read against a Postgres snapshot
(``services/request_snapshot_store.py``) so the lifetime request total
survives a Redis restart — Redis itself is now cache-tier for this
instrument, same as it is for nonces and rate-limit buckets elsewhere in the
codebase, not the source of truth.
"""

from __future__ import annotations

import logging
import os

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Keys — namespaced under archimedes:telemetry: to match the redis_state.py
# key convention (archimedes:<domain>:<name>).
KEY_HUMANS = "archimedes:telemetry:humans"
KEY_AGENTS = "archimedes:telemetry:agents"


class TelemetryStore:
    """Thin, fail-safe Redis wrapper for the human/agent traction counters."""

    def __init__(self, url: str | None = None) -> None:
        self._url = url or REDIS_URL
        self._redis: aioredis.Redis | None = None

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(self._url, decode_responses=True)
        return self._redis

    # ─── Increment (write path — request middleware) ─────────────

    async def increment_human(self) -> None:
        """Atomically increment the human counter. Never raises."""
        await self._incr(KEY_HUMANS)

    async def increment_agent(self) -> None:
        """Atomically increment the agent counter. Never raises."""
        await self._incr(KEY_AGENTS)

    async def _incr(self, key: str) -> None:
        try:
            r = await self._get_redis()
            await r.incr(key)
        except Exception as exc:
            # Fail-safe: a Redis outage must never break the request it is
            # measuring. Log at debug so it isn't noisy in normal operation.
            logger.debug("telemetry incr failed for %s: %s", key, exc)

    # ─── Read (exposure path — /api/metrics, /health) ────────────

    async def get_counts(self) -> tuple[int, int]:
        """Return ``(human_count, agent_count)``. Returns ``(0, 0)`` on error."""
        try:
            r = await self._get_redis()
            humans = await r.get(KEY_HUMANS)
            agents = await r.get(KEY_AGENTS)
            return int(humans or 0), int(agents or 0)
        except Exception as exc:
            logger.debug("telemetry read failed: %s", exc)
            return 0, 0

    async def get_counts_or_none(self) -> tuple[int, int] | None:
        """Return ``(human_count, agent_count)``, or ``None`` if Redis is unreachable.

        Unlike ``get_counts``, this does NOT collapse a Redis outage to
        ``(0, 0)`` — a genuine "Redis says zero" and "Redis didn't answer" are
        different facts, and D8 (issue #1028) needs to tell them apart: a
        caller can fall back to the durable Postgres snapshot
        (``services/request_snapshot_store.py``) on ``None`` instead of
        reporting a false zero. Never raises.
        """
        try:
            r = await self._get_redis()
            humans = await r.get(KEY_HUMANS)
            agents = await r.get(KEY_AGENTS)
            return int(humans or 0), int(agents or 0)
        except Exception as exc:
            logger.debug("telemetry read failed (Redis unreachable): %s", exc)
            return None

    # ─── Lifecycle ────────────────────────────────────────────────

    async def close(self) -> None:
        if self._redis:
            try:
                await self._redis.aclose()
            except Exception as exc:
                logger.debug("telemetry store close failed: %s", exc)
            self._redis = None
