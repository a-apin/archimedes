"""Per-user proposals ownership (dbrowneup/proposals-owner-scope).

Confirmed-live privacy leak: `GET /api/proposals/` and
`GET /api/proposals/{generation_id}/siblings` (backend/archimedes/api/
proposals_routes.py) were completely unauthenticated and returned EVERY
user's `StrategyProposal` rows — each row's `payload` carries the user's
private `intent` (their strategic brief), full `strategy_spec`, and
`rigor_verdict`, including rejected candidates. `StrategyProposal` had no
owner column, so there was nothing to scope on.

Hermetic (tmp-sqlite, no .env / network / Redis). Covers:
  * unauthenticated GET /api/proposals/ -> 401
  * authenticated wallet A sees only A's proposals (B's rows excluded)
  * get_proposal_siblings for a generation owned by B -> empty for A
  * a legacy NULL-owner row (pre-dates this column) -> returned to NO ONE
  * persist_proposal(owner_wallet=...) stamps the column, lowercased

DB fixture + SIWE cookie helper copy the `_use_tmp_db` / `_siwe_cookies`
pattern from test_strategy_ownership.py.
"""

from __future__ import annotations

import time

import archimedes.db as db
import pytest
from archimedes.api.auth_siwe import _COOKIE_NAME, _sign_session
from httpx import ASGITransport, AsyncClient

_W_A = "0xAaAa00000000000000000000000000000000000A"  # mixed case on purpose
_W_B = "0x000000000000000000000000000000000000000B"


@pytest.fixture(autouse=True)
def _use_tmp_db(tmp_path, monkeypatch):
    """Point the DB at a FRESH temp sqlite (rebinds db.engine + db.SessionLocal).

    db.engine/SessionLocal are created once at import, so setenv alone doesn't
    re-point them; rebind both to a per-test engine, then init_db() registers
    every table.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    url = f"sqlite:///{tmp_path / 'proposals_ownership.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    eng = create_engine(url, connect_args={"check_same_thread": False})
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=eng, autocommit=False, autoflush=False))
    db.init_db()
    yield


def _siwe_cookies(wallet: str) -> dict[str, str]:
    """Valid signed SIWE session cookie for `wallet` (same helper shape as
    test_strategy_ownership.py / test_user_routes.py — a real signed session,
    not header spoofing)."""
    return {_COOKIE_NAME: _sign_session(wallet, time.time())}


def _mk_proposal(
    generation_id: str,
    *,
    owner: str | None,
    intent: str = "test intent",
    verdict: str = "pending",
):
    """Write a StrategyProposal row directly (bypasses persist_proposal's
    content-hash dedup so every seeded row is guaranteed distinct + present,
    including the legacy-NULL-owner case that predates the owner_wallet
    column ever being populated)."""
    import json
    import uuid

    from archimedes.models.strategy_proposal import StrategyProposal

    proposal_id = uuid.uuid4().hex[:16]
    content_hash = "0x" + uuid.uuid4().hex + uuid.uuid4().hex[:24]  # unique, right shape
    with db.get_session() as session:
        session.add(
            StrategyProposal(
                id=content_hash[:16],
                generation_id=generation_id,
                proposal_id=proposal_id,
                verdict=verdict,
                trust_level="CANDIDATE",
                content_hash=content_hash,
                agent="fusion",
                owner_wallet=owner.lower() if owner else None,
                payload=json.dumps({"intent": intent, "strategy_spec": {}, "rigor_verdict": None}),
            )
        )
        session.commit()
    return proposal_id


# ── GET /api/proposals/ ────────────────────────────────────────────────


async def test_list_proposals_unauthenticated_401():
    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/proposals/")
    assert resp.status_code == 401


async def test_list_proposals_owner_a_excludes_owner_b():
    _mk_proposal("gen_a_1", owner=_W_A, intent="A's private brief")
    _mk_proposal("gen_a_2", owner=_W_A, intent="A's second brief")
    _mk_proposal("gen_b_1", owner=_W_B, intent="B's private brief")

    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp_a = await client.get("/api/proposals/", cookies=_siwe_cookies(_W_A))
        resp_b = await client.get("/api/proposals/", cookies=_siwe_cookies(_W_B))

    assert resp_a.status_code == 200
    data_a = resp_a.json()
    gen_ids_a = {p["generation_id"] for p in data_a["proposals"]}
    assert gen_ids_a == {"gen_a_1", "gen_a_2"}
    assert data_a["total"] == 2
    # B's private intent text must never appear in A's response.
    intents_a = {p["payload"]["intent"] for p in data_a["proposals"]}
    assert "B's private brief" not in intents_a

    assert resp_b.status_code == 200
    data_b = resp_b.json()
    gen_ids_b = {p["generation_id"] for p in data_b["proposals"]}
    assert gen_ids_b == {"gen_b_1"}
    assert data_b["total"] == 1


async def test_list_proposals_legacy_null_owner_returned_to_no_one():
    _mk_proposal("gen_orphan", owner=None, intent="orphaned legacy row")
    _mk_proposal("gen_a_owned", owner=_W_A, intent="A owns this one")

    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp_a = await client.get("/api/proposals/", cookies=_siwe_cookies(_W_A))
        resp_b = await client.get("/api/proposals/", cookies=_siwe_cookies(_W_B))

    gen_ids_a = {p["generation_id"] for p in resp_a.json()["proposals"]}
    gen_ids_b = {p["generation_id"] for p in resp_b.json()["proposals"]}
    assert "gen_orphan" not in gen_ids_a
    assert "gen_orphan" not in gen_ids_b
    assert gen_ids_a == {"gen_a_owned"}
    assert gen_ids_b == set()


async def test_list_proposals_case_insensitive_wallet_match():
    """owner_wallet is stored lowercase; the SIWE session wallet is also
    lowercase (get_verified_wallet), but the seeded row + the caller wallet
    here are deliberately mixed-case at the call boundary to prove the match
    isn't accidentally case-sensitive."""
    _mk_proposal("gen_case", owner="0xCaFe00000000000000000000000000000000000C", intent="case test")

    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/proposals/", cookies=_siwe_cookies("0xCAFE00000000000000000000000000000000000C"))

    assert resp.status_code == 200
    gen_ids = {p["generation_id"] for p in resp.json()["proposals"]}
    assert gen_ids == {"gen_case"}


