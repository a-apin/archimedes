"""Hermetic tests for the leaderboard's single-user MVP pivot (Dan's
directive: no publish mechanism exists yet, so ranking a global cohort was
incoherent — the board goes single-user by default).

Pins the contract:
  1. POISON TEST — a signed-in user A never sees user B's strategies under
     ``scope=own``, published or not. This is the load-bearing guarantee:
     "own" must be ownership-only, never "anyone who opted in".
  2. Anonymous callers are served ``scope=curated`` only — the curated seed
     library, never a stranger's generated strategy, even explicitly asking
     for ``scope=own``.
  3. Default scope is contextual: 'own' when signed in, 'curated' when
     anonymous — without the caller having to pass ?scope= at all.
  4. The response's own ``scope`` field always reports what was actually
     served (never blindly the query param), so the UI can label the board
     honestly even after a silent coercion.
  5. A signed-in user with zero generated strategies gets an honest EMPTY
     "own" board (not a fallback to curated, not an error) — the single-user
     board must never quietly show someone else's strategies as filler.

Uses the same two patterns already established in this test suite:
  - tmp-sqlite DB isolation + StrategyRecord/StrategyPassportRecord fixture
    builders (test_unify_curated_generated.py's ``_mk_strategy``/``_mk_passport``).
  - Better Auth session simulation via monkeypatching
    ``account_auth._fetch_session`` + the session cookie header
    (test_paper_routes_auth.py's ``_sign_in``/``_client`` pattern).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import archimedes.db as db
import httpx
import pytest
from archimedes.api import account_auth
from archimedes.main import app


@pytest.fixture(autouse=True)
def _use_tmp_db(tmp_path, monkeypatch):
    """Point the DB at a FRESH temp sqlite (rebinds db.engine + db.SessionLocal),
    same pattern as test_strategy_ownership.py / test_unify_curated_generated.py.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    url = f"sqlite:///{tmp_path / 'leaderboard_scope.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    eng = create_engine(url, connect_args={"check_same_thread": False})
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=eng, autocommit=False, autoflush=False))
    db.init_db()
    yield


def _mk_strategy(
    sid: str,
    *,
    owner_user_id: str | None = None,
    published: bool = False,
    status: str = "candidate",
    name: str = "Test Strategy",
) -> None:
    from archimedes.models.strategy_store import StrategyRecord

    with db.get_session() as session:
        session.add(
            StrategyRecord(
                id=sid,
                content_hash=("0x" + sid).ljust(66, "0"),
                generation_method="fusion",
                source_papers="[]",
                strategy_name=name,
                thesis="test thesis",
                asset_universe="[]",
                risk_profile="moderate",
                status=status,
                is_example=False,
                owner_user_id=owner_user_id,
                is_published=published,
            )
        )
        session.commit()


def _mk_passport(
    sid: str,
    *,
    owner_user_id: str | None = None,
    status: str = "candidate",
    passes: bool = True,
    sharpe: float | None = 1.1,
    dsr: float | None = 0.95,
    oos: float | None = 1.0,
    pbo: float | None = 0.1,
    title: str = "Gen Strat",
) -> None:
    from archimedes.models.strategy_passport_record import PassportPaperRef, StrategyPassportRecord

    with db.get_session() as session:
        record = StrategyPassportRecord(
            id=sid,
            content_hash=("0x" + sid).ljust(66, "0"),
            generation_method="fusion",
            methodology_summary="test methodology",
            asset_universe='["SPY", "GLD"]',
            status=status,
            owner_user_id=owner_user_id,
            sharpe_ratio=sharpe,
            cagr=0.12,
            max_drawdown=-0.10,
            win_rate=0.55,
            calmar_ratio=1.2,
            correlation_to_spy=0.2,
            deflated_sharpe_ratio=dsr,
            dsr_p_value=dsr,
            pbo_score=pbo,
            out_of_sample_sharpe=oos,
            passes_rigor_gate=passes,
        )
        record.paper_refs = [PassportPaperRef(passport_id=sid, arxiv_id="2401.00001", title=title)]
        session.add(record)
        session.commit()


def _own(sid: str, user_id: str, **kw) -> None:
    """Create a strategy + passport pair owned by *user_id*."""
    _mk_strategy(sid, owner_user_id=user_id, **{k: v for k, v in kw.items() if k in {"published", "status", "name"}})
    _mk_passport(sid, owner_user_id=user_id, **{k: v for k, v in kw.items() if k not in {"published", "name"}})


def _session_for(user_id: str):
    async def fetch(_request):
        return {
            "user": {"id": user_id, "name": user_id, "email": f"{user_id}@example.com", "emailVerified": True},
            "session": {"id": f"s-{user_id}", "expiresAt": (datetime.now(UTC) + timedelta(hours=1)).isoformat()},
        }

    return fetch


