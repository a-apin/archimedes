"""Regression tests for GET /api/papers/ (issue #854 finding #9).

Corpus Catalog's Authors column rendered "—" for every row because the
`processed_only=True` (default) fallback — triggered whenever no paper has
been through KB-pipeline clustering yet — routed through
`strategy_fusion.load_corpus()`'s reduced `CorpusPaper` dataclass, which has
no `authors` field at all. The DB itself carries real author data (see
`PaperRecord.authors` / `to_dict()`); this test locks in that the fallback
now reads directly from the DB (preserving authors) instead of losing the
field through the reduced dataclass, and only drops to the truly-author-less
file manifest when the DB itself is empty.

Hermetic — no network, no Redis, in-memory SQLite only.
"""

from __future__ import annotations

import asyncio
import json

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


def _add_paper(session, arxiv_id, *, authors, cluster_id=None, title=None, abstract="Abstract"):
    session.add(
        PaperRecord(
            arxiv_id=arxiv_id,
            title=title if title is not None else f"Paper {arxiv_id}",
            abstract=abstract,
            authors=json.dumps(authors),
            primary_category="q-fin.PM",
            categories='["q-fin.PM"]',
            published="2026-01-01",
            updated="2026-01-01",
            source="seed",
            cluster_id=cluster_id,
        )
    )


class TestListPapersAuthorsFallback:
    def test_unprocessed_catalog_still_returns_real_authors(self, session, monkeypatch):
        """No paper is KB-processed (cluster_id all null) — the fallback must
        still surface the real author names from the DB, not '—' for every
        row."""
        from archimedes.api import papers_routes

        _add_paper(session, "2601.00001", authors=["Ada Lovelace", "Alan Turing"])
        _add_paper(session, "2601.00002", authors=["Grace Hopper"])
        session.commit()

        monkeypatch.setattr(papers_routes, "get_session", lambda: _ctx_session(session))

        result = asyncio.run(
            papers_routes.list_papers(page=1, page_size=20, category=None, search=None, processed_only=True)
        )

        assert result["total"] == 2
        authors_by_id = {p["arxiv_id"]: p["authors"] for p in result["papers"]}
        assert authors_by_id["2601.00001"] == ["Ada Lovelace", "Alan Turing"]
        assert authors_by_id["2601.00002"] == ["Grace Hopper"]

    def test_processed_papers_take_normal_path_unaffected(self, session, monkeypatch):
        """When KB-processed rows exist, the primary (non-fallback) path is
        unchanged and still carries authors."""
        from archimedes.api import papers_routes

        _add_paper(session, "2601.00001", authors=["Ada Lovelace"], cluster_id="c1")
        session.commit()

        monkeypatch.setattr(papers_routes, "get_session", lambda: _ctx_session(session))

        result = asyncio.run(
            papers_routes.list_papers(page=1, page_size=20, category=None, search=None, processed_only=True)
        )

        assert result["total"] == 1
        assert result["papers"][0]["authors"] == ["Ada Lovelace"]

    def test_empty_db_falls_back_to_file_manifest(self, session, monkeypatch, tmp_path):
        """DB has zero rows — falls through to the file-manifest loader (which
        genuinely has no author data), not an error."""
        from archimedes.api import papers_routes

        monkeypatch.setattr(papers_routes, "get_session", lambda: _ctx_session(session))
        # Point the file-fallback at an empty manifest dir so `load_corpus()`
        # resolves deterministically to zero papers rather than picking up
        # whatever manifest happens to exist on this machine.
        monkeypatch.setenv("ARCHIMEDES_CORPUS_MANIFEST", str(tmp_path / "does-not-exist.jsonl"))

        result = asyncio.run(
            papers_routes.list_papers(page=1, page_size=20, category=None, search=None, processed_only=True)
        )

        assert result["total"] == 0
        assert result["papers"] == []


