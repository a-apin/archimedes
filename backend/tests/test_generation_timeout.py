"""Bound generation hangs (v8 Lane 1.3b).

``_run_with_cleanup`` wraps the awaited ``run_generation`` call in
``asyncio.wait_for(..., timeout=GENERATION_TIMEOUT_SECONDS)``. Two guards:

  * env parsing is defensive — a missing/non-numeric/non-positive
    ``GENERATION_TIMEOUT_SECONDS`` falls back to the 600s default, never
    crashes (mirrors ``_max_concurrent_generations`` / ``revenue_sweep._min_usdc``);
  * a run that never returns is PROVEN to end the job ``error`` (with an
    honest "exceeded the N-second limit" message, not the pipeline's own
    "cancelled by client" wording) within the configured timeout, AND that
    write is PROVEN to flow through the existing
    ``_release_credit_if_undelivered`` path — ``generation_credits.restore_for_job``
    is actually invoked, not just assumed reachable.

Hermetic — no Redis/DB/network. Mirrors the mocked-store harness in
test_generation_concurrency.py (``_run_with_cleanup`` driven directly, the
same coroutine ``/start`` spawns).
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from archimedes.api import generate_routes


def _reset_gate(monkeypatch) -> None:
    monkeypatch.setattr(generate_routes, "_GENERATION_GATE", None)
    monkeypatch.setattr(generate_routes, "_GENERATION_GATE_LOOP", None)
    monkeypatch.setattr(generate_routes, "_WAITING_GENERATIONS", 0)
    monkeypatch.setenv("GENERATION_MAX_CONCURRENT", "1")
    monkeypatch.setenv("GENERATION_MAX_QUEUE", "10")


class _FakeStore:
    """Minimal stateful stand-in for JobStore — real enough that a status
    written by one call is visible to the next `get`, which is exactly the
    wiring this test needs to prove (unlike a stateless MagicMock)."""

    def __init__(self) -> None:
        self.status: str | None = None
        self.error: str = ""
        self.history: list[tuple[str, str]] = []

    async def get(self, job_id: str) -> dict:
        return {"status": self.status, "error": self.error}

    async def update_status(self, job_id: str, status: str, *, result=None, error: str = "") -> None:
        self.status = status
        self.error = error
        self.history.append((status, error))

    async def touch(self, job_id: str) -> bool:
        return True

    async def push_event(self, job_id: str, payload: dict) -> int:
        return 1


async def _hang_forever(*, job_id: str, **_kwargs) -> None:
    """Stands in for a `run_generation` that never returns (stuck LLM call,
    stuck backtest thread) — the exact failure mode this guard bounds."""
    await asyncio.Event().wait()


def test_generation_timeout_env_parsing_is_defensive(monkeypatch) -> None:
    monkeypatch.setenv("GENERATION_TIMEOUT_SECONDS", "garbage")
    assert generate_routes._generation_timeout_seconds() == 600.0
    monkeypatch.setenv("GENERATION_TIMEOUT_SECONDS", "0")
    assert generate_routes._generation_timeout_seconds() == 600.0  # floor: never a 0s (=instant-fail) timeout
    monkeypatch.setenv("GENERATION_TIMEOUT_SECONDS", "-5")
    assert generate_routes._generation_timeout_seconds() == 600.0
    monkeypatch.delenv("GENERATION_TIMEOUT_SECONDS", raising=False)
    assert generate_routes._generation_timeout_seconds() == 600.0
    monkeypatch.setenv("GENERATION_TIMEOUT_SECONDS", "45")
    assert generate_routes._generation_timeout_seconds() == 45.0


async def test_hung_run_generation_ends_job_error_and_releases_credit(monkeypatch) -> None:
    _reset_gate(monkeypatch)
    monkeypatch.setenv("GENERATION_TIMEOUT_SECONDS", "0.05")  # tiny, monkeypatched bound

    store = _FakeStore()
    brief = MagicMock()

    with (
        patch.object(generate_routes, "run_generation", _hang_forever),
        patch.object(generate_routes, "get_job_store", return_value=store),
        patch.object(generate_routes.generation_credits, "restore_for_job", return_value=True) as restore_mock,
    ):
        # Outer safety bound (generous — 5s), NOT the guard under test: without
        # it, a regression that removes the inner wait_for would hang this
        # test (and the suite) forever instead of failing fast and readably.
        await asyncio.wait_for(
            generate_routes._run_with_cleanup("job-hung", brief, 1),
            timeout=5,
        )

    # The job ended in the honest terminal state — not silently stuck
    # "running", and not the pipeline's own (dishonest, for a timeout)
    # "cancelled by client" write.
    assert store.status == "error"
    assert "exceeded" in store.error
    assert "0.05-second limit" in store.error

    # The EXISTING _release_credit_if_undelivered path actually ran: it reads
    # the just-written status (not "done") and calls
    # generation_credits.restore_for_job — proven here by mocking that exact
    # call, not merely asserting reachability.
    restore_mock.assert_called_once_with("job-hung")
