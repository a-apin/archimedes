"""Per-user strategy ownership + private-until-published (dbrowneup/strategy-ownership).

Hermetic (tmp-sqlite, no .env / network / Redis). Covers:
  * the idempotent ownership-column migration on a pre-existing legacy DB
  * SIWE-wallet threading: run_generation → _persist_candidate → upsert_strategy
    (+ the strategy_passports mirror), stored lowercase
  * /api/strategies/generated visibility: anon sees only published; owner also
    sees their own unpublished rows
  * GET /api/strategies/{id}: unpublished non-example rows 404 for non-owners
  * PATCH /api/strategies/{id}: owner-gated rename, non-example rows only
  * scripts/purge_orphan_generated.py: dry-run (default) vs --execute

DB fixture copies the `_use_tmp_db` pattern from test_live_gate_returns.py.
"""

from __future__ import annotations

import importlib.util
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import archimedes.db as db
import pytest
from archimedes.api.auth_siwe import _COOKIE_NAME, _sign_session
from httpx import ASGITransport, AsyncClient

_W_OWNER = "0xAbC0000000000000000000000000000000000001"  # mixed case on purpose
_W_OTHER = "0x0000000000000000000000000000000000000002"

_PURGE_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "purge_orphan_generated.py"


@pytest.fixture(autouse=True)
def _use_tmp_db(tmp_path, monkeypatch):
    """Point the DB at a FRESH temp sqlite (rebinds db.engine + db.SessionLocal).

    db.engine/SessionLocal are created once at import, so setenv alone doesn't
    re-point them; rebind both to a per-test engine, then init_db() registers
    every table (incl. the passport side-effect imports).
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    url = f"sqlite:///{tmp_path / 'ownership.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    eng = create_engine(url, connect_args={"check_same_thread": False})
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=eng, autocommit=False, autoflush=False))
    db.init_db()
    yield


def _siwe_cookies(wallet: str) -> dict[str, str]:
    """Valid signed SIWE session cookie for `wallet` (same helper shape as
    test_user_routes.py — a real signed session, not header spoofing)."""
    return {_COOKIE_NAME: _sign_session(wallet, time.time())}


def _mk_strategy(
    sid: str,
    *,
    owner: str | None = None,
    owner_user: str | None = None,
    published: bool = False,
    example: bool = False,
    name: str = "Test Strategy",
):
    from archimedes.models.strategy_store import StrategyRecord

    with db.get_session() as session:
        row = StrategyRecord(
            id=sid,
            content_hash=("0x" + sid).ljust(66, "0"),
            generation_method="fusion",
            source_papers="[]",
            strategy_name=name,
            thesis="test thesis",
            asset_universe="[]",
            risk_profile="moderate",
            status="candidate",
            is_example=example,
            owner_wallet=owner.lower() if owner else None,
            owner_user_id=owner_user,
            is_published=published,
        )
        session.add(row)
        session.commit()


def _mk_passport(sid: str, *, with_paper_ref: bool = True):
    from archimedes.models.strategy_passport_record import PassportPaperRef, StrategyPassportRecord

    with db.get_session() as session:
        record = StrategyPassportRecord(
            id=sid,
            content_hash=("0y" + sid).ljust(66, "0"),
            generation_method="fusion",
            methodology_summary="test",
            asset_universe="[]",
        )
        if with_paper_ref:
            record.paper_refs = [PassportPaperRef(passport_id=sid, arxiv_id="2401.00001", title="Paper")]
        session.add(record)
        session.commit()


def _mk_backtest(sid: str):
    from archimedes.models.backtest_store import BacktestResultRecord

    with db.get_session() as session:
        session.add(BacktestResultRecord(strategy_id=sid, content_hash=f"bt_{sid}", source_pipeline="test"))
        session.commit()


# ── Migration: idempotent ensure-columns on a legacy DB ──────────────────


def test_migration_adds_ownership_columns_to_legacy_sqlite(tmp_path, monkeypatch):
    """A pre-existing strategy_store WITHOUT the new columns gets them ALTERed
    in by init_db() (create_all skips existing tables, so introspection-driven
    ADD COLUMN is what covers live DBs). Second init_db() is a no-op."""
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.orm import sessionmaker

    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    eng = create_engine(url, connect_args={"check_same_thread": False})
    with eng.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE strategy_store ("
                "id VARCHAR(64) PRIMARY KEY, content_hash VARCHAR(66) NOT NULL, "
                "generation_method VARCHAR(32) NOT NULL, source_papers TEXT NOT NULL, "
                "strategy_name VARCHAR(256) NOT NULL, thesis TEXT NOT NULL, "
                "asset_universe TEXT NOT NULL, risk_profile VARCHAR(32) NOT NULL, "
                "status VARCHAR(16) NOT NULL, rigor_verdict TEXT, "
                "is_example BOOLEAN NOT NULL DEFAULT 0, "
                "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
            )
        )

    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=eng, autocommit=False, autoflush=False))

    db.init_db()
    cols = {c["name"] for c in inspect(eng).get_columns("strategy_store")}
    assert "owner_wallet" in cols
    assert "is_published" in cols
    pcols = {c["name"] for c in inspect(eng).get_columns("strategy_passports")}
    assert "owner_wallet" in pcols

    db.init_db()  # idempotent — no duplicate-column error
    cols2 = {c["name"] for c in inspect(eng).get_columns("strategy_store")}
    assert cols2 == cols


# ── Wallet threading: upsert + pipeline ──────────────────────────────────


def test_upsert_strategy_persists_lowercase_owner_and_defaults_unpublished():
    from archimedes.models.strategy_store import StrategyRecord, upsert_strategy

    with db.get_session() as session:
        record = upsert_strategy(
            session,
            generation_method="fusion",
            strategy_name="Owned",
            thesis="t",
            source_papers=[],
            asset_universe=["SPY"],
            owner_wallet=_W_OWNER,  # mixed case in — lowercase out
        )
        session.commit()
        sid = record.id

    with db.get_session() as session:
        row = session.query(StrategyRecord).filter_by(id=sid).first()
        assert row.owner_wallet == _W_OWNER.lower()
        assert row.is_published is False
        d = row.to_dict()
        assert d["owner_wallet"] == _W_OWNER.lower()
        assert d["is_published"] is False


def test_upsert_backfills_ownerless_row_but_never_reassigns():
    from archimedes.models.strategy_store import StrategyRecord, upsert_strategy

    kwargs = {
        "generation_method": "fusion",
        "strategy_name": "Backfill",
        "thesis": "t",
        "source_papers": [],
        "asset_universe": ["SPY"],
    }
    with db.get_session() as session:
        sid = upsert_strategy(session, **kwargs).id  # ownerless legacy row
        session.commit()
    with db.get_session() as session:
        upsert_strategy(session, **kwargs, owner_wallet=_W_OWNER)  # backfills
        session.commit()
    with db.get_session() as session:
        upsert_strategy(session, **kwargs, owner_wallet=_W_OTHER)  # must NOT steal
        session.commit()
    with db.get_session() as session:
        row = session.query(StrategyRecord).filter_by(id=sid).first()
        assert row.owner_wallet == _W_OWNER.lower()


class _FakeStore:
    """In-memory JobStore stand-in (same shape as test_generation_pipeline.py)."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.status: list[tuple[str, dict | None, str]] = []

    async def push_event(self, job_id, payload):
        self.events.append(payload)
        return len(self.events)

    async def update_status(self, job_id, status, *, result=None, error=""):
        self.status.append((status, result, error))


