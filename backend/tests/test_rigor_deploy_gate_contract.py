"""Regression: lock the API contract the UI rigor-Deploy gate depends on.

LEG C made the StrategyPassport Deploy button refuse to deploy a
rigor-failing strategy: the button is `disabled` whenever
`passes_rigor_gate === false` on the strategy fetched from
`GET /api/strategies/{id}`. There is no JS test runner in `ui/`
(`ui/package.json` exposes only dev/build/lint/preview), so the
enforceable guard for that UI behaviour is this backend invariant —
it pins the two facts the gate reads:

  1. A strategy that fails the rigor gate serializes to the API with
     `passes_rigor_gate=False` (the exact boolean the button checks).
  2. The candidate viewer (`GET /api/generate/jobs/{job_id}/candidates`)
     returns exactly one `selected=True` candidate, and every rejected
     candidate carries its own `passes_rigor` flag — so the UI can
     render "Considered N candidates" + the per-candidate rigor verdict
     and never silently promote a reject.

Hermetic by construction: no DB, no Redis, no LLM, no network. The
schema objects (`StrategyResponse`, `CandidatesListResponse`) are the
literal wire shapes the two endpoints emit, plus a fixture-path
`run_generation` round-trip that proves the live result dict matches.
"""

from __future__ import annotations

import pytest
from archimedes.agents.generation_pipeline import run_generation
from archimedes.api.generate_schemas import (
    CandidatesListResponse,
    CandidateSummary,
    GenerateBrief,
)
from archimedes.api.schemas import StrategyResponse


@pytest.fixture(autouse=True)
def force_fixture_path(monkeypatch):
    """No LLM / network — drive the deterministic fixture pipeline."""
    monkeypatch.setenv("GENERATION_PIPELINE_FIXTURE", "1")


class _FakeStore:
    """In-memory JobStore stand-in (mirrors test_generation_pipeline.py)."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.status: list[tuple[str, dict | None, str]] = []
        self.current_status: str | None = None

    async def push_event(self, job_id, payload):
        self.events.append(payload)
        return len(self.events)

    async def update_status(self, job_id, status, *, result=None, error=""):
        self.status.append((status, result, error))
        self.current_status = status

    async def update_terminal_status(self, job_id, status, *, result=None, error=""):
        """Mirrors ``JobStore.update_terminal_status`` (#1355): a no-op once
        ``cancelled`` has already been recorded."""
        if self.current_status == "cancelled":
            return False
        await self.update_status(job_id, status, result=result, error=error)
        return True


# ── Invariant 1: rigor-fail serializes to the API as passes_rigor_gate=False ──


def _minimal_response(**overrides) -> StrategyResponse:
    base = {
        "id": "strat_under_test",
        "methodology_summary": "brief test strategy",
        "asset_universe": ["SPY"],
        "position_sizing": "equal_weight",
        "rebalance_frequency": "weekly",
        "status": "candidate",
    }
    base.update(overrides)
    return StrategyResponse(**base)


def test_rigor_fail_serializes_passes_rigor_gate_false():
    """A failing-rigor strategy carries passes_rigor_gate=False on the wire.

    This is the exact field the Deploy button reads
    (`s.passes_rigor_gate === false`) to disable itself.
    """
    resp = _minimal_response(passes_rigor_gate=False)
    payload = resp.model_dump()
    assert "passes_rigor_gate" in payload
    assert payload["passes_rigor_gate"] is False
    # JSON mode (what the HTTP client actually receives) keeps it a bool False,
    # never null/omitted — `=== false` in the UI must remain a real comparison.
    assert resp.model_dump(mode="json")["passes_rigor_gate"] is False


def test_rigor_pass_serializes_passes_rigor_gate_true():
    """Symmetry: a passing strategy serializes True, so the gate opens."""
    resp = _minimal_response(passes_rigor_gate=True)
    assert resp.model_dump()["passes_rigor_gate"] is True


def test_passes_rigor_gate_defaults_false():
    """Absent rigor data must default to False (closed), not True (open).

    Fail-closed is the claim-integrity stance: an un-evaluated strategy is
    not deployable until it demonstrably clears the gate.
    """
    assert _minimal_response().model_dump()["passes_rigor_gate"] is False


# ── Invariant 2: candidates viewer — exactly one selected + reject flags ──────


def test_candidates_response_has_exactly_one_selected_with_reject_flags():
    """Build the wire shape the /candidates endpoint emits and assert the
    contract the RejectedCandidates UI + 'Considered N' counter rely on."""
    candidates = [
        CandidateSummary(
            candidate_id="cand_bull",
            strategy_id="strat_bull",
            strategy_name="Bull Regime Strategy",
            rigor_verdict={"passing": True, "dsr": 0.4},
            passes_rigor=True,
            selected=True,
            regime="bull",
        ),
        CandidateSummary(
            candidate_id="cand_bear",
            strategy_id="strat_bear",
            strategy_name="Bear Regime Strategy",
            rigor_verdict={"passing": False, "dsr": -0.1},
            passes_rigor=False,
            selected=False,
            regime="bear",
        ),
    ]
    resp = CandidatesListResponse(
        job_id="job_under_test",
        best_candidate_id="cand_bull",
        candidates=candidates,
    )

    selected = [c for c in resp.candidates if c.selected]
    assert len(selected) == 1, "exactly one candidate may be marked selected"
    assert resp.best_candidate_id == selected[0].candidate_id

    rejects = [c for c in resp.candidates if not c.selected]
    assert len(rejects) == len(resp.candidates) - 1
    # Each reject exposes its own rigor verdict so the UI can render WHY it lost.
    for r in rejects:
        assert isinstance(r.passes_rigor, bool)
        assert r.passes_rigor is False


@pytest.mark.asyncio
async def test_live_pipeline_result_matches_candidates_contract(monkeypatch):
    """End-to-end (fixture path): the dict run_generation stashes on the job
    is exactly the shape /candidates rebuilds — one selected, rejects flagged.

    This locks the producer (pipeline) and the consumer (schema) together so a
    future refactor can't drift one without the test catching it.
    """

    async def _fake_persist(c, _brief, **_kw):  # mirrors _persist_candidate(c, brief, *, owner_wallet=None)
        return (f"strat_{c.candidate_id}", f"0x{c.candidate_id}")

    monkeypatch.setattr(
        "archimedes.agents.generation_pipeline._persist_candidate",
        _fake_persist,
    )

    store = _FakeStore()
    brief = GenerateBrief(intent="dual regime trend following", risk_appetite="moderate")
    # Default dual_regime=True → bull + bear candidates persisted.
    await run_generation(job_id="job_contract", brief=brief, store=store)

    result = store.status[-1][1]
    assert result is not None
    cands = result["candidates"]
    assert len(cands) == 2

    # Rebuild the exact endpoint payload from the stashed result (this is what
    # list_candidates() does) — it must validate against the wire schema.
    resp = CandidatesListResponse(
        job_id="job_contract",
        best_candidate_id=result["best_candidate_id"],
        candidates=[CandidateSummary(**c) for c in cands],
    )

    selected = [c for c in resp.candidates if c.selected]
    assert len(selected) == 1, "pipeline must mark exactly one candidate selected"
    assert resp.best_candidate_id == selected[0].candidate_id

    # Every candidate (selected or reject) exposes a bool passes_rigor flag.
    for c in resp.candidates:
        assert isinstance(c.passes_rigor, bool)
