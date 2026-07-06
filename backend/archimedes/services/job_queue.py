"""Redis-backed async job queue for strategy generation.

Jobs are stored as Redis hashes with a TTL. States: queued → running → done | failed.
Uses the same aioredis pattern as redis_state.py.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis

from archimedes.services.log_scrubber import sanitize_log_value

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
KEY_PREFIX = "archimedes:job:"
EVENT_LOG_SUFFIX = ":events"
JOB_TTL = 3600  # 1 hour
EVENT_LOG_TTL = 900  # 15 minutes after terminal state — spec 0.5 § Reconnection


def _safe_json(raw, default, *, context: str):
    """``json.loads`` that returns ``default`` on malformed data (#919).

    A truncated / tampered ``payload`` or ``result`` field must not 500 the job
    read path; log and fall back to the default so the job still lists.
    """
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("Malformed JSON in job store (%s) — using default: %s", context, exc)
        return default


class JobStore:
    """Thin Redis wrapper for async job lifecycle."""

    def __init__(self, url: str | None = None) -> None:
        self._url = url or REDIS_URL
        self._redis: aioredis.Redis | None = None

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(self._url, decode_responses=True)
        return self._redis

    async def enqueue(
        self,
        *,
        job_type: str,
        payload: dict[str, Any],
    ) -> str:
        """Create a queued job and return its ID."""
        job_id = uuid.uuid4().hex[:16]
        now = datetime.now(UTC).isoformat()
        data = {
            "id": job_id,
            "type": job_type,
            "status": "queued",
            "payload": json.dumps(payload, default=str),
            "result": "",
            "error": "",
            "created_at": now,
            "updated_at": now,
        }
        r = await self._get_redis()
        key = f"{KEY_PREFIX}{job_id}"
        await r.hset(key, mapping=data)
        await r.expire(key, JOB_TTL)
        logger.info("job: enqueued %s (%s)", job_id, job_type)
        return job_id

    async def get(self, job_id: str) -> dict[str, Any] | None:
        """Get job data by ID. Returns None if not found."""
        r = await self._get_redis()
        key = f"{KEY_PREFIX}{job_id}"
        raw = await r.hgetall(key)
        if not raw:
            return None
        return {
            "id": raw.get("id", job_id),
            "type": raw.get("type", ""),
            "status": raw.get("status", "unknown"),
            "payload": _safe_json(raw.get("payload"), {}, context=f"job:{job_id}:payload"),
            "result": _safe_json(raw.get("result"), None, context=f"job:{job_id}:result"),
            "error": raw.get("error", ""),
            "created_at": raw.get("created_at", ""),
            "updated_at": raw.get("updated_at", ""),
        }

    async def update_status(
        self,
        job_id: str,
        status: str,
        *,
        result: dict[str, Any] | None = None,
        error: str = "",
    ) -> None:
        """Transition a job to a new status with optional result/error."""
        r = await self._get_redis()
        key = f"{KEY_PREFIX}{job_id}"
        now = datetime.now(UTC).isoformat()
        updates: dict[str, str] = {
            "status": status,
            "updated_at": now,
        }
        if result is not None:
            updates["result"] = json.dumps(result, default=str)
        if error:
            updates["error"] = error
        await r.hset(key, mapping=updates)
        logger.info("job: %s → %s", sanitize_log_value(job_id), status)

    # ── Event log (for streaming jobs) ────────────────────────────────────

    async def push_event(self, job_id: str, event_payload: dict[str, Any]) -> int:
        """Append an event to the job's event log and return its monotonic ID.

        Events are stored as JSON strings in a Redis list keyed
        ``archimedes:job:{id}:events``. The returned ID is the 1-based list
        index used as the SSE ``id:`` header — the client sends it back as
        ``Last-Event-ID`` to resume from a known point.
        """
        r = await self._get_redis()
        key = f"{KEY_PREFIX}{job_id}{EVENT_LOG_SUFFIX}"
        new_length = await r.rpush(key, json.dumps(event_payload, default=str))
        await r.expire(key, EVENT_LOG_TTL)
        return new_length

    async def list_events(self, job_id: str, *, after_id: int = 0) -> list[dict[str, Any]]:
        """Return events with ID > ``after_id`` in order.

        The returned dicts each have ``id`` (the monotonic event ID) plus
        whatever keys ``push_event`` was given.
        """
        r = await self._get_redis()
        key = f"{KEY_PREFIX}{job_id}{EVENT_LOG_SUFFIX}"
        if after_id < 0:
            after_id = 0
        raw = await r.lrange(key, after_id, -1)
        out: list[dict[str, Any]] = []
        for i, blob in enumerate(raw, start=after_id + 1):
            try:
                payload = json.loads(blob)
            except (TypeError, ValueError):
                continue
            payload["id"] = i
            out.append(payload)
        return out

    async def event_count(self, job_id: str) -> int:
        """Length of the event log."""
        r = await self._get_redis()
        key = f"{KEY_PREFIX}{job_id}{EVENT_LOG_SUFFIX}"
        return await r.llen(key)

    async def list_recent_jobs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """Scan recent jobs for the GenerationStatus UI.

        SCAN-based; bounded by ``limit`` so the call stays cheap. Sorted by
        ``updated_at`` desc.
        """
        r = await self._get_redis()
        pattern = f"{KEY_PREFIX}*"
        keys: list[str] = []
        async for key in r.scan_iter(match=pattern, count=200):
            if key.endswith(EVENT_LOG_SUFFIX):
                continue
            keys.append(key)
            if len(keys) >= limit * 4:
                break
        jobs: list[dict[str, Any]] = []
        for key in keys:
            raw = await r.hgetall(key)
            if not raw:
                continue
            jobs.append(
                {
                    "id": raw.get("id", key.removeprefix(KEY_PREFIX)),
                    "type": raw.get("type", ""),
                    "status": raw.get("status", "unknown"),
                    "payload": _safe_json(raw.get("payload"), {}, context=f"job:{key}:payload"),
                    "result": _safe_json(raw.get("result"), None, context=f"job:{key}:result"),
                    "error": raw.get("error", ""),
                    "created_at": raw.get("created_at", ""),
                    "updated_at": raw.get("updated_at", ""),
                }
            )
        jobs.sort(key=lambda j: j.get("updated_at", ""), reverse=True)
        return jobs[:limit]

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()
            self._redis = None


_default_store: JobStore | None = None


def get_job_store() -> JobStore:
    """Singleton accessor used by routes + the pipeline."""
    global _default_store
    if _default_store is None:
        _default_store = JobStore()
    return _default_store
