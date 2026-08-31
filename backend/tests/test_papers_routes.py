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


def _seed_author_fixture(session):
    """A corpus slice where author names and prose deliberately do not overlap.

    Every title/abstract here is neutral filler: no row contains the string
    "Harvey" (or any other surname) anywhere except in its `authors` column, so
    a hit can only have come from the author leg of the predicate. That is what
    makes `test_author_only_match_is_returned` fail when the author leg is
    removed rather than passing for an incidental prose reason.
    """
    _add_paper(
        session,
        "2601.20001",
        authors=["Campbell R. Harvey", "Yan Liu"],
        title="A neutral title about factor construction",
        abstract="A neutral abstract about factor construction.",
    )
    _add_paper(
        session,
        "2601.20002",
        authors=["Marcos Lopez de Prado"],
        title="Backtest overfitting under multiple testing",
        abstract="Deflated performance statistics for repeated trials.",
    )
    _add_paper(
        session,
        "2601.20003",
        authors=["Ada Lovelace"],
        title="Cross-sectional momentum in equities",
        abstract="Momentum sorted portfolios across a broad universe.",
    )
    session.commit()


class TestAuthorSearch:
    """Author matching — issue #1451 item 1.

    `PaperRecord.authors` is populated but was never part of the search
    predicate, so searching a researcher's name returned only the papers that
    happened to mention that name in the title or abstract. On the real corpus
    that is close to zero: authors are cited by name in *other* people's
    abstracts far more often than in their own.
    """

    def test_author_only_match_is_returned(self, session, monkeypatch):
        """THE REGRESSION: a paper whose AUTHOR matches but whose title and
        abstract do not contain the term must be returned."""
        from archimedes.api import papers_routes

        _seed_author_fixture(session)
        monkeypatch.setattr(papers_routes, "get_session", lambda: _ctx_session(session))

        result = asyncio.run(
            papers_routes.list_papers(page=1, page_size=20, category=None, search="Harvey", processed_only=True)
        )

        assert result["total"] == 1, "author search returned nothing — the author leg is missing"
        hit = result["papers"][0]
        assert hit["arxiv_id"] == "2601.20001"
        assert "Campbell R. Harvey" in hit["authors"]
        # Proof the hit came from the author leg and not from prose.
        assert "harvey" not in hit["title"].lower()
        assert "harvey" not in hit["abstract"].lower()

    def test_author_search_is_case_insensitive(self, session, monkeypatch):
        """ilike, not like — "lopez" must find "Lopez"."""
        from archimedes.api import papers_routes

        _seed_author_fixture(session)
        monkeypatch.setattr(papers_routes, "get_session", lambda: _ctx_session(session))

        result = asyncio.run(
            papers_routes.list_papers(page=1, page_size=20, category=None, search="lopez", processed_only=True)
        )
        assert result["total"] == 1
        assert result["papers"][0]["arxiv_id"] == "2601.20002"

    def test_title_and_abstract_legs_are_unchanged(self, session, monkeypatch):
        """The author leg is additive: prose matching still behaves as before."""
        from archimedes.api import papers_routes

        _seed_author_fixture(session)
        monkeypatch.setattr(papers_routes, "get_session", lambda: _ctx_session(session))

        result = asyncio.run(
            papers_routes.list_papers(page=1, page_size=20, category=None, search="momentum", processed_only=True)
        )
        assert result["total"] == 1
        assert result["papers"][0]["arxiv_id"] == "2601.20003"

    def test_author_search_still_discriminates(self, session, monkeypatch):
        """A name nobody in the fixture carries returns nothing."""
        from archimedes.api import papers_routes

        _seed_author_fixture(session)
        monkeypatch.setattr(papers_routes, "get_session", lambda: _ctx_session(session))

        result = asyncio.run(
            papers_routes.list_papers(page=1, page_size=20, category=None, search="Zzzznobody", processed_only=True)
        )
        assert result["total"] == 0


class TestAuthorLegGuard:
    """The author leg must reject the terms that would make it match everything.

    `authors` is serialised JSON, so a bare substring over it can match the
    serialisation instead of a name. Two classes of term do that; both are
    rejected before the leg is added to the predicate, while title/abstract
    matching for the same term is left untouched.
    """

    @pytest.mark.parametrize("term", ["a", "Li", " a ", ""])
    def test_short_terms_do_not_reach_the_author_leg(self, term):
        from archimedes.api import papers_routes

        assert papers_routes._author_search_pattern(term) is None

    @pytest.mark.parametrize("term", ['", "', '["', '"]', "], [", "Harvey\\", '{"a"'])
    def test_json_structural_terms_do_not_reach_the_author_leg(self, term):
        from archimedes.api import papers_routes

        assert papers_routes._author_search_pattern(term) is None

    def test_a_real_name_does_reach_the_author_leg(self):
        """Anti-vacuity: the guard must still let a genuine name through, or it
        is rejecting everything rather than rejecting the bad cases."""
        from archimedes.api import papers_routes

        assert papers_routes._author_search_pattern("Harvey") == "%Harvey%"
        assert papers_routes._author_search_pattern("  Lopez de Prado  ") == "%Lopez de Prado%"

    def test_like_wildcards_in_an_accepted_term_are_escaped(self):
        """A literal '%' must search for a percent sign, not match every row."""
        from archimedes.api import papers_routes

        assert papers_routes._author_search_pattern("a%b") == r"%a\%b%"
        assert papers_routes._author_search_pattern("a_b") == r"%a\_b%"

    def test_single_char_query_does_not_return_the_whole_corpus(self, session, monkeypatch):
        """End-to-end form of the anti-goal (#1451: "do NOT make the author leg
        match on a 1-2 character query").

        Both rows carry an "a" in their author names and no "a" anywhere in
        title or abstract, so an unguarded author leg returns the whole corpus
        for a one-letter query and a guarded one returns nothing.
        """
        from archimedes.api import papers_routes

        _add_paper(session, "2601.30001", authors=["Ada Lovelace"], title="Nine", abstract="Nine.")
        _add_paper(session, "2601.30002", authors=["Alan Turing"], title="Ten", abstract="Ten.")
        session.commit()
        monkeypatch.setattr(papers_routes, "get_session", lambda: _ctx_session(session))

        result = asyncio.run(
            papers_routes.list_papers(page=1, page_size=20, category=None, search="a", processed_only=True)
        )
        assert result["total"] == 0, "a one-character query reached the author leg and matched every row"

    def test_structural_query_does_not_return_the_whole_corpus(self, session, monkeypatch):
        """`", "` is the separator between every pair of serialised authors."""
        from archimedes.api import papers_routes

        _add_paper(session, "2601.30003", authors=["Ada Lovelace", "Alan Turing"], title="Zeta", abstract="Zeta.")
        _add_paper(session, "2601.30004", authors=["Grace Hopper", "Jean Bartik"], title="Eta", abstract="Eta.")
        session.commit()
        monkeypatch.setattr(papers_routes, "get_session", lambda: _ctx_session(session))

        result = asyncio.run(
            papers_routes.list_papers(page=1, page_size=20, category=None, search='", "', processed_only=True)
        )
        assert result["total"] == 0, "a JSON-structural term matched the serialisation of every author list"
