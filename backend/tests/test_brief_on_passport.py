"""User's brief surfaced on the strategy passport (v8 Lane 3.3, dbrowneup/brief-on-passport).

The free-text brief that produced a generated strategy previously lived only
in ``strategy_proposals.payload["intent"]`` — an episodic, non-authoritative
log the passport route never reads. This adds ``strategy_store.brief_intent``
(written at generation time by ``generation_pipeline._persist_candidate``) and
surfaces it as "Your brief" on ``GET /api/strategies/{id}`` — **to the row's
OWNER only**.

Four contracts under test:

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
  3. Owner gate (the privacy contract) — ``GET /api/strategies/{id}`` returns
     the brief to the OWNER, and returns ``null`` to everyone else, INCLUDING
     on a PUBLISHED row that the same request is otherwise allowed to read in
     full. Publishing a strategy shares the strategy, not the sentence its
     owner typed to ask for it (the same reasoning ``_redact_owner_wallet``
     already applies to ``owner_wallet``). The gate is the shared #850
     predicate ``is_strategy_visible`` asked in ownership-only form.
  4. List surfaces — the Library list route (``GET /api/strategies/generated``,
     which serializes ``StrategyRecord.to_dict()`` rather than the passport
     response schema) never carries the brief, not even for the owner asking
     about their own row. Only the detail route surfaces it.

Two hermetic patterns, matching what's already established in this suite:
  - In-memory sqlite + ``Base.metadata.create_all`` for the direct
    ``_passport_to_strategy_response`` unit check (test_multipaper_passport.py).
  - tmp-sqlite-file + ``db.init_db()`` + ``httpx.ASGITransport`` for the
    full-app route checks (test_strategy_ownership.py /
    test_leaderboard_single_user_scope.py), authenticated with the signed SIWE
    fixture cookie that conftest's autouse ``_legacy_siwe_test_adapter`` maps
    onto a canonical Better Auth user.
"""

from __future__ import annotations

import time

import httpx
import pytest
from archimedes.api.auth_siwe import _COOKIE_NAME, _sign_session
from archimedes.main import app
from archimedes.models.chat import Base
from archimedes.models.strategy_passport_record import StrategyPassportRecord
from archimedes.models.strategy_store import StrategyRecord
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

_W_OWNER = "0xAbC0000000000000000000000000000000000001"  # mixed case on purpose
_W_STRANGER = "0x0000000000000000000000000000000000000002"


def _legacy_user_id(wallet: str) -> str:
    """The canonical user id conftest's ``_legacy_siwe_test_adapter`` mints for
    a SIWE fixture cookie. Derived, not hard-coded, so it cannot silently drift
    from the adapter (which builds ``legacy-test:{wallet}`` from
    ``auth_siwe._verify_session``, whose payload is lower-cased at signing)."""
    return f"legacy-test:{wallet.lower()}"


def _siwe_cookies(wallet: str) -> dict[str, str]:
    """Valid signed SIWE session cookie for *wallet* — a real signed session,
    not header spoofing (same helper shape as test_strategy_ownership.py)."""
    return {_COOKIE_NAME: _sign_session(wallet, time.time())}


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
    from unittest.mock import AsyncMock, patch

    import archimedes.db as db
    from archimedes.agents.generation_pipeline import run_generation
    from archimedes.api.generate_schemas import GenerateBrief

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


def _seed_pair(
    session: Session,
    sid: str,
    *,
    brief_intent: str | None,
    owner_user_id: str | None = None,
    is_published: bool = False,
) -> StrategyPassportRecord:
    """A StrategyRecord (carrying brief_intent + ownership) + its
    StrategyPassportRecord mirror under the SAME id — exactly what
    ``_persist_candidate`` writes."""
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
            is_published=is_published,
            owner_user_id=owner_user_id,
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
        owner_user_id=owner_user_id,
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


# ── 3. Owner gate on the detail route ─────────────────────────────────────


