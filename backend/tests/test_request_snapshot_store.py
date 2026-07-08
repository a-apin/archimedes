"""Unit tests for the D8 request-count snapshot store (issue #1028, "metrics-views" chunk).

Exercises the real DB boundary directly (no HTTP layer) since the singleton
row (``request_count_snapshots``, id=1) is shared process-wide — every test
resets it first via ``_reset_row`` so behavior is deterministic regardless of
what ran before it in the same pytest process.
"""

from __future__ import annotations

from archimedes.db import get_session
from archimedes.models.request_snapshot import SNAPSHOT_ROW_ID, RequestCountSnapshot
from archimedes.services.request_snapshot_store import get_last_snapshot_totals, record_and_get_totals


def _reset_row() -> None:
    session = get_session()
    try:
        session.query(RequestCountSnapshot).filter(RequestCountSnapshot.id == SNAPSHOT_ROW_ID).delete()
        session.commit()
    finally:
        session.close()


def test_record_and_get_totals_creates_row_on_first_call():
    _reset_row()
    totals = record_and_get_totals(5, 2)
    assert totals["human_total"] == 5
    assert totals["agent_total"] == 2
    assert totals["epoch_count"] == 1
    assert totals["epoch_started_at"] is not None
    assert totals["last_reset_at"] is None


def test_record_and_get_totals_advances_monotonically_without_reset():
    _reset_row()
    record_and_get_totals(5, 2)
    totals = record_and_get_totals(12, 6)
    assert totals["human_total"] == 12
    assert totals["agent_total"] == 6
    assert totals["epoch_count"] == 1  # no reset — same epoch
    assert totals["last_reset_at"] is None


def test_record_and_get_totals_absorbs_a_redis_reset_without_regressing():
    """The core D8/AC6 guarantee: a lower reading (Redis restart) must not lower the total."""
    _reset_row()
    record_and_get_totals(100, 20)
    totals = record_and_get_totals(2, 1)  # Redis restarted; counters dropped
    # baseline (100, 20) rolled in + the new post-reset reading (2, 1).
    assert totals["human_total"] == 102
    assert totals["agent_total"] == 21
    assert totals["epoch_count"] == 2
    assert totals["last_reset_at"] is not None


def test_record_and_get_totals_detects_reset_on_either_counter_independently():
    """A reset on just ONE of human/agent still rolls BOTH baselines forward (single epoch boundary)."""
    _reset_row()
    record_and_get_totals(50, 50)
    # Only the human counter dropped (agent kept climbing) — still a reset event.
    totals = record_and_get_totals(1, 60)
    assert totals["human_total"] == 51  # 50 baseline + 1
    assert totals["agent_total"] == 110  # 50 baseline + 60
    assert totals["epoch_count"] == 2


def test_get_last_snapshot_totals_matches_last_record_call():
    _reset_row()
    record_and_get_totals(7, 3)
    fallback = get_last_snapshot_totals()
    assert fallback["human_total"] == 7
    assert fallback["agent_total"] == 3
    assert fallback["epoch_count"] == 1


def test_get_last_snapshot_totals_is_zero_on_a_fresh_row():
    _reset_row()
    fallback = get_last_snapshot_totals()
    assert fallback["human_total"] == 0
    assert fallback["agent_total"] == 0
    assert fallback["epoch_started_at"] is None
    assert fallback["epoch_count"] is None


def test_get_last_snapshot_totals_never_touches_redis():
    """The fallback path is pure Postgres — used precisely because Redis is unavailable (AC6)."""
    _reset_row()
    record_and_get_totals(9, 1)
    # No Redis client exists in this test process at all; a successful call
    # here (no ConnectionError) is itself proof this path never dials out.
    fallback = get_last_snapshot_totals()
    assert fallback["human_total"] == 9
