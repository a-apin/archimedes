"""Funnel store — distinct-visitor conversion-funnel counters in Redis (#787).

Backs the conversion-funnel instrument. We have a zero-conversion problem
(~40k requests → 2 wallets → 2 vaults) and, until now, no way to see *where*
visitors drop. This records the distinct visitors that reach each stage of the
journey:

    landed → wallet_connected → generation_started → vault_deployed

using Redis HyperLogLog (``PFADD`` / ``PFCOUNT``) so the counts are *distinct
visitors* without retaining any raw identifier — privacy-friendly (no tracking
dossier) and O(1) memory per stage.

The funnel is naturally human-weighted, unlike the raw human/agent counters in
``telemetry_store.py``: ``landed`` is emitted by the SPA's JS (crawlers don't
run JS) and the downstream stages are real product actions, so the bot floor
that dominates the raw request counts largely drops out here.

Design mirrors ``services/telemetry_store.py`` deliberately:
  - same ``redis.asyncio.from_url`` + ``REDIS_URL`` convention,
  - **fail-safe by construction** — every method swallows Redis errors and logs
    at ``debug``; a Redis outage must never turn a request into a 5xx. The write
    path returns silently; the read path returns zeros.

Two keyspaces per stage:
  - ``archimedes:funnel:total:<stage>`` — all-time distinct visitors (no TTL).
  - ``archimedes:funnel:day:<YYYY-MM-DD>:<stage>`` — per-day distinct visitors,
    with a TTL so old day-buckets self-expire (no unbounded growth).
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

_PREFIX = "archimedes:funnel"

# Ordered funnel stages. Order is load-bearing: ratios are computed against the
# first stage (``landed``) and against the immediately preceding stage.
STAGES: tuple[str, ...] = (
    "landed",
    "wallet_connected",
    "generation_started",
    "vault_deployed",
)

# Stages a browser client is allowed to self-report via the beacon endpoint.
# Only the top of funnel is client-emittable; every downstream stage is recorded
# server-side at the authoritative transition so a client can't inflate them.
CLIENT_EMITTABLE_STAGES: frozenset[str] = frozenset({"landed"})

# Per-day buckets self-expire after this window (90 days of trend history).
_DAY_TTL_SECONDS = 90 * 24 * 60 * 60


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


class FunnelStore:
    """Thin, fail-safe Redis wrapper for the conversion-funnel HLL counters."""

    def __init__(self, url: str | None = None) -> None:
        self._url = url or REDIS_URL
        self._redis: aioredis.Redis | None = None

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(self._url, decode_responses=True)
        return self._redis

    # ─── Record (write path — server-side emit + client beacon) ──────────

    async def record(self, stage: str, visitor_id: str) -> None:
        """Record that ``visitor_id`` reached ``stage``. Never raises.

        No-ops on an unknown stage or an empty visitor id (defensive — a missing
        id must not create a bogus distinct count).
        """
        if stage not in STAGES or not visitor_id:
            return
        try:
            r = await self._get_redis()
            day_key = f"{_PREFIX}:day:{_today()}:{stage}"
            pipe = r.pipeline()
            pipe.pfadd(f"{_PREFIX}:total:{stage}", visitor_id)
            pipe.pfadd(day_key, visitor_id)
            pipe.expire(day_key, _DAY_TTL_SECONDS)
            await pipe.execute()
        except Exception as exc:
            # Fail-safe: a Redis outage must never break the request it measures.
            logger.debug("funnel record failed for stage %s: %s", stage, exc)

    # ─── Read (exposure path — GET /api/metrics/funnel) ──────────────────

    async def get_totals(self) -> dict[str, int]:
        """All-time distinct visitors per stage. Returns zeros on error."""
        return await self._counts(f"{_PREFIX}:total:{{stage}}")

    async def get_day(self, date_str: str | None = None) -> dict[str, int]:
        """Per-day distinct visitors per stage (defaults to today). Zeros on error."""
        day = date_str or _today()
        return await self._counts(f"{_PREFIX}:day:{day}:{{stage}}")

    async def _counts(self, key_template: str) -> dict[str, int]:
        counts = dict.fromkeys(STAGES, 0)
        try:
            r = await self._get_redis()
            pipe = r.pipeline()
            for stage in STAGES:
                pipe.pfcount(key_template.format(stage=stage))
            results = await pipe.execute()
            for stage, count in zip(STAGES, results, strict=False):
                counts[stage] = int(count or 0)
        except Exception as exc:
            logger.debug("funnel read failed: %s", exc)
        return counts

    # ─── Lifecycle ───────────────────────────────────────────────────────

    async def close(self) -> None:
        if self._redis:
            try:
                await self._redis.aclose()
            except Exception as exc:
                logger.debug("funnel store close failed: %s", exc)
            self._redis = None
