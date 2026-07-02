"""Tests for the SIWE gate on expensive LLM-generation endpoints.

The gate (``gate_generation``) is controlled by REQUIRE_SIWE_FOR_GENERATION and
is now SECURE BY DEFAULT (2026-07 flip): with the flag unset, production requires
a verified SIWE session. Two carve-outs keep everything else working:
  - an explicit ``false`` opts out (local dev / demo — documented in .env.example);
  - under TESTING (the hermetic suite, set by conftest) an UNSET flag stays off,
    mirroring the slowapi-limiter / wallet-less-quota TESTING treatment — an
    explicit true/false always wins over the carve-out.
These tests exercise the dependency in isolation against a minimal app (no
Redis / job store needed), minting a session cookie with the same in-process
HMAC key the verifier uses.
"""

import time

from archimedes.api.auth_siwe import _COOKIE_NAME, _generation_auth_required, _sign_session, gate_generation
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient


def _make_client() -> TestClient:
    app = FastAPI()

    @app.post("/g")
    async def _g(wallet: str | None = Depends(gate_generation)):
        return {"wallet": wallet}

    return TestClient(app)


def test_gate_off_allows_anonymous(monkeypatch):
    """Flag unset under TESTING (conftest sets it): anonymous callers pass through."""
    monkeypatch.delenv("REQUIRE_SIWE_FOR_GENERATION", raising=False)
    resp = _make_client().post("/g")
    assert resp.status_code == 200
    assert resp.json()["wallet"] is None


# ── The 2026-07 secure-by-default flip ──────────────────────────────


def test_default_is_secure_outside_testing(monkeypatch):
    """Flag unset AND no TESTING (production shape): the gate is ON — anon → 401."""
    monkeypatch.delenv("REQUIRE_SIWE_FOR_GENERATION", raising=False)
    monkeypatch.delenv("TESTING", raising=False)
    assert _generation_auth_required() is True
    resp = _make_client().post("/g")
    assert resp.status_code == 401


def test_explicit_false_opts_out_outside_testing(monkeypatch):
    """REQUIRE_SIWE_FOR_GENERATION=false (the documented local-dev opt-out) keeps
    the open behavior even without TESTING."""
    monkeypatch.setenv("REQUIRE_SIWE_FOR_GENERATION", "false")
    monkeypatch.delenv("TESTING", raising=False)
    assert _generation_auth_required() is False
    resp = _make_client().post("/g")
    assert resp.status_code == 200
    assert resp.json()["wallet"] is None


def test_explicit_true_wins_over_testing_carveout(monkeypatch):
    """An explicit true is enforced even under TESTING — gating-on tests rely on this."""
    monkeypatch.setenv("REQUIRE_SIWE_FOR_GENERATION", "true")
    assert _generation_auth_required() is True


def test_default_secure_with_valid_session(monkeypatch):
    """Production default (gate on): a valid SIWE session is accepted."""
    monkeypatch.delenv("REQUIRE_SIWE_FOR_GENERATION", raising=False)
    monkeypatch.delenv("TESTING", raising=False)
    wallet = "0x" + "ef" * 20
    client = _make_client()
    client.cookies.set(_COOKIE_NAME, _sign_session(wallet, time.time()))
    resp = client.post("/g")
    assert resp.status_code == 200
    assert resp.json()["wallet"] == wallet.lower()


def test_gate_off_attributes_session_when_present(monkeypatch):
    """Flag off but a valid session present: best-effort attribution, still 200."""
    monkeypatch.delenv("REQUIRE_SIWE_FOR_GENERATION", raising=False)
    wallet = "0x" + "ab" * 20
    client = _make_client()
    client.cookies.set(_COOKIE_NAME, _sign_session(wallet, time.time()))
    resp = client.post("/g")
    assert resp.status_code == 200
    assert resp.json()["wallet"] == wallet.lower()


def test_gate_on_blocks_anonymous(monkeypatch):
    """Flag on: anonymous callers are rejected with 401."""
    monkeypatch.setenv("REQUIRE_SIWE_FOR_GENERATION", "true")
    resp = _make_client().post("/g")
    assert resp.status_code == 401


def test_gate_on_allows_valid_session(monkeypatch):
    """Flag on: a valid SIWE session cookie is accepted and returns the wallet."""
    monkeypatch.setenv("REQUIRE_SIWE_FOR_GENERATION", "1")
    wallet = "0x" + "cd" * 20
    client = _make_client()
    client.cookies.set(_COOKIE_NAME, _sign_session(wallet, time.time()))
    resp = client.post("/g")
    assert resp.status_code == 200
    assert resp.json()["wallet"] == wallet.lower()


def test_gate_on_rejects_tampered_session(monkeypatch):
    """Flag on: a cookie with a bad signature is rejected (401)."""
    monkeypatch.setenv("REQUIRE_SIWE_FOR_GENERATION", "yes")
    client = _make_client()
    client.cookies.set(_COOKIE_NAME, '{"wallet":"0xdeadbeef","iat":9999999999}|deadbeef')
    resp = client.post("/g")
    assert resp.status_code == 401
