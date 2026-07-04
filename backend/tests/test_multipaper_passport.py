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
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from archimedes.models.chat import Base
from archimedes.models.corpus_store import PaperRecord
from archimedes.models.strategy_passport_record import PassportPaperRef, StrategyPassportRecord


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
