"""SSE heartbeat during long-running debate/fan-out compute (#891).

Reported/observed in prod (#891): the generation SSE stream advances through
``job_queued -> ... -> agent_iteration -> tool_called`` then the transport
dies ("peer closed connection ... incomplete chunked read") once debate
compute starts, even though the job keeps running server-side. Root cause:
``stream_events``'s poll loop only wrote bytes to the response when a new
job-store event existed; during a long silent compute stretch (sequential
debate LLM turns, then a parallel backtest gather across the whole
candidate pool) it would sit in ``asyncio.sleep`` cycles writing nothing at
all. Any intermediary with an idle-read timeout shorter than that stretch
(CloudFront's default origin idle timeout, corporate proxies, etc.) drops a
chunked connection that goes byte-silent that long, independent of whether
the origin is still alive.

Fix: emit a ``: heartbeat\\n\\n`` SSE comment every
``_HEARTBEAT_INTERVAL_SECONDS`` of silence. SSE comments are ignored by
EventSource/any spec-compliant parser, so this changes nothing for the
client's event handling — it only keeps the transport from going quiet.

Hermetic: the Redis-backed job store is boundary-mocked (test_generate_job_scoping
precedent); no real Redis/DB/LLM call. The poll/heartbeat cadence constants are
monkeypatched down so the test runs in well under a second of wall-clock time
instead of needing tens of real seconds to observe a heartbeat.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

_JOB_ID = "job-heartbeat-1"


def _job() -> dict:
    return {
        "id": _JOB_ID,
        "type": "generate",
        "status": "running",
        "created_at": "2026-07-07T00:00:00+00:00",
        "updated_at": "2026-07-07T00:00:05+00:00",
        "payload": {"owner_wallet": None, "brief": {"intent": "x"}, "n_candidates": 1},
        "result": {},
    }


def _mock_store_with_delayed_terminal_event(*, silent_cycles: int) -> MagicMock:
    """A store whose ``list_events`` returns nothing for ``silent_cycles`` polls
    (simulating a long-running debate/backtest compute stretch with no new
    job-store event), then returns a terminal ``done`` event so the stream
    generator returns cleanly.
    """
    store = MagicMock()
    store.get = AsyncMock(return_value=_job())
    calls = {"n": 0}

    async def _list_events(_job_id: str, *, after_id: int = 0):
        calls["n"] += 1
        if calls["n"] <= silent_cycles:
            return []
        return [{"id": after_id + 1, "event": "done", "data": {"job_id": _job_id}}]

    store.list_events = AsyncMock(side_effect=_list_events)
    return store


@pytest.mark.asyncio
async def test_heartbeat_emitted_during_long_silent_compute_stretch(monkeypatch):
    """The stream must not go byte-silent across a simulated long compute step.

    Sets a tiny poll interval and heartbeat interval so ~30 silent poll cycles
    (standing in for a slow debate/backtest phase that produces no job-store
    event) comfortably cross several heartbeat boundaries — proving the
    generator writes keep-alive bytes on its own, not just at connect/timeout.
    """
    import archimedes.api.generate_routes as routes

    monkeypatch.setattr(routes, "_POLL_INTERVAL_SECONDS", 0.001)
    monkeypatch.setattr(routes, "_HEARTBEAT_INTERVAL_SECONDS", 0.01)
    monkeypatch.setenv("TESTING", "1")

    store = _mock_store_with_delayed_terminal_event(silent_cycles=30)

    with patch("archimedes.api.generate_routes.get_job_store", return_value=store):
        from archimedes.main import app

        chunks: list[str] = []
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            async with client.stream("GET", f"/api/generate/stream/{_JOB_ID}") as resp:
                assert resp.status_code == 200
                async for line in resp.aiter_lines():
                    chunks.append(line)

    heartbeat_lines = [c for c in chunks if c.strip() == ": heartbeat"]
    done_lines = [c for c in chunks if c.strip() == "event: done"]

    # The silent stretch (30 cycles x 0.001s poll, heartbeat every 0.01s) must
    # have produced at least one keep-alive comment before the terminal event —
    # this is the exact byte-silence gap that let CloudFront/proxies drop the
    # connection in the #891 repro.
    assert len(heartbeat_lines) >= 1, f"expected at least one heartbeat, got chunks={chunks}"
    # And the stream still correctly delivers the terminal event afterward —
    # heartbeats must not interfere with normal event delivery or the
    # generator's terminal-event return.
    assert done_lines, f"expected a terminal 'done' event in the stream, got chunks={chunks}"


@pytest.mark.asyncio
async def test_no_heartbeat_needed_when_events_flow_continuously(monkeypatch):
    """Sanity check: when events keep arriving, no heartbeat filler is needed —
    the silence clock resets on every real event so we don't spam comments
    into an already-live stream.
    """
    import archimedes.api.generate_routes as routes

    monkeypatch.setattr(routes, "_POLL_INTERVAL_SECONDS", 0.001)
    monkeypatch.setattr(routes, "_HEARTBEAT_INTERVAL_SECONDS", 10.0)  # effectively unreachable
    monkeypatch.setenv("TESTING", "1")

    store = MagicMock()
    store.get = AsyncMock(return_value=_job())
    store.list_events = AsyncMock(
        side_effect=[
            [{"id": 1, "event": "agent_iteration", "data": {"job_id": _JOB_ID}}],
            [{"id": 2, "event": "done", "data": {"job_id": _JOB_ID}}],
        ]
    )

    with patch("archimedes.api.generate_routes.get_job_store", return_value=store):
        from archimedes.main import app

        chunks: list[str] = []
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            async with client.stream("GET", f"/api/generate/stream/{_JOB_ID}") as resp:
                assert resp.status_code == 200
                async for line in resp.aiter_lines():
                    chunks.append(line)

    heartbeat_lines = [c for c in chunks if c.strip() == ": heartbeat"]
    assert not heartbeat_lines, f"unexpected heartbeat with continuously-flowing events: {chunks}"
