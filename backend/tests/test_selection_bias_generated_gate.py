"""Tests for the GENERATED-strategy rigor gate (companion to test_selection_bias_routes.py).

``GET /api/selection-bias/gate/{strategy_id}`` previously 404d for every
GENERATED strategy (fusion/architect, persisted to ``strategy_store`` — not the
filesystem-backed curated ``LocalStrategyProvider`` the route otherwise grades).
That left the Strategy Passport's Deploy button + Rigor Strictness slider dead
for every generated strategy. These tests cover the fix
(``selection_bias_routes._generated_strategy_rigor``):

  1. A generated strategy with a real persisted backtest grades on ITS OWN
     context (never merged into the curated cohort) and returns 200 with a
     non-null ``min_passing_level``.
  2. Ownership (#850 pattern): an unpublished, non-owned strategy 404s for a
     non-owner — existence never leaks via a different response shape.
  3. "Pending Backtest": a strategy that exists but has no (or too few)
     persisted daily returns returns 200 with a MISSING-shaped result, not a
     404 and not a crash.
  4. An unknown id is still a plain 404 (no regression on the pre-existing
     curated-cohort-only behavior).
  5. The companion look-ahead-threading fix in
     ``live_rigor_gate.verdict_from_returns``: passing
     ``look_ahead_audit_passed=True`` can flip a verdict that would otherwise
     be blocked by the always-on look-ahead floor.

DB fixture follows the proven rebind pattern from test_strategy_ownership.py /
test_live_gate_returns.py: db.engine/SessionLocal are module-level globals
created once at import, so ``monkeypatch.setenv`` alone does not repoint them —
both must be rebound to a fresh per-test sqlite engine.
"""

from __future__ import annotations

import json
import time

import archimedes.db as db
import numpy as np
import pytest
from archimedes.api.auth_siwe import _COOKIE_NAME, _sign_session
from httpx import ASGITransport, AsyncClient

_W_OWNER = "0xAbC1230000000000000000000000000000000A"  # mixed case on purpose
_W_OTHER = "0x0000000000000000000000000000000000000B"


