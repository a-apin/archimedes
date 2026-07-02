"""Tests for the streaming Generate pipeline.

Forces the fixture path (no LLM credentials needed) and asserts that the
event sequence matches the spec ordering and that a strategy is persisted
at the end.
"""

from __future__ import annotations

import asyncio
import json
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

    async def mock_persist(c, b, **_kw):  # accepts owner_wallet kwarg
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


# ── Fusion dispatch wire — hermetic end-to-end (Stack A) ───────────────────
#
# Proves the #1 fix: the "fusion" pipeline choice ACTUALLY drives dispatch.
# Drives run_generation with fusion_enabled=true + a ≥20-paper fixture corpus +
# a MOCKED LLM backend (no network, no real Anthropic/Bedrock), and asserts the
# persisted strategy is generation_method=="fusion", cites ≥2 source_arxiv_ids,
# and carries a non-null rigor verdict (real DSR/PBO/OOS from the DSL backtest).


def _fixture_corpus(n: int = 24):
    """A ≥n-paper fixture corpus the deterministic selector can rank.

    Returns CorpusPaper objects (the type load_corpus yields). Titles/abstracts
    carry equities+momentum+volatility terms so select_candidates matches them
    for a generic steer.
    """
    from archimedes.agents.strategy_fusion import CorpusPaper

    papers = []
    for i in range(n):
        papers.append(
            CorpusPaper(
                arxiv_id=f"24{i:02d}.{1000 + i}",
                title=f"Cross-sectional equity momentum and volatility timing #{i}",
                abstract=(
                    "We study momentum, trend-following and volatility-managed "
                    "portfolios across equities, with risk-on and defensive regimes."
                ),
                primary_category="q-fin.PM",
                categories=("q-fin.PM", "q-fin.ST"),
                published=f"2024-01-{(i % 27) + 1:02d}",
            )
        )
    return papers


class _MockFusionBackend:
    """Deterministic LLM stand-in that returns a REAL, parseable fusion.

    Mirrors the live LLMBackend Protocol (model_id / served_model / available /
    complete). ``available`` is True so the fusion path treats it as a live
    model. ``complete`` echoes back ≥2 of the candidate arxiv_ids it sees in the
    user prompt (so the engine's anti-hallucination filter keeps them) and emits
    a real Archimedes DSL strategy_spec the evaluator can backtest + rigor-gate.
    """

    model_id = "mock-fusion-model"
    served_model = "mock-fusion-model-served"

    @property
    def available(self) -> bool:
        return True

    def complete(self, system: str, user: str) -> str:
        import json as _json
        import re as _re

        ids = _re.findall(r'"arxiv_id"\s*:\s*"([^"]+)"', user)[:3]
        return _json.dumps(
            {
                "strategy_name": "Mock Fused SMA+Vol Strategy",
                "thesis": "Fuses SMA-200 trend timing with vol-managed sizing (pre-backtest).",
                "source_arxiv_ids": ids,
                "fusion_reasoning": "Paper A contributes trend timing; paper B vol scaling.",
                "novelty_rationale": "Combination not published together.",
                "risk_notes": "Pre-backtest hypothesis; rigor gate applies.",
                "strategy_spec": {
                    "name": "Mock Fused SMA+Vol Strategy",
                    "asset_universe": ["SPY"],
                    "rebalance_frequency": "monthly",
                    "entry": {"gt": ["close", "sma_200"]},
                    "exit": {"lt": ["close", "sma_200"]},
                    "position_sizing": {"type": "full_invested_when_in_market"},
                    "source_arxiv_ids": ids,
                    "look_ahead_safe": True,
                    "indicators": ["sma_200"],
                    "parameter_variants": {"sma_200": [150, 200, 250]},
                },
            }
        )


