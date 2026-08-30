"""GET /api/strategies/generated must cite the PAPER, not name the strategy.

Hermetic (tmp-sqlite, no .env / network / Redis); DB fixture copies the
`_use_tmp_db` pattern from test_strategy_ownership.py.

The defect: ``StrategyRecord.to_dict()`` returns ``source_papers`` exactly as
stored — an ``arxiv_id`` and, usually, no ``title`` — so the Library card had no
paper title to render and rendered the generated STRATEGY NAME in the
cited-paper slot instead (``coerceGenerated``'s
``paper_title: row.strategy_name``, under a panel headed "Source paper"). That
is a fabricated citation, which is worse than a missing one: a reader cannot
tell it apart from a real one.

The route now resolves real titles against the same ``papers`` corpus table the
passport path reads, in ONE query for the whole page, and leaves
``resolved_title`` **None** when the corpus genuinely doesn't know — the
frontend renders that as "title unavailable — arXiv:<id>", never as a name.
"""

from __future__ import annotations

import time

import archimedes.db as db
import pytest
from archimedes.api.auth_siwe import _COOKIE_NAME, _sign_session
from archimedes.api.strategies_routes import (
    _corpus_paper_meta,
    _resolve_source_papers,
    _year_from_published,
)
from httpx import ASGITransport, AsyncClient

_W_OWNER = "0xAbC0000000000000000000000000000000000001"

_STRATEGY_NAME = "Adaptive Cross-Sectional Momentum Overlay"
_REAL_TITLE = "Deep Learning Statistical Arbitrage"


@pytest.fixture(autouse=True)
def _use_tmp_db(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    url = f"sqlite:///{tmp_path / 'citations.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    eng = create_engine(url, connect_args={"check_same_thread": False})
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=eng, autocommit=False, autoflush=False))
    db.init_db()
    yield


def _siwe_cookies(wallet: str) -> dict[str, str]:
    return {_COOKIE_NAME: _sign_session(wallet, time.time())}


def _mk_strategy(sid: str, *, source_papers: str, name: str = _STRATEGY_NAME):
    from archimedes.models.strategy_store import StrategyRecord

    with db.get_session() as session:
        session.add(
            StrategyRecord(
                id=sid,
                content_hash=("0x" + sid).ljust(66, "0"),
                generation_method="fusion",
                source_papers=source_papers,
                strategy_name=name,
                thesis="test thesis",
                asset_universe="[]",
                risk_profile="moderate",
                status="candidate",
                is_example=False,
                owner_wallet=_W_OWNER.lower(),
                is_published=False,
            )
        )
        session.commit()


def _mk_paper(arxiv_id: str, *, title: str, published: str = "2021-06-14"):
    from archimedes.models.corpus_store import PaperRecord

    with db.get_session() as session:
        session.add(PaperRecord(arxiv_id=arxiv_id, title=title, published=published))
        session.commit()


# ── Pure helpers ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("published", "expected"),
    [
        ("2021-06-14", 2021),
        ("2021", 2021),
        ("2021-06-14T00:00:00Z", 2021),
        ("", None),
        (None, None),
        ("n/a", None),
        ("21-06", None),  # not a 4-digit leading year — guessing one is a lie
    ],
)
def test_year_from_published(published, expected):
    assert _year_from_published(published) == expected


def test_resolve_source_papers_prefers_the_stored_title():
    resolved = _resolve_source_papers(
        [{"arxiv_id": "2106.04028", "title": "Stored Title"}],
        {"2106.04028": {"title": "Corpus Title", "year": 2021}},
    )
    assert resolved[0]["resolved_title"] == "Stored Title"
    assert resolved[0]["resolved_year"] == 2021


def test_resolve_source_papers_falls_back_to_the_corpus():
    resolved = _resolve_source_papers(
        [{"arxiv_id": "2106.04028", "title": "   "}],
        {"2106.04028": {"title": _REAL_TITLE, "year": 2021}},
    )
    assert resolved[0]["resolved_title"] == _REAL_TITLE


def test_resolve_source_papers_leaves_none_when_unresolvable():
    """The load-bearing case: with nothing to cite, the field is None. The
    frontend turns None into 'title unavailable — arXiv:<id>'. Anything else
    here — most of all the strategy's own name — would be a fabrication."""
    resolved = _resolve_source_papers([{"arxiv_id": "9999.99999"}], {})
    assert resolved[0]["resolved_title"] is None
    assert resolved[0]["resolved_year"] is None
    assert resolved[0]["arxiv_id"] == "9999.99999"