async def test_run_generation_threads_owner_wallet_to_store_and_passport(monkeypatch):
    """Fixture-path run_generation stamps the SIWE wallet (lowercased) onto every
    persisted strategy_store row AND its strategy_passports mirror."""
    from archimedes.agents.generation_pipeline import run_generation
    from archimedes.api.generate_schemas import GenerateBrief
    from archimedes.models.strategy_passport_record import StrategyPassportRecord
    from archimedes.models.strategy_store import StrategyRecord

    monkeypatch.setenv("GENERATION_PIPELINE_FIXTURE", "1")

    # Run persists inline (not on a worker thread) — deterministic under sqlite.
    async def _inline(fn, *a, **k):
        return fn(*a, **k)

    monkeypatch.setattr("archimedes.agents.generation_pipeline.asyncio.to_thread", _inline)

    store = _FakeStore()
    brief = GenerateBrief(intent="balanced treasuries", risk_appetite="conservative")

    # Mock the network boundary (yfinance-driven multi-year backtests), not the
    # persist path under test.
    with patch("archimedes.agents.generation_pipeline._backtest_and_persist", new=AsyncMock()):
        await run_generation(job_id="job_own_1", brief=brief, store=store, owner_wallet=_W_OWNER)

    with db.get_session() as session:
        rows = session.query(StrategyRecord).filter(StrategyRecord.is_example.is_(False)).all()
        assert rows, "pipeline persisted no strategies"
        for row in rows:
            assert row.owner_wallet == _W_OWNER.lower()
            assert row.is_published is False
        # Passport mirror for the GENERATED candidates only (the pipeline also
        # auto-ingests the curated library passports, which are rightly ownerless).
        generated_ids = [row.id for row in rows]
        passports = session.query(StrategyPassportRecord).filter(StrategyPassportRecord.id.in_(generated_ids)).all()
        assert len(passports) == len(generated_ids), "pipeline persisted no passport mirrors"
        for p in passports:
            assert p.owner_wallet == _W_OWNER.lower()