@pytest.mark.asyncio
async def test_fusion_dispatch_end_to_end_persists_fusion_strategy(tmp_path, monkeypatch):
    """The fusion choice drives dispatch → a fusion strategy is persisted.

    Hermetic: tmp SQLite DB, fixture corpus, mocked LLM backend — no network.
    """
    # Real (temp) DB so _persist_candidate's upsert_strategy lands a row. db.py
    # binds its engine/SessionLocal at IMPORT time, so an env tweak alone won't
    # rebind them once another test has imported db — rebind the globals here so
    # get_session() (used by upsert_strategy) writes to OUR isolated sqlite file.
    import archimedes.db as _db
    from archimedes.agents import generation_pipeline as gp
    from archimedes.agents import strategy_fusion as sf
    from archimedes.models.strategy_store import StrategyRecord
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine(
        f"sqlite:///{tmp_path / 'fusion_e2e.db'}",
        connect_args={"check_same_thread": False},
    )
    monkeypatch.setattr(_db, "engine", test_engine)
    monkeypatch.setattr(_db, "SessionLocal", sessionmaker(bind=test_engine, autocommit=False, autoflush=False))
    # Register all ORM tables, then create them on the isolated engine.
    from archimedes.models import kg, strategy_passport_record  # noqa: F401

    _db.Base.metadata.create_all(bind=test_engine)

    # Enable fusion + take the LIVE path (override the file's fixture autouse).
    monkeypatch.setenv("ARCHIMEDES_FUSION_ENABLED", "1")
    monkeypatch.delenv("GENERATION_PIPELINE_FIXTURE", raising=False)
    # Skip the buy-and-hold backtest network round-trip for any agent fallback.
    monkeypatch.setenv("GENERATION_PIPELINE_SKIP_BACKTEST", "1")

    # Live LLM available (validator + fusion gating both read this).
    monkeypatch.setattr(gp, "_llm_available", lambda: True)

    async def _permissive_validate(brief):
        return {
            "is_valid": True,
            "intent_summary": brief.intent[:140],
            "asset_classes_inferred": brief.asset_classes or [],
            "time_horizon_inferred": "unknown",
            "risk_appetite_adjusted": brief.risk_appetite,
        }

    monkeypatch.setattr(gp, "_validate_brief", _permissive_validate)

    # ≥20-paper fixture corpus, injected at the engine's load_corpus seam (both
    # the no-LLM precheck and StrategyFusion read through this).
    corpus = _fixture_corpus(24)
    monkeypatch.setattr(sf, "load_corpus", lambda *a, **k: corpus)

    # MOCKED LLM backend — inject via a StrategyFusion built with our backend +
    # corpus so propose() does no network call. Patch on the agents module
    # (the canonical home; services.strategy_fusion is the same object via
    # the services/__init__ re-export).
    monkeypatch.setattr(
        sf,
        "default_fusion",
        lambda: sf.StrategyFusion(backend=_MockFusionBackend(), corpus=corpus),
    )

    store = _FakeStore()
    brief = GenerateBrief(
        intent="equity momentum with a volatility overlay",
        risk_appetite="moderate",
        asset_classes=["equities"],
    )

    await run_generation(job_id="job_fusion_e2e", brief=brief, store=store, dual_regime=False, n_candidates=1)

    # ── pipeline_selected announced fusion (and it actually ran) ──
    ps = [e for e in store.events if e["event"] == "pipeline_selected"]
    assert ps, "no pipeline_selected event emitted"
    assert ps[0]["data"]["pipeline"] == "fusion", (
        f"fusion was not dispatched; got {ps[0]['data']['pipeline']!r} (reason={ps[0]['data'].get('reason')!r})"
    )

    # ── Same streaming events the agent path emits surfaced the fusion run ──
    names = [e["event"] for e in store.events]
    for required in ("candidate_drafted", "candidate_evaluated", "persisted", "done"):
        assert required in names, f"fusion run did not emit {required!r} (events={names})"

    # ── The persisted strategy is a real fusion with provenance + rigor ──
    # Filter on the mock's exact name (not just generation_method) so a fusion
    # row left by another test in the same session can't be mistaken for ours.
    with _db.get_session() as session:
        record = (
            session.query(StrategyRecord)
            .filter(
                StrategyRecord.generation_method == "fusion",
                StrategyRecord.strategy_name == "Mock Fused SMA+Vol Strategy",
            )
            .first()
        )

    assert record is not None, "no fusion StrategyRecord was persisted"
    assert record.generation_method == "fusion"

    source_papers = json.loads(record.source_papers)
    arxiv_ids = [p.get("arxiv_id") for p in source_papers if p.get("arxiv_id")]
    assert len(arxiv_ids) >= 2, f"fusion must cite ≥2 source_arxiv_ids; got {arxiv_ids}"

    assert record.rigor_verdict is not None, "fusion strategy persisted without a rigor verdict"
    verdict = json.loads(record.rigor_verdict)
    # Real DSL backtest verdict: the four admission primitives + a passing bool.
    for key in ("dsr", "pbo", "oos_sharpe", "passing"):
        assert key in verdict, f"rigor verdict missing {key!r}: {verdict}"
    assert isinstance(verdict["passing"], bool)
    # A non-null rigor verdict (real DSR/PBO/OOS) — at least one numeric metric
    # populated, proving evaluate_fusion_spec actually ran a backtest.
    assert any(verdict.get(k) is not None for k in ("dsr", "pbo", "oos_sharpe")), (
        f"rigor verdict has no populated numeric metric (backtest didn't run?): {verdict}"
    )


