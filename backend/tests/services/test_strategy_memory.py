"""Tests for strategy_proposals episodic memory (Issue #165).

Covers: ORM model, strategy_memory write path, proposals API endpoint.
"""

from __future__ import annotations

from datetime import UTC
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# ── ORM model tests ──────────────────────────────────────────────────────


class TestStrategyProposalModel:
    """Verify the StrategyProposal ORM model schema."""

    def test_model_imports(self):
        from archimedes.models.strategy_proposal import StrategyProposal

        assert StrategyProposal.__tablename__ == "strategy_proposals"

    def test_to_dict_roundtrip(self):
        from datetime import datetime

        from archimedes.models.strategy_proposal import StrategyProposal

        row = StrategyProposal(
            id="abc123",
            generation_id="gen_001",
            proposal_id="prop_001",
            parent_proposal_id=None,
            verdict="rigor_pass",
            trust_level="VALIDATED",
            content_hash="0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
            agent="fusion",
            regime_tag="bull",
            payload='{"intent": "test"}',
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        d = row.to_dict()
        assert d["id"] == "abc123"
        assert d["generation_id"] == "gen_001"
        assert d["verdict"] == "rigor_pass"
        assert d["trust_level"] == "VALIDATED"
        assert d["agent"] == "fusion"
        assert d["payload"] == {"intent": "test"}


# ── strategy_memory write path tests ────────────────────────────────────


class TestStrategyMemory:
    """Verify persist_proposal and query_proposals."""

    @pytest.fixture(autouse=True)
    def _setup_db(self, tmp_path):
        """Use a temp SQLite DB for isolation."""
        import os

        self._db_path = str(tmp_path / "test_proposals.db")
        self._env = {
            "DATABASE_URL": f"sqlite:///{self._db_path}",
        }
        self._orig = {k: os.environ.get(k) for k in self._env}
        for k, v in self._env.items():
            os.environ[k] = v

        # Re-init engine + tables
        from archimedes import db as db_mod
        from archimedes.models.chat import Base
        from archimedes.models.strategy_proposal import StrategyProposal  # noqa

        db_mod.DATABASE_URL = self._env["DATABASE_URL"]
        db_mod.engine = db_mod.create_engine(db_mod.DATABASE_URL, connect_args={"check_same_thread": False})
        db_mod.SessionLocal = db_mod.sessionmaker(bind=db_mod.engine, autocommit=False, autoflush=False)
        Base.metadata.create_all(bind=db_mod.engine)

        yield

        # Restore
        for k in self._orig:
            if self._orig[k] is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = self._orig[k]

    def test_persist_proposal_basic(self):
        from archimedes.services.strategy_memory import persist_proposal

        proposal_id = persist_proposal(
            generation_id="gen_test",
            agent="fusion",
            intent="trend following with low drawdown",
            strategy_spec={"strategy_name": "Test Strategy"},
            papers=["arxiv:2103.01", "arxiv:2205.02"],
            rigor_verdict={"passing": True, "dsr": 1.5},
        )
        assert proposal_id is not None
        assert len(proposal_id) == 16

    def test_persist_proposal_rigor_fail(self):
        from archimedes.services.strategy_memory import persist_proposal

        proposal_id = persist_proposal(
            generation_id="gen_fail",
            agent="architect",
            intent="high leverage crypto strategy",
            rigor_verdict={"passing": False, "dsr": 0.1},
        )
        assert proposal_id is not None

    def test_persist_proposal_dedup_by_content_hash(self):
        from archimedes.services.strategy_memory import persist_proposal

        pid1 = persist_proposal(
            generation_id="gen_dedup",
            agent="fusion",
            intent="same intent",
            strategy_spec={"key": "val"},
            papers=["arxiv:0001"],
        )
        pid2 = persist_proposal(
            generation_id="gen_dedup",
            agent="fusion",
            intent="same intent",
            strategy_spec={"key": "val"},
            papers=["arxiv:0001"],
        )
        # Same content hash → same proposal_id (dedup)
        assert pid1 == pid2

    def test_query_proposals_returns_rows(self):
        from archimedes.services.strategy_memory import (
            persist_proposal,
            query_proposals,
        )

        persist_proposal(
            generation_id="gen_query1",
            agent="fusion",
            intent="test query",
        )
        persist_proposal(
            generation_id="gen_query2",
            agent="architect",
            intent="test query 2",
        )
        _proposals, total = query_proposals()
        assert total >= 2

    def test_query_proposals_filter_verdict(self):
        from archimedes.services.strategy_memory import (
            persist_proposal,
            query_proposals,
        )

        persist_proposal(
            generation_id="gen_pass_filter",
            agent="fusion",
            intent="pass test",
            rigor_verdict={"passing": True},
        )
        persist_proposal(
            generation_id="gen_pending_filter",
            agent="fusion",
            intent="pending test",
        )
        proposals, total = query_proposals(verdict="rigor_pass")
        assert total >= 1
        assert all(p["verdict"] == "rigor_pass" for p in proposals)

    def test_query_proposals_filter_agent(self):
        from archimedes.services.strategy_memory import (
            persist_proposal,
            query_proposals,
        )

        persist_proposal(
            generation_id="gen_agent_f",
            agent="fusion",
            intent="agent filter test",
        )
        proposals, total = query_proposals(agent="fusion")
        assert total >= 1
        assert all(p["agent"] == "fusion" for p in proposals)

    def test_query_proposals_pagination(self):
        from archimedes.services.strategy_memory import (
            persist_proposal,
            query_proposals,
        )

        for i in range(5):
            persist_proposal(
                generation_id=f"gen_page_{i}",
                agent="fusion",
                intent=f"pagination test {i}",
            )
        proposals, _total = query_proposals(limit=2, offset=0)
        assert len(proposals) <= 2

    def test_get_siblings(self):
        from archimedes.services.strategy_memory import get_siblings, persist_proposal

        gid = "gen_siblings_test"
        persist_proposal(generation_id=gid, agent="agent", intent="sib 1")
        persist_proposal(generation_id=gid, agent="agent", intent="sib 2")
        siblings = get_siblings(gid)
        assert len(siblings) >= 2

    def test_persist_proposal_non_blocking_on_failure(self):
        """Memory write failure must not raise."""
        from archimedes.services.strategy_memory import persist_proposal

        # Patch the lazy import inside persist_proposal
        with patch("archimedes.db.get_session", side_effect=Exception("DB down")):
            result = persist_proposal(
                generation_id="gen_fail_test",
                agent="fusion",
                intent="should not crash",
            )
            assert result is None  # Non-blocking: returns None, doesn't raise


# ── API endpoint tests ───────────────────────────────────────────────────


class TestProposalsAPI:
    """Verify /api/strategies/proposals endpoint."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        import os

        self._db_path = str(tmp_path / "test_api_proposals.db")
        self._env_key = "DATABASE_URL"
        self._orig = os.environ.get(self._env_key)
        os.environ[self._env_key] = f"sqlite:///{self._db_path}"

        from archimedes import db as db_mod
        from archimedes.models.chat import Base
        from archimedes.models.strategy_proposal import StrategyProposal  # noqa

        db_mod.DATABASE_URL = os.environ[self._env_key]
        db_mod.engine = db_mod.create_engine(db_mod.DATABASE_URL, connect_args={"check_same_thread": False})
        db_mod.SessionLocal = db_mod.sessionmaker(bind=db_mod.engine, autocommit=False, autoflush=False)
        Base.metadata.create_all(bind=db_mod.engine)

        yield

        if self._orig is None:
            os.environ.pop(self._env_key, None)
        else:
            os.environ[self._env_key] = self._orig

    @pytest.fixture
    def client(self):
        from archimedes.main import app

        return TestClient(app)

    def test_list_proposals_empty(self, client):
        resp = client.get("/api/proposals")
        assert resp.status_code == 200
        data = resp.json()
        assert "proposals" in data
        assert "total" in data

    def test_list_proposals_after_persist(self, client):
        from archimedes.services.strategy_memory import persist_proposal

        persist_proposal(
            generation_id="gen_api_test",
            agent="fusion",
            intent="api test proposal",
        )
        resp = client.get("/api/proposals")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1

    def test_list_proposals_filter_verdict(self, client):
        from archimedes.services.strategy_memory import persist_proposal

        persist_proposal(
            generation_id="gen_api_fail",
            agent="fusion",
            intent="failing proposal",
            rigor_verdict={"passing": False},
        )
        resp = client.get("/api/proposals?verdict=rigor_fail")
        assert resp.status_code == 200
        data = resp.json()
        assert all(p["verdict"] == "rigor_fail" for p in data["proposals"])

    def test_siblings_endpoint(self, client):
        from archimedes.services.strategy_memory import persist_proposal

        gid = "gen_sib_api"
        persist_proposal(generation_id=gid, agent="fusion", intent="sib api 1")
        persist_proposal(generation_id=gid, agent="architect", intent="sib api 2")
        resp = client.get(f"/api/proposals/{gid}/siblings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["generation_id"] == gid
        assert data["count"] >= 2