# ── Visibility: /api/strategies/generated + single GET ───────────────────


async def test_generated_list_requires_account_even_for_published_rows():
    _mk_strategy("pub00000000000001", owner=_W_OTHER, published=True)
    _mk_strategy("own00000000000001", owner=_W_OWNER, published=False)
    _mk_strategy("orp00000000000001", owner=None, published=False)  # legacy orphan

    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/strategies/generated")
    assert resp.status_code == 401


async def test_generated_list_owner_sees_own_unpublished():
    _mk_strategy("pub00000000000002", owner=_W_OTHER, published=True)
    _mk_strategy("own00000000000002", owner=_W_OWNER, published=False)
    _mk_strategy("oth00000000000002", owner=_W_OTHER, published=False)

    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/strategies/generated", cookies=_siwe_cookies(_W_OWNER))
    assert resp.status_code == 200
    ids = {s["id"] for s in resp.json()["strategies"]}
    assert ids == {"pub00000000000002", "own00000000000002"}  # not the other user's private row


async def test_detail_unpublished_404_unless_owner():
    sid = "det00000000000001"
    _mk_strategy(sid, owner=_W_OWNER, published=False)
    _mk_passport(sid)

    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        anon = await client.get(f"/api/strategies/{sid}")
        other = await client.get(f"/api/strategies/{sid}", cookies=_siwe_cookies(_W_OTHER))
        owner = await client.get(f"/api/strategies/{sid}", cookies=_siwe_cookies(_W_OWNER))
    assert anon.status_code == 404
    assert other.status_code == 404  # 404, not 403 — existence stays hidden
    assert owner.status_code == 200
    assert owner.json()["id"] == sid


async def test_detail_published_is_public():
    sid = "det00000000000002"
    _mk_strategy(sid, owner=_W_OWNER, published=True)
    _mk_passport(sid)

    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/strategies/{sid}")
    assert resp.status_code == 200


# ── Owner-gated rename ────────────────────────────────────────────────────


async def test_rename_owner_only():
    sid = "ren00000000000001"
    _mk_strategy(sid, owner=_W_OWNER, published=False, name="Before")

    from archimedes.main import app
    from archimedes.models.strategy_store import StrategyRecord

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        anon = await client.patch(f"/api/strategies/{sid}", json={"name": "After"})
        other = await client.patch(f"/api/strategies/{sid}", json={"name": "After"}, cookies=_siwe_cookies(_W_OTHER))
        owner = await client.patch(
            f"/api/strategies/{sid}", json={"name": "  After  "}, cookies=_siwe_cookies(_W_OWNER)
        )
    assert anon.status_code == 401
    assert other.status_code == 404  # unpublished → hidden from non-owners
    assert owner.status_code == 200
    assert owner.json()["strategy"]["strategy_name"] == "After"  # stripped

    with db.get_session() as session:
        assert session.query(StrategyRecord).filter_by(id=sid).first().strategy_name == "After"


async def test_rename_legacy_wallet_fallback_reclaims_row_onto_canonical_owner():
    """#1283: renaming a pre-account row via the legacy-wallet fallback must
    migrate it onto canonical account ownership in the same request, not just
    permit the rename and leave ``owner_user_id`` NULL forever."""
    sid = "ren00000000000004"
    _mk_strategy(sid, owner=_W_OWNER, published=False, name="Before")  # owner_user_id NULL

    from archimedes.main import app
    from archimedes.models.strategy_store import StrategyRecord

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.patch(f"/api/strategies/{sid}", json={"name": "After"}, cookies=_siwe_cookies(_W_OWNER))
    assert resp.status_code == 200

    expected_owner_user_id = f"legacy-test:{_W_OWNER.lower()}"
    with db.get_session() as session:
        row = session.query(StrategyRecord).filter_by(id=sid).first()
        assert row.strategy_name == "After"
        assert row.owner_user_id == expected_owner_user_id  # reclaimed, no longer NULL
        assert row.owner_wallet == _W_OWNER.lower()  # wallet provenance untouched