@pytest.fixture(autouse=True)
def _use_tmp_db(tmp_path, monkeypatch):
    """Point the DB at a FRESH temp sqlite (rebinds db.engine + db.SessionLocal).

    db.engine/SessionLocal are created once at import, so setenv alone doesn't
    re-point them; rebind both to a per-test engine, then init_db() registers
    every table (incl. the passport/backtest side-effect imports).
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    url = f"sqlite:///{tmp_path / 'generated_gate.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    eng = create_engine(url, connect_args={"check_same_thread": False})
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=eng, autocommit=False, autoflush=False))
    db.init_db()
    yield


def _siwe_cookies(wallet: str) -> dict[str, str]:
    """Valid signed SIWE session cookie for `wallet` (same helper shape as
    test_user_routes.py / test_strategy_ownership.py — a real signed session,
    not header spoofing)."""
    return {_COOKIE_NAME: _sign_session(wallet, time.time())}


def _mk_strategy(
    sid: str,
    *,
    owner: str | None = None,
    published: bool = False,
    example: bool = False,
    name: str = "Generated Test Strategy",
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
            is_published=published,
        )
        session.add(row)
        session.commit()


def _strong_returns(seed: int = 42, n: int = 300) -> list[float]:
    """A realistic, comfortably-passing daily-return series (deterministic)."""
    return np.random.default_rng(seed).normal(0.0018, 0.006, n).tolist()


def _mk_backtest(
    sid: str,
    *,
    returns: list[float] | None,
    num_trials: int | None = 1,
    pbo: float | None = 0.05,
    look_ahead: bool = True,
):
    """Persist a BacktestResultRecord whose artifact_json carries daily_returns
    (mirrors how the real pipeline / analytics-engine writes them — see
    backend/archimedes/services/backtest_repository.get_daily_returns)."""
    from archimedes.models.backtest_store import BacktestResultRecord

    artifact_json = None
    if returns is not None:
        artifact_json = json.dumps({"results": [{"metrics": {"daily_returns": returns}}]})

    with db.get_session() as session:
        session.add(
            BacktestResultRecord(
                strategy_id=sid,
                content_hash=f"bt_{sid}",
                artifact_json=artifact_json,
                num_trials_in_selection=num_trials,
                pbo_score=pbo,
                look_ahead_audit_passed=look_ahead,
                source_pipeline="test",
            )
        )
        session.commit()


# ── 1. Found + graded: real persisted backtest → 200, non-null min_passing_level ──


async def test_generated_strategy_found_and_graded_not_404():
    sid = "gen0000000000001"
    _mk_strategy(sid, owner=_W_OWNER, published=False)
    _mk_backtest(sid, returns=_strong_returns())

    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/selection-bias/gate/{sid}", cookies=_siwe_cookies(_W_OWNER))

    assert resp.status_code == 200
    body = resp.json()
    assert body["strategy_id"] == sid
    assert body["min_passing_level"] is not None
    # Real numbers, not the MISSING/pending shape.
    assert body["gate_details"]["dsr"] != "MISSING (no backtest data)"
    assert body["dsr_p_value"] is not None


async def test_generated_strategy_published_visible_to_anonymous_caller():
    sid = "gen0000000000002"
    _mk_strategy(sid, owner=_W_OWNER, published=True)
    _mk_backtest(sid, returns=_strong_returns())

    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/selection-bias/gate/{sid}")  # no cookie

    assert resp.status_code == 200
    assert resp.json()["min_passing_level"] is not None


# ── 2. Ownership: unpublished + non-owner (and anonymous) → 404, existence hidden ──


async def test_generated_strategy_ownership_hides_existence():
    sid = "gen0000000000003"
    _mk_strategy(sid, owner=_W_OWNER, published=False)
    _mk_backtest(sid, returns=_strong_returns())

    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        anon = await client.get(f"/api/selection-bias/gate/{sid}")
        other = await client.get(f"/api/selection-bias/gate/{sid}", cookies=_siwe_cookies(_W_OTHER))
        owner = await client.get(f"/api/selection-bias/gate/{sid}", cookies=_siwe_cookies(_W_OWNER))

    assert anon.status_code == 404
    assert other.status_code == 404  # 404, not 403 — existence stays hidden
    assert owner.status_code == 200


# ── 3. Pending: strategy exists but no (or too little) backtest data ─────────


async def test_generated_strategy_no_backtest_row_is_missing_shaped_not_404():
    sid = "gen0000000000004"
    _mk_strategy(sid, owner=_W_OWNER, published=False)
    # No BacktestResultRecord at all.

    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/selection-bias/gate/{sid}", cookies=_siwe_cookies(_W_OWNER))

    assert resp.status_code == 200
    body = resp.json()
    assert body["min_passing_level"] is None
    assert body["blocked_by_floor"] is False
    assert body["passes_all"] is False
    assert body["gate_details"]["dsr"] == "MISSING (no backtest data)"
    assert body["gate_details"]["look_ahead"] == "MISSING (no code)"


async def test_generated_strategy_too_few_returns_is_missing_shaped_not_404():
    sid = "gen0000000000005"
    _mk_strategy(sid, owner=_W_OWNER, published=False)
    _mk_backtest(sid, returns=[0.001] * 5)  # < 10 observations

    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/selection-bias/gate/{sid}", cookies=_siwe_cookies(_W_OWNER))

    assert resp.status_code == 200
    body = resp.json()
    assert body["min_passing_level"] is None
    assert body["gate_details"]["pbo"] == "MISSING (no backtest data)"


# ── 4. Unknown id is still a plain 404 (no regression) ────────────────────────


async def test_unknown_id_still_404():
    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/selection-bias/gate/totally-unknown-id-xyz")

    assert resp.status_code == 404


# ── 5. Look-ahead threading: verdict_from_returns(look_ahead_audit_passed=True) ──


def test_look_ahead_override_flips_blocked_verdict_to_pass():
    """Without the override, strategy_code=None forces look_ahead_passed=False
    unconditionally, tripping the always-on floor (blocked_by_floor=True) no
    matter how strong the DSR/PBO/OOS numbers are. Passing the pipeline's own
    closed-DSL self-attestation through fixes that — the companion bug fix in
    live_rigor_gate.verdict_from_returns (backend/archimedes/agents/
    generation_pipeline.py:1436 wires this from the already-computed
    result.look_ahead_audit_passed)."""
    from archimedes.services.live_rigor_gate import verdict_from_returns
    from archimedes.services.rigor_evaluator import compute_pbo

    a = np.random.default_rng(0).normal(0.0015, 0.004, 500).tolist()
    b = np.random.default_rng(1).normal(0.0015, 0.004, 500).tolist()
    pbo = compute_pbo({"a": a, "b": b})

    without = verdict_from_returns("a", a, num_trials=2, pbo_scores=pbo)
    assert without.status == "fail"
    assert without.passes is False
    assert without.blocked_by_floor is True

    with_override = verdict_from_returns("a", a, num_trials=2, pbo_scores=pbo, look_ahead_audit_passed=True)
    assert with_override.status == "pass"
    assert with_override.passes is True
    assert with_override.blocked_by_floor is False
