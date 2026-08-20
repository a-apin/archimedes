"""``POST /api/rigor/verify`` — the CLI ``verify`` command backend (#1305).

Covers the honesty contract this endpoint exists to enforce:

  1. every route 401s without a Better Auth session;
  2. DSR and walk-forward OOS consistency are EVALUABLE and reuse the exact
     same gate functions/thresholds the passport verdict uses
     (``compute_dsr_hac_and_iid`` gated on ``DSR_P_FLOOR``,
     ``compute_oos_sharpe`` gated on ``OOS_ABS_FLOOR``) — never new thresholds;
  3. PBO and look-ahead are ALWAYS ``not_evaluable`` for a bare returns series
     — never silently passed, never defaulted, never reported as a FAIL they
     were never evaluated for;
  4. ``passes`` is true only when no evaluable check failed AND at least one
     check was evaluable (a request where nothing was evaluable must not read
     as passing by vacuous truth);
  5. ``trials`` is self-attested, `>= 1` enforced at the schema (0 is a 422,
     not a silently-accepted degenerate deflation);
  6. the ADVERSARIAL demonstration the repo's guard-review rule requires: a
     synthetic series constructed to FAIL both evaluable checks is shown
     failing both, not merely "not passing" for an unrelated reason.

Hermetic: mocks only the Better Auth session fetch (the account-session
boundary); the rigor math runs for real against synthetic numpy series with a
fixed seed, so the DSR/OOS numbers below are reproducible, not fixtures.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import numpy as np
import pytest
from archimedes.api import account_auth, rigor_verify_routes
from fastapi import FastAPI

# ── Synthetic, reproducible fixture series ──────────────────────────────
# Deterministic (fixed seed) so CI and local runs see byte-identical DSR/OOS
# numbers — not tuned by hand to "happen" to pass/fail.

_STRONG_SERIES = np.random.default_rng(7).normal(0.0015, 0.004, 300).tolist()  # DSR PASS, OOS PASS
_WEAK_SERIES = np.random.default_rng(11).normal(-0.002, 0.01, 300).tolist()  # DSR FAIL, OOS FAIL
_SHORT_SERIES = [0.01, -0.02, 0.015]  # T=3: DSR needs T>=4, OOS needs T>=10 -> both not_evaluable


def _returns_body(series: list[float], trials: int = 1) -> dict:
    base = datetime(2024, 1, 1)
    return {
        "returns": [
            {"date": (base + timedelta(days=i)).date().isoformat(), "daily_return": r} for i, r in enumerate(series)
        ],
        "trials": trials,
    }


@pytest.fixture()
def app():
    application = FastAPI()
    application.middleware("http")(account_auth.better_auth_session_middleware)
    application.include_router(rigor_verify_routes.rigor_verify_router)
    return application


def _session_for(user_id: str):
    async def fetch(_request):
        return {
            "user": {"id": user_id, "name": user_id, "email": f"{user_id}@example.com", "emailVerified": True},
            "session": {"id": f"s-{user_id}", "expiresAt": (datetime.now(UTC) + timedelta(hours=1)).isoformat()},
        }

    return fetch


def _sign_in(monkeypatch, user_id: str = "user-1"):
    monkeypatch.setattr(account_auth, "_fetch_session", _session_for(user_id))


def _client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"cookie": "better-auth.session_token=opaque", "host": "archimedes-arc.com"},
    )


# ── 1. Auth gate ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_verify_requires_a_better_auth_session(app, monkeypatch):
    monkeypatch.setattr(account_auth, "_fetch_session", AsyncMock(return_value=None))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/rigor/verify", json=_returns_body(_STRONG_SERIES))
    assert resp.status_code == 401


# ── 2/3/4. The honesty contract ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_strong_lookahead_free_series_passes_both_evaluable_checks(app, monkeypatch):
    _sign_in(monkeypatch)
    async with _client(app) as client:
        resp = await client.post("/api/rigor/verify", json=_returns_body(_STRONG_SERIES))
    assert resp.status_code == 200
    body = resp.json()

    assert body["dsr"]["status"] == "pass"
    assert body["dsr"]["dsr_p_value"] >= 0.50  # DSR_P_FLOOR, reused not reinvented
    assert body["oos_consistency"]["status"] == "pass"
    assert body["oos_consistency"]["oos_sharpe"] > 0.0

    # PBO and look-ahead are structurally not_evaluable for a bare series —
    # even on a strong, clean series, never silently passed.
    assert body["pbo"]["status"] == "not_evaluable"
    assert body["pbo"]["reason"]
    assert body["look_ahead"]["status"] == "not_evaluable"
    assert body["look_ahead"]["reason"]

    assert body["passes"] is True
    assert body["trials"] == 1
    assert body["self_attested"] is True


@pytest.mark.asyncio
async def test_weak_series_fails_dsr_and_oos_the_adversarial_demonstration(app, monkeypatch):
    """The guard-review rule (CLAUDE.md): show the input that SHOULD fail the
    guard actually failing it — not merely absent from the passing case."""
    _sign_in(monkeypatch)
    async with _client(app) as client:
        resp = await client.post("/api/rigor/verify", json=_returns_body(_WEAK_SERIES))
    assert resp.status_code == 200
    body = resp.json()

    assert body["dsr"]["status"] == "fail"
    assert body["dsr"]["dsr_p_value"] < 0.50
    assert body["oos_consistency"]["status"] == "fail"
    assert body["oos_consistency"]["oos_sharpe"] <= 0.0

    assert body["pbo"]["status"] == "not_evaluable"
    assert body["look_ahead"]["status"] == "not_evaluable"

    assert body["passes"] is False


@pytest.mark.asyncio
async def test_synthetic_failing_series_pbo_is_not_evaluable_never_a_default_pass_or_fail(app, monkeypatch):
    """Mirrors the issue's stated acceptance check: on a failing series,
    pbo.status == "not_evaluable" — never silently passed, never a FAIL for a
    check that was structurally never run."""
    _sign_in(monkeypatch)
    async with _client(app) as client:
        resp = await client.post("/api/rigor/verify", json=_returns_body(_WEAK_SERIES))
    body = resp.json()
    assert body["passes"] is False
    assert body["pbo"]["status"] == "not_evaluable"


@pytest.mark.asyncio
async def test_too_short_series_neither_evaluable_check_runs_and_passes_is_false(app, monkeypatch):
    """No evaluable check at all — passes must be False by construction
    (vacuous truth is not honesty), not True because nothing failed."""
    _sign_in(monkeypatch)
    async with _client(app) as client:
        resp = await client.post("/api/rigor/verify", json=_returns_body(_SHORT_SERIES))
    assert resp.status_code == 200
    body = resp.json()

    assert body["dsr"]["status"] == "not_evaluable"
    assert body["oos_consistency"]["status"] == "not_evaluable"
    assert body["pbo"]["status"] == "not_evaluable"
    assert body["look_ahead"]["status"] == "not_evaluable"
    assert body["passes"] is False  # nothing evaluable -> never vacuously "passes"


# ── 5. trials validation ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_trials_zero_is_rejected_422(app, monkeypatch):
    """trials is self-attested but must be >= 1 — a degenerate/absent trial
    count must not silently pass schema validation and reach the DSR math."""
    _sign_in(monkeypatch)
    async with _client(app) as client:
        resp = await client.post("/api/rigor/verify", json=_returns_body(_STRONG_SERIES, trials=0))
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_trials_default_is_one_and_is_echoed_self_attested(app, monkeypatch):
    _sign_in(monkeypatch)
    body = _returns_body(_STRONG_SERIES)
    del body["trials"]  # omit -> default
    async with _client(app) as client:
        resp = await client.post("/api/rigor/verify", json=body)
    assert resp.status_code == 200
    assert resp.json()["trials"] == 1


@pytest.mark.asyncio
async def test_higher_declared_trials_deflates_dsr_p_value(app, monkeypatch):
    """The DSR must actually respond to the declared trial count (deflation is
    real, not decorative) — more trials -> harder to clear the same floor."""
    _sign_in(monkeypatch)
    async with _client(app) as client:
        p1 = (await client.post("/api/rigor/verify", json=_returns_body(_STRONG_SERIES, trials=1))).json()["dsr"][
            "dsr_p_value"
        ]
        p50 = (await client.post("/api/rigor/verify", json=_returns_body(_STRONG_SERIES, trials=50))).json()["dsr"][
            "dsr_p_value"
        ]
    assert p50 < p1


# ── empty returns rejected ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_returns_rejected_422(app, monkeypatch):
    _sign_in(monkeypatch)
    async with _client(app) as client:
        resp = await client.post("/api/rigor/verify", json={"returns": [], "trials": 1})
    assert resp.status_code == 422
