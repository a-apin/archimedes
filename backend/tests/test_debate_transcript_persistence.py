"""``_persist_debate_transcripts`` (debate-transcript-capture): the
``run_generation``-level glue that writes a ``debate_transcripts`` row for the
K=1 winner AND every final-round loser, purely from data ``run_generation``
already has in scope by the time it is called (``job_id``, the full
``candidates`` leaderboard, ``strategy_ids``) — no new threading through
``_persist_candidate``/``_do_persist``.

Hermetic: tmp-file sqlite via ``redirect_to_tmp_sqlite``. No LLM, no network,
no Redis.
"""

from __future__ import annotations

import pytest
from archimedes.agents.generation_pipeline import _CandidateResult, _persist_debate_transcripts
from archimedes.models.debate_transcript import DebateTranscriptRecord

from tests.db_isolation import redirect_to_tmp_sqlite


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path):
    yield from redirect_to_tmp_sqlite(tmp_path)


_TRANSCRIPT = [{"role": "bull", "round": 1, "verdict": "act", "claims": ["c1"]}]


def _mk_candidate(candidate_id: str, *, transcript=None, generation_method: str = "debate") -> _CandidateResult:
    return _CandidateResult(
        candidate_id=candidate_id,
        strategy_name=f"Strategy {candidate_id}",
        thesis="t",
        asset_universe=["SPY"],
        source_papers=[],
        weights={"SPY": 1.0},
        reasoning="r",
        rigor_verdict={"passing": True},
        passes_rigor=True,
        generation_method=generation_method,
        debate_transcript=transcript,
    )


async def test_persists_winner_with_strategy_id_and_loser_with_null_strategy_id():
    """The single write site: one pass over `candidates` covers the K=1
    winner (real strategy_id, threaded via `strategy_ids`) AND the
    final-round loser (never threaded into strategy_store -> NULL)."""
    from archimedes.db import get_session

    winner = _mk_candidate("cand_neutral", transcript=_TRANSCRIPT)
    loser = _mk_candidate("cand_neutral_alt1", transcript=_TRANSCRIPT)

    await _persist_debate_transcripts(
        job_id="job-1",
        candidates=[winner, loser],
        strategy_ids={"cand_neutral": "strat-abc"},  # only the winner is ever in here
    )

    with get_session() as session:
        rows = {r.candidate_id: r for r in session.query(DebateTranscriptRecord).filter_by(generation_id="job-1").all()}
    assert set(rows) == {"cand_neutral", "cand_neutral_alt1"}
    assert rows["cand_neutral"].strategy_id == "strat-abc"
    assert rows["cand_neutral_alt1"].strategy_id is None


async def test_skips_candidates_with_no_transcript():
    """Fusion / fixture / single-agent candidates carry
    debate_transcript=None (no debate ran) — must not write a row for them."""
    from archimedes.db import get_session

    fusion_cand = _mk_candidate("cand_1", transcript=None, generation_method="fusion")
    await _persist_debate_transcripts(job_id="job-2", candidates=[fusion_cand], strategy_ids={"cand_1": "strat-x"})

    with get_session() as session:
        assert session.query(DebateTranscriptRecord).filter_by(generation_id="job-2").count() == 0


async def test_empty_candidate_list_is_a_clean_noop():
    from archimedes.db import get_session

    await _persist_debate_transcripts(job_id="job-3", candidates=[], strategy_ids={})

    with get_session() as session:
        assert session.query(DebateTranscriptRecord).filter_by(generation_id="job-3").count() == 0


async def test_persist_failure_is_swallowed_non_blocking(monkeypatch):
    """CLAUDE.md guard 4 / mirrors the sibling `_persist_generation_cost`:
    instrumentation must never fail a generation that already succeeded. A DB
    session that raises must produce a log line, not an exception out of
    `_persist_debate_transcripts` itself."""

    def _raise(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr("archimedes.db.get_session", _raise)

    cand = _mk_candidate("cand_neutral", transcript=_TRANSCRIPT)
    # Must not raise — a caller in run_generation's success path calls this
    # unconditionally after the winner is already durably persisted.
    await _persist_debate_transcripts(job_id="job-4", candidates=[cand], strategy_ids={"cand_neutral": "s1"})
