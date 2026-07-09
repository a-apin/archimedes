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

# A validated DSL spec (same shape strategy_dsl.FABER_2007_SPEC exercises,
# reused here rather than importing the module — this file's tests are about
# persistence, not DSL validation itself).
DSL_SPEC = {
    "name": "SMA-200 Tactical Allocation",
    "asset_universe": ["SPY"],
    "rebalance_frequency": "monthly",
    "entry": {"gt": ["close", "sma_200"]},
    "exit": {"lt": ["close", "sma_200"]},
    "position_sizing": {"type": "full_invested_when_in_market"},
    "source_arxiv_ids": ["0706.1497"],
    "look_ahead_safe": True,
}


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

    def test_no_spec_is_null(self, session):
        """A candidate persisted without a DSL spec (fixture/buy-and-hold
        path) round-trips strategy_spec as None, not an empty dict."""
        r = upsert_strategy(
            session,
            generation_method="portfolio_agent_streaming",
            strategy_name="No Spec",
            thesis="X",
            source_papers=PAPERS_A,
            asset_universe=["SPY"],
        )
        assert r.strategy_spec is None
        assert r.to_dict()["strategy_spec"] is None

    def test_corrupt_spec_json_returns_none_not_raise(self, session):
        """Copilot review on #1076: to_dict() must not raise
        json.JSONDecodeError on a corrupt strategy_spec column — a single bad
        row shouldn't 500 an API response (api/strategies_routes.py calls
        record.to_dict() directly on a passport lookup). Decodes
        defensively, returning None — the same fallback convention as
        VaultMetadata.get_strategy_ids() (models/chat.py)."""
        r = upsert_strategy(
            session,
            generation_method="debate",
            strategy_name="Corrupt Spec",
            thesis="X",
            source_papers=PAPERS_A,
            asset_universe=["SPY"],
            strategy_spec=DSL_SPEC,
        )
        # Simulate a corrupt DB row — bypasses upsert_strategy's json.dumps,
        # which would never itself write invalid JSON.
        r.strategy_spec = "{not valid json at all"
        d = r.to_dict()  # must not raise
        assert d["strategy_spec"] is None


class TestStrategySpecPersistence:
    """Rebalancer decouple (Part A #1): strategy_spec persistence + backfill."""

    def test_insert_with_spec_persists_and_round_trips(self, session):
        r = upsert_strategy(
            session,
            generation_method="debate",
            strategy_name="Faber SMA200 Clone",
            thesis="X",
            source_papers=PAPERS_A,
            asset_universe=["SPY"],
            strategy_spec=DSL_SPEC,
        )
        assert r.strategy_spec is not None
        assert json.loads(r.strategy_spec) == DSL_SPEC
        assert r.to_dict()["strategy_spec"] == DSL_SPEC

    def test_insert_without_spec_is_null(self, session):
        r = upsert_strategy(
            session,
            generation_method="debate",
            strategy_name="No Spec Debate",
            thesis="X",
            source_papers=PAPERS_A,
            asset_universe=["SPY"],
        )
        assert r.strategy_spec is None

    def test_insert_with_explicit_empty_dict_spec_is_persisted_not_dropped(self, session):
        """Copilot review on #1076: the insert branch used a truthiness check
        (`if strategy_spec else None`), which silently dropped an explicitly-
        provided empty dict ({}) by storing NULL — treating "provided but
        empty" the same as "not provided at all". `is not None` (matching the
        backfill branch a few lines up) must persist it as the literal "{}",
        not NULL."""
        r = upsert_strategy(
            session,
            generation_method="debate",
            strategy_name="Empty Spec Explicitly Provided",
            thesis="X",
            source_papers=PAPERS_A,
            asset_universe=["SPY"],
            strategy_spec={},
        )
        assert r.strategy_spec == "{}"
        assert r.strategy_spec is not None

    def test_content_hash_match_backfills_missing_spec(self, session):
        """Same content, first call with no spec, second call WITH a spec —
        the existing row is backfilled (never a duplicate row)."""
        r1 = upsert_strategy(
            session,
            generation_method="debate",
            strategy_name="Backfill Me",
            thesis="X",
            source_papers=PAPERS_A,
            asset_universe=["SPY"],
        )
        assert r1.strategy_spec is None

        r2 = upsert_strategy(
            session,
            generation_method="debate",
            strategy_name="Backfill Me",
            thesis="X",
            source_papers=PAPERS_A,
            asset_universe=["SPY"],
            strategy_spec=DSL_SPEC,
        )
        assert r1.id == r2.id
        assert session.query(StrategyRecord).count() == 1
        assert json.loads(r2.strategy_spec) == DSL_SPEC

    def test_content_hash_match_never_overwrites_existing_spec(self, session):
        """A row that already has a spec keeps it — a later upsert (even with
        a DIFFERENT spec dict) never clobbers the persisted one."""
        r1 = upsert_strategy(
            session,
            generation_method="debate",
            strategy_name="Keep My Spec",
            thesis="X",
            source_papers=PAPERS_A,
            asset_universe=["SPY"],
            strategy_spec=DSL_SPEC,
        )
        other_spec = {**DSL_SPEC, "name": "A different spec"}
        r2 = upsert_strategy(
            session,
            generation_method="debate",
            strategy_name="Keep My Spec",
            thesis="X",
            source_papers=PAPERS_A,
            asset_universe=["SPY"],
            strategy_spec=other_spec,
        )
        assert r1.id == r2.id
        assert json.loads(r2.strategy_spec) == DSL_SPEC  # unchanged, not other_spec


class TestToStrategyPassport:
    """StrategyRecord -> StrategyPassport adapter (used by the live agent
    runner to evaluate a generated strategy's own DSL spec)."""

    def test_adapts_id_universe_and_spec(self, session):
        from archimedes.models.strategy import StrategyPassport

        r = upsert_strategy(
            session,
            generation_method="debate",
            strategy_name="Faber Clone",
            thesis="Momentum thesis",
            source_papers=[{"arxiv_id": "0706.1497", "title": "A Quantitative Approach"}],
            asset_universe=["SPY"],
            strategy_spec=DSL_SPEC,
        )
        passport = r.to_strategy_passport()
        assert isinstance(passport, StrategyPassport)
        assert passport.id == r.id
        assert passport.asset_universe == ["SPY"]
        assert passport.strategy_spec == DSL_SPEC
        assert passport.paper_arxiv_id == "0706.1497"
        assert passport.paper_title == "A Quantitative Approach"

    def test_adapts_with_no_spec(self, session):
        r = upsert_strategy(
            session,
            generation_method="portfolio_agent_streaming",
            strategy_name="No Spec",
            thesis="X",
            source_papers=PAPERS_A,
            asset_universe=["SPY"],
        )
        passport = r.to_strategy_passport()
        assert passport.strategy_spec is None

    def test_adapts_with_no_source_papers_falls_back_to_strategy_name(self, session):
        r = upsert_strategy(
            session,
            generation_method="debate",
            strategy_name="Nameless Provenance",
            thesis="X",
            source_papers=[],
            asset_universe=["SPY"],
            strategy_spec=DSL_SPEC,
        )
        passport = r.to_strategy_passport()
        assert passport.paper_title == "Nameless Provenance"
