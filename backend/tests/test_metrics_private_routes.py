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

**Round 4 review finding — the wallet-resolver shim gap.** Every 200/403 case
above runs behind ``conftest.py``'s autouse ``_legacy_siwe_test_adapter``,
which monkeypatches ``wallet_routes.get_linked_wallet_address`` to derive the
wallet straight from the SIWE cookie — so session identity and linked wallet
agree BY CONSTRUCTION in every test in this file, and the REAL production
resolver (``get_current_user`` -> a DB-backed ``LinkedWallet`` lookup keyed on
``user_id`` + ``chain_id``, with an ``X-Wallet-Address`` header override and an
``is_primary`` fallback) has zero behavioral coverage — a regression there
(a bad filter, a dropped ``is_primary`` check, a broken chain_id parse) would
pass every test in this file while silently changing who the gate admits in
production. The ``*_via_the_real_wallet_resolver`` tests below restore the
production ``get_linked_wallet_address`` for their own scope and seed a real
``AuthUser`` + ``LinkedWallet`` row into a tmp-sqlite DB (the
``redirect_to_tmp_sqlite`` precedent, ``test_engagement_metrics.py`` /
``tests/db_isolation.py``) so the DB lookup itself actually runs.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from archimedes.api import wallet_routes
from archimedes.api.auth_siwe import _COOKIE_NAME, _sign_session
from archimedes.api.wallet_routes import get_linked_wallet_address as _real_get_linked_wallet_address
from fastapi.testclient import TestClient

from tests.db_isolation import redirect_to_tmp_sqlite

_ADMIN_WALLET = "0x1111111111111111111111111111111111111111"
_NON_ADMIN_WALLET = "0x2222222222222222222222222222222222222222"
# A THIRD wallet, used only as the SIWE-cookie session identity in the
# real-resolver tests below — distinct from both wallets above so a passing
# test can only be explained by the DB-backed LinkedWallet lookup actually
# running (as opposed to the gate somehow keying off the session cookie's own
# wallet, which is what every OTHER test in this file effectively does via
# the autouse shim).
_SESSION_IDENTITY_WALLET = "0x3333333333333333333333333333333333333333"


def _siwe_cookies(wallet: str) -> dict[str, str]:
    """Build a valid SIWE session cookie for `wallet` (copied from test_user_routes)."""
    return {_COOKIE_NAME: _sign_session(wallet, time.time())}


@pytest.fixture
def tmp_db(tmp_path):
    yield from redirect_to_tmp_sqlite(tmp_path)


def _seed_linked_wallet(*, session_wallet: str, linked_address: str, is_primary: bool = True) -> None:
    """Seed the AuthUser + LinkedWallet rows the REAL get_linked_wallet_address
    needs: an account matching the id the autouse shim's `legacy_session`
    would derive from `session_wallet`'s SIWE cookie, with `linked_address`
    as its (chain_id=5042002) linked wallet.
    """
    from archimedes.db import get_session
    from archimedes.models.account import AuthUser, LinkedWallet

    now_dt = datetime.now(UTC)
    user_id = f"legacy-test:{session_wallet}"
    with get_session() as session:
        session.add(
            AuthUser(
                id=user_id,
                name=user_id,
                email=f"{session_wallet[2:10]}@legacy.test",
                email_verified=True,
                created_at=now_dt,
                updated_at=now_dt,
            )
        )
        session.add(
            LinkedWallet(
                user_id=user_id,
                normalized_identity=f"5042002:{linked_address}",
                address=linked_address,
                display_address=linked_address,
                chain_id=5042002,
                provider="metamask",
                is_primary=is_primary,
                verified_at=now_dt,
                created_at=now_dt,
                updated_at=now_dt,
            )
        )
        session.commit()


