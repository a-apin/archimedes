"""User's brief surfaced on the strategy passport (v8 Lane 3.3, dbrowneup/brief-on-passport).

The free-text brief that produced a generated strategy previously lived only
in ``strategy_proposals.payload["intent"]`` — an episodic, non-authoritative
log the passport route never reads. This adds ``strategy_store.brief_intent``
(written at generation time by ``generation_pipeline._persist_candidate``) and
surfaces it as "Your brief" on ``GET /api/strategies/{id}`` ONLY.

Three contracts under test:

  1. Pipeline — ``run_generation`` (fixture path) threads ``brief.intent``
     through ``_persist_candidate`` into ``StrategyRecord.brief_intent``.
  2. Isolation (the anti-goal) — the SHARED response builder
     (``_passport_to_strategy_response``, which also backs Library and the
     public leaderboard via ``_passport_responses`` /
     ``_public_generated_strategy_responses`` / ``_owned_generated_strategy_
     responses``) never sets ``brief_intent``, even when the underlying
     ``StrategyRecord`` carries a real one. Only ``get_strategy`` (the
     single-strategy detail route) attaches it, from ``StrategyRecord`` it has
     already loaded for the visibility check.
  3. Wire-level confirmation — ``GET /api/strategies/{id}`` exposes it;
     ``GET /api/leaderboard`` (which re-shapes into the disjoint
     ``LeaderboardEntry`` schema) never does, for the SAME underlying strategy.

Two hermetic patterns, matching what's already established in this suite:
  - In-memory sqlite + ``Base.metadata.create_all`` for the direct
    ``_passport_to_strategy_response`` unit check (test_multipaper_passport.py).
  - tmp-sqlite-file + ``db.init_db()`` + ``httpx.ASGITransport`` for the
    full-app route checks (test_strategy_ownership.py /
    test_leaderboard_single_user_scope.py).
"""

from __future__ import annotations

import httpx
import pytest
from archimedes.main import app
from archimedes.models.chat import Base
from archimedes.models.strategy_passport_record import StrategyPassportRecord
from archimedes.models.strategy_store import StrategyRecord
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# ── 1. Pipeline: brief.intent reaches StrategyRecord.brief_intent ─────────


