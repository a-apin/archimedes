"""Tests for the streaming Generate pipeline.

Forces the fixture path (no LLM credentials needed) and asserts that the
event sequence matches the spec ordering and that a strategy is persisted
at the end.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from archimedes.agents.generation_pipeline import run_generation
from archimedes.api.generate_schemas import GenerateBrief


@pytest.fixture(autouse=True)
def force_fixture_path(monkeypatch):
    monkeypatch.setenv("GENERATION_PIPELINE_FIXTURE", "1")


class _FakeStore:
    """In-memory JobStore stand-in. Captures the event sequence."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.status: list[tuple[str, dict | None, str]] = []

    async def push_event(self, job_id, payload):
        self.events.append(payload)
        return len(self.events)

    async def update_status(self, job_id, status, *, result=None, error=""):
        self.status.append((status, result, error))


@pytest.mark.asyncio
async def test_fixture_pipeline_emits_full_event_sequence():
    store = _FakeStore()
    brief = GenerateBrief(intent="13-week treasury alternative", risk_appetite="conservative")

    with patch(
        "archimedes.agents.generation_pipeline._persist_candidate",
        new=AsyncMock(return_value=("strat_fixture_001", "0xdeadbeef")),
    ):
        await run_generation(job_id="job_fixture_001", brief=brief, n_candidates=1, store=store)

    names = [e["event"] for e in store.events]
    # Spec ordering — terminal `done` must be last
    assert names[0] == "job_queued"
    assert names[1] == "brief_validated"
    assert names[2] == "pipeline_selected"
    assert names[3] == "candidates_selected"
    assert "pipeline_selected" in names
    assert "agent_iteration" in names
    assert "tool_called" in names
    assert "candidate_drafted" in names
    assert "candidate_evaluated" in names
    assert "best_selected" in names
    assert "trace_hashed" in names
    assert "persisted" in names
    assert names[-1] == "done"


@pytest.mark.asyncio
async def test_pipeline_terminates_with_done_status():
    store = _FakeStore()
    brief = GenerateBrief(intent="balanced macro", risk_appetite="moderate")

    with patch(
        "archimedes.agents.generation_pipeline._persist_candidate",
        new=AsyncMock(return_value=("strat_test_002", "0xabc")),
    ):
        await run_generation(job_id="job_002", brief=brief, n_candidates=1, store=store)

    # Status sequence: running → done
    statuses = [s[0] for s in store.status]
    assert statuses == ["running", "done"]
    last_result = store.status[-1][1]
    assert last_result is not None
    assert last_result["best_strategy_id"] == "strat_test_002"
    assert len(last_result["candidates"]) == 1


def test_rigor_adapter_computes_dsr_and_oos_sharpe_on_synthetic_series():
    """Wires Önder's rigor_evaluator on a synthetic return series.

    Verifies the verdict shape matches the contract the SSE event +
    frontend RejectedCandidates view expect — all four fields present,
    `passing` is a bool, numeric fields are floats.
    """
    # Synthetic price histories — two assets, trending up with noise.
    import random

    from archimedes.agents.generation_pipeline import (
        _portfolio_return_series,
        _rigor_verdict_for,
    )

    random.seed(42)
    n = 250
    spy = [100.0]
    gld = [100.0]
    for _ in range(n - 1):
        spy.append(spy[-1] * (1 + 0.0005 + random.gauss(0, 0.01)))
        gld.append(gld[-1] * (1 + 0.0002 + random.gauss(0, 0.008)))

    histories = {
        "sSPY": {"close": spy},
        "sGLD": {"close": gld},
    }
    weights = {"sSPY": 0.6, "sGLD": 0.4}

    series = _portfolio_return_series(weights, histories)
    assert len(series) == n - 1

    verdict = _rigor_verdict_for(series, num_trials=3)
    assert set(verdict.keys()) >= {
        "dsr",
        "oos_sharpe",
        "lookahead_audit_passed",
        "passing",
        "pbo",
    }
    assert isinstance(verdict["passing"], bool)
    assert verdict["dsr"] is not None
    assert verdict["oos_sharpe"] is not None


def test_rigor_adapter_handles_empty_series():
    """Short series should produce None metrics + non-passing verdict."""
    from archimedes.agents.generation_pipeline import _rigor_verdict_for

    verdict = _rigor_verdict_for([], num_trials=1)
    assert verdict["dsr"] is None
    assert verdict["oos_sharpe"] is None
    assert verdict["passing"] is False