async def test_rename_legacy_wallet_fallback_reclaims_sibling_rows_but_not_other_wallets():
    """The reclaim triggered by a legacy-wallet rename is the SAME bulk claim
    wallet-linking performs (`claim_legacy_wallet_data`): every unclaimed row
    for that wallet migrates, not just the one row touched by the request —
    and a different wallet's unclaimed rows must be left alone."""
    renamed_sid = "sib00000000000001"
    _mk_strategy(renamed_sid, owner=_W_OWNER, published=False, name="Before")
    sibling_sid = "sib00000000000002"
    _mk_strategy(sibling_sid, owner=_W_OWNER, published=False, name="Sibling")  # same wallet, untouched by request
    other_wallet_sid = "sib00000000000003"
    _mk_strategy(other_wallet_sid, owner=_W_OTHER, published=False, name="Other")  # different wallet

    from archimedes.main import app
    from archimedes.models.strategy_store import StrategyRecord

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.patch(
            f"/api/strategies/{renamed_sid}", json={"name": "After"}, cookies=_siwe_cookies(_W_OWNER)
        )
    assert resp.status_code == 200

    expected_owner_user_id = f"legacy-test:{_W_OWNER.lower()}"
    with db.get_session() as session:
        renamed = session.query(StrategyRecord).filter_by(id=renamed_sid).first()
        sibling = session.query(StrategyRecord).filter_by(id=sibling_sid).first()
        other = session.query(StrategyRecord).filter_by(id=other_wallet_sid).first()
        assert renamed.owner_user_id == expected_owner_user_id
        assert sibling.owner_user_id == expected_owner_user_id  # reclaimed as a side effect
        assert other.owner_user_id is None  # a different wallet's data is never touched


async def test_rename_published_non_owner_403():
    sid = "ren00000000000002"
    _mk_strategy(sid, owner=_W_OWNER, published=True)

    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.patch(f"/api/strategies/{sid}", json={"name": "Hijack"}, cookies=_siwe_cookies(_W_OTHER))
    assert resp.status_code == 403


async def test_rename_rejects_bad_names_and_examples():
    sid = "ren00000000000003"
    _mk_strategy(sid, owner=_W_OWNER)
    ex_sid = "exa00000000000003"
    _mk_strategy(ex_sid, owner=_W_OWNER, example=True)

    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        empty = await client.patch(f"/api/strategies/{sid}", json={"name": "   "}, cookies=_siwe_cookies(_W_OWNER))
        too_long = await client.patch(
            f"/api/strategies/{sid}", json={"name": "x" * 81}, cookies=_siwe_cookies(_W_OWNER)
        )
        missing = await client.patch(f"/api/strategies/{sid}", json={}, cookies=_siwe_cookies(_W_OWNER))
        example = await client.patch(
            f"/api/strategies/{ex_sid}", json={"name": "Nope"}, cookies=_siwe_cookies(_W_OWNER)
        )
    assert empty.status_code == 422
    assert too_long.status_code == 422
    assert missing.status_code == 422
    assert example.status_code == 404  # curated examples are never renamable


# ── Purge script ──────────────────────────────────────────────────────────