@pytest.fixture
def client(monkeypatch):
    """Test client over the real app, with the user-count boundary pinned to a known value.

    ``get_distinct_user_count`` is imported by-name into ``metrics_private_routes``, so we
    patch it where it is *used*, not where it is defined — otherwise a sibling test that
    seeds real ``user_profiles`` rows into the shared in-memory DB would leak into the
    assertion (order-dependent flake). ``metrics_routes`` (the public ``/api/metrics``
    endpoint) imports the honest-null variant instead (round 4 fix — see
    ``services/user_stats.py``), so it is patched by ITS name there.

    ``PLATFORM_ADMIN_WALLETS`` is pinned to ``_ADMIN_WALLET`` so admin-gate tests
    are hermetic (independent of whatever a real deploy's env sets).
    """
    from archimedes.main import app

    monkeypatch.setenv("PLATFORM_ADMIN_WALLETS", _ADMIN_WALLET)
    with (
        patch("archimedes.api.metrics_private_routes.get_distinct_user_count", return_value=2),
        patch("archimedes.api.metrics_routes.get_distinct_user_count_or_none", return_value=2),
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


# ── /whoami — the admin-gate probe the frontend calls on entry to Insights
# (owner directive 2026-08-20: /app/insights is now admin-only; supersedes
# #1028 D8). Six-case pattern mirrors /cost above. ─────────────────────────


def test_whoami_401_without_session(client):
    """Anonymous GET /api/metrics/private/whoami → 401 (no SIWE session)."""
    res = client.get("/api/metrics/private/whoami")
    assert res.status_code == 401


def test_whoami_401_with_bad_cookie(client):
    """A forged/garbage session cookie must not authenticate → 401."""
    res = client.get("/api/metrics/private/whoami", cookies={_COOKIE_NAME: "not-a-valid-token"})
    assert res.status_code == 401


def test_whoami_403_for_non_admin_wallet(client):
    """A verified SIWE session for a wallet NOT in PLATFORM_ADMIN_WALLETS → 403.

    This is the exact case the frontend gate depends on: a signed-in,
    non-admin visitor probing /app/insights must get a 4xx here so the page
    falls through to the unknown-page treatment instead of rendering.
    """
    res = client.get("/api/metrics/private/whoami", cookies=_siwe_cookies(_NON_ADMIN_WALLET))
    assert res.status_code == 403


def test_whoami_403_for_signed_in_account_with_no_linked_wallet(client, monkeypatch):
    """A valid session whose account has NO linked wallet (docs/api/admin-
    private.md step 2) → 403 "A verified linked wallet is required" — a
    DIFFERENT 403 than the admin-membership one above.

    This file's `client` fixture builds sessions through the autouse
    `_legacy_siwe_test_adapter` (conftest.py), which monkeypatches
    `wallet_routes.get_linked_wallet_address` to derive the wallet straight
    from the SIWE cookie — so session and linked wallet agree BY
    CONSTRUCTION in every other test here, and `require_linked_wallet`'s own
    403 branch (the actual `LinkedWallet` DB lookup / X-Wallet-Address
    header / is_primary fallback in production) was never exercised by any
    test on this router. Re-overriding the shim here to return `None`
    (mid-request the account clearly still exists — the cookie verifies —
    it simply has no linked wallet) reaches that branch directly.
    """
    from archimedes.api import wallet_routes

    monkeypatch.setattr(wallet_routes, "get_linked_wallet_address", lambda request: None)
    res = client.get("/api/metrics/private/whoami", cookies=_siwe_cookies(_ADMIN_WALLET))
    assert res.status_code == 403
    assert res.json()["detail"] == "A verified linked wallet is required"


def test_whoami_200_with_valid_admin_siwe_session(client):
    """Valid SIWE session for an admin wallet → 200 {admin: true, wallet}."""
    res = client.get("/api/metrics/private/whoami", cookies=_siwe_cookies(_ADMIN_WALLET))
    assert res.status_code == 200
    body = res.json()
    assert body["admin"] is True
    assert body["wallet"] == _ADMIN_WALLET.lower()


def test_whoami_admin_wallets_parsed_case_insensitively(client, monkeypatch):
    """Admin match is case-insensitive, mirroring wallet_can_publish's parsing."""
    monkeypatch.setenv("PLATFORM_ADMIN_WALLETS", _ADMIN_WALLET.upper())
    res = client.get("/api/metrics/private/whoami", cookies=_siwe_cookies(_ADMIN_WALLET))
    assert res.status_code == 200
    assert res.json()["admin"] is True


# ── Real wallet resolver (round 4 fix — see the module docstring) ─────────
# The tests above all run behind the autouse SIWE-cookie shim, so
# `require_linked_wallet`'s actual DB-backed lookup has never executed. These
# restore the PRODUCTION `get_linked_wallet_address` and seed a real
# AuthUser + LinkedWallet row: the SIWE cookie only proves "there is a
# session for legacy-test:<_SESSION_IDENTITY_WALLET>" — admin/non-admin
# status is determined ENTIRELY by which address that account's LinkedWallet
# row carries, not by anything in the cookie itself.


def test_whoami_200_via_the_real_wallet_resolver_with_an_admin_linked_wallet(client, monkeypatch, tmp_db):
    """The DB-backed resolver, not the SIWE cookie's own wallet, must decide
    admin status: the session identity here is _SESSION_IDENTITY_WALLET (not
    on the admin allowlist), but its LINKED wallet is _ADMIN_WALLET.

    Mutation-verified: monkeypatching `get_linked_wallet_address` to always
    return None (simulating a broken lookup) makes this fail with `403 != 200`;
    monkeypatching it to always return `_SESSION_IDENTITY_WALLET` (simulating
    the resolver being silently bypassed in favor of the raw cookie wallet)
    makes it fail with `403 != 200` too, since that wallet isn't on the admin
    allowlist either — either mutation is caught.
    """
    monkeypatch.setattr(wallet_routes, "get_linked_wallet_address", _real_get_linked_wallet_address)
    _seed_linked_wallet(session_wallet=_SESSION_IDENTITY_WALLET, linked_address=_ADMIN_WALLET)

    res = client.get("/api/metrics/private/whoami", cookies=_siwe_cookies(_SESSION_IDENTITY_WALLET))
    assert res.status_code == 200
    body = res.json()
    assert body["admin"] is True
    # The reported wallet is the LINKED admin wallet, never the session
    # cookie's own (non-admin) wallet — proof the DB lookup, not the cookie,
    # produced this answer.
    assert body["wallet"] == _ADMIN_WALLET.lower()


def test_whoami_403_via_the_real_wallet_resolver_with_a_non_admin_linked_wallet(client, monkeypatch, tmp_db):
    """Same real-resolver wiring, but the linked wallet is NOT on the admin
    allowlist -> the admin-membership 403 (proving the resolver found a real
    linked wallet — this must NOT be the linked-wallet-missing 403).

    Mutation-verified: breaking the resolver wiring by monkeypatching
    `get_linked_wallet_address` back to a function that always returns None
    (simulating a broken DB lookup) makes this test fail with the WRONG 403
    detail ("A verified linked wallet is required" instead of "Admin access
    required."), and breaking it to always return _ADMIN_WALLET (simulating
    the resolver being bypassed entirely) makes it fail with `200 != 403`.
    """
    monkeypatch.setattr(wallet_routes, "get_linked_wallet_address", _real_get_linked_wallet_address)
    _seed_linked_wallet(session_wallet=_SESSION_IDENTITY_WALLET, linked_address=_NON_ADMIN_WALLET)

    res = client.get("/api/metrics/private/whoami", cookies=_siwe_cookies(_SESSION_IDENTITY_WALLET))
    assert res.status_code == 403
    assert res.json()["detail"] == "Admin access required."


def test_whoami_shape_has_no_extra_fields_and_never_reports_admin_false(client):
    """Shape guard: the response is exactly {admin, wallet} on success — no
    extra fields that could leak ops data through the probe endpoint, and
    'admin' is never anything but True (a non-admin never reaches a 200 at
    all — see the 403 case above; this negative control proves the guard by
    checking there is no code path that could emit {"admin": false})."""
    res = client.get("/api/metrics/private/whoami", cookies=_siwe_cookies(_ADMIN_WALLET))
    assert res.status_code == 200
    body = res.json()
    assert set(body.keys()) == {"admin", "wallet"}
    assert body["admin"] is True


# ── /engagement — dashboard v2 engagement/adoption tiles (admin-only) ──────


def test_engagement_401_without_session(client):
    res = client.get("/api/metrics/private/engagement")
    assert res.status_code == 401


def test_engagement_401_with_bad_cookie(client):
    """A forged/garbage session cookie must not authenticate → 401."""
    res = client.get("/api/metrics/private/engagement", cookies={_COOKIE_NAME: "not-a-valid-token"})
    assert res.status_code == 401


def test_engagement_403_for_non_admin_wallet(client):
    res = client.get("/api/metrics/private/engagement", cookies=_siwe_cookies(_NON_ADMIN_WALLET))
    assert res.status_code == 403


def test_engagement_403_for_signed_in_account_with_no_linked_wallet(client, monkeypatch):
    """Same gap as test_whoami_403_for_signed_in_account_with_no_linked_wallet
    above, on the second new endpoint — the linked-wallet half of the gate
    was mocked away for BOTH new routes, not just one."""
    from archimedes.api import wallet_routes

    monkeypatch.setattr(wallet_routes, "get_linked_wallet_address", lambda request: None)
    res = client.get("/api/metrics/private/engagement", cookies=_siwe_cookies(_ADMIN_WALLET))
    assert res.status_code == 403
    assert res.json()["detail"] == "A verified linked wallet is required"


def test_engagement_admin_wallets_parsed_case_insensitively(client, monkeypatch):
    """Admin match is case-insensitive, mirroring wallet_can_publish's parsing."""
    monkeypatch.setenv("PLATFORM_ADMIN_WALLETS", _ADMIN_WALLET.upper())
    with patch(
        "archimedes.api.metrics_private_routes.get_engagement_snapshot",
        return_value={"accounts": {"total": 0, "new_7d": 0, "new_30d": 0}},
    ):
        res = client.get("/api/metrics/private/engagement", cookies=_siwe_cookies(_ADMIN_WALLET))
    assert res.status_code == 200


def test_engagement_shape_keys_present(client):
    """Shape guard: every documented tile key is present on a 200 response."""
    fake_snapshot = {
        "accounts": {"total": 5, "new_7d": 1, "new_30d": 3},
        "linked_wallets": {"total": 2},
        "strategies": {"total": 10, "new_7d": 4, "daily_new": []},
        "generation_costs": {
            "measured_count": 3,
            "total_input_tokens": 100,
            "total_output_tokens": 50,
            "total_tokens": 150,
        },
        "paper_deployments": {"active": 1, "stopped": 0},
        "repeat_generation_users": {"generating_users": 2, "repeat_users": 1, "note": "x"},
        "payments": {"dry_run": True, "settled_volume_usd": None, "note": "dry-run"},
        "timestamp": "2026-08-20T00:00:00+00:00",
    }
    with patch("archimedes.api.metrics_private_routes.get_engagement_snapshot", return_value=fake_snapshot):
        res = client.get("/api/metrics/private/engagement", cookies=_siwe_cookies(_ADMIN_WALLET))
    assert res.status_code == 200
    body = res.json()
    for key in (
        "accounts",
        "linked_wallets",
        "strategies",
        "generation_costs",
        "paper_deployments",
        "repeat_generation_users",
        "payments",
        "authenticated_wallet",
        "timestamp",
    ):
        assert key in body, f"missing {key} in {list(body.keys())}"


def test_engagement_200_with_valid_admin_siwe_session(client):
    """Admin session → 200 with the composed engagement snapshot shape.

    Mocks the service-layer boundary (consistent with how this file mocks
    get_distinct_user_count above) — the query-level correctness of each
    tile is covered separately in test_engagement_metrics.py against real
    seeded DB rows.
    """
    fake_snapshot = {
        "accounts": {"total": 5, "new_7d": 1, "new_30d": 3},
        "linked_wallets": {"total": 2},
        "strategies": {"total": 10, "new_7d": 4, "daily_new": []},
        "generation_costs": {
            "measured_count": 3,
            "total_input_tokens": 100,
            "total_output_tokens": 50,
            "total_tokens": 150,
        },
        "paper_deployments": {"active": 1, "stopped": 0},
        "repeat_generation_users": {"generating_users": 2, "repeat_users": 1, "note": "x"},
        "payments": {"dry_run": True, "settled_volume_usd": None, "note": "dry-run"},
        "timestamp": "2026-08-20T00:00:00+00:00",
    }
    with patch("archimedes.api.metrics_private_routes.get_engagement_snapshot", return_value=fake_snapshot):
        res = client.get("/api/metrics/private/engagement", cookies=_siwe_cookies(_ADMIN_WALLET))
    assert res.status_code == 200
    body = res.json()
    assert body["accounts"]["total"] == 5
    assert body["payments"]["settled_volume_usd"] is None
    assert body["authenticated_wallet"] == _ADMIN_WALLET.lower()


# ── Real wallet resolver (round 4 fix — see the module docstring) ─────────
# /engagement's twin of the /whoami real-resolver tests above.


def test_engagement_200_via_the_real_wallet_resolver_with_an_admin_linked_wallet(client, monkeypatch, tmp_db):
    """Mirrors test_whoami_200_via_the_real_wallet_resolver_with_an_admin_linked_wallet
    on the /engagement route.

    Mutation-verified: monkeypatching `get_linked_wallet_address` to always
    return None makes this fail with `403 != 200`.
    """
    monkeypatch.setattr(wallet_routes, "get_linked_wallet_address", _real_get_linked_wallet_address)
    _seed_linked_wallet(session_wallet=_SESSION_IDENTITY_WALLET, linked_address=_ADMIN_WALLET)

    fake_snapshot = {
        "accounts": {"total": 1, "new_7d": 0, "new_30d": 1},
        "linked_wallets": {"total": 1},
        "strategies": {"total": 0, "new_7d": 0, "daily_new": []},
        "generation_costs": {"measured_count": 0, "total_input_tokens": 0, "total_output_tokens": 0, "total_tokens": 0},
        "paper_deployments": {"active": 0, "stopped": 0},
        "repeat_generation_users": {"generating_users": 0, "repeat_users": 0, "note": "x"},
        "payments": {"dry_run": True, "settled_volume_usd": None, "note": "dry-run"},
        "timestamp": "2026-08-20T00:00:00+00:00",
    }
    with patch("archimedes.api.metrics_private_routes.get_engagement_snapshot", return_value=fake_snapshot):
        res = client.get("/api/metrics/private/engagement", cookies=_siwe_cookies(_SESSION_IDENTITY_WALLET))
    assert res.status_code == 200
    assert res.json()["authenticated_wallet"] == _ADMIN_WALLET.lower()


def test_engagement_403_via_the_real_wallet_resolver_with_a_non_admin_linked_wallet(client, monkeypatch, tmp_db):
    """Mirrors test_whoami_403_via_the_real_wallet_resolver_with_a_non_admin_linked_wallet
    on the /engagement route: a real, DB-resolved linked wallet that simply
    isn't on the admin allowlist -> the admin-membership 403.

    Mutation-verified: monkeypatching `get_linked_wallet_address` to always
    return `_ADMIN_WALLET` (simulating the resolver being bypassed and the
    gate trusting the header/cookie instead) makes this fail with `200 != 403`.
    """
    monkeypatch.setattr(wallet_routes, "get_linked_wallet_address", _real_get_linked_wallet_address)
    _seed_linked_wallet(session_wallet=_SESSION_IDENTITY_WALLET, linked_address=_NON_ADMIN_WALLET)

    res = client.get("/api/metrics/private/engagement", cookies=_siwe_cookies(_SESSION_IDENTITY_WALLET))
    assert res.status_code == 403
    assert res.json()["detail"] == "Admin access required."


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
