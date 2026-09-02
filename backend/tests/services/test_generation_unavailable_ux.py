"""The too-few-papers run is a first-class outcome, not a dead red line.

Owner's screenshot: the run ended with one line — "Generation is unavailable
right now: the corpus yielded <2 papers for this steer — the society cannot
fuse." — rendered as error event #3, and nothing else. No steer, no count, no
way forward, and no record on the job of what actually happened.

These tests pin the contract the UI reads:

* the ``error`` event still carries ``code=GENERATION_UNAVAILABLE`` (unchanged
  wire contract) AND the structured fields ``steer`` / ``candidates_found`` /
  ``suggestions[]``;
* the machine reason string survives verbatim for log greps and the job record;
* the job is recorded as failed BEFORE synthesis, so nothing half-persisted can
  reach Library or the leaderboard.

Kept in its own module rather than appended to ``test_generation_pipeline.py``:
that file's autouse fixture forces the fixture runner, which is the one path
this failure branch is unreachable from.
"""

from __future__ import annotations

import pytest
from archimedes.agents import generation_pipeline as gp
from archimedes.api.generate_schemas import GenerateBrief

from ..test_corpus_viability import _CRYPTO_CORPUS


class _FakeStore:
    """In-memory JobStore stand-in. Captures events, statuses and results."""

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
        await self.update_status(job_id, status, result=result, error=error)
        return True


@pytest.fixture
def thin_corpus_run(monkeypatch):
    """Live LLM, crypto-only corpus, rates steer → the owner's failure."""
    from archimedes.agents import strategy_fusion as sf

    monkeypatch.delenv("GENERATION_PIPELINE_FIXTURE", raising=False)
    monkeypatch.setattr(gp, "_llm_available", lambda: True)
    monkeypatch.setattr(sf, "load_corpus", lambda *a, **k: list(_CRYPTO_CORPUS))


async def _run(store: _FakeStore) -> None:
    brief = GenerateBrief(
        intent="build a treasury ladder that beats holding cash over a two year horizon",
        risk_appetite="conservative",
        asset_classes=["rates"],
    )
    await gp.run_generation(job_id="job_thin_corpus", brief=brief, store=store, dual_regime=False, n_candidates=1)


def _error_event(store: _FakeStore) -> dict:
    err = next((e for e in store.events if e["event"] == "error"), None)
    assert err is not None, f"no error event; got {[e['event'] for e in store.events]}"
    return err["data"]


@pytest.mark.asyncio
async def test_error_event_carries_the_structured_fields_the_ui_needs(thin_corpus_run):
    store = _FakeStore()
    await _run(store)
    data = _error_event(store)

    # Unchanged wire contract.
    assert data["code"] == "GENERATION_UNAVAILABLE"
    assert data["recoverable"] is True
    # The machine reason, preserved verbatim.
    assert data["reason"] == "the corpus yielded <2 papers for this steer — the society cannot fuse"

    # The three fields the error card renders.
    assert data["reason_code"] == "CORPUS_TOO_FEW_PAPERS"
    assert data["steer"] == "build a treasury ladder that beats holding cash over a two year horizon"
    assert data["candidates_found"] == 1, data
    assert data["candidates_found"] < data["min_papers"]
    assert data["retrieval"] == "lexical"

    suggestions = data["suggestions"]
    assert suggestions, "the error event must carry the ways forward, not just the failure"
    assert len(suggestions) <= 3
    for s in suggestions:
        assert set(s) == {"term", "kind", "papers"}, s
        # Asset classes only: they are the sole axis `select_candidates`
        # filters membership on, so the sole axis a chip can act on.
        assert s["kind"] == "asset_class", s
        assert s["papers"] >= data["min_papers"], f"suggested {s['term']} on {s['papers']} papers"


@pytest.mark.asyncio
async def test_message_states_the_steer_and_the_lexical_count(thin_corpus_run):
    store = _FakeStore()
    await _run(store)
    msg = _error_event(store)["message"]

    assert "build a treasury ladder that beats holding cash" in msg, msg
    assert "matched 1 paper" in msg, msg
    assert "lexical" in msg.lower(), msg
    for banned in ("semantic", "embedding"):
        assert banned not in msg.lower(), f"message claims {banned}: {msg}"