# ── GET /api/proposals/{generation_id}/siblings ──────────────────────────


async def test_siblings_unauthenticated_401():
    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/proposals/gen_a_1/siblings")
    assert resp.status_code == 401


async def test_siblings_owned_by_other_wallet_empty_for_caller():
    gid = "gen_b_siblings"
    _mk_proposal(gid, owner=_W_B, intent="B sibling 1")
    _mk_proposal(gid, owner=_W_B, intent="B sibling 2")

    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp_a = await client.get(f"/api/proposals/{gid}/siblings", cookies=_siwe_cookies(_W_A))
        resp_b = await client.get(f"/api/proposals/{gid}/siblings", cookies=_siwe_cookies(_W_B))

    assert resp_a.status_code == 200
    data_a = resp_a.json()
    assert data_a["siblings"] == []
    assert data_a["count"] == 0

    assert resp_b.status_code == 200
    data_b = resp_b.json()
    assert data_b["count"] == 2


async def test_siblings_legacy_null_owner_empty_for_everyone():
    gid = "gen_legacy_siblings"
    _mk_proposal(gid, owner=None, intent="legacy sibling 1")
    _mk_proposal(gid, owner=None, intent="legacy sibling 2")

    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp_a = await client.get(f"/api/proposals/{gid}/siblings", cookies=_siwe_cookies(_W_A))

    assert resp_a.status_code == 200
    assert resp_a.json()["count"] == 0


# ── persist_proposal(owner_wallet=...) write path ────────────────────────


def test_persist_proposal_stamps_owner_wallet_lowercased():
    from archimedes.models.strategy_proposal import StrategyProposal
    from archimedes.services.strategy_memory import persist_proposal

    proposal_id = persist_proposal(
        generation_id="gen_write_path",
        agent="fusion",
        intent="write-path ownership test",
        owner_wallet="0xDEAD00000000000000000000000000000000000F",
    )
    assert proposal_id is not None

    with db.get_session() as session:
        row = session.query(StrategyProposal).filter_by(proposal_id=proposal_id).one()
        assert row.owner_wallet == "0xdead00000000000000000000000000000000000f"
