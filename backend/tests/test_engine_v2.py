"""Tests for Engine v2: async job queue, is_example flag, market context in fusion.

Hermetic — no network, no Redis, no external DB. Uses fakes for Redis and in-memory
SQLite for StrategyStore.
"""

from __future__ import annotations

import json

import pytest
from archimedes.models.chat import Base
from archimedes.models.strategy_store import (
    upsert_strategy,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


PAPERS = [{"arxiv_id": "2401.12345", "sha256": "abc123"}]


# ── is_example flag ───────────────────────────────────────────────


class TestIsExampleFlag:
    def test_default_is_not_example(self, session):
        r = upsert_strategy(
            session,
            generation_method="fusion",
            strategy_name="T",
            thesis="X",
            source_papers=PAPERS,
            asset_universe=["SPY"],
        )
        assert r.is_example is False
        d = r.to_dict()
        assert d["is_example"] is False

    def test_explicit_example(self, session):
        r = upsert_strategy(
            session,
            generation_method="curated",
            strategy_name="SMA200 Timing",
            thesis="Faber 2007",
            source_papers=PAPERS,
            asset_universe=["SPY"],
            is_example=True,
        )
        assert r.is_example is True
        d = r.to_dict()
        assert d["is_example"] is True

    def test_idempotent_preserves_example_flag(self, session):
        r1 = upsert_strategy(
            session,
            generation_method="curated",
            strategy_name="T",
            thesis="X",
            source_papers=PAPERS,
            asset_universe=["SPY"],
            is_example=True,
        )
        assert r1.is_example is True

        # Second upsert with same content — same record returned
        r2 = upsert_strategy(
            session,
            generation_method="curated",
            strategy_name="T",
            thesis="X",
            source_papers=PAPERS,
            asset_universe=["SPY"],
            is_example=True,
        )
        assert r2.id == r1.id
        assert r2.is_example is True

    def test_mixed_strategies_distinguishable(self, session):
        example = upsert_strategy(
            session,
            generation_method="curated",
            strategy_name="Example",
            thesis="Static",
            source_papers=PAPERS,
            asset_universe=["SPY"],
            is_example=True,
        )
        live = upsert_strategy(
            session,
            generation_method="fusion",
            strategy_name="Live",
            thesis="Generated",
            source_papers=PAPERS,
            asset_universe=["TSLA"],
            is_example=False,
        )
        assert example.is_example is True
        assert live.is_example is False
        assert example.id != live.id


# ── FusionBrief market_context field ──────────────────────────────


class TestFusionBriefMarketContext:
    def test_brief_carries_market_context(self):
        from archimedes.agents.strategy_fusion import FusionBrief
        from archimedes.models.portfolio import RiskProfile

        ctx = {
            "regime": "risk_on",
            "confidence": 0.85,
            "signals": {"SPY": {"signal": "long", "weight": 0.6}},
        }
        brief = FusionBrief(
            asset_classes=["equities"],
            risk_appetite=RiskProfile.MODERATE,
            market_context=ctx,
        )
        assert brief.market_context == ctx

    def test_brief_default_empty_context(self):
        from archimedes.agents.strategy_fusion import FusionBrief

        brief = FusionBrief()
        assert brief.market_context == {}

    def test_market_context_in_user_prompt(self):
        from archimedes.agents.strategy_fusion import (
            CorpusPaper,
            FusionBrief,
            _build_user_prompt,
        )

        ctx = {"regime": "risk_off", "confidence": 0.7}
        brief = FusionBrief(market_context=ctx)
        paper = CorpusPaper(
            arxiv_id="2401.00001",
            title="Test Paper",
            abstract="Abstract",
            primary_category="q-fin.pm",
            categories=("q-fin.pm",),
            published="2024-01-01",
        )
        prompt = _build_user_prompt(brief, [paper])
        parsed = json.loads(prompt)
        assert "market_context" in parsed
        assert parsed["market_context"]["regime"] == "risk_off"

    def test_no_market_context_omitted_from_prompt(self):
        from archimedes.agents.strategy_fusion import (
            CorpusPaper,
            FusionBrief,
            _build_user_prompt,
        )

        brief = FusionBrief()
        paper = CorpusPaper(
            arxiv_id="2401.00001",
            title="Test",
            abstract="A",
            primary_category="q-fin.pm",
            categories=("q-fin.pm",),
            published="2024-01-01",
        )
        prompt = _build_user_prompt(brief, [paper])
        parsed = json.loads(prompt)
        assert "market_context" not in parsed


# ── JobStore (fake for hermetic tests) ────────────────────────────


class _FakeRedis:
    """In-memory dict-based fake Redis for hermetic job queue tests."""

    def __init__(self, **kwargs):
        self._store: dict[str, dict[str, str]] = {}
        self._ttls: dict[str, int] = {}

    async def hset(self, key: str, *, mapping: dict[str, str]) -> None:
        if key not in self._store:
            self._store[key] = {}
        self._store[key].update(mapping)

    async def hgetall(self, key: str) -> dict[str, str]:
        return self._store.get(key, {})

    async def expire(self, key: str, ttl: int) -> None:
        self._ttls[key] = ttl

    async def aclose(self) -> None:
        pass


class TestJobStore:
    @pytest.fixture
    def job_store(self, monkeypatch):
        from archimedes.services import job_queue

        fake = _FakeRedis()
        store = job_queue.JobStore.__new__(job_queue.JobStore)
        store._url = "fake://"
        store._redis = fake
        return store

    @pytest.mark.asyncio
    async def test_enqueue_creates_queued_job(self, job_store):
        job_id = await job_store.enqueue(
            job_type="fusion",
            payload={"risk_appetite": "moderate"},
        )
        assert job_id

        job = await job_store.get(job_id)
        assert job is not None
        assert job["status"] == "queued"
        assert job["type"] == "fusion"
        assert job["payload"]["risk_appetite"] == "moderate"

    @pytest.mark.asyncio
    async def test_update_status_to_done(self, job_store):
        job_id = await job_store.enqueue(
            job_type="fusion",
            payload={},
        )
        await job_store.update_status(
            job_id,
            "done",
            result={"strategy_name": "Test Strategy"},
        )
        job = await job_store.get(job_id)
        assert job["status"] == "done"
        assert job["result"]["strategy_name"] == "Test Strategy"

    @pytest.mark.asyncio
    async def test_update_status_to_failed(self, job_store):
        job_id = await job_store.enqueue(
            job_type="fusion",
            payload={},
        )
        await job_store.update_status(
            job_id,
            "failed",
            error="LLM backend unavailable",
        )
        job = await job_store.get(job_id)
        assert job["status"] == "failed"
        assert "LLM backend unavailable" in job["error"]

    @pytest.mark.asyncio
    async def test_job_lifecycle_queued_running_done(self, job_store):
        job_id = await job_store.enqueue(
            job_type="fusion",
            payload={"max_papers": 3},
        )
        job = await job_store.get(job_id)
        assert job["status"] == "queued"

        await job_store.update_status(job_id, "running")
        job = await job_store.get(job_id)
        assert job["status"] == "running"

        await job_store.update_status(
            job_id,
            "done",
            result={"status": "ok", "source_arxiv_ids": ["2401.001", "2401.002"]},
        )
        job = await job_store.get(job_id)
        assert job["status"] == "done"
        assert len(job["result"]["source_arxiv_ids"]) >= 2

    @pytest.mark.asyncio
    async def test_get_nonexistent_job_returns_none(self, job_store):
        job = await job_store.get("nonexistent")
        assert job is None

    @pytest.mark.asyncio
    async def test_close_idempotent(self, job_store):
        await job_store.close()
        await job_store.close()


# ── Corpus overview computation ───────────────────────────────────


class TestCorpusOverview:
    def test_overview_from_corpus(self):
        from archimedes.agents.strategy_fusion import CorpusPaper

        papers = [
            CorpusPaper("2401.001", "Paper A", "Abstract", "q-fin.PM", ("q-fin.PM",), "2024-01-15"),
            CorpusPaper("2402.002", "Paper B", "Abstract", "q-fin.TR", ("q-fin.TR", "q-fin.PM"), "2024-02-20"),
            CorpusPaper("2303.003", "Paper C", "Abstract", "q-fin.CP", ("q-fin.CP",), "2023-03-10"),
        ]
        from collections import Counter

        cat_counts: Counter = Counter()
        year_counts: Counter = Counter()
        for p in papers:
            cat_counts[p.primary_category] += 1
            for c in p.categories:
                cat_counts[c] += 1
            year = p.published[:4]
            year_counts[year] += 1

        assert cat_counts["q-fin.PM"] == 3  # Paper A primary + Paper B primary + Paper B categories
        assert year_counts["2024"] == 2
        assert year_counts["2023"] == 1
        assert sum(year_counts.values()) == 3