@pytest.mark.asyncio
async def test_run_is_recorded_as_failed_before_synthesis(thin_corpus_run):
    """Library/leaderboard read persisted strategies; the job record must say
    plainly that this run never reached synthesis, with the reason attached."""
    store = _FakeStore()
    await _run(store)

    errors = [(s, r, e) for (s, r, e) in store.status if s == "error"]
    assert errors, f"job was never flipped to error; statuses={[s for s, _, _ in store.status]}"
    _status, result, error_str = errors[-1]

    assert error_str == "generation unavailable: the corpus yielded <2 papers for this steer — the society cannot fuse"
    assert result is not None, "the failure must be ON the job record, not only in the event stream"
    assert result["failed_before_synthesis"] is True
    failure = result["failure"]
    assert failure["code"] == "GENERATION_UNAVAILABLE"
    assert failure["reason_code"] == "CORPUS_TOO_FEW_PAPERS"
    assert failure["candidates_found"] == 1
    assert failure["suggestions"]

    # Nothing was drafted, evaluated or persisted — there is no half-strategy.
    names = [e["event"] for e in store.events]
    for forbidden in ("candidate_drafted", "candidate_evaluated", "best_selected", "persisted", "done"):
        assert forbidden not in names, f"{forbidden} fired on a run that failed before synthesis: {names}"
    assert names[-1] == "error"
    # `/jobs/{id}/candidates` and `_job_summary` read these keys off `result`.
    assert "best_strategy_id" not in result
    assert "candidates" not in result


@pytest.mark.asyncio
async def test_a_gate_that_disagrees_with_the_explanation_reports_no_numbers(thin_corpus_run, monkeypatch):
    """The gate and the card are two retrievals; they can disagree.

    ``_debate_can_run`` decides, then the failure branch re-assesses to explain.
    If the corpus moved in between (transient DB failure into the file fallback,
    a concurrent intake) the second assessment can come back viable while the
    run is already committed to failing. Emitting its fields then would print
    "matched 8 papers … needs at least 2, so no strategy was drafted" — a
    sentence that contradicts itself — and ``reason_code=OK`` would leave the
    user back at the bare red line with no card at all.
    """
    from archimedes.agents import debate_engine as de

    # The gate says no; the brief it says no to is one the corpus can serve, so
    # the explaining assessment comes back viable — the disagreement, staged.
    monkeypatch.setattr(de, "_debate_can_run", lambda *_a, **_k: False)
    brief = GenerateBrief(intent="momentum on crypto majors", risk_appetite="aggressive", asset_classes=["crypto"])

    store = _FakeStore()
    await gp.run_generation(job_id="job_gate_disagree", brief=brief, store=store, dual_regime=False, n_candidates=1)
    data = _error_event(store)

    assert data["code"] == "GENERATION_UNAVAILABLE"
    assert data["reason_code"] == "CORPUS_UNAVAILABLE", data
    assert "disagreed" in data["message"], data["message"]
    # No count may be quoted for a retrieval whose answer we are not using.
    for numeric in ("candidates_found", "min_papers", "corpus_size", "suggestions"):
        assert numeric not in data, f"{numeric} was reported from an assessment the run did not act on"


@pytest.mark.asyncio
async def test_no_llm_backend_is_reported_as_a_different_reason(monkeypatch):
    """Not every GENERATION_UNAVAILABLE is the user's brief. Say which one it is.

    Offering "broaden your brief" when the LLM backend is simply unreachable
    would send the user to rewrite a brief that was never the problem.
    """
    monkeypatch.delenv("GENERATION_PIPELINE_FIXTURE", raising=False)
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.setattr(gp, "_llm_available", lambda: False)

    store = _FakeStore()
    await _run(store)
    data = _error_event(store)

    assert data["code"] == "GENERATION_UNAVAILABLE"
    assert data["reason_code"] == "NO_LLM_BACKEND"
    assert "suggestions" not in data, "no corpus assessment should run when the corpus was not the problem"
    assert "Nothing in your brief caused this" in data["message"]