def test_pick_pipeline_architect_branch_calls_provider_factory(monkeypatch):
    # `default_provider` is a factory function. The architect-check branch
    # of _pick_pipeline previously called `default_provider.list_strategies()`
    # without the `()`, raising AttributeError that the broad `except
    # Exception` swallowed silently — collapsing the pipeline to "agent" on
    # every call. This test forces the architect path (fusion disabled) and
    # asserts it actually selects "architect" — which can only happen if
    # `.list_strategies()` succeeds.
    from archimedes.agents import generation_pipeline as gp
    from archimedes.api.generate_schemas import GenerateBrief

    # Force fusion off so we exit the fusion branch and hit architect.
    monkeypatch.setattr(
        "archimedes.agents.strategy_fusion.fusion_enabled",
        lambda: False,
    )

    brief = GenerateBrief(intent="trend-following", risk_appetite="moderate")
    pipeline, reason = gp._pick_pipeline(brief)
    assert pipeline == "architect", f"expected architect, got {pipeline!r} (reason={reason!r})"
    assert "strategies" in reason


@pytest.mark.asyncio
async def test_pipeline_emits_cancellation_event_when_task_cancelled():
    """CancelledError path emits a CANCELLED error event + flips status."""
    store = _FakeStore()
    brief = GenerateBrief(intent="cancel me mid-run", risk_appetite="moderate")

    # Drive the pipeline as a real task so we can cancel it.
    task = asyncio.create_task(
        run_generation(
            job_id="job_cancel_001",
            brief=brief,
            n_candidates=1,
            store=store,
        )
    )
    # Let it kick off (job_queued + brief_validated emit synchronously).
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # The cancellation handler must have logged the error event + status.
    names = [e["event"] for e in store.events]
    assert "error" in names
    err = next(e for e in store.events if e["event"] == "error")
    assert err["data"]["code"] == "CANCELLED"
    assert err["data"]["recoverable"] is False
    statuses = [s[0] for s in store.status]
    assert statuses[-1] == "cancelled"


@pytest.mark.asyncio
async def test_brief_validation_rejects_invalid_brief(monkeypatch):
    """When the validator says is_valid=false, emit BRIEF_INVALID and stop.

    Forces the live path (so the validator runs) by mocking _llm_available;
    fake the LLM result to flag the brief invalid; assert the pipeline
    emits the error event and never reaches candidates_selected.
    """
    from archimedes.services import generation_pipeline as gp

    monkeypatch.setattr(gp, "_llm_available", lambda: True)

    async def fake_validate(brief):
        return {
            "is_valid": False,
            "reason": "Brief looks like a recipe, not a strategy intent.",
            "hint": "Try mentioning an asset class or risk appetite.",
        }

    monkeypatch.setattr(gp, "_validate_brief", fake_validate)

    store = _FakeStore()
    brief = GenerateBrief(intent="add flour and bake at 350F", risk_appetite="moderate")
    await gp.run_generation(job_id="job_invalid_001", brief=brief, n_candidates=1, store=store)

    names = [e["event"] for e in store.events]
    assert "candidates_selected" not in names  # pipeline halted before agent ran
    err = next((e for e in store.events if e["event"] == "error"), None)
    assert err is not None
    assert err["data"]["code"] == "BRIEF_INVALID"
    assert err["data"]["recoverable"] is True
    statuses = [s[0] for s in store.status]
    assert statuses[-1] == "error"


@pytest.mark.asyncio
async def test_pipeline_multi_candidate_picks_best():
    store = _FakeStore()
    brief = GenerateBrief(intent="aggressive crypto", risk_appetite="aggressive")

    with patch(
        "archimedes.agents.generation_pipeline._persist_candidate",
        new=AsyncMock(return_value=("strat_multi_001", "0xfeed")),
    ):
        await run_generation(job_id="job_multi", brief=brief, n_candidates=3, store=store)

    # 3 candidates should produce 3 candidate_drafted events
    drafted = [e for e in store.events if e["event"] == "candidate_drafted"]
    assert len(drafted) == 3
    best = next((e for e in store.events if e["event"] == "best_selected"), None)
    assert best is not None
    assert best["data"]["considered_count"] == 3