def test_resolve_source_papers_survives_a_malformed_entry():
    assert _resolve_source_papers(["not-a-dict", None], {}) == []
    assert _resolve_source_papers(None, {}) == []


# ── Corpus lookup is batched, not N+1 ───────────────────────────────────────


def test_corpus_paper_meta_is_one_query_for_the_whole_page():
    """The page's citations resolve in a SINGLE papers-table query. A per-row
    lookup would put a query on every Library render, scaling with page size."""
    from sqlalchemy import event

    for i in range(8):
        _mk_paper(f"2106.0400{i}", title=f"Paper {i}", published="2021-06-14")

    statements: list[str] = []

    def _record(_conn, _cursor, statement, *_rest):
        statements.append(statement)

    with db.get_session() as session:
        event.listen(session.bind, "before_cursor_execute", _record)
        try:
            meta = _corpus_paper_meta([f"2106.0400{i}" for i in range(8)], session)
        finally:
            event.remove(session.bind, "before_cursor_execute", _record)

    assert len(meta) == 8
    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    assert len(selects) == 1, f"expected 1 batched SELECT, got {len(selects)}: {selects}"


def test_corpus_paper_meta_short_circuits_on_no_ids():
    with db.get_session() as session:
        assert _corpus_paper_meta([], session) == {}
        assert _corpus_paper_meta([None, ""], session) == {}


def test_corpus_paper_meta_returns_empty_map_on_db_error():
    """Non-fatal by contract: a citation decoration must never take down a list
    read. The caller then renders the honest 'unavailable' fallback."""

    class _Boom:
        def query(self, *_a, **_k):
            raise RuntimeError("db down")

    assert _corpus_paper_meta(["2106.04028"], _Boom()) == {}


# ── The route ───────────────────────────────────────────────────────────────


async def test_generated_route_serves_the_resolved_paper_title():
    _mk_paper("2106.04028", title=_REAL_TITLE, published="2021-06-14")
    _mk_strategy("cit00000000000001", source_papers='[{"arxiv_id": "2106.04028"}]')

    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/strategies/generated", cookies=_siwe_cookies(_W_OWNER))

    assert resp.status_code == 200
    papers = resp.json()["strategies"][0]["source_papers"]
    assert papers[0]["resolved_title"] == _REAL_TITLE
    assert papers[0]["resolved_year"] == 2021


async def test_generated_route_never_serves_the_strategy_name_as_a_title():
    """The regression that matters: with the paper absent from the corpus, the
    resolved title stays None. It must NOT quietly become the strategy name —
    which is exactly what the pre-fix Library card printed."""
    _mk_strategy("cit00000000000002", source_papers='[{"arxiv_id": "9999.99999"}]')

    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/strategies/generated", cookies=_siwe_cookies(_W_OWNER))

    row = resp.json()["strategies"][0]
    assert row["strategy_name"] == _STRATEGY_NAME  # still there, in its own field
    papers = row["source_papers"]
    assert papers[0]["resolved_title"] is None
    assert _STRATEGY_NAME not in str(papers)


async def test_generated_route_resolves_titles_across_the_whole_page():
    """Every row on the page is resolved, not just the first — the batch must
    cover the page, and a row whose paper is missing must not poison the rest."""
    _mk_paper("2106.04028", title=_REAL_TITLE, published="2021-06-14")
    _mk_paper("2202.11111", title="Another Real Paper", published="2022")
    _mk_strategy("cit00000000000003", source_papers='[{"arxiv_id": "2106.04028"}]')
    _mk_strategy("cit00000000000004", source_papers='[{"arxiv_id": "2202.11111"}]')
    _mk_strategy("cit00000000000005", source_papers='[{"arxiv_id": "0000.00000"}]')

    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/strategies/generated", cookies=_siwe_cookies(_W_OWNER))

    by_id = {r["id"]: r["source_papers"][0] for r in resp.json()["strategies"]}
    assert by_id["cit00000000000003"]["resolved_title"] == _REAL_TITLE
    assert by_id["cit00000000000004"]["resolved_title"] == "Another Real Paper"
    assert by_id["cit00000000000004"]["resolved_year"] == 2022
    assert by_id["cit00000000000005"]["resolved_title"] is None


async def test_generated_route_handles_a_row_with_no_source_papers():
    _mk_strategy("cit00000000000006", source_papers="[]")

    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/strategies/generated", cookies=_siwe_cookies(_W_OWNER))

    assert resp.status_code == 200
    assert resp.json()["strategies"][0]["source_papers"] == []
