"""Request-count snapshot store — D8 (issue #1028): Redis demotes to cache.

Reconciles a live Redis read (``services/telemetry_store.py``) against the
durable Postgres floor (``models/request_snapshot.RequestCountSnapshot``) on
every read of ``GET /api/metrics``. Two entry points:

  - ``record_and_get_totals(redis_human, redis_agent)`` — the live path. Rolls
    a detected Redis reset into the baseline (see the model docstring for the
    detection rule), advances the snapshot, and returns the honest lifetime
    totals plus epoch metadata. Called whenever Redis answered.
  - ``get_last_snapshot_totals()`` — the fallback path, used when Redis itself
    is unreachable. Reads the snapshot only — no live counter touched — so the
    Insights page keeps showing the last known-good lifetime total instead of
    a Redis-outage zero (AC6).

Both are fail-safe by construction, matching every other instrument in this
codebase (``telemetry_store.py``, ``funnel_store.py``, ``user_stats.py``): a
DB error never raises into the request path. ``record_and_get_totals`` falls
back to the raw Redis reading (no worse than the pre-D8 behavior) on error;
``get_last_snapshot_totals`` falls back to an honest zero.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TypedDict

logger = logging.getLogger(__name__)


class RequestTotals(TypedDict):
    human_total: int
    agent_total: int
    epoch_started_at: str | None
    epoch_count: int | None
    last_reset_at: str | None


def _empty_totals(human: int = 0, agent: int = 0) -> RequestTotals:
    return {
        "human_total": human,
        "agent_total": agent,
        "epoch_started_at": None,
        "epoch_count": None,
        "last_reset_at": None,
    }


def _get_or_create_row(session):
    """Get the singleton snapshot row, creating it (all-zero) if missing."""
    from archimedes.models.request_snapshot import SNAPSHOT_ROW_ID, RequestCountSnapshot

    row = session.get(RequestCountSnapshot, SNAPSHOT_ROW_ID)
    if row is None:
        now = datetime.now(UTC)
        row = RequestCountSnapshot(
            id=SNAPSHOT_ROW_ID,
            human_baseline=0,
            agent_baseline=0,
            last_redis_human=0,
            last_redis_agent=0,
            epoch_count=1,
            epoch_started_at=now,
            last_reset_at=None,
            snapshotted_at=now,
        )
        session.add(row)
        session.flush()
    return row


def record_and_get_totals(redis_human: int, redis_agent: int) -> RequestTotals:
    """Reconcile a fresh Redis read against the durable snapshot; return lifetime totals.

    A reset is detected when either freshly-read counter is LOWER than the
    last one this process observed for that counter (Redis's own ``INCR`` is
    monotonic within an epoch). On reset, the last-observed values are rolled
    into the baseline before the new, lower reading is adopted — so the
    returned total never regresses even though the live Redis counter did.

    Never raises. On any DB error, returns the raw Redis reading unreconciled
    (identical to the instrument's behavior before D8) with epoch fields
    absent, so a DB outage degrades this back to "just read Redis", not to a
    5xx.
    """
    try:
        from archimedes.db import get_session

        session = get_session()
        try:
            row = _get_or_create_row(session)
            now = datetime.now(UTC)
            reset = redis_human < row.last_redis_human or redis_agent < row.last_redis_agent
            if reset:
                row.human_baseline += row.last_redis_human
                row.agent_baseline += row.last_redis_agent
                row.epoch_count += 1
                row.last_reset_at = now
            row.last_redis_human = redis_human
            row.last_redis_agent = redis_agent
            row.snapshotted_at = now
            session.commit()
            return {
                "human_total": row.human_baseline + row.last_redis_human,
                "agent_total": row.agent_baseline + row.last_redis_agent,
                "epoch_started_at": row.epoch_started_at.isoformat(),
                "epoch_count": row.epoch_count,
                "last_reset_at": row.last_reset_at.isoformat() if row.last_reset_at else None,
            }
        finally:
            session.close()
    except Exception as exc:
        logger.debug("request snapshot record failed (falling back to raw Redis reading): %s", exc)
        return _empty_totals(redis_human, redis_agent)


def get_last_snapshot_totals() -> RequestTotals:
    """Pure-Postgres fallback read — used when Redis itself is unreachable (AC6).

    Never touches Redis. Never raises; returns an honest all-zero shape when
    no snapshot exists yet (fresh DB) or on any DB error of its own.
    """
    try:
        from archimedes.db import get_session
        from archimedes.models.request_snapshot import SNAPSHOT_ROW_ID, RequestCountSnapshot

        session = get_session()
        try:
            row = session.get(RequestCountSnapshot, SNAPSHOT_ROW_ID)
            if row is None:
                return _empty_totals()
            return {
                "human_total": row.human_baseline + row.last_redis_human,
                "agent_total": row.agent_baseline + row.last_redis_agent,
                "epoch_started_at": row.epoch_started_at.isoformat(),
                "epoch_count": row.epoch_count,
                "last_reset_at": row.last_reset_at.isoformat() if row.last_reset_at else None,
            }
        finally:
            session.close()
    except Exception as exc:
        logger.debug("request snapshot fallback read failed: %s", exc)
        return _empty_totals()
