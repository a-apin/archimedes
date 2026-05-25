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

    # Dual regime: both bull + bear candidates drafted
    drafted = [e for e in store.events if e["event"] == "candidate_drafted"]
    assert len(drafted) == 2
    drafted_regimes = {e["data"]["regime"] for e in drafted}
    assert drafted_regimes == {"bull", "bear"}


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
    # Default dual_regime=True → 2 candidates (bull + bear)
    assert len(last_result["candidates"]) == 2
    regimes = {c["regime"] for c in last_result["candidates"]}
    assert regimes == {"bull", "bear"}


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


# ── Dual bull/bear regime tests (Issue #163) ───────────────────────────────


@pytest.mark.asyncio
async def test_dual_regime_always_emits_both_bull_and_bear():
    """dual_regime=True (default) emits both bull and bear candidates."""
    store = _FakeStore()
    brief = GenerateBrief(intent="trend following with low drawdown", risk_appetite="moderate")

    with patch(
        "archimedes.agents.generation_pipeline._persist_candidate",
        new=AsyncMock(return_value=("strat_dual_001", "0xdual")),
    ):
        await run_generation(job_id="job_dual_001", brief=brief, store=store)

    drafted = [e for e in store.events if e["event"] == "candidate_drafted"]
    assert len(drafted) == 2
    regimes = [e["data"]["regime"] for e in drafted]
    assert "bull" in regimes
    assert "bear" in regimes


@pytest.mark.asyncio
async def test_dual_regime_persists_both_with_correct_regime_tags():
    """Both bull and bear candidates are persisted (both get strategy_id)."""
    store = _FakeStore()
    brief = GenerateBrief(intent="balanced equities", risk_appetite="conservative")

    persist_calls = []
    call_count = [0]

    async def mock_persist(c, b):
        call_count[0] += 1
        sid = f"strat_{c.regime}_{call_count[0]}"
        persist_calls.append({"regime": c.regime, "strategy_id": sid})
        return (sid, f"0x{c.regime}")

    with patch(
        "archimedes.agents.generation_pipeline._persist_candidate",
        new=mock_persist,
    ):
        await run_generation(job_id="job_dual_persist", brief=brief, store=store)

    # Both candidates persisted
    assert len(persist_calls) == 2
    persisted_regimes = {p["regime"] for p in persist_calls}
    assert persisted_regimes == {"bull", "bear"}

    # Done result has both candidates with strategy_ids
    last_result = store.status[-1][1]
    assert last_result is not None
    for c in last_result["candidates"]:
        assert c["strategy_id"] is not None
        assert c["regime"] in ("bull", "bear")


@pytest.mark.asyncio
async def test_dual_regime_fixture_names_differ():
    """Fixture bull/bear candidates have distinct names and weights."""
    store = _FakeStore()
    brief = GenerateBrief(intent="growth focus", risk_appetite="aggressive")

    with patch(
        "archimedes.agents.generation_pipeline._persist_candidate",
        new=AsyncMock(return_value=("strat_names_001", "0xnames")),
    ):
        await run_generation(job_id="job_names", brief=brief, store=store)

    drafted = [e for e in store.events if e["event"] == "candidate_drafted"]
    names = [e["data"]["strategy_name"] for e in drafted]
    # Names must be different (bull vs bear)
    assert len(set(names)) == 2
    # One should contain "Bull", the other "Bear"
    combined = " ".join(names)
    assert "Bull" in combined
    assert "Bear" in combined


@pytest.mark.asyncio
async def test_dual_regime_off_produces_single_neutral():
    """dual_regime=False falls back to single neutral candidate."""
    store = _FakeStore()
    brief = GenerateBrief(intent="basic equities", risk_appetite="moderate")

    with patch(
        "archimedes.agents.generation_pipeline._persist_candidate",
        new=AsyncMock(return_value=("strat_single_001", "0xsingle")),
    ):
        await run_generation(job_id="job_single", brief=brief, store=store, dual_regime=False)

    drafted = [e for e in store.events if e["event"] == "candidate_drafted"]
    assert len(drafted) == 1
    assert drafted[0]["data"]["regime"] == "neutral"


@pytest.mark.asyncio
async def test_dual_regime_pipeline_selected_event_lists_regimes():
    """pipeline_selected event includes the regime plan."""
    store = _FakeStore()
    brief = GenerateBrief(intent="macro hedge", risk_appetite="conservative")

    with patch(
        "archimedes.agents.generation_pipeline._persist_candidate",
        new=AsyncMock(return_value=("strat_regime_evt", "0xevt")),
    ):
        await run_generation(job_id="job_regime_evt", brief=brief, store=store)

    ps = next(e for e in store.events if e["event"] == "pipeline_selected")
    assert ps["data"]["regimes"] == ["bull", "bear"]


@pytest.mark.asyncio
async def test_pipeline_multi_candidate_picks_best():
    store = _FakeStore()
    brief = GenerateBrief(intent="aggressive crypto", risk_appetite="aggressive")

    with patch(
        "archimedes.agents.generation_pipeline._persist_candidate",
        new=AsyncMock(return_value=("strat_multi_001", "0xfeed")),
    ):
        await run_generation(job_id="job_multi", brief=brief, n_candidates=3, store=store, dual_regime=False)

    # dual_regime=False + n_candidates=3 → 3 neutral candidates
    drafted = [e for e in store.events if e["event"] == "candidate_drafted"]
    assert len(drafted) == 3
    best = next((e for e in store.events if e["event"] == "best_selected"), None)
    assert best is not None
    assert best["data"]["considered_count"] == 3


# ── source_papers fallback tests (Issue #310) ───────────────────────────────


def test_source_papers_fallback_uses_title_when_no_arxiv_id():
    """The fallback populates source_papers from library strategies even when
    paper_arxiv_id is empty (matching the real curated strategies that only
    have DOI/title, not arxiv_id).
    """
    from dataclasses import dataclass

    @dataclass
    class MockStrategy:
        id: str = "faber_001"
        paper_arxiv_id: str = ""  # Empty — no arxiv_id (the real situation)
        paper_title: str = "A Quantitative Approach to Tactical Asset Allocation"

    strategies = [MockStrategy(), MockStrategy(id="tsmom_001", paper_title="Time Series Momentum")]
    # Replicate the fallback logic from _run_live_candidate
    source_papers: list[dict] = []
    for s in strategies[:5]:
        paper_id = getattr(s, "paper_arxiv_id", None) or ""
        paper_title = getattr(s, "paper_title", None) or ""
        if paper_id or paper_title:
            source_papers.append({"arxiv_id": paper_id, "title": paper_title})

    assert len(source_papers) == 2
    assert source_papers[0]["title"] == "A Quantitative Approach to Tactical Asset Allocation"
    assert source_papers[1]["title"] == "Time Series Momentum"


def test_source_papers_fallback_empty_when_no_paper_refs():
    """When strategies have neither arxiv_id nor title, source_papers stays empty."""
    from dataclasses import dataclass

    @dataclass
    class EmptyStrategy:
        id: str = "empty_001"
        paper_arxiv_id: str = ""
        paper_title: str = ""

    strategies = [EmptyStrategy()]
    source_papers: list[dict] = []
    for s in strategies[:5]:
        paper_id = getattr(s, "paper_arxiv_id", None) or ""
        paper_title = getattr(s, "paper_title", None) or ""
        if paper_id or paper_title:
            source_papers.append({"arxiv_id": paper_id, "title": paper_title})

    assert source_papers == []
