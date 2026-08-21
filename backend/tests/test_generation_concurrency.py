"""Generation admission control: the run-cap semaphore + queue-full refusal.

Hermetic — no Redis/DB. The semaphore laws are tested against
``_run_with_cleanup`` directly (the exact coroutine /start spawns); the
queue-full 429 is tested at the route layer with the same mocked-store
harness as test_generate_payment_gate.py.

The guard-must-reject demonstrations (CLAUDE.md rule 4):
  * with the cap at 1, a second pipeline is PROVEN not to overlap the first
    (max_active is measured, not assumed);
  * with the queue full, /start is PROVEN to refuse 429 and to do so BEFORE
    the payment gate ever runs (enqueue never called, payment module never
    consulted).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from archimedes.api import generate_routes
from fastapi.testclient import TestClient

from tests.auth_helpers import auth_cookies

_BODY = {"brief": {"intent": "low-vol treasury alternative", "risk_appetite": "moderate"}}


def _reset_gate(monkeypatch, *, max_concurrent: str | None = None, max_queue: str | None = None) -> None:
    monkeypatch.setattr(generate_routes, "_GENERATION_GATE", None)
    monkeypatch.setattr(generate_routes, "_GENERATION_GATE_LOOP", None)
    monkeypatch.setattr(generate_routes, "_WAITING_GENERATIONS", 0)
    if max_concurrent is not None:
        monkeypatch.setenv("GENERATION_MAX_CONCURRENT", max_concurrent)
    if max_queue is not None:
        monkeypatch.setenv("GENERATION_MAX_QUEUE", max_queue)


def _tracking_pipeline(release: asyncio.Event, counters: dict):
    async def fake_run_generation(*, job_id: str, **_kwargs) -> None:
        counters["active"] += 1
        counters["max_active"] = max(counters["max_active"], counters["active"])
        counters["order"].append(job_id)
        try:
            await release.wait()
        finally:
            counters["active"] -= 1

    return fake_run_generation


async def _drive(n_jobs: int, cap_env: str, monkeypatch) -> tuple[dict, list[dict]]:
    """Spawn n_jobs through _run_with_cleanup under GENERATION_MAX_CONCURRENT=cap_env."""
    release = asyncio.Event()
    counters = {"active": 0, "max_active": 0, "order": []}
    pushed: list[dict] = []

    store = MagicMock()

    async def _push(job_id, payload):
        pushed.append(payload)
        return len(pushed)

    store.push_event = AsyncMock(side_effect=_push)

    _reset_gate(monkeypatch, max_concurrent=cap_env)
    brief = MagicMock()
    with (
        patch.object(generate_routes, "run_generation", _tracking_pipeline(release, counters)),
        patch.object(generate_routes, "get_job_store", return_value=store),
    ):
        tasks = [asyncio.create_task(generate_routes._run_with_cleanup(f"job-{i}", brief, 1)) for i in range(n_jobs)]
        # Let every task reach its steady state (running or gate-waiting).
        for _ in range(20):
            await asyncio.sleep(0)
        release.set()
        await asyncio.gather(*tasks)
    return counters, pushed


async def test_cap_one_serializes_and_emits_job_queued(monkeypatch) -> None:
    counters, pushed = await _drive(3, "1", monkeypatch)
    # The guard demonstrably rejects overlap: 3 jobs, never 2 at once.
    assert counters["max_active"] == 1
    assert len(counters["order"]) == 3  # every job still ran to completion
    # The two jobs that had to wait told their SSE stream so.
    queued = [p for p in pushed if p.get("event") == "job_queued"]
    assert len(queued) == 2
    assert all(p["data"]["max_concurrent"] == 1 for p in queued)


async def test_cap_two_allows_exactly_two(monkeypatch) -> None:
    counters, _ = await _drive(3, "2", monkeypatch)
    assert counters["max_active"] == 2


async def test_waiting_counter_returns_to_zero_after_drain(monkeypatch) -> None:
    await _drive(3, "1", monkeypatch)
    assert generate_routes._WAITING_GENERATIONS == 0


async def test_cancel_while_waiting_releases_queue_slot(monkeypatch) -> None:
    release = asyncio.Event()
    counters = {"active": 0, "max_active": 0, "order": []}
    store = MagicMock()
    store.push_event = AsyncMock(return_value=1)
    _reset_gate(monkeypatch, max_concurrent="1")
    brief = MagicMock()
    with (
        patch.object(generate_routes, "run_generation", _tracking_pipeline(release, counters)),
        patch.object(generate_routes, "get_job_store", return_value=store),
    ):
        first = asyncio.create_task(generate_routes._run_with_cleanup("job-a", brief, 1))
        for _ in range(5):
            await asyncio.sleep(0)
        second = asyncio.create_task(generate_routes._run_with_cleanup("job-b", brief, 1))
        for _ in range(5):
            await asyncio.sleep(0)
        assert generate_routes._WAITING_GENERATIONS == 1
        second.cancel()
        await asyncio.gather(second, return_exceptions=True)
        assert generate_routes._WAITING_GENERATIONS == 0
        release.set()
        await first


def test_queue_full_is_429_before_payment_and_enqueue(monkeypatch) -> None:
    from archimedes.main import app

    monkeypatch.setenv("GENERATION_MAX_QUEUE", "0")
    monkeypatch.setattr(generate_routes, "_WAITING_GENERATIONS", 1)
    store = MagicMock()
    store.enqueue = AsyncMock(return_value="job-x")
    payment_probe = MagicMock()
    with (
        patch.object(generate_routes, "get_job_store", return_value=store),
        patch.object(generate_routes.generation_payment, "payment_required", side_effect=payment_probe),
    ):
        resp = TestClient(app).post("/api/generate/start", json=_BODY, cookies=auth_cookies())
    assert resp.status_code == 429
    assert resp.json()["detail"]["reason"] == "generation_queue_full"
    store.enqueue.assert_not_awaited()  # no work was burned
    payment_probe.assert_not_called()  # and nobody was asked to pay


def test_queue_not_full_admits(monkeypatch) -> None:
    from archimedes.main import app

    monkeypatch.setenv("GENERATION_MAX_QUEUE", "10")
    monkeypatch.setattr(generate_routes, "_WAITING_GENERATIONS", 0)
    store = MagicMock()
    store.enqueue = AsyncMock(return_value="job-y")

    def _close(coro):
        coro.close()
        return MagicMock()

    with (
        patch.object(generate_routes, "get_job_store", return_value=store),
        patch.object(generate_routes.asyncio, "create_task", side_effect=_close),
    ):
        resp = TestClient(app).post("/api/generate/start", json=_BODY, cookies=auth_cookies())
    assert resp.status_code == 202
    store.enqueue.assert_awaited_once()


def test_env_parsing_is_defensive(monkeypatch) -> None:
    monkeypatch.setenv("GENERATION_MAX_CONCURRENT", "garbage")
    assert generate_routes._max_concurrent_generations() == 1
    monkeypatch.setenv("GENERATION_MAX_CONCURRENT", "0")
    assert generate_routes._max_concurrent_generations() == 1  # floor: never deadlock
    monkeypatch.setenv("GENERATION_MAX_QUEUE", "-3")
    assert generate_routes._max_queued_generations() == 0
    monkeypatch.setenv("GENERATION_MAX_QUEUE", "junk")
    assert generate_routes._max_queued_generations() == 10
