"""Tests for the SIWE + platform-admin-gated private metrics routes (issue #830).

The public metrics endpoints (/api/metrics, /funnel, /visitors) are PII-free and
stay anonymous. The private cost/ops dashboard (/api/metrics/private/*) reuses the
existing SIWE session gate (auth_siwe.require_verified_wallet) AND additionally
requires ``PLATFORM_ADMIN_WALLETS`` membership (Insights-page fix — cost/ops data
is operationally sensitive, not just PII, so "any authenticated wallet" was too
wide a bar). Anonymous → 401; verified-but-non-admin → 403; verified admin → 200.

Hermetic: the SIWE session cookie is built with the real ``_sign_session`` (the
auth layer's own signer), exercising the production ``_verify_session`` gate
rather than a mock — the same ``_siwe_cookies`` helper pattern as
``test_user_routes.py``. The distinct-user count is mocked at the DB boundary so
no live Postgres is touched. ``PLATFORM_ADMIN_WALLETS`` is set via monkeypatch,
not read from a real ``.env``, so the admin allowlist is hermetic too.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from archimedes.api.auth_siwe import _COOKIE_NAME, _sign_session
from fastapi.testclient import TestClient

_ADMIN_WALLET = "0x1111111111111111111111111111111111111111"
_NON_ADMIN_WALLET = "0x2222222222222222222222222222222222222222"


def _siwe_cookies(wallet: str) -> dict[str, str]:
    """Build a valid SIWE session cookie for `wallet` (copied from test_user_routes)."""
    return {_COOKIE_NAME: _sign_session(wallet, time.time())}


@pytest.fixture
def client(monkeypatch):
    """Test client over the real app, with the user-count boundary pinned to a known value.

    ``get_distinct_user_count`` is imported by-name into the route modules, so we
    patch it where it is *used* (the route modules), not where it is defined —
    otherwise a sibling test that seeds real ``user_profiles`` rows into the shared
    in-memory DB would leak into the assertion (order-dependent flake).

    ``PLATFORM_ADMIN_WALLETS`` is pinned to ``_ADMIN_WALLET`` so admin-gate tests
    are hermetic (independent of whatever a real deploy's env sets).
    """
    from archimedes.main import app

    monkeypatch.setenv("PLATFORM_ADMIN_WALLETS", _ADMIN_WALLET)
    with (
        patch("archimedes.api.metrics_private_routes.get_distinct_user_count", return_value=2),
        patch("archimedes.api.metrics_routes.get_distinct_user_count", return_value=2),
    ):
        yield TestClient(app)


def test_private_cost_401_without_session(client):
    """Anonymous GET /api/metrics/private/cost → 401 (no SIWE session)."""
    res = client.get("/api/metrics/private/cost")
    assert res.status_code == 401


def test_private_cost_401_with_bad_cookie(client):
    """A forged/garbage session cookie must not authenticate → 401."""
    res = client.get("/api/metrics/private/cost", cookies={_COOKIE_NAME: "not-a-valid-token"})
    assert res.status_code == 401


def test_private_cost_403_for_non_admin_wallet(client):
    """A verified SIWE session for a wallet NOT in PLATFORM_ADMIN_WALLETS → 403.

    This is the core of the Insights-page lockdown: before this fix, ANY
    authenticated wallet could read Bedrock/infra/cost-per-user data. Now only
    admin wallets can.
    """
    res = client.get("/api/metrics/private/cost", cookies=_siwe_cookies(_NON_ADMIN_WALLET))
    assert res.status_code == 403


def test_private_cost_200_with_valid_admin_siwe_session(client):
    """Valid SIWE session for an admin wallet → 200, with the honest real-users denominator present."""
    res = client.get("/api/metrics/private/cost", cookies=_siwe_cookies(_ADMIN_WALLET))
    assert res.status_code == 200
    body = res.json()
    # The honest distinct-user count is present (denominator for any per-user $).
    assert body["real_users"] == 2
    assert body["authenticated_wallet"] == _ADMIN_WALLET.lower()
    # Cost fields are draft placeholders until billing wiring lands.
    assert body["source"] == "draft"


def test_private_cost_admin_wallets_parsed_case_insensitively(client, monkeypatch):
    """Admin match is case-insensitive, mirroring wallet_can_publish's parsing."""
    monkeypatch.setenv("PLATFORM_ADMIN_WALLETS", _ADMIN_WALLET.upper())
    res = client.get("/api/metrics/private/cost", cookies=_siwe_cookies(_ADMIN_WALLET))
    assert res.status_code == 200


def test_public_metrics_stays_public_and_pii_free(client):
    """The public /api/metrics endpoint is anonymous + PII-free (diagnosis #5)."""
    res = client.get("/api/metrics")
    assert res.status_code == 200
    body = res.json()
    # Request tallies + the honest real-users count; no PII.
    assert "human_count" in body
    assert "agent_count" in body
    assert body["real_users"] == 2
    assert "email" not in body
    assert "display_name" not in body
