"""Regression tests for GET /api/corpus/overview (issue #854 finding #5).

Header badges + Overview tab previously read "0 papers · 0 categories" while
the Catalog tab (papers_routes.py) listed all 10,000 real papers, because
`corpus_overview()` scoped `total_papers` / category / year counts to only
the KB-pipeline-*processed* subset (`cluster_id IS NOT NULL`) — which is
empty until the KB pipeline's first clustering run. Catalog never hits that
emptiness because `list_papers()` falls back to the full corpus whenever the
processed-only query returns zero rows. This test locks in that
`corpus_overview()` now reports the same full-catalog totals Catalog uses,
while genuinely-KB-pipeline-only facts (graph/kg endpoints) are untouched.

Hermetic — no network, no Redis, in-memory SQLite only.
"""

from __future__ import annotations

import asyncio

import pytest
from archimedes.models.chat import Base
from archimedes.models.corpus_store import PaperRecord
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


class _CtxSession:
    """Context-manager wrapper so `with get_session() as s:` works."""

    def __init__(self, session):
        self._session = session

    def __enter__(self):
        return self._session

    def __exit__(self, *args):
        pass


def _ctx_session(session):
    return _CtxSession(session)


def _add_paper(session, arxiv_id, category, year, *, cluster_id=None):
    session.add(
        PaperRecord(
            arxiv_id=arxiv_id,
            title=f"Paper {arxiv_id}",
            abstract="Abstract",
            primary_category=category,
            categories=f'["{category}"]',
            published=f"{year}-01-01",
            updated=f"{year}-01-01",
            source="seed",
            cluster_id=cluster_id,
        )
    )


class TestCorpusOverviewUnprocessedCatalog:
    """The realistic prod state: 10K (here, 3) ingested papers, 0 KB-processed."""

    def test_total_papers_reflects_full_catalog_not_zero(self, session, monkeypatch):
        from archimedes.api import corpus_routes

        _add_paper(session, "2601.00001", "q-fin.PM", 2026)
        _add_paper(session, "2601.00002", "q-fin.TR", 2026)
        _add_paper(session, "2601.00003", "cs.LG", 2025)
        session.commit()

        monkeypatch.setattr("archimedes.db.get_session", lambda: _ctx_session(session))

        result = asyncio.run(corpus_routes.corpus_overview())

        # Before the fix these were all 0/0/0/empty despite 3 real rows —
        # matching the live "0 papers · 0 categories" header bug.
        assert result["total_papers"] == 3
        assert len(result["categories"]) == 3
        assert len(result["year_distribution"]) == 2

    def test_category_and_year_counts_match_full_catalog(self, session, monkeypatch):
        from archimedes.api import corpus_routes

        _add_paper(session, "2601.00001", "q-fin.PM", 2026)
        _add_paper(session, "2601.00002", "q-fin.PM", 2026)
        _add_paper(session, "2601.00003", "cs.LG", 2025)
        session.commit()

        monkeypatch.setattr("archimedes.db.get_session", lambda: _ctx_session(session))

        result = asyncio.run(corpus_routes.corpus_overview())

        by_name = {c["name"]: c["count"] for c in result["categories"]}
        assert by_name == {"q-fin.PM": 2, "cs.LG": 1}

        by_year = {y["year"]: y["count"] for y in result["year_distribution"]}
        assert by_year == {2026: 2, 2025: 1}

    def test_processed_papers_still_reports_kb_pipeline_subset(self, session, monkeypatch):
        """The legacy KB-pipeline fields must still reflect only clustered rows —
        this fix broadens the UI-facing catalog totals, not the KB-processing
        signal itself."""
        from archimedes.api import corpus_routes

        _add_paper(session, "2601.00001", "q-fin.PM", 2026, cluster_id="c1")
        _add_paper(session, "2601.00002", "q-fin.TR", 2026)
        session.commit()

        monkeypatch.setattr("archimedes.db.get_session", lambda: _ctx_session(session))

        result = asyncio.run(corpus_routes.corpus_overview())

        assert result["total_papers"] == 2
        assert result["processed_papers"] == 1
        assert result["metadata_only_papers"] == 1

    def test_empty_db_still_reports_zero_honestly(self, session, monkeypatch):
        """No papers ingested at all → zero is the honest answer, not a bug."""
        from archimedes.api import corpus_routes

        monkeypatch.setattr("archimedes.db.get_session", lambda: _ctx_session(session))

        result = asyncio.run(corpus_routes.corpus_overview())

        assert result["total_papers"] == 0
        assert result["categories"] == []
        assert result["year_distribution"] == []