def _sign_in(monkeypatch, user_id: str) -> None:
    monkeypatch.setattr(account_auth, "_fetch_session", _session_for(user_id))


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"cookie": "better-auth.session_token=opaque", "host": "test"},
    )


# ── 1. Poison test: user A never sees user B's rows under scope=own ────────


@pytest.mark.asyncio
async def test_own_scope_never_leaks_another_users_strategy(monkeypatch):
    _own("a-strat-1", "user-a", published=False, status="live", sharpe=1.9, dsr=0.97, oos=1.4, pbo=0.05)
    _own("b-strat-1", "user-b", published=True, status="live", sharpe=2.5, dsr=0.99, oos=1.8, pbo=0.02)

    _sign_in(monkeypatch, "user-a")
    async with _client() as client:
        resp = await client.get("/api/leaderboard?scope=own")
    assert resp.status_code == 200
    body = resp.json()
    ids = [e["id"] for e in body["entries"]]
    assert "a-strat-1" in ids
    # The poison case: user B's strategy is PUBLISHED (would appear on the old
    # global-cohort board) and scores strictly higher, so if ownership scoping
    # were broken it would not just leak — it would leak to the TOP of A's board.
    assert "b-strat-1" not in ids
    assert body["scope"] == "own"


@pytest.mark.asyncio
async def test_own_scope_symmetric_for_both_users(monkeypatch):
    """Same fixtures, from B's side: B sees only B's row, never A's — the
    leak must not be one-directional (e.g. an accidental owner_user_id-is-None
    fallback that only fires for one of the two users)."""
    _own("a-strat-2", "user-a", published=True, status="live")
    _own("b-strat-2", "user-b", published=False, status="live")

    _sign_in(monkeypatch, "user-b")
    async with _client() as client:
        resp = await client.get("/api/leaderboard?scope=own")
    ids = [e["id"] for e in resp.json()["entries"]]
    assert ids == ["b-strat-2"]


# ── 2. Anonymous is coerced to curated, even if it asks for "own" ──────────


@pytest.mark.asyncio
async def test_anonymous_requesting_own_scope_gets_curated_not_error_not_leak():
    _own("anon-poison-1", "user-a", published=False, status="live", sharpe=3.0, dsr=0.99, oos=2.0, pbo=0.01)

    async with _client() as client:
        # No cookie sent — genuinely anonymous, not just an unsigned-in client.
        client.headers.pop("cookie", None)
        resp = await client.get("/api/leaderboard?scope=own")
    assert resp.status_code == 200  # never 401 — public, no-wallet endpoint
    body = resp.json()
    assert body["scope"] == "curated"
    assert "anon-poison-1" not in [e["id"] for e in body["entries"]]


@pytest.mark.asyncio
async def test_anonymous_default_scope_is_curated():
    async with _client() as client:
        client.headers.pop("cookie", None)
        resp = await client.get("/api/leaderboard")
    assert resp.status_code == 200
    assert resp.json()["scope"] == "curated"


# ── 3 & 4. Signed-in default is "own"; response always reports served scope ─


@pytest.mark.asyncio
async def test_signed_in_default_scope_is_own_without_query_param(monkeypatch):
    _own("default-own-1", "user-a", published=False, status="live")

    _sign_in(monkeypatch, "user-a")
    async with _client() as client:
        resp = await client.get("/api/leaderboard")  # no ?scope= at all
    body = resp.json()
    assert body["scope"] == "own"
    assert [e["id"] for e in body["entries"]] == ["default-own-1"]


@pytest.mark.asyncio
async def test_signed_in_user_explicit_curated_scope_excludes_own_private_row(monkeypatch):
    """A signed-in user can explicitly ask for the curated reference view; it
    must not include their own private (unpublished) strategy — curated means
    curated, not 'curated plus my stuff'."""
    _own("mine-not-curated", "user-a", published=False, status="live")

    _sign_in(monkeypatch, "user-a")
    async with _client() as client:
        resp = await client.get("/api/leaderboard?scope=curated")
    body = resp.json()
    assert body["scope"] == "curated"
    assert "mine-not-curated" not in [e["id"] for e in body["entries"]]


# ── 5. Zero-strategy signed-in user gets an honest empty board, not curated ─


@pytest.mark.asyncio
async def test_own_scope_empty_for_user_with_no_generated_strategies(monkeypatch):
    _sign_in(monkeypatch, "user-nobody")
    async with _client() as client:
        resp = await client.get("/api/leaderboard?scope=own")
    body = resp.json()
    assert body["scope"] == "own"
    assert body["entries"] == []
    assert body["total"] == 0
    # Scoring-engine metadata stays intact even for an empty board (existing
    # fail-safe contract — must survive the new scope branch too).
    assert abs(sum(body["scoring_engine"]["weights"].values()) - 1.0) < 1e-9
    # A signed-in caller's empty "own" board is an honest result, not a
    # failure (#1356) — must NOT be flagged degraded the way an empty
    # curated cohort is.
    assert body["degraded"] is False
