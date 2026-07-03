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


def _add_paper(session, arxiv_id, *, authors, cluster_id=None):
    session.add(
        PaperRecord(
            arxiv_id=arxiv_id,
            title=f"Paper {arxiv_id}",
            abstract="Abstract",
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