class _MockTextOnlyFusionBackend:
    """Deterministic LLM stand-in returning a parseable fusion WITHOUT a
    machine-readable strategy_spec — the *text-only* path (``has_real_rigor``
    stays False).

    This is the path that produced the live #784 conversion bug: such a candidate
    carries ``weights={}`` AND ``has_real_rigor=False``, so before the fix it
    slipped past the ``if not c.has_real_rigor`` skip-guard into the static-weights
    backtester and emitted a misleading ``backtest_failed("no weights emitted by
    agent")`` — making every Generate result look broken.
    """

    model_id = "mock-textonly-fusion"
    served_model = "mock-textonly-fusion-served"

    @property
    def available(self) -> bool:
        return True

    def complete(self, system: str, user: str) -> str:
        import json as _json
        import re as _re

        ids = _re.findall(r'"arxiv_id"\s*:\s*"([^"]+)"', user)[:3]
        # NOTE: no "strategy_spec" key → proposal.strategy_spec is None → text-only.
        return _json.dumps(
            {
                "strategy_name": "Mock Text-Only Fusion",
                "thesis": "Fuses two papers as prose; no machine-readable DSL spec emitted.",
                "source_arxiv_ids": ids,
                "fusion_reasoning": "Paper A contributes trend timing; paper B vol scaling.",
                "novelty_rationale": "Combination not published together.",
                "risk_notes": "Pre-backtest hypothesis; rigor gate applies.",
            }
        )