class TestCatalogSearchOnUnprocessedCorpus:
    """Corpus search returned zero results for every term, always.

    `processed_only` defaults to True and filters `cluster_id IS NOT NULL`. The
    KB pipeline has never run in production, so that predicate matches zero
    rows. The two `total == 0` fallbacks that rescue the catalog are both
    guarded by `and not category and not search` — deliberately, so a search
    could not be silently widened. The combination meant: empty search box →
    full 10,000-paper catalog; type anything → nothing, for every term.

    Verified against production before the fix:
        /api/papers/?search=momentum                        → total=0
        /api/papers/?search=momentum&processed_only=false   → total=119
    """

    def test_search_returns_matches_when_nothing_is_kb_processed(self, session, monkeypatch):
        """THE REGRESSION: every cluster_id is null and a search must still hit."""
        from archimedes.api import papers_routes

        _add_paper(session, "2601.10001", authors=["A"], title="Cross-sectional momentum in equities")
        _add_paper(session, "2601.10002", authors=["B"], title="Volatility targeting and drawdown")
        _add_paper(session, "2601.10003", authors=["C"], title="Unrelated microstructure paper")
        session.commit()

        monkeypatch.setattr(papers_routes, "get_session", lambda: _ctx_session(session))

        result = asyncio.run(
            papers_routes.list_papers(page=1, page_size=20, category=None, search="momentum", processed_only=True)
        )

        assert result["total"] == 1, "search over an unprocessed corpus returned nothing"
        assert result["papers"][0]["arxiv_id"] == "2601.10001"

    def test_search_matches_the_abstract_too(self, session, monkeypatch):
        from archimedes.api import papers_routes

        _add_paper(
            session,
            "2601.10004",
            authors=["A"],
            title="A title with no keyword",
            abstract="This abstract mentions momentum explicitly.",
        )
        session.commit()
        monkeypatch.setattr(papers_routes, "get_session", lambda: _ctx_session(session))

        result = asyncio.run(
            papers_routes.list_papers(page=1, page_size=20, category=None, search="momentum", processed_only=True)
        )
        assert result["total"] == 1

    def test_search_still_discriminates(self, session, monkeypatch):
        """The fix must not turn search into 'return everything'."""
        from archimedes.api import papers_routes

        _add_paper(session, "2601.10005", authors=["A"], title="Cross-sectional momentum")
        _add_paper(session, "2601.10006", authors=["B"], title="Volatility targeting")
        session.commit()
        monkeypatch.setattr(papers_routes, "get_session", lambda: _ctx_session(session))

        result = asyncio.run(
            papers_routes.list_papers(page=1, page_size=20, category=None, search="zzzznonsense", processed_only=True)
        )
        assert result["total"] == 0
        assert result["papers"] == []

    def test_processed_only_still_filters_once_the_pipeline_has_run(self, session, monkeypatch):
        """Intent preserved: where processed rows DO exist, the filter applies."""
        from archimedes.api import papers_routes

        _add_paper(session, "2601.10007", authors=["A"], title="Momentum, processed", cluster_id=3)
        _add_paper(session, "2601.10008", authors=["B"], title="Momentum, unprocessed")
        session.commit()
        monkeypatch.setattr(papers_routes, "get_session", lambda: _ctx_session(session))

        result = asyncio.run(
            papers_routes.list_papers(page=1, page_size=20, category=None, search="momentum", processed_only=True)
        )
        assert result["total"] == 1
        assert result["papers"][0]["arxiv_id"] == "2601.10007"

    def test_category_filter_also_works_on_an_unprocessed_corpus(self, session, monkeypatch):
        """`category` was guarded by the same `and not category` clause."""
        from archimedes.api import papers_routes

        _add_paper(session, "2601.10009", authors=["A"], title="Anything")
        session.commit()
        monkeypatch.setattr(papers_routes, "get_session", lambda: _ctx_session(session))

        result = asyncio.run(
            papers_routes.list_papers(page=1, page_size=20, category="q-fin.PM", search=None, processed_only=True)
        )
        assert result["total"] == 1
