"""Hermetic unit tests for strategy_store — no network, no Redis, no external DB."""

from __future__ import annotations

import json

import pytest
from archimedes.models.chat import Base
from archimedes.models.strategy_store import (
    StrategyRecord,
    _compute_content_hash,
    resolve_source_papers,
    strategies_by_paper,
    upsert_strategy,
)
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


PAPERS_A = [{"arxiv_id": "2401.12345", "sha256": "abc123"}]
PAPERS_B = [
    {"arxiv_id": "2401.12345", "sha256": "abc123"},
    {"arxiv_id": "2401.99999", "sha256": "def456"},
]


class TestContentHash:
    def test_deterministic(self):
        h1 = _compute_content_hash("fusion", "Test", "Thesis", PAPERS_A, ["SPY"])
        h2 = _compute_content_hash("fusion", "Test", "Thesis", PAPERS_A, ["SPY"])
        assert h1 == h2

    def test_different_inputs_different_hash(self):
        h1 = _compute_content_hash("fusion", "Test", "Thesis", PAPERS_A, ["SPY"])
        h2 = _compute_content_hash("architect", "Test", "Thesis", PAPERS_A, ["SPY"])
        assert h1 != h2

    def test_source_paper_order_irrelevant(self):
        h1 = _compute_content_hash("fusion", "T", "X", PAPERS_B, ["SPY"])
        reversed_papers = list(reversed(PAPERS_B))
        h2 = _compute_content_hash("fusion", "T", "X", reversed_papers, ["SPY"])
        assert h1 == h2


class TestUpsertStrategy:
    def test_insert_new(self, session):
        r = upsert_strategy(
            session,
            generation_method="fusion",
            strategy_name="Test Strategy",
            thesis="A thesis",
            source_papers=PAPERS_A,
            asset_universe=["SPY"],
        )
        assert r.id
        assert r.generation_method == "fusion"
        assert r.status == "candidate"
        assert json.loads(r.source_papers) == PAPERS_A

    def test_idempotent_same_content(self, session):
        r1 = upsert_strategy(
            session,
            generation_method="fusion",
            strategy_name="T",
            thesis="X",
            source_papers=PAPERS_A,
            asset_universe=["SPY"],
        )
        r2 = upsert_strategy(
            session,
            generation_method="fusion",
            strategy_name="T",
            thesis="X",
            source_papers=PAPERS_A,
            asset_universe=["SPY"],
        )
        assert r1.id == r2.id
        assert session.query(StrategyRecord).count() == 1

    def test_different_content_creates_new(self, session):
        r1 = upsert_strategy(
            session,
            generation_method="fusion",
            strategy_name="T1",
            thesis="X",
            source_papers=PAPERS_A,
            asset_universe=["SPY"],
        )
        r2 = upsert_strategy(
            session,
            generation_method="architect",
            strategy_name="T2",
            thesis="Y",
            source_papers=PAPERS_B,
            asset_universe=["TSLA"],
        )
        assert r1.id != r2.id
        assert session.query(StrategyRecord).count() == 2

    def test_rigor_verdict_transitions_to_live(self, session):
        r = upsert_strategy(
            session,
            generation_method="fusion",
            strategy_name="T",
            thesis="X",
            source_papers=PAPERS_A,
            asset_universe=["SPY"],
            rigor_verdict={"passing": True, "dsr": 1.5, "pbo": 0.1},
        )
        assert r.status == "live"

    def test_rigor_verdict_transitions_to_rejected_if_not_passing(self, session):
        """Per issue #133: failed rigor must transition to a distinguishable
        'rejected' status — NOT silently dropped at 'candidate'. The honesty
        wedge depends on failed strategies being visible failures rather than
        looking indistinguishable from un-evaluated candidates."""
        r = upsert_strategy(
            session,
            generation_method="fusion",
            strategy_name="T",
            thesis="X",
            source_papers=PAPERS_A,
            asset_universe=["SPY"],
            rigor_verdict={"passing": False, "dsr": 0.3, "pbo": 0.9},
        )
        assert r.status == "rejected"

    def test_late_rigor_verdict_updates_existing(self, session):
        r1 = upsert_strategy(
            session,
            generation_method="fusion",
            strategy_name="T",
            thesis="X",
            source_papers=PAPERS_A,
            asset_universe=["SPY"],
        )
        assert r1.status == "candidate"
        r2 = upsert_strategy(
            session,
            generation_method="fusion",
            strategy_name="T",
            thesis="X",
            source_papers=PAPERS_A,
            asset_universe=["SPY"],
            rigor_verdict={"passing": True, "dsr": 2.0},
        )
        assert r2.status == "live"
        assert session.query(StrategyRecord).count() == 1


class TestResolveSourcePapers:
    def test_returns_source_papers(self, session):
        upsert_strategy(
            session,
            generation_method="fusion",
            strategy_name="T",
            thesis="X",
            source_papers=PAPERS_B,
            asset_universe=["SPY"],
        )
        record = session.query(StrategyRecord).first()
        papers = resolve_source_papers(session, record.id)
        assert len(papers) == 2
        assert papers[0]["arxiv_id"] == "2401.12345"

    def test_unknown_strategy_returns_empty(self, session):
        assert resolve_source_papers(session, "nonexistent") == []


class TestStrategiesByPaper:
    def test_finds_citing_strategies(self, session):
        upsert_strategy(
            session,
            generation_method="fusion",
            strategy_name="T1",
            thesis="X",
            source_papers=PAPERS_B,
            asset_universe=["SPY"],
        )
        upsert_strategy(
            session,
            generation_method="architect",
            strategy_name="T2",
            thesis="Y",
            source_papers=PAPERS_A,
            asset_universe=["TSLA"],
        )
        results = strategies_by_paper(session, "2401.12345")
        assert len(results) == 2

    def test_no_match(self, session):
        assert strategies_by_paper(session, "nonexistent") == []

    def test_empty_arxiv_id_returns_empty(self, session):
        # Guard: an empty id must not LIKE-match every row.
        assert strategies_by_paper(session, "") == []

    def test_substring_false_positive_excluded(self, session):
        # The DB LIKE prefilter matches substrings; the exact JSON check must
        # then exclude an id that is only a *substring* of a cited id.
        upsert_strategy(
            session,
            generation_method="fusion",
            strategy_name="LongId",
            thesis="X",
            source_papers=[{"arxiv_id": "2401.00120", "sha256": "z"}],
            asset_universe=["SPY"],
        )
        # "2401.0012" is a substring of "2401.00120" but not an exact citation.
        assert strategies_by_paper(session, "2401.0012") == []
        # The exact id still matches.
        assert len(strategies_by_paper(session, "2401.00120")) == 1


class TestToDict:
    def test_roundtrip(self, session):
        r = upsert_strategy(
            session,
            generation_method="fusion",
            strategy_name="Test Strat",
            thesis="A thesis about things",
            source_papers=PAPERS_A,
            asset_universe=["SPY", "TSLA"],
            risk_profile="aggressive",
        )
        d = r.to_dict()
        assert d["generation_method"] == "fusion"
        assert d["source_papers"] == PAPERS_A
        assert d["asset_universe"] == ["SPY", "TSLA"]
        assert d["risk_profile"] == "aggressive"
        assert d["status"] == "candidate"