def _client(wallet: str | None = None) -> httpx.AsyncClient:
    """ASGI client, optionally signed in as *wallet* (SIWE fixture cookie →
    canonical user via conftest's autouse legacy adapter)."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        cookies=_siwe_cookies(wallet) if wallet else None,
    )


async def test_get_strategy_route_returns_the_brief_to_its_owner(_tmp_db):
    """The whole point of the feature: the owner asking about their own
    strategy gets their own words back."""
    import archimedes.db as db

    with db.get_session() as session:
        _seed_pair(
            session,
            "brief-detail-1",
            brief_intent="Low-vol income for retirement",
            owner_user_id=_legacy_user_id(_W_OWNER),
        )
        session.commit()

    async with _client(_W_OWNER) as client:
        resp = await client.get("/api/strategies/brief-detail-1")
    assert resp.status_code == 200
    assert resp.json()["brief_intent"] == "Low-vol income for retirement"


@pytest.mark.parametrize("caller_wallet", [_W_STRANGER, None], ids=["signed-in-stranger", "anonymous"])
async def test_get_strategy_route_hides_the_brief_from_non_owners(_tmp_db, caller_wallet):
    """PRIVACY GUARD — the negative half of the contract, and the one the
    owner gate exists for.

    The row is PUBLISHED, so ``is_strategy_visible`` grants the request in
    full: the caller legitimately reads the strategy, gets a 200, and sees its
    methodology. What they must NOT see is the owner's free-text brief.
    Without the ownership check in ``get_strategy`` this returns the brief to
    a stranger and to an anonymous visitor alike — every published strategy
    leaking the sentence its owner typed.

    Asserted on the raw response text as well as the field, so a future
    refactor that free-forms the brief into some other key (a nested passport
    blob, a debug echo) still trips this.
    """
    import archimedes.db as db

    secret_brief = "A private brief that must never reach a stranger"
    with db.get_session() as session:
        _seed_pair(
            session,
            "brief-detail-published",
            brief_intent=secret_brief,
            owner_user_id=_legacy_user_id(_W_OWNER),
            is_published=True,
        )
        session.commit()

    async with _client(caller_wallet) as client:
        resp = await client.get("/api/strategies/brief-detail-published")

    assert resp.status_code == 200, "published row must stay readable — this is a redaction, not a 404"
    body = resp.json()
    assert body["id"] == "brief-detail-published"
    assert body["methodology_summary"] == "Test methodology", "the strategy itself is still public"
    assert body["brief_intent"] is None
    assert secret_brief not in resp.text


async def test_get_strategy_route_reports_none_to_the_owner_when_no_brief_recorded(_tmp_db):
    """A legacy/curated row with no brief reports None, not a fabricated string.

    Deliberately asked AS THE OWNER: with the owner gate in place, a
    non-owner request would return None whatever the column holds, so an
    anonymous caller here would prove nothing about the no-brief case.
    """
    import archimedes.db as db

    with db.get_session() as session:
        _seed_pair(
            session,
            "brief-detail-2",
            brief_intent=None,
            owner_user_id=_legacy_user_id(_W_OWNER),
        )
        session.commit()

    async with _client(_W_OWNER) as client:
        resp = await client.get("/api/strategies/brief-detail-2")
    assert resp.status_code == 200
    assert resp.json()["brief_intent"] is None


# ── 4. List surfaces: the Library list never carries the brief ────────────


async def test_library_list_route_never_exposes_brief_intent(_tmp_db):
    """The Library list (``GET /api/strategies/generated``) serializes
    ``StrategyRecord.to_dict()`` — the same ORM row the detail route reads the
    brief off — so it is the one list surface that could pick the column up by
    accident, simply by someone adding a ``"brief_intent"`` key to
    ``to_dict()``.

    Asked as the OWNER on purpose: this is not the ownership gate under test
    (the owner is entitled to their brief), it is the surface rule — the brief
    belongs on the single-strategy passport and nowhere else, so even the
    owner's own Library row must not carry it.

    (This replaces an earlier leaderboard assertion that could not fail: the
    leaderboard re-shapes into the disjoint ``LeaderboardEntry`` schema, which
    has no ``brief_intent`` field to populate and no ORM row to leak from, so
    "brief_intent not in entry" held regardless of what the code under test
    did. Test 2 above already pins the shared passport-response helper the
    leaderboard actually goes through.)
    """
    import archimedes.db as db

    secret_brief = "A private brief that belongs only on the passport"
    with db.get_session() as session:
        _seed_pair(
            session,
            "brief-library-1",
            brief_intent=secret_brief,
            owner_user_id=_legacy_user_id(_W_OWNER),
        )
        session.commit()

    async with _client(_W_OWNER) as client:
        resp = await client.get("/api/strategies/generated")

    assert resp.status_code == 200
    body = resp.json()
    row = next((r for r in body["strategies"] if r["id"] == "brief-library-1"), None)
    assert row is not None, "owner's own generated strategy did not reach their Library list"
    assert "brief_intent" not in row
    assert secret_brief not in resp.text