class _FakeStore:
    """In-memory JobStore stand-in (mirrors test_strategy_ownership.py)."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.status: list[tuple[str, dict | None, str]] = []
        self.current_status: str | None = None

    async def push_event(self, job_id, payload):
        self.events.append(payload)
        return len(self.events)

    async def update_status(self, job_id, status, *, result=None, error=""):
        self.status.append((status, result, error))
        self.current_status = status

    async def update_terminal_status(self, job_id, status, *, result=None, error=""):
        if self.current_status == "cancelled":
            return False
        await self.update_status(job_id, status, result=result, error=error)
        return True


@pytest.fixture
def _tmp_db(tmp_path, monkeypatch):
    """Point the DB at a FRESH temp sqlite (rebinds db.engine + db.SessionLocal),
    same pattern as test_strategy_ownership.py."""
    import archimedes.db as db

    url = f"sqlite:///{tmp_path / 'brief_on_passport.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    eng = create_engine(url, connect_args={"check_same_thread": False})
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=eng, autocommit=False, autoflush=False))
    db.init_db()
    return db


async def test_run_generation_persists_brief_intent_to_store(_tmp_db, monkeypatch):
    """Fixture-path run_generation stamps brief.intent onto every persisted
    strategy_store row (K=1: exactly the winner)."""
    import archimedes.db as db
    from archimedes.agents.generation_pipeline import run_generation
    from archimedes.api.generate_schemas import GenerateBrief
    from unittest.mock import AsyncMock, patch

    monkeypatch.setenv("GENERATION_PIPELINE_FIXTURE", "1")

    async def _inline(fn, *a, **k):
        return fn(*a, **k)

    monkeypatch.setattr("archimedes.agents.generation_pipeline.asyncio.to_thread", _inline)

    store = _FakeStore()
    brief = GenerateBrief(intent="Something that beats SPY in a recession", risk_appetite="conservative")

    with patch("archimedes.agents.generation_pipeline._backtest_and_persist", new=AsyncMock()):
        await run_generation(job_id="job_brief_1", brief=brief, store=store)

    with db.get_session() as session:
        rows = session.query(StrategyRecord).filter(StrategyRecord.is_example.is_(False)).all()
        assert rows, "pipeline persisted no strategies"
        for row in rows:
            assert row.brief_intent == "Something that beats SPY in a recession"


# ── 2. Isolation: the shared response builder never surfaces it ───────────


@pytest.fixture
def _mem_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    sess = SessionLocal()
    yield sess
    sess.close()


def _seed_pair(session: Session, sid: str, *, brief_intent: str | None) -> StrategyPassportRecord:
    """A StrategyRecord (carrying brief_intent) + its StrategyPassportRecord
    mirror under the SAME id — exactly what ``_persist_candidate`` writes."""
    session.add(
        StrategyRecord(
            id=sid,
            content_hash=("0x" + sid).ljust(66, "0"),
            generation_method="debate",
            source_papers="[]",
            strategy_name="Test Strategy",
            thesis="test thesis",
            asset_universe="[]",
            risk_profile="moderate",
            status="candidate",
            is_example=False,
            is_published=True,
            brief_intent=brief_intent,
        )
    )
    passport = StrategyPassportRecord(
        id=sid,
        generation_method="debate",
        methodology_summary="Test methodology",
        asset_universe="[]",
        position_sizing="equal_weight",
        rebalance_frequency="weekly",
        status="candidate",
        regime_tag="regime_neutral",
        passes_rigor_gate=False,
    )
    session.add(passport)
    session.flush()
    return passport


def test_passport_to_strategy_response_never_sets_brief_intent(_mem_session):
    """GUARD: the response builder shared by Library / leaderboard / risk
    surfaces (``_passport_responses`` and everything that calls it) must NEVER
    read brief_intent — even though the row it's building from HAS one.

    This is the anti-goal ("do not touch the leaderboard payloads") pinned at
    exactly the boundary that matters: if a future edit moves the brief_intent
    lookup INTO this shared function, this assertion catches it before it ever
    reaches a list/leaderboard response.
    """
    from archimedes.api.strategies_routes import _passport_to_strategy_response

    passport = _seed_pair(_mem_session, "brief-guard-1", brief_intent="A private brief that must not leak")

    resp = _passport_to_strategy_response(passport, session=_mem_session)
    assert resp.brief_intent is None


# ── 3. Wire-level: detail exposes it, leaderboard doesn't ─────────────────


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_get_strategy_route_exposes_brief_intent(_tmp_db):
    import archimedes.db as db

    with db.get_session() as session:
        _seed_pair(session, "brief-detail-1", brief_intent="Low-vol income for retirement")
        session.commit()

    async with _client() as client:
        resp = await client.get("/api/strategies/brief-detail-1")
    assert resp.status_code == 200
    assert resp.json()["brief_intent"] == "Low-vol income for retirement"


async def test_get_strategy_route_reports_none_when_no_brief_recorded(_tmp_db):
    """A legacy/curated row with no brief reports None, not a fabricated string."""
    import archimedes.db as db

    with db.get_session() as session:
        _seed_pair(session, "brief-detail-2", brief_intent=None)
        session.commit()

    async with _client() as client:
        resp = await client.get("/api/strategies/brief-detail-2")
    assert resp.status_code == 200
    assert resp.json()["brief_intent"] is None


async def test_leaderboard_never_exposes_brief_intent(_tmp_db):
    """The SAME published strategy that carries a real brief on the detail
    route (above) must not leak it through the leaderboard's ``scope=curated``
    cohort, which re-shapes into the disjoint LeaderboardEntry schema via the
    exact same ``_passport_to_strategy_response``/``_passport_responses``
    helpers Test 2 pins directly. Asserting on the raw response text (not
    just "no brief_intent key") also catches a future refactor that free-forms
    extra fields onto an entry instead of using the typed schema."""
    import archimedes.db as db

    secret_brief = "A private brief that must never reach the public board"
    with db.get_session() as session:
        _seed_pair(session, "brief-board-1", brief_intent=secret_brief)
        session.commit()

    async with _client() as client:
        resp = await client.get("/api/leaderboard?scope=curated")
    assert resp.status_code == 200
    body = resp.json()
    assert secret_brief not in resp.text
    entry = next((e for e in body["entries"] if e["id"] == "brief-board-1"), None)
    assert entry is not None, "seeded published strategy did not reach the curated board"
    assert "brief_intent" not in entry
