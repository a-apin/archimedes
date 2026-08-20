"""A dead generation job must stop claiming to be alive (#1355).

Before this file: a job's Redis hash got its TTL set once at ``enqueue`` and
never refreshed (`HSET` preserves an existing key's TTL), so an active hour-
long run could see its own record vanish mid-flight; the SSE loop only ever
looked at the event log, never the job's own status, so a job whose backend
process died mid-run (the routine trigger: build-on-deploy rolling the
Fargate task on every merge to `main`) rendered "Streaming live" forever; and
a Cancel request racing the pipeline's own terminal write could be silently
overwritten back to "done" (`asyncio.Task.cancel()` can't interrupt a
`to_thread` LLM call already in flight).

Six guards, one per node ID this file's docstring — and the issue's
acceptance criteria — name explicitly:

* **G1** ``test_update_status_refreshes_the_job_ttl`` — every `update_status`
  write re-`expire`s the hash to the full `JOB_TTL`, not just `enqueue`.
* **G2** ``test_touch_refreshes_heartbeat_and_ttl`` — the new `touch()`
  primitive refreshes `heartbeat_at` + TTL only, no status change.
* **G3** ``test_running_job_with_stale_heartbeat_reports_stalled`` /
* **G4** ``test_running_job_with_fresh_heartbeat_reports_running`` — read-time
  normalisation (`_normalize_state`) derives `state: "stalled"` from a stale
  `heartbeat_at` without ever writing it back to Redis.
* **G5** ``test_stream_emits_stalled_error_and_closes_for_a_dead_job`` — the
  SSE loop reads the job record every poll cycle and closes a dead stream
  with one synthetic `error`/`STALLED` event instead of heartbeating for the
  full 300s stream cap.
* **G6** ``test_terminal_write_does_not_overwrite_a_cancelled_job`` — the
  pipeline's terminal write (`JobStore.update_terminal_status`) is a no-op
  once a job is already `cancelled`.

A handful of sanity-check siblings (the other side of each threshold) sit
alongside each guard so a fix that over-fires is caught too, not just one
that under-fires.

Hermetic throughout: G1/G2/G6 use a real `JobStore` on
`fakeredis.FakeAsyncRedis` (the only way to assert on TTL/`heartbeat_at`
without a live Redis — `test_generation_cost_meter.py`'s `_fake_store()`
precedent). G3-G5 boundary-mock the job store and drive the real FastAPI
routes through `httpx.ASGITransport`, following
`test_generate_stream_heartbeat.py`'s pattern (dependency-overridden auth +
monkeypatched cadence constants so the SSE test runs in well under a second).
No live Redis/DB/LLM/network anywhere in this file.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis
import pytest
from archimedes.api.account_auth import CurrentUser, require_current_user
from archimedes.services.job_queue import JOB_TTL, KEY_PREFIX, JobStore
from httpx import ASGITransport, AsyncClient

_JOB_ID = "job-liveness-1"
_USER_ID = "user-liveness-1"


def _fake_store() -> JobStore:
    store = JobStore(url="redis://unused")
    store._redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    return store


@pytest.fixture(autouse=True)
def _authenticated_account():
    """Dependency-overrides Better Auth for the HTTP-level tests below
    (G3-G5); a no-op for the store-only tests (G1/G2/G6), which never touch
    the app. Mirrors `test_generate_stream_heartbeat.py`.
    """
    from archimedes.main import app

    app.dependency_overrides[require_current_user] = lambda: CurrentUser(
        _USER_ID, "Liveness Test", "liveness@example.com", True
    )
    yield
    app.dependency_overrides.pop(require_current_user, None)


# ── G1: update_status refreshes the job's TTL on EVERY write ──────────────


async def test_update_status_refreshes_the_job_ttl():
    """The input that SHOULD fail against pre-fix code: shrink the TTL to
    stand in for a long-running job whose countdown has nearly expired, then
    write an ordinary status transition. Pre-fix, `update_status` called only
    `hset` — Redis's `HSET` preserves an existing key's TTL, so the countdown
    kept ticking down regardless of activity, and this assertion would see
    the same shrunk TTL (or less) survive the write."""
    store = _fake_store()
    job_id = await store.enqueue(job_type="generate", payload={})
    key = f"{KEY_PREFIX}{job_id}"
    await store._redis.expire(key, 5)
    assert await store._redis.ttl(key) <= 5

    await store.update_status(job_id, "running")

    ttl = await store._redis.ttl(key)
    assert ttl > 5, "update_status must re-expire the hash to the full JOB_TTL on every write"
    assert ttl <= JOB_TTL


async def test_update_status_also_refreshes_heartbeat_at():
    """`heartbeat_at` is a liveness signal distinct from `updated_at` — a
    status transition is itself proof of life, so it must move too."""
    store = _fake_store()
    job_id = await store.enqueue(job_type="generate", payload={})
    await store._redis.hset(f"{KEY_PREFIX}{job_id}", "heartbeat_at", "2020-01-01T00:00:00+00:00")

    await store.update_status(job_id, "running")

    job = await store.get(job_id)
    assert job["heartbeat_at"] != "2020-01-01T00:00:00+00:00"
    age = (datetime.now(UTC) - datetime.fromisoformat(job["heartbeat_at"])).total_seconds()
    assert age < 5


# ── G2: touch() — heartbeat + TTL only, no status change ──────────────────


async def test_touch_refreshes_heartbeat_and_ttl():
    store = _fake_store()
    job_id = await store.enqueue(job_type="generate", payload={})
    key = f"{KEY_PREFIX}{job_id}"
    await store._redis.hset(key, "heartbeat_at", "2020-01-01T00:00:00+00:00")
    await store._redis.expire(key, 5)

    ok = await store.touch(job_id)

    assert ok is True
    job = await store.get(job_id)
    # Status/updated_at are untouched — a heartbeat is not a transition.
    assert job["status"] == "queued"
    age = (datetime.now(UTC) - datetime.fromisoformat(job["heartbeat_at"])).total_seconds()
    assert age < 5, "touch() must set heartbeat_at to now"
    ttl = await store._redis.ttl(key)
    assert ttl > 5, "touch() must also re-expire the hash to the full JOB_TTL"
    assert ttl <= JOB_TTL


async def test_touch_on_a_missing_job_is_a_noop():
    """The input that SHOULD fail the guard: a job id with no backing Redis
    key (already expired, or never existed). A bare `hset` would materialise
    a TTL-less phantom hash — the same failure mode `merge_result` already
    guards against for its own write."""
    store = _fake_store()
    assert await store.touch("does-not-exist") is False
    assert await store._redis.exists(f"{KEY_PREFIX}does-not-exist") == 0


# ── G3/G4: read-time STALLED normalisation ─────────────────────────────────


def _job(*, status: str, heartbeat_at: str | None) -> dict:
    return {
        "id": _JOB_ID,
        "type": "generate",
        "status": status,
        "created_at": "2026-08-20T00:00:00+00:00",
        "updated_at": "2026-08-20T00:00:00+00:00",
        "heartbeat_at": heartbeat_at,
        "payload": {
            "owner_user_id": _USER_ID,
            "owner_wallet": None,
            "brief": {"intent": "dead-job repro"},
            "n_candidates": 1,
        },
        "result": {},
    }


def _mock_store(job: dict | None) -> MagicMock:
    store = MagicMock()
    store.get = AsyncMock(return_value=job)
    return store


async def test_running_job_with_stale_heartbeat_reports_stalled():
    """The input that SHOULD fail against pre-fix code: a heartbeat 10
    minutes old (past the 5-minute `_STALLED_AFTER_SECONDS` threshold).
    Pre-fix, `_normalize_state` only ever echoed the raw Redis status — this
    job would report `running` forever, exactly the "Streaming live" bug the
    issue describes."""
    stale = (datetime.now(UTC) - timedelta(seconds=600)).isoformat()
    store = _mock_store(_job(status="running", heartbeat_at=stale))
    with patch("archimedes.api.generate_routes.get_job_store", return_value=store):
        from archimedes.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/api/generate/jobs/{_JOB_ID}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "stalled"


async def test_running_job_with_fresh_heartbeat_reports_running():
    """Sanity check on the other side of the threshold: an actively
    heartbeating job must NOT be misreported as dead — the guard must reject
    staleness, not merely re-flag every running job."""
    fresh = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
    store = _mock_store(_job(status="running", heartbeat_at=fresh))
    with patch("archimedes.api.generate_routes.get_job_store", return_value=store):
        from archimedes.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/api/generate/jobs/{_JOB_ID}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "running"


async def test_running_job_with_no_heartbeat_field_reports_running_not_stalled():
    """Honest absence, not a false positive: a job that predates this field
    (`heartbeat_at` missing/empty) carries no liveness signal at all, so it
    must fall back to the raw status rather than being assumed dead. This is
    also what keeps every pre-existing `test_generate_job_status.py` fixture
    (none of which sets `heartbeat_at`) reporting unchanged."""
    store = _mock_store(_job(status="running", heartbeat_at=""))
    with patch("archimedes.api.generate_routes.get_job_store", return_value=store):
        from archimedes.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/api/generate/jobs/{_JOB_ID}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "running"


# ── G5: the SSE loop closes a dead stream with a synthetic STALLED error ──


def _stream_job(*, status: str = "running", heartbeat_at: str | None) -> dict:
    return {
        "id": _JOB_ID,
        "type": "generate",
        "status": status,
        "created_at": "2026-08-20T00:00:00+00:00",
        "updated_at": "2026-08-20T00:00:00+00:00",
        "heartbeat_at": heartbeat_at,
        "payload": {"owner_wallet": None, "brief": {"intent": "x"}, "n_candidates": 1},
        "result": {},
    }


@pytest.mark.asyncio
async def test_stream_emits_stalled_error_and_closes_for_a_dead_job(monkeypatch):
    """The input that SHOULD fail against pre-fix code: a `running` job whose
    heartbeat is 10 minutes stale and whose event log never produces a new
    event (the process is gone — nothing is left to push one). Pre-fix, the
    SSE loop only ever consulted the event log; it would heartbeat-comment
    its way through the full 300s `_STREAM_TIMEOUT_SECONDS` cap and exit with
    "stream timeout", never an `error` event — the client has no way to
    distinguish that from a merely slow job.
    """
    import archimedes.api.generate_routes as routes

    monkeypatch.setattr(routes, "_POLL_INTERVAL_SECONDS", 0.001)
    monkeypatch.setenv("TESTING", "1")

    stale = (datetime.now(UTC) - timedelta(seconds=600)).isoformat()
    store = MagicMock()
    store.get = AsyncMock(return_value=_stream_job(heartbeat_at=stale))
    store.list_events = AsyncMock(return_value=[])  # nothing left to push — the writer is dead

    with patch("archimedes.api.generate_routes.get_job_store", return_value=store):
        from archimedes.main import app

        chunks: list[str] = []
        async with (
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
            client.stream("GET", f"/api/generate/stream/{_JOB_ID}") as resp,
        ):
            assert resp.status_code == 200
            async for line in resp.aiter_lines():
                chunks.append(line)

    error_lines = [c for c in chunks if c.strip() == "event: error"]
    assert error_lines, f"expected a synthetic 'error' event, got chunks={chunks}"
    data_lines = [c for c in chunks if c.startswith("data:")]
    assert any("STALLED" in c for c in data_lines), f"expected code STALLED, got chunks={chunks}"
    # And it must not have sat through the heartbeat/timeout machinery — a
    # "stream timeout" comment means detection didn't fire promptly.
    assert not any(c.strip() == "stream timeout" for c in chunks), f"chunks={chunks}"


@pytest.mark.asyncio
async def test_stream_does_not_falsely_flag_a_fresh_running_job(monkeypatch):
    """Sanity check on the other side: a job with a fresh heartbeat and no
    new events yet (a legitimate silent compute stretch) must keep
    heartbeating, not get closed out as STALLED."""
    import archimedes.api.generate_routes as routes

    monkeypatch.setattr(routes, "_POLL_INTERVAL_SECONDS", 0.001)
    monkeypatch.setattr(routes, "_HEARTBEAT_INTERVAL_SECONDS", 0.01)
    monkeypatch.setenv("TESTING", "1")

    fresh = datetime.now(UTC).isoformat()
    store = MagicMock()
    store.get = AsyncMock(return_value=_stream_job(heartbeat_at=fresh))
    calls = {"n": 0}

    async def _list_events(_job_id: str, *, after_id: int = 0):
        calls["n"] += 1
        if calls["n"] <= 5:
            return []
        return [{"id": after_id + 1, "event": "done", "data": {"job_id": _job_id}}]

    store.list_events = AsyncMock(side_effect=_list_events)

    with patch("archimedes.api.generate_routes.get_job_store", return_value=store):
        from archimedes.main import app

        chunks: list[str] = []
        async with (
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
            client.stream("GET", f"/api/generate/stream/{_JOB_ID}") as resp,
        ):
            assert resp.status_code == 200
            async for line in resp.aiter_lines():
                chunks.append(line)

    assert not any(c.strip() == "event: error" for c in chunks), f"chunks={chunks}"
    assert any(c.strip() == "event: done" for c in chunks), f"chunks={chunks}"


# ── G6: the terminal write never clobbers an already-cancelled job ────────


async def test_terminal_write_does_not_overwrite_a_cancelled_job():
    """The input that SHOULD fail against pre-fix code: a job already flipped
    to `cancelled` (as `cancel_job` does), followed by the pipeline's own
    terminal write reaching Redis afterward — the exact race
    `asyncio.Task.cancel()` cannot close when the coroutine is mid
    `to_thread(llm_call)`. Pre-fix, `generation_pipeline.py` called the
    unconditional `store.update_status(job_id, "done", ...)` here, which
    would silently overwrite `cancelled` with `done`."""
    store = _fake_store()
    job_id = await store.enqueue(job_type="generate", payload={})
    await store.update_status(job_id, "running")
    await store.update_status(job_id, "cancelled", error="cancelled by user")

    wrote = await store.update_terminal_status(job_id, "done", result={"best_strategy_id": "s1"})

    assert wrote is False
    job = await store.get(job_id)
    assert job["status"] == "cancelled", "a cancelled job must stay cancelled"
    assert job["result"] is None, "the clobbering 'done' result must never have landed"
    assert job["error"] == "cancelled by user"


async def test_terminal_write_lands_normally_on_a_non_cancelled_job():
    """Sanity check: the guard must not block ordinary terminal writes — only
    the specific already-cancelled race."""
    store = _fake_store()
    job_id = await store.enqueue(job_type="generate", payload={})
    await store.update_status(job_id, "running")

    wrote = await store.update_terminal_status(job_id, "done", result={"best_strategy_id": "s1"})

    assert wrote is True
    job = await store.get(job_id)
    assert job["status"] == "done"
    assert job["result"]["best_strategy_id"] == "s1"
