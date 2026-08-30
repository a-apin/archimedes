"""Hermetic tests for multi-paper passport provenance rendering.

Covers the server-side paper title enrichment in
``_passport_to_strategy_response``: when a ``PassportPaperRef`` row has an
empty title but the corpus ``papers`` table has a matching arxiv_id, the API
response should use the corpus title instead of an empty string.  Falls back
to the bare arxiv_id string when the corpus has no matching row.

All tests use in-memory SQLite — no real Postgres, no network, no .env.
Run: env -i HOME=$HOME PATH=$PATH PYTHONPATH=backend python -m pytest \
       backend/tests/test_multipaper_passport.py -q
"""

from __future__ import annotations

import pytest
from archimedes.models.chat import Base
from archimedes.models.corpus_store import PaperRecord
from archimedes.models.strategy_passport_record import PassportPaperRef, StrategyPassportRecord
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def session() -> Session:
    """In-memory SQLite session with all ORM tables created."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    sess = SessionLocal()
    yield sess
    sess.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _add_corpus_paper(
    session: Session,
    arxiv_id: str,
    title: str,
) -> PaperRecord:
    row = PaperRecord(
        arxiv_id=arxiv_id,
        title=title,
        authors="[]",
        abstract="",
        primary_category="q-fin",
        categories="[]",
        published="2023-01-01",
        updated="2023-01-01",
        source="seed",
    )
    session.add(row)
    session.flush()
    return row


def _make_passport_record(
    session: Session,
    strategy_id: str,
    papers: list[dict],  # [{arxiv_id, title}]
) -> StrategyPassportRecord:
    """Create a StrategyPassportRecord with the given paper refs."""
    record = StrategyPassportRecord(
        id=strategy_id,
        generation_method="fusion",
        methodology_summary="Test fusion methodology",
        asset_universe="[]",
        position_sizing="equal_weight",
        rebalance_frequency="weekly",
        status="candidate",
        regime_tag="regime_neutral",
        passes_rigor_gate=False,
    )
    session.add(record)
    session.flush()

    for p in papers:
        ref = PassportPaperRef(
            passport_id=strategy_id,
            arxiv_id=p.get("arxiv_id"),
            title=p.get("title", ""),
            authors="[]",
        )
        session.add(ref)
    session.flush()
    return record


# ---------------------------------------------------------------------------
# _enrich_paper_titles_from_corpus (unit-level)
# ---------------------------------------------------------------------------


class TestEnrichPaperTitlesFromCorpus:
    """Unit tests for the standalone enrichment helper."""

    def test_returns_title_for_matching_arxiv_id(self, session: Session):
        _add_corpus_paper(session, "2301.00001", "Momentum Everywhere")

        from archimedes.api.strategies_routes import _enrich_paper_titles_from_corpus
        from archimedes.models.strategy_passport_record import PassportPaperRef

        refs = [PassportPaperRef(arxiv_id="2301.00001", title="", authors="[]")]
        result = _enrich_paper_titles_from_corpus(refs, session)
        assert result == {"2301.00001": "Momentum Everywhere"}

    def test_skips_refs_with_stored_titles(self, session: Session):
        _add_corpus_paper(session, "2301.00002", "Corpus Title")

        from archimedes.api.strategies_routes import _enrich_paper_titles_from_corpus
        from archimedes.models.strategy_passport_record import PassportPaperRef

        # Title already stored — should NOT appear in missing_ids query
        refs = [PassportPaperRef(arxiv_id="2301.00002", title="Stored Title", authors="[]")]
        result = _enrich_paper_titles_from_corpus(refs, session)
        # No missing ids → empty map
        assert result == {}

    def test_returns_empty_map_when_no_corpus_match(self, session: Session):
        from archimedes.api.strategies_routes import _enrich_paper_titles_from_corpus
        from archimedes.models.strategy_passport_record import PassportPaperRef

        refs = [PassportPaperRef(arxiv_id="2301.99999", title="", authors="[]")]
        result = _enrich_paper_titles_from_corpus(refs, session)
        assert result == {}

    def test_empty_refs_returns_empty_map(self, session: Session):
        from archimedes.api.strategies_routes import _enrich_paper_titles_from_corpus

        result = _enrich_paper_titles_from_corpus([], session)
        assert result == {}


# ---------------------------------------------------------------------------
# _passport_to_strategy_response (integration-level, SQLite)
# ---------------------------------------------------------------------------


class TestPassportToStrategyResponse:
    """Integration tests for the full API response builder with title enrichment."""

    def test_multi_paper_titles_enriched_from_corpus(self, session: Session):
        """Two fusion papers with empty titles get filled from corpus."""
        _add_corpus_paper(session, "2301.00001", "Momentum Everywhere")
        _add_corpus_paper(session, "2301.00002", "Volatility-Managed Portfolios")

        _make_passport_record(
            session,
            "fuse-001",
            [
                {"arxiv_id": "2301.00001", "title": ""},
                {"arxiv_id": "2301.00002", "title": ""},
            ],
        )
        session.flush()

        from archimedes.api.strategies_routes import _passport_to_strategy_response
        from archimedes.services.passport_loader import get_passport

        record = get_passport(session, "fuse-001")
        assert record is not None

        resp = _passport_to_strategy_response(record, session=session)

        assert len(resp.papers) == 2
        title_by_id = {p.arxiv_id: p.title for p in resp.papers}
        assert title_by_id["2301.00001"] == "Momentum Everywhere"
        assert title_by_id["2301.00002"] == "Volatility-Managed Portfolios"

    def test_stored_titles_take_priority_over_corpus(self, session: Session):
        """Pre-stored non-empty titles are NOT overwritten by corpus."""
        _add_corpus_paper(session, "2301.00003", "Corpus Title")

        _make_passport_record(
            session,
            "fuse-002",
            [{"arxiv_id": "2301.00003", "title": "Hand-curated Title"}],
        )
        session.flush()

        from archimedes.api.strategies_routes import _passport_to_strategy_response
        from archimedes.services.passport_loader import get_passport

        record = get_passport(session, "fuse-002")
        resp = _passport_to_strategy_response(record, session=session)

        assert resp.papers[0].title == "Hand-curated Title"

    def test_unknown_arxiv_id_falls_back_to_arxiv_id(self, session: Session):
        """When corpus has no matching row, the title falls back to the arxiv_id."""
        _make_passport_record(
            session,
            "fuse-003",
            [{"arxiv_id": "2301.99999", "title": ""}],
        )
        session.flush()

        from archimedes.api.strategies_routes import _passport_to_strategy_response
        from archimedes.services.passport_loader import get_passport

        record = get_passport(session, "fuse-003")
        resp = _passport_to_strategy_response(record, session=session)

        assert resp.papers[0].title == "2301.99999"

    def test_no_session_falls_back_to_arxiv_id(self, session: Session):
        """Without a session, enrichment is skipped and title falls back to arxiv_id."""
        _add_corpus_paper(session, "2301.00004", "Would be enriched")

        _make_passport_record(
            session,
            "fuse-004",
            [{"arxiv_id": "2301.00004", "title": ""}],
        )
        session.flush()

        from archimedes.api.strategies_routes import _passport_to_strategy_response
        from archimedes.services.passport_loader import get_passport

        record = get_passport(session, "fuse-004")
        resp = _passport_to_strategy_response(record, session=None)

        # No session → falls back to arxiv_id (the title is "" so _resolved_title
        # returns arxiv_id as the final fallback)
        assert resp.papers[0].title == "2301.00004"

    def test_papers_field_populated_for_multi_paper_strategy(self, session: Session):
        """The ``papers`` list on the response contains all source papers."""
        _add_corpus_paper(session, "2301.00010", "Paper Alpha")
        _add_corpus_paper(session, "2301.00011", "Paper Beta")
        _add_corpus_paper(session, "2301.00012", "Paper Gamma")

        _make_passport_record(
            session,
            "fuse-005",
            [
                {"arxiv_id": "2301.00010", "title": ""},
                {"arxiv_id": "2301.00011", "title": ""},
                {"arxiv_id": "2301.00012", "title": ""},
            ],
        )
        session.flush()

        from archimedes.api.strategies_routes import _passport_to_strategy_response
        from archimedes.services.passport_loader import get_passport

        record = get_passport(session, "fuse-005")
        resp = _passport_to_strategy_response(record, session=session)

        assert len(resp.papers) == 3
        assert resp.paper_title == "Paper Alpha"  # legacy field = enriched first paper title

    def test_single_paper_curated_strategy_unaffected(self, session: Session):
        """Single-paper curated strategies render correctly — no regression."""
        _make_passport_record(
            session,
            "cur-001",
            [{"arxiv_id": "0706.2631", "title": "Quantitative Trading with ML"}],
        )
        session.flush()

        from archimedes.api.strategies_routes import _passport_to_strategy_response
        from archimedes.services.passport_loader import get_passport

        record = get_passport(session, "cur-001")
        resp = _passport_to_strategy_response(record, session=session)

        assert len(resp.papers) == 1
        assert resp.papers[0].title == "Quantitative Trading with ML"
        assert resp.papers[0].arxiv_id == "0706.2631"
        # Legacy scalar fields still populated
        assert resp.paper_title == "Quantitative Trading with ML"
        assert resp.paper_arxiv_id == "0706.2631"


# ---------------------------------------------------------------------------
# #1184 — a zero-variance persisted series must not report as "pending"
# ---------------------------------------------------------------------------


def _add_backtest_row(
    session: Session,
    strategy_id: str,
    daily_returns: list[float],
) -> None:
    """Persist a backtest row carrying ``daily_returns`` in ``artifact_json``.

    The nesting mirrors what ``get_daily_returns`` actually parses
    (``backtest_repository.py`` — ``artifact["results"][i]["metrics"]["daily_returns"]``),
    not a shape invented for the test.
    """
    import json as _json

    from archimedes.models.backtest_store import BacktestResultRecord
    from archimedes.services.backtest_repository import SOURCE_PIPELINE_DSL_FUSION

    session.add(
        BacktestResultRecord(
            strategy_id=strategy_id,
            content_hash="deadbeef",
            # Required by the model, no default — these rows stand in for the
            # fusion pipeline's own writes, which is the path #1184 is about.
            source_pipeline=SOURCE_PIPELINE_DSL_FUSION,
            artifact_json=_json.dumps({"results": [{"metrics": {"daily_returns": daily_returns}}]}),
        )
    )
    session.flush()


def _passport_with_aggregate(
    session: Session,
    strategy_id: str,
    *,
    sharpe_ratio: float | None,
    passes: bool,
) -> StrategyPassportRecord:
    record = _make_passport_record(session, strategy_id, [{"arxiv_id": "2301.00009", "title": "T"}])
    record.sharpe_ratio = sharpe_ratio
    record.passes_rigor_gate = passes
    session.flush()
    return record


class TestZeroVarianceSeriesReportsDegenerate:
    """#1184: the generated/fusion passport read path graded the stored aggregate
    alone, so a flat persisted series — broken data, or a zero-trade backtest —
    surfaced as ``"pending"``. "Pending" is a claim ("not graded yet"), and for
    these rows it was false. These tests pin the four-state answer.

    Anti-vacuity: every assertion below is written against a specific mutation,
    named in its docstring. Reverting ``rigor_gate_status`` to the old
    ``"pending" if record.sharpe_ratio is None else ("pass" if ... else "fail")``
    expression fails each one.
    """

    def test_flat_series_with_no_stored_sharpe_is_degenerate_not_pending(self, session: Session):
        """MUTATION: the old three-way returns "pending" here — the exact defect."""
        from archimedes.api.strategies_routes import _passport_to_strategy_response

        record = _passport_with_aggregate(session, "flat-none", sharpe_ratio=None, passes=False)
        _add_backtest_row(session, "flat-none", [0.0] * 400)

        resp = _passport_to_strategy_response(record, session)

        assert resp.rigor_gate_status == "degenerate"
        assert resp.is_backtest_placeholder is False, "a persisted series exists, so this is graded, not ungraded"

    def test_flat_series_with_a_stored_sharpe_is_degenerate_not_fail(self, session: Session):
        """MUTATION: the old three-way returns "fail" here.

        Kept alongside the case above deliberately: a fix that only handled the
        NULL-sharpe shape would pass that test and fail this one.
        """
        from archimedes.api.strategies_routes import _passport_to_strategy_response

        record = _passport_with_aggregate(session, "flat-zero", sharpe_ratio=0.0, passes=False)
        _add_backtest_row(session, "flat-zero", [0.0] * 400)

        assert _passport_to_strategy_response(record, session).rigor_gate_status == "degenerate"

    def test_a_flat_series_can_never_report_as_passing(self, session: Session):
        """MUTATION: drop the ``and _rigor_status != "degenerate"`` conjunct.

        A stored ``passes_rigor_gate=True`` on a row with no variance to grade is
        contradictory data. The badge must not repeat the contradiction — this is
        the claims-integrity half of the fix, not the labelling half.
        """
        from archimedes.api.strategies_routes import _passport_to_strategy_response

        record = _passport_with_aggregate(session, "flat-claims-pass", sharpe_ratio=1.9, passes=True)
        _add_backtest_row(session, "flat-claims-pass", [0.0] * 400)

        resp = _passport_to_strategy_response(record, session)

        assert resp.rigor_gate_status == "degenerate"
        assert resp.passes_rigor_gate is False

    def test_series_flat_only_inside_the_oos_window_is_degenerate(self, session: Session):
        """MUTATION: drop the ``is_oos_zero_variance_series`` half of the OR.

        A strategy that stops trading partway through — one of the two causes
        #1184 names — leaves the full series non-constant, so the full-series
        predicate alone misses it while ``compute_oos_sharpe`` still returns None.
        """
        from archimedes.api.strategies_routes import _passport_to_strategy_response

        varied = [0.001 * ((i % 7) - 3) for i in range(280)]
        record = _passport_with_aggregate(session, "flat-oos", sharpe_ratio=None, passes=False)
        _add_backtest_row(session, "flat-oos", varied + [0.0] * 120)

        assert _passport_to_strategy_response(record, session).rigor_gate_status == "degenerate"

    def test_a_real_series_with_no_stored_sharpe_still_reports_pending(self, session: Session):
        """CONTROL. MUTATION: relabel unconditionally, ignoring the series.

        Without this, a fix that returned "degenerate" for everything would pass
        every test above. "Pending" has to survive where it is still true.
        """
        from archimedes.api.strategies_routes import _passport_to_strategy_response

        record = _passport_with_aggregate(session, "varied-none", sharpe_ratio=None, passes=False)
        _add_backtest_row(session, "varied-none", [0.001 * ((i % 11) - 5) for i in range(400)])

        resp = _passport_to_strategy_response(record, session)

        assert resp.rigor_gate_status == "pending"
        assert resp.is_backtest_placeholder is True

    def test_no_persisted_series_at_all_still_reports_pending(self, session: Session):
        """CONTROL: genuinely ungraded. An absent series is not a flat series."""
        from archimedes.api.strategies_routes import _passport_to_strategy_response

        record = _passport_with_aggregate(session, "no-series", sharpe_ratio=None, passes=False)

        assert _passport_to_strategy_response(record, session).rigor_gate_status == "pending"

    def test_a_real_series_that_passed_still_reports_pass(self, session: Session):
        """CONTROL: the fix must not disturb the verdict it was not about."""
        from archimedes.api.strategies_routes import _passport_to_strategy_response

        record = _passport_with_aggregate(session, "varied-pass", sharpe_ratio=1.4, passes=True)
        _add_backtest_row(session, "varied-pass", [0.001 * ((i % 11) - 5) for i in range(400)])

        resp = _passport_to_strategy_response(record, session)

        assert resp.rigor_gate_status == "pass"
        assert resp.passes_rigor_gate is True


class TestTheListPathAgreesWithTheDetailPath:
    """The list endpoints bulk-prefetch returns rather than loading per row.

    That is a second code path to the same claim, so it gets its own guard: a
    prefetch that silently handed every row an empty series would restore the
    bug on exactly the surface (Library, the leaderboard) where it is most
    visible, while every single-row test above kept passing.
    """

    def test_bulk_prefetch_reaches_the_same_verdict_as_the_single_row_load(self, session: Session):
        """MUTATION: make ``_passport_responses`` pass ``[]`` for every row."""
        from archimedes.api.strategies_routes import _passport_responses, _passport_to_strategy_response

        flat = _passport_with_aggregate(session, "bulk-flat", sharpe_ratio=None, passes=False)
        _add_backtest_row(session, "bulk-flat", [0.0] * 400)
        varied = _passport_with_aggregate(session, "bulk-varied", sharpe_ratio=None, passes=False)
        _add_backtest_row(session, "bulk-varied", [0.001 * ((i % 11) - 5) for i in range(400)])

        bulk = {r.id: r.rigor_gate_status for r in _passport_responses([flat, varied], session)}

        assert bulk == {"bulk-flat": "degenerate", "bulk-varied": "pending"}
        # And it agrees with the per-row path, which is the point of the guard.
        assert bulk["bulk-flat"] == _passport_to_strategy_response(flat, session).rigor_gate_status
        assert bulk["bulk-varied"] == _passport_to_strategy_response(varied, session).rigor_gate_status

    def test_an_explicitly_empty_prefetch_slice_is_not_treated_as_unknown(self, session: Session):
        """A row the cohort read found nothing for is ungraded, not degenerate.

        Guards the ``_RETURNS_NOT_PREFETCHED`` sentinel: collapsing "no prefetch
        happened" into "the prefetch found nothing" would make the list path
        re-query per row, quietly reintroducing the N+1 the bulk helper exists to
        avoid.
        """
        from archimedes.api.strategies_routes import _passport_to_strategy_response

        record = _passport_with_aggregate(session, "empty-slice", sharpe_ratio=None, passes=False)
        _add_backtest_row(session, "empty-slice", [0.0] * 400)

        # Explicit empty slice: do NOT go back to the DB, even though a row exists.
        assert _passport_to_strategy_response(record, session, []).rigor_gate_status == "pending"