@pytest.mark.asyncio
async def test_fusion_text_only_does_not_emit_spurious_backtest_failed(tmp_path, monkeypatch):
    """Regression guard for the live #784 conversion bug.

    A text-only fusion candidate (no DSL spec → has_real_rigor=False) must NOT
    emit the misleading ``backtest_failed("no weights emitted by agent")``. Fusion
    candidates carry ``weights={}`` by design and must never be routed into the
    static-weights backtester — the skip is keyed on ``generation_method``, not
    ``has_real_rigor`` (which is False for the text-only path).

    Hermetic: tmp SQLite DB, fixture corpus, mocked LLM backend — no network.
    Crucially does NOT set GENERATION_PIPELINE_SKIP_BACKTEST: the bug only
    manifests when ``_backtest_and_persist`` is actually reached (SKIP_BACKTEST
    returns earlier, masking it).
    """
    import archimedes.db as _db
    from archimedes.agents import generation_pipeline as gp
    from archimedes.agents import strategy_fusion as sf
    from archimedes.models.strategy_store import StrategyRecord
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine(
        f"sqlite:///{tmp_path / 'fusion_textonly.db'}",
        connect_args={"check_same_thread": False},
    )
    monkeypatch.setattr(_db, "engine", test_engine)
    monkeypatch.setattr(_db, "SessionLocal", sessionmaker(bind=test_engine, autocommit=False, autoflush=False))
    from archimedes.models import kg, strategy_passport_record  # noqa: F401

    _db.Base.metadata.create_all(bind=test_engine)

    monkeypatch.setenv("ARCHIMEDES_FUSION_ENABLED", "1")
    monkeypatch.delenv("GENERATION_PIPELINE_FIXTURE", raising=False)
    # Intentionally NOT setting GENERATION_PIPELINE_SKIP_BACKTEST — see docstring.
    monkeypatch.setattr(gp, "_llm_available", lambda: True)

    async def _permissive_validate(brief):
        return {
            "is_valid": True,
            "intent_summary": brief.intent[:140],
            "asset_classes_inferred": brief.asset_classes or [],
            "time_horizon_inferred": "unknown",
            "risk_appetite_adjusted": brief.risk_appetite,
        }

    monkeypatch.setattr(gp, "_validate_brief", _permissive_validate)

    corpus = _fixture_corpus(24)
    monkeypatch.setattr(sf, "load_corpus", lambda *a, **k: corpus)
    monkeypatch.setattr(
        sf,
        "default_fusion",
        lambda: sf.StrategyFusion(backend=_MockTextOnlyFusionBackend(), corpus=corpus),
    )

    store = _FakeStore()
    brief = GenerateBrief(
        intent="equity momentum with a volatility overlay",
        risk_appetite="moderate",
        asset_classes=["equities"],
    )

    await run_generation(job_id="job_fusion_textonly", brief=brief, store=store, dual_regime=False, n_candidates=1)

    # Fusion was dispatched (the candidate IS a fusion candidate).
    ps = [e for e in store.events if e["event"] == "pipeline_selected"]
    assert ps and ps[0]["data"]["pipeline"] == "fusion", f"fusion not dispatched: {ps}"

    # The run still produced + persisted a candidate and finished cleanly.
    names = [e["event"] for e in store.events]
    for required in ("candidate_drafted", "persisted", "done"):
        assert required in names, f"text-only fusion run did not emit {required!r} (events={names})"

    # THE FIX: no spurious "no weights" backtest_failed for the fusion candidate.
    backtest_failed = [e for e in store.events if e["event"] == "backtest_failed"]
    spurious = [e for e in backtest_failed if "no weights" in (e["data"].get("error", "") or "")]
    assert not spurious, f"fusion candidate was wrongly routed into the static backtester: {spurious}"

    # Persisted as a fusion strategy (generation_method preserved).
    with _db.get_session() as session:
        record = (
            session.query(StrategyRecord)
            .filter(
                StrategyRecord.generation_method == "fusion",
                StrategyRecord.strategy_name == "Mock Text-Only Fusion",
            )
            .first()
        )
    assert record is not None, "no text-only fusion StrategyRecord was persisted"
    assert record.generation_method == "fusion"


@pytest.mark.asyncio
async def test_fusion_falls_back_to_agent_when_no_llm(monkeypatch):
    """Fusion selected but no LLM → relabel to agent + run the fixture path.

    Confirms the agent FALLBACK still works when fusion can't run, and that the
    pipeline_selected event is HONEST (says "agent", not "fusion").
    """
    from archimedes.agents import generation_pipeline as gp

    monkeypatch.setenv("ARCHIMEDES_FUSION_ENABLED", "1")
    # Fixture path (no LLM) is forced by the file's autouse fixture; _pick_pipeline
    # may still select fusion via the flag, but use_live=False must relabel.
    monkeypatch.setattr(gp, "_pick_pipeline", lambda brief, mode_override=None: ("fusion", "forced fusion for test"))

    store = _FakeStore()
    brief = GenerateBrief(intent="balanced macro", risk_appetite="moderate")

    with patch(
        "archimedes.agents.generation_pipeline._persist_candidate",
        new=AsyncMock(return_value=("strat_fallback_001", "0xfb")),
    ):
        await run_generation(job_id="job_fusion_fallback", brief=brief, store=store, dual_regime=False, n_candidates=1)

    ps = next(e for e in store.events if e["event"] == "pipeline_selected")
    # No LLM (fixture path) → fusion is NOT runnable → honest relabel to agent.
    assert ps["data"]["pipeline"] == "agent", (
        f"fusion with no LLM must relabel to agent, got {ps['data']['pipeline']!r}"
    )
    # And the agent fallback still produced + persisted a candidate.
    names = [e["event"] for e in store.events]
    assert "candidate_drafted" in names
    assert "persisted" in names
    assert names[-1] == "done"