def _load_purge_module():
    spec = importlib.util.spec_from_file_location("purge_orphan_generated", _PURGE_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _purge_fixture_rows():
    _mk_strategy("orphan0000000001", owner=None)  # orphan → purged
    _mk_passport("orphan0000000001")
    _mk_backtest("orphan0000000001")
    _mk_strategy("owned00000000001", owner=_W_OWNER)  # legacy wallet-owned → kept
    _mk_strategy("account000000001", owner_user="test-user")  # canonical account-owned → kept
    _mk_strategy("examp00000000001", owner=None, example=True)  # curated example → kept


def test_purge_dry_run_is_default_and_deletes_nothing(capsys):
    from archimedes.models.strategy_store import StrategyRecord

    _purge_fixture_rows()
    purge = _load_purge_module()

    summary = purge.purge_orphans()  # execute defaults to False
    assert summary == {"strategies": 1, "passports": 1, "backtests": 1, "executed": False}

    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "orphan0000000001" in out  # prints exactly what would be deleted
    assert "owned00000000001" not in out

    with db.get_session() as session:
        assert session.query(StrategyRecord).count() == 4  # nothing deleted


def test_purge_execute_deletes_orphans_and_cascades():
    from archimedes.models.backtest_store import BacktestResultRecord
    from archimedes.models.strategy_passport_record import PassportPaperRef, StrategyPassportRecord
    from archimedes.models.strategy_store import StrategyRecord

    _purge_fixture_rows()
    purge = _load_purge_module()

    summary = purge.purge_orphans(execute=True)
    assert summary == {"strategies": 1, "passports": 1, "backtests": 1, "executed": True}

    with db.get_session() as session:
        remaining = {r.id for r in session.query(StrategyRecord).all()}
        assert remaining == {"owned00000000001", "account000000001", "examp00000000001"}
        assert session.query(StrategyPassportRecord).filter_by(id="orphan0000000001").first() is None
        assert session.query(PassportPaperRef).filter_by(passport_id="orphan0000000001").count() == 0
        assert session.query(BacktestResultRecord).filter_by(strategy_id="orphan0000000001").count() == 0


# ── /passports must not bypass private-until-published (adversarial-verify
#    blocker fix): the passports table mirrors strategy_store ids, so these
#    endpoints must enforce the SAME visibility rule as /generated and
#    GET /api/strategies/{id}, and must not disclose owner wallets publicly. ──


def _mk_owned_passport(sid: str, *, owner: str | None = None, generation_method: str = "fusion"):
    from archimedes.models.strategy_passport_record import StrategyPassportRecord

    with db.get_session() as session:
        session.add(
            StrategyPassportRecord(
                id=sid,
                content_hash=("0z" + sid).ljust(66, "0"),
                generation_method=generation_method,
                methodology_summary="test",
                asset_universe="[]",
                owner_wallet=owner.lower() if owner else None,
            )
        )
        session.commit()


async def test_passports_list_enforces_private_until_published():
    _mk_strategy("ppb00000000000001", owner=_W_OTHER, published=True)
    _mk_owned_passport("ppb00000000000001", owner=_W_OTHER)
    _mk_strategy("ppv00000000000001", owner=_W_OWNER, published=False)
    _mk_owned_passport("ppv00000000000001", owner=_W_OWNER)

    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        anon = (await client.get("/api/strategies/passports")).json()["passports"]
        anon_ids = {p["id"] for p in anon}
        # The unpublished strategy must not be enumerable anonymously…
        assert "ppv00000000000001" not in anon_ids
        assert "ppb00000000000001" in anon_ids
        # …and the published one must not disclose its owner's wallet.
        pub = next(p for p in anon if p["id"] == "ppb00000000000001")
        assert "owner_wallet" not in pub

        owner = (await client.get("/api/strategies/passports", cookies=_siwe_cookies(_W_OWNER))).json()["passports"]
        owner_ids = {p["id"] for p in owner}
        assert "ppv00000000000001" in owner_ids  # owner sees their own private row
        mine = next(p for p in owner if p["id"] == "ppv00000000000001")
        assert mine.get("owner_wallet") == _W_OWNER.lower()  # visible to the owner only


async def test_passport_detail_404_hides_unpublished_from_non_owners():
    sid = "ppd00000000000001"
    _mk_strategy(sid, owner=_W_OWNER, published=False)
    _mk_owned_passport(sid, owner=_W_OWNER)

    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # The verifier's exact counterexample: id substitution into /passports/{id}.
        assert (await client.get(f"/api/strategies/passports/{sid}")).status_code == 404
        assert (
            await client.get(f"/api/strategies/passports/{sid}", cookies=_siwe_cookies(_W_OTHER))
        ).status_code == 404  # 404 (not 403) — existence stays hidden
        ok = await client.get(f"/api/strategies/passports/{sid}", cookies=_siwe_cookies(_W_OWNER))
        assert ok.status_code == 200
        assert ok.json().get("owner_wallet") == _W_OWNER.lower()


async def test_passport_curated_stays_public():
    _mk_owned_passport("pcu00000000000001", owner=None, generation_method="curated")

    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/strategies/passports/pcu00000000000001")
    assert resp.status_code == 200


async def test_generated_list_redacts_owner_wallet_for_non_owners():
    _mk_strategy("prd00000000000001", owner=_W_OTHER, published=True)

    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        non_owner = (await client.get("/api/strategies/generated", cookies=_siwe_cookies(_W_OWNER))).json()[
            "strategies"
        ]
        public_copy = next(s for s in non_owner if s["id"] == "prd00000000000001")
        assert "owner_wallet" not in public_copy
        owned = (await client.get("/api/strategies/generated", cookies=_siwe_cookies(_W_OTHER))).json()["strategies"]
        mine = next(s for s in owned if s["id"] == "prd00000000000001")
        assert mine.get("owner_wallet") == _W_OTHER.lower()
