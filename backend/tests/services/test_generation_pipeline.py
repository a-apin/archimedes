"""Tests for the streaming Generate pipeline.

Forces the fixture path (no LLM credentials needed) and asserts that the
event sequence matches the spec ordering and that a strategy is persisted
at the end.

Debate-path tests (test_debate_dispatch_*, test_debate_critic_rigor_*,
test_debate_text_only_*) stub the debate engine's proposer and evaluator
seams to run _run_debate_leaderboard hermetically without a live LLM or
real market-data backtest. Pattern mirrors test_debate_engine.py.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
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


def test_rigor_adapter_deflates_with_num_trials():
    """Higher num_trials must deflate the DSR (lower deflated Sharpe).

    Regression for the audit finding that the Generate flow called
    `_rigor_verdict_for(..., num_trials=1)`, which collapses the DSR
    expectation-of-max term to 0 and leaves the ratio undeflated. With a real
    library-size trial count the deflated Sharpe must be strictly lower.
    """
    import random

    from archimedes.agents.generation_pipeline import _rigor_verdict_for

    random.seed(7)
    series = [0.001 + random.gauss(0, 0.01) for _ in range(250)]

    v1 = _rigor_verdict_for(series, num_trials=1)
    v_lib = _rigor_verdict_for(series, num_trials=6)
    assert v1["dsr"] is not None and v_lib["dsr"] is not None
    # Deflating for 6 trials instead of 1 lowers the deflated Sharpe.
    assert v_lib["dsr"] < v1["dsr"]


def test_rigor_adapter_lookahead_gates_passing():
    """A failed look-ahead audit must force `passing` False even if DSR/OOS pass.

    Regression for the audit finding that look-ahead was hardcoded True and never
    gated the verdict (the fourth admission primitive was unenforced).
    """
    import random

    from archimedes.agents.generation_pipeline import _rigor_verdict_for

    random.seed(11)
    # Strongly positive drift so DSR/OOS would otherwise pass.
    series = [0.004 + random.gauss(0, 0.006) for _ in range(300)]

    clean = _rigor_verdict_for(series, num_trials=4, lookahead_passed=True)
    leaked = _rigor_verdict_for(series, num_trials=4, lookahead_passed=False)
    assert leaked["lookahead_audit_passed"] is False
    assert leaked["passing"] is False
    # Same series, only the look-ahead flag differs → it is the deciding factor.
    if clean["passing"]:
        assert leaked["passing"] is False


def test_lookahead_for_candidate_ignores_backtrader_negative_index(tmp_path):
    """Curated backtrader strategies use safe close[-N]; that must not fail audit.

    Genuine forward-looking patterns (shift(-1), positive data index) must fail.
    """
    from archimedes.agents.generation_pipeline import _lookahead_for_candidate

    class _Strat:
        def __init__(self, path, title):
            self.strategy_code_path = path
            self.paper_title = title

    safe = tmp_path / "safe_strat.py"
    safe.write_text("def next(self):\n    return self.data.close[-1] - self.data.close[-2]\n")
    leaky = tmp_path / "leaky_strat.py"
    leaky.write_text("import pandas as pd\ndef signal(df):\n    return df['close'].shift(-1)\n")

    assert _lookahead_for_candidate([_Strat(str(safe), "Safe")]) is True
    assert _lookahead_for_candidate([_Strat(str(leaky), "Leaky")]) is False
    # No auditable source → vacuously clean (buy-and-hold by construction).
    assert _lookahead_for_candidate([]) is True


def test_pick_pipeline_always_debate_after_cutover(monkeypatch):
    # T1.1 Phase-3: _pick_pipeline always returns 'debate' regardless of flag
    # state, fusion enabled/disabled, library size, or mode override.
    # The legacy fusion/architect/agent decision tree is deleted; no mocking of
    # fusion_enabled or default_provider is needed or meaningful.
    #
    # Equivalent coverage of the old architect-branch regression now lives in
    # test_debate_engine.py::test_pick_pipeline_is_debate_unconditionally.
    from archimedes.agents import generation_pipeline as gp

    brief = GenerateBrief(intent="trend-following", risk_appetite="moderate")
    for override in (None, "fusion", "architect", "agent", "debate"):
        name, reason = gp._pick_pipeline(brief, mode_override=override)
        assert name == "debate", f"override {override!r} routed off the society (got {name!r})"
        assert "debate" in reason.lower() or "Phase-3" in reason


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
async def test_dual_regime_k1_only_winner_persisted():
    """K=1 persistence (Phase-3 contract): fixture dual-regime run yields 2
    candidates in the result payload (bull+bear regimes intact) but exactly
    ONE _persist_candidate call (the winner) and ONE persist_proposal call
    for the reject (verdict='rejected').

    Replaces test_dual_regime_persists_both_with_correct_regime_tags which
    tested the retired K=2 pattern (both regimes persisted as StrategyRecords).
    """
    import archimedes.services.strategy_memory as sm

    store = _FakeStore()
    brief = GenerateBrief(intent="balanced equities", risk_appetite="conservative")

    persist_calls: list[dict] = []

    async def mock_persist(c, b, **_kw):
        persist_calls.append({"regime": c.regime, "candidate_id": c.candidate_id})
        return (f"strat_{c.regime}", f"0x{c.regime}")

    proposal_calls: list[dict] = []

    def mock_persist_proposal(**kwargs):
        proposal_calls.append(kwargs)

    with (
        patch("archimedes.agents.generation_pipeline._persist_candidate", new=mock_persist),
        patch.object(sm, "persist_proposal", side_effect=mock_persist_proposal),
    ):
        await run_generation(job_id="job_k1_persist", brief=brief, store=store)

    # K=1: exactly ONE _persist_candidate call (the winner only)
    assert len(persist_calls) == 1, f"K=1 must persist exactly 1 winner; got {len(persist_calls)} calls"

    # The rejected candidate lands in strategy_proposals with verdict='rejected'
    rejected = [c for c in proposal_calls if c.get("verdict") == "rejected"]
    assert len(rejected) == 1, f"Expected 1 rejected persist_proposal; got {rejected}"
    assert rejected[0]["regime_tag"] in ("bull", "bear"), "reject must carry a regime_tag"

    # Both candidates still surface in the result payload (stream visibility preserved)
    last_result = store.status[-1][1]
    assert last_result is not None
    candidates = last_result["candidates"]
    assert len(candidates) == 2
    regimes = {c["regime"] for c in candidates}
    assert regimes == {"bull", "bear"}


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


@pytest.mark.asyncio
async def test_best_selected_abstains_when_no_candidate_passes_rigor():
    # ABSTAIN semantics (#818): when NO candidate clears the gate, best_selected must
    # honestly carry validated_count=0 and deployable=False — the surfaced best is a
    # *considered* alternative, not a validated, deployable winner. Pins the new fields
    # so the ABSTAIN-vs-deployable distinction can't regress silently.
    store = _FakeStore()
    brief = GenerateBrief(intent="aggressive crypto", risk_appetite="aggressive")

    def _fail_all(cands):
        # Stand in for _patch_pbo: force every candidate to fail the gate.
        for c in cands:
            c.rigor_verdict["passing"] = False

    with (
        patch("archimedes.agents.generation_pipeline._patch_pbo", side_effect=_fail_all),
        patch(
            "archimedes.agents.generation_pipeline._persist_candidate",
            new=AsyncMock(return_value=("strat_abstain_001", "0xfeed")),
        ),
    ):
        await run_generation(job_id="job_abstain", brief=brief, n_candidates=3, store=store, dual_regime=False)

    best = next((e for e in store.events if e["event"] == "best_selected"), None)
    assert best is not None
    assert best["data"]["considered_count"] == 3
    assert best["data"]["validated_count"] == 0
    assert best["data"]["deployable"] is False


# ── Debate dispatch wire — hermetic end-to-end ─────────────────────────────
#
# Re-targets the old "fusion dispatch wire" tests at the debate path. Instead
# of the retired standalone-fusion dispatch, we stub the debate engine's
# proposer (_propose_pool) and evaluator (evaluate_fusion_spec) seams so
# _run_debate_leaderboard runs hermetically through run_generation — no live
# LLM, no real market-data backtest, no network. Pattern follows the
# _CannedFusionBackend + monkeypatch seams in test_debate_engine.py.


# ── Shared helpers for debate dispatch tests ──────────────────────────────────


def _debate_fake_corpus(n: int = 4):
    """A small fixture corpus whose arxiv_ids match the fake proposals' citations.

    Kept small (n=4) since we're mocking _propose_pool anyway and just need
    _critic_prov to pass provenance checks.
    """
    from archimedes.agents.strategy_fusion import CorpusPaper

    return [
        CorpusPaper(
            arxiv_id=f"240{r}.0000{i}",
            title=f"Paper {r}-{i}",
            abstract="Momentum trend-following equities volatility hedge.",
            primary_category="q-fin.PM",
            categories=("q-fin.PM",),
            published="2024-01-01",
        )
        for r in (1, 2)
        for i in range(1, (n // 2) + 1)
    ]


# Conformant DSL spec — passes _dsl_conformance_ok and evaluate_fusion_spec.
_DEBATE_SPEC = {
    "name": "Debate canned SMA",
    "asset_universe": ["SPY"],
    "rebalance_frequency": "monthly",
    "entry": {"gt": ["sma_50", 0]},
    "exit": {"lt": ["close", "sma_200"]},
    "position_sizing": {"type": "full_invested_when_in_market"},
    "look_ahead_safe": True,
}


def _make_fake_proposal(name: str, ids: list[str]) -> SimpleNamespace:
    return SimpleNamespace(
        strategy_name=name,
        thesis=f"Thesis for {name}.",
        source_arxiv_ids=ids,
        strategy_spec=dict(_DEBATE_SPEC, name=name),
        fusion_reasoning="canned reasoning",
        novelty_rationale="canned novelty",
        is_actionable=True,
    )


def _fake_eval_result(*, num_trials: int | None = None) -> SimpleNamespace:
    """Canned FusionEvalResult that passes all rigor gates + C-null."""
    rigor = SimpleNamespace(
        dsr=1.5,
        dsr_p_value=0.01,
        pbo_score=0.1,
        oos_sharpe=1.2,
        in_sample_sharpe=1.3,
        look_ahead_clean=True,
        look_ahead_label="clean",
        num_trials=num_trials,
        passing=True,
        data_source="synthetic",
        admissible=False,
    )
    bt = SimpleNamespace(
        sharpe_ratio=1.4,
        sortino_ratio=1.6,
        max_drawdown=-0.1,
        cagr=0.2,  # > MIN_COST_BENEFIT (0.05%) → clears C-null
        calmar_ratio=1.1,
        win_rate=0.55,
        total_trades=42,
        equity_curve=None,  # no return series needed for hermetic tests
    )
    return SimpleNamespace(rigor=rigor, backtest=bt, success=True, admissible=False, error=None, spec={})


def _setup_debate_hermetic(monkeypatch, *, gp, de, sf, fe, fake_proposals, fake_corpus):
    """Apply the standard debate-path hermetic stubs.

    Monkeypatches:
    - gp._llm_available → True (live path)
    - de._debate_can_run → True (bypass flag + corpus size check)
    - de._propose_pool → returns fake_proposals (no LLM call)
    - sf.load_corpus → returns fake_corpus (no DB/disk read)
    - fe.evaluate_fusion_spec → returns _fake_eval_result (no backtest)
    - de._debate_round → no-op (best-effort transcript, never gates)
    """
    monkeypatch.setattr(gp, "_llm_available", lambda: True)
    monkeypatch.setattr(de, "_debate_can_run", lambda brief: True)

    async def _fake_pool(*a, **k):
        return list(fake_proposals)

    monkeypatch.setattr(de, "_propose_pool", _fake_pool)
    monkeypatch.setattr(sf, "load_corpus", lambda *a, **k: list(fake_corpus))

    def _fake_eval(spec, *, num_trials=None, use_real_data=False, **kw):
        return _fake_eval_result(num_trials=num_trials)

    monkeypatch.setattr(fe, "evaluate_fusion_spec", _fake_eval)

    async def _noop_debate_round(*a, **k):
        return []

    monkeypatch.setattr(de, "_debate_round", _noop_debate_round)


def _permissive_validate(brief):
    """Permissive brief validator (no LLM call)."""

    async def _inner(brief):
        return {
            "is_valid": True,
            "intent_summary": brief.intent[:140],
            "asset_classes_inferred": brief.asset_classes or [],
            "time_horizon_inferred": "unknown",
            "risk_appetite_adjusted": brief.risk_appetite,
        }

    return _inner(brief)


# ── Test 3 — debate dispatch end-to-end persists a debate strategy ────────────


@pytest.mark.asyncio
async def test_debate_dispatch_end_to_end_persists_debate_strategy(tmp_path, monkeypatch):
    """Debate dispatch drives run_generation → a StrategyRecord with
    generation_method='debate' is persisted.

    Re-targets test_fusion_dispatch_end_to_end_persists_fusion_strategy which
    tested the retired standalone-fusion dispatch. Hermetic: tmp SQLite, mocked
    proposer + evaluator — no network, no LLM.

    Asserts: (a) pipeline_selected=debate, (b) candidate_drafted + persisted
    emitted, (c) StrategyRecord.generation_method == 'debate'.
    """
    import archimedes.db as _db
    import archimedes.agents.debate_engine as de
    import archimedes.agents.strategy_fusion as sf
    import archimedes.services.fusion_evaluator as fe
    from archimedes.agents import generation_pipeline as gp
    from archimedes.models.strategy_store import StrategyRecord
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine(
        f"sqlite:///{tmp_path / 'debate_e2e.db'}",
        connect_args={"check_same_thread": False},
    )
    monkeypatch.setattr(_db, "engine", test_engine)
    monkeypatch.setattr(_db, "SessionLocal", sessionmaker(bind=test_engine, autocommit=False, autoflush=False))
    from archimedes.models import kg, strategy_passport_record  # noqa: F401

    _db.Base.metadata.create_all(bind=test_engine)

    monkeypatch.delenv("GENERATION_PIPELINE_FIXTURE", raising=False)

    async def _pv(brief):
        return await _permissive_validate(brief)

    monkeypatch.setattr(gp, "_validate_brief", _pv)

    fake_corpus = _debate_fake_corpus(4)
    fake_proposals = [
        _make_fake_proposal("Debate A", ["2401.00001", "2402.00001"]),
        _make_fake_proposal("Debate B", ["2401.00002", "2402.00002"]),
    ]
    _setup_debate_hermetic(
        monkeypatch, gp=gp, de=de, sf=sf, fe=fe, fake_proposals=fake_proposals, fake_corpus=fake_corpus
    )

    store = _FakeStore()
    brief = GenerateBrief(
        intent="equity momentum with a volatility overlay",
        risk_appetite="moderate",
        asset_classes=["equities"],
    )
    await run_generation(job_id="job_debate_e2e", brief=brief, store=store, dual_regime=False, n_candidates=1)

    # pipeline_selected announced debate
    ps = [e for e in store.events if e["event"] == "pipeline_selected"]
    assert ps, "no pipeline_selected event emitted"
    assert ps[0]["data"]["pipeline"] == "debate", (
        f"debate not dispatched; got {ps[0]['data']['pipeline']!r} (reason={ps[0]['data'].get('reason')!r})"
    )

    # Required streaming events present
    names = [e["event"] for e in store.events]
    for required in ("candidate_drafted", "candidate_evaluated", "persisted", "done"):
        assert required in names, f"debate run did not emit {required!r} (events={names})"

    # Persisted as a debate strategy (generation_method='debate')
    with _db.get_session() as session:
        record = session.query(StrategyRecord).filter(StrategyRecord.generation_method == "debate").first()
    assert record is not None, "no debate StrategyRecord was persisted"
    assert record.generation_method == "debate"

    # Rigor verdict shape present and has required fields
    assert record.rigor_verdict is not None, "debate strategy persisted without a rigor verdict"
    verdict = json.loads(record.rigor_verdict)
    for key in ("dsr", "pbo", "oos_sharpe", "passing"):
        assert key in verdict, f"rigor verdict missing {key!r}: {verdict}"
    assert isinstance(verdict["passing"], bool)


# ── Test 4 — debate C-rigor threads _society_num_trials(library, pool) ────────


@pytest.mark.asyncio
async def test_debate_critic_rigor_num_trials_matches_society_formula(tmp_path, monkeypatch):
    """Debate C-rigor passes num_trials = _society_num_trials(library_size, pool_size)
    to evaluate_fusion_spec — the same formula the live agent path uses.

    Re-targets test_fusion_path_num_trials_matches_society_formula.  pool_size is
    the society's own internal count of conformant proposals (the real selection
    set), not n_candidates. The spy asserts evaluate_fusion_spec receives the right
    value across the full proposal pool.
    """
    import archimedes.db as _db
    import archimedes.agents.debate_engine as de
    import archimedes.agents.strategy_fusion as sf
    import archimedes.services.fusion_evaluator as fe
    from archimedes.agents import generation_pipeline as gp
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine(
        f"sqlite:///{tmp_path / 'debate_num_trials.db'}",
        connect_args={"check_same_thread": False},
    )
    monkeypatch.setattr(_db, "engine", test_engine)
    monkeypatch.setattr(_db, "SessionLocal", sessionmaker(bind=test_engine, autocommit=False, autoflush=False))
    from archimedes.models import kg, strategy_passport_record  # noqa: F401

    _db.Base.metadata.create_all(bind=test_engine)

    monkeypatch.delenv("GENERATION_PIPELINE_FIXTURE", raising=False)

    async def _pv(brief):
        return await _permissive_validate(brief)

    monkeypatch.setattr(gp, "_validate_brief", _pv)

    # Fixed library_size so _society_num_trials(library, pool) is computable.
    library_size = 4

    class _FakeStrategy:
        paper_arxiv_id = "1234.5678"
        paper_title = "Fake Paper"

    class _FakeProvider:
        def list_strategies(self, *a, **k):
            return [_FakeStrategy() for _ in range(library_size)]

    import archimedes.services.strategy_provider as sp

    monkeypatch.setattr(sp, "default_provider", lambda *a, **k: _FakeProvider())

    # The pool has 3 proposals → pool_size = 3 inside _run_debate_leaderboard.
    pool_size = 3
    fake_corpus = _debate_fake_corpus(pool_size * 2)
    fake_proposals = [
        _make_fake_proposal(f"Debate {i}", [f"2401.0000{i}", f"2402.0000{i}"]) for i in range(1, pool_size + 1)
    ]

    # Spy on evaluate_fusion_spec — capture num_trials for every call.
    captured_num_trials: list[int | None] = []

    def _spy_evaluate(spec, *, num_trials=None, use_real_data=False, **kw):
        captured_num_trials.append(num_trials)
        return _fake_eval_result(num_trials=num_trials)

    # Setup hermetic stubs but override evaluate_fusion_spec with the spy.
    monkeypatch.setattr(gp, "_llm_available", lambda: True)
    monkeypatch.setattr(de, "_debate_can_run", lambda brief: True)

    async def _fake_pool(*a, **k):
        return list(fake_proposals)

    monkeypatch.setattr(de, "_propose_pool", _fake_pool)
    monkeypatch.setattr(sf, "load_corpus", lambda *a, **k: list(fake_corpus))
    monkeypatch.setattr(fe, "evaluate_fusion_spec", _spy_evaluate)

    async def _noop_debate_round(*a, **k):
        return []

    monkeypatch.setattr(de, "_debate_round", _noop_debate_round)

    store = _FakeStore()
    brief = GenerateBrief(
        intent="equity momentum with volatility overlay",
        risk_appetite="moderate",
        asset_classes=["equities"],
    )
    await run_generation(
        job_id="job_debate_num_trials",
        brief=brief,
        store=store,
        dual_regime=False,
        n_candidates=1,
    )

    assert captured_num_trials, "evaluate_fusion_spec was never called — debate C-rigor did not run"
    expected = gp._society_num_trials(library_size, pool_size)
    assert all(n == expected for n in captured_num_trials), (
        f"debate num_trials {captured_num_trials} != formula {expected} "
        f"(library_size={library_size}, pool_size={pool_size})"
    )

    # candidate_evaluated event carries the verdict; num_trials should match.
    evaluated = [e for e in store.events if e["event"] == "candidate_evaluated"]
    assert evaluated, "no candidate_evaluated event emitted"
    for e in evaluated:
        verdict = e["data"].get("rigor_verdict") or {}
        if verdict.get("num_trials") is not None:
            assert verdict["num_trials"] == expected, (
                f"candidate_evaluated verdict num_trials={verdict['num_trials']} != {expected}"
            )


# ── Test 5 — debate winner with weights={} never causes spurious backtest_failed ─


@pytest.mark.asyncio
async def test_debate_text_only_pool_no_spurious_backtest_failed(tmp_path, monkeypatch):
    """Regression guard (debate-path re-target of #784 live bug).

    A debate winner has weights={} by design (it emits a DSL spec scored by
    C-rigor, not a static weight vector). It must NOT be routed into
    _backtest_and_persist (the static buy-and-hold backtester), which would
    emit the misleading backtest_failed("no weights emitted by agent").

    Protection: generation_method='debate' is in _static_skip. Crucially, the
    test does NOT set GENERATION_PIPELINE_SKIP_BACKTEST — the bug only surfaces
    when _backtest_and_persist is actually reachable.

    Re-targets test_fusion_text_only_does_not_emit_spurious_backtest_failed which
    tested the same _static_skip guard on the retired standalone-fusion dispatch.
    """
    import archimedes.db as _db
    import archimedes.agents.debate_engine as de
    import archimedes.agents.strategy_fusion as sf
    import archimedes.services.fusion_evaluator as fe
    from archimedes.agents import generation_pipeline as gp
    from archimedes.models.strategy_store import StrategyRecord
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine(
        f"sqlite:///{tmp_path / 'debate_textonly.db'}",
        connect_args={"check_same_thread": False},
    )
    monkeypatch.setattr(_db, "engine", test_engine)
    monkeypatch.setattr(_db, "SessionLocal", sessionmaker(bind=test_engine, autocommit=False, autoflush=False))
    from archimedes.models import kg, strategy_passport_record  # noqa: F401

    _db.Base.metadata.create_all(bind=test_engine)

    monkeypatch.delenv("GENERATION_PIPELINE_FIXTURE", raising=False)
    # Intentionally NOT setting GENERATION_PIPELINE_SKIP_BACKTEST — the bug only
    # manifests when _backtest_and_persist is reachable (SKIP_BACKTEST masks it).

    async def _pv(brief):
        return await _permissive_validate(brief)

    monkeypatch.setattr(gp, "_validate_brief", _pv)

    fake_corpus = _debate_fake_corpus(4)
    fake_proposals = [
        _make_fake_proposal("Debate Winner", ["2401.00001", "2402.00001"]),
    ]
    _setup_debate_hermetic(
        monkeypatch, gp=gp, de=de, sf=sf, fe=fe, fake_proposals=fake_proposals, fake_corpus=fake_corpus
    )

    store = _FakeStore()
    brief = GenerateBrief(
        intent="equity momentum with volatility overlay",
        risk_appetite="moderate",
        asset_classes=["equities"],
    )
    await run_generation(job_id="job_debate_textonly", brief=brief, store=store, dual_regime=False, n_candidates=1)

    # Debate was dispatched.
    ps = [e for e in store.events if e["event"] == "pipeline_selected"]
    assert ps and ps[0]["data"]["pipeline"] == "debate", f"debate not dispatched: {ps}"

    # Run completed cleanly.
    names = [e["event"] for e in store.events]
    for required in ("candidate_drafted", "persisted", "done"):
        assert required in names, f"debate run did not emit {required!r} (events={names})"

    # THE FIX: no spurious "no weights" backtest_failed for the debate winner.
    backtest_failed = [e for e in store.events if e["event"] == "backtest_failed"]
    spurious = [e for e in backtest_failed if "no weights" in (e["data"].get("error", "") or "")]
    assert not spurious, f"debate winner (weights={{}}) was wrongly routed into the static backtester: {spurious}"

    # Persisted as a debate strategy.
    with _db.get_session() as session:
        record = session.query(StrategyRecord).filter(StrategyRecord.generation_method == "debate").first()
    assert record is not None, "no debate StrategyRecord was persisted"
    assert record.generation_method == "debate"


# ── Test 6 — no-LLM dispatch: fixture path or GENERATION_UNAVAILABLE ──────────


@pytest.mark.asyncio
async def test_no_llm_testing_mode_selects_fixture_pipeline():
    """Under TESTING with no LLM, dispatch selects the 'fixture' pipeline.

    The autouse fixture already sets GENERATION_PIPELINE_FIXTURE=1, which forces
    _llm_available() → False AND sets fixture_mode=True. Asserts pipeline_selected
    says 'fixture' and the run completes cleanly.

    Replaces test_fusion_falls_back_to_agent_when_no_llm (which tested the
    retired agent-fallback relabeling; fixture/GENERATION_UNAVAILABLE is now the
    honest contract).
    """
    store = _FakeStore()
    brief = GenerateBrief(intent="balanced macro", risk_appetite="moderate")
    # autouse fixture forces GENERATION_PIPELINE_FIXTURE=1 → fixture path.
    with patch(
        "archimedes.agents.generation_pipeline._persist_candidate",
        new=AsyncMock(return_value=("strat_fixture_nollm", "0xfx")),
    ):
        await run_generation(job_id="job_nollm_testing", brief=brief, store=store, dual_regime=False, n_candidates=1)

    ps = next(e for e in store.events if e["event"] == "pipeline_selected")
    assert ps["data"]["pipeline"] == "fixture", (
        f"no-LLM+fixture-env must select 'fixture', got {ps['data']['pipeline']!r}"
    )
    names = [e["event"] for e in store.events]
    assert "candidate_drafted" in names
    assert names[-1] == "done"


@pytest.mark.asyncio
async def test_no_llm_prod_shape_emits_generation_unavailable(monkeypatch):
    """Prod shape (no TESTING, no fixture env, no LLM) → GENERATION_UNAVAILABLE.

    With the retired agent-fallback removed, an LLM-less prod environment emits
    an honest GENERATION_UNAVAILABLE error code and sets status to 'error'.
    Completes the new no-LLM contract (both cases of TASK 1 test 6).
    """
    from archimedes.agents import generation_pipeline as gp

    monkeypatch.delenv("GENERATION_PIPELINE_FIXTURE", raising=False)
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.setattr(gp, "_llm_available", lambda: False)

    store = _FakeStore()
    brief = GenerateBrief(intent="balanced macro", risk_appetite="moderate")
    await gp.run_generation(job_id="job_nollm_prod", brief=brief, store=store, dual_regime=False, n_candidates=1)

    err = next((e for e in store.events if e["event"] == "error"), None)
    assert err is not None, "prod no-LLM must emit an error event"
    assert err["data"]["code"] == "GENERATION_UNAVAILABLE", (
        f"expected GENERATION_UNAVAILABLE, got {err['data']['code']!r}"
    )
    statuses = [s[0] for s in store.status]
    assert "error" in statuses
