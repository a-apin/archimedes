"""Tests for user profile API — WelcomeProfileModal backend.

Updated for Issue #181 (user-data minimization):
  - POST stores email encrypted at rest
  - GET without owner header strips PII (display_name, email, marketing_opt_in)
  - GET with X-Wallet-Address header matching the profile wallet returns full data
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from archimedes.db import get_session
from archimedes.models.user_profile import UserProfile
from fastapi.testclient import TestClient

# Unique wallets per test to avoid slowapi rate-limit collisions.
_W_ALICE = "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B"
_W_BOB = "0x2222222222222222222222222222222222222222"
_W_CHARLIE = "0x3333333333333333333333333333333333333333"
_W_UPDATE = "0x4444444444444444444444444444444444444444"
_W_MINIMAL = "0x0000000000000000000000000000000000000002"
_W_CASE = "0x5555555555555555555555555555555555555555"


@pytest.fixture
def client():
    """Create a test client with a fresh in-memory DB.

    Disables rate limiting so tests don't hit 429s from rapid
    sequential requests to the same endpoint from the same IP.
    """
    from archimedes.db import engine
    from archimedes.models.chat import Base

    Base.metadata.create_all(bind=engine)

    # Patch the limiter decorator to be a no-op during tests.
    with patch("archimedes.api.user_routes.limiter") as mock_limiter:
        mock_limiter.limit = lambda *a, **kw: lambda f: f
        from archimedes.main import app

        tc = TestClient(app)
        yield tc


class TestUserProfileRoutes:
    """GET and POST /api/user/profile."""

    def test_get_profile_returns_404_for_unknown_wallet(self, client):
        """Unknown wallet should return 404."""
        res = client.get("/api/user/profile/0x0000000000000000000000000000000000000001")
        assert res.status_code == 404

    def test_create_profile_with_display_name(self, client):
        """POST creates a profile and returns it (POST always returns owner view)."""
        res = client.post(
            "/api/user/profile",
            json={
                "wallet_address": _W_ALICE,
                "display_name": "Alice",
                "marketing_opt_in": False,
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["display_name"] == "Alice"
        assert data["wallet_address"] == _W_ALICE.lower()

    def test_get_profile_after_create_owner(self, client):
        """GET with owner header returns full profile after POST."""
        client.post(
            "/api/user/profile",
            json={
                "wallet_address": _W_BOB,
                "display_name": "Bob",
            },
        )
        # GET with owner header → full data
        res = client.get(
            f"/api/user/profile/{_W_BOB}",
            headers={"X-Wallet-Address": _W_BOB},
        )
        assert res.status_code == 200
        assert res.json()["display_name"] == "Bob"

    def test_get_profile_after_create_anonymous(self, client):
        """GET without owner header strips PII (Issue #181)."""
        client.post(
            "/api/user/profile",
            json={
                "wallet_address": _W_BOB,
                "display_name": "Bob",
            },
        )
        # GET without owner header → PII stripped
        res = client.get(f"/api/user/profile/{_W_BOB}")
        assert res.status_code == 200
        assert res.json()["display_name"] is None, "Anonymous GET must strip display_name"

    def test_create_profile_with_all_fields(self, client):
        """POST with every field populated. POST returns owner view (full data)."""
        res = client.post(
            "/api/user/profile",
            json={
                "wallet_address": _W_CHARLIE,
                "display_name": "Charlie",
                "email": "charlie@example.com",
                "interests": ["Equities", "Crypto"],
                "attribution": "Twitter",
                "marketing_opt_in": True,
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["display_name"] == "Charlie"
        assert data["email"] == "charlie@example.com"
        assert data["interests"] == ["Equities", "Crypto"]
        assert data["attribution"] == "Twitter"
        assert data["marketing_opt_in"] is True

    def test_email_stored_encrypted(self, client):
        """Verify email is encrypted in DB, not plaintext (Issue #181)."""
        email = "encrypt-test@example.com"
        client.post(
            "/api/user/profile",
            json={
                "wallet_address": _W_CHARLIE,
                "email": email,
            },
        )
        # Read directly from DB
        session = get_session()
        try:
            profile = session.query(UserProfile).filter(UserProfile.wallet_address == _W_CHARLIE.lower()).first()
            assert profile is not None
            assert profile.email != email, "Email must not be stored as plaintext"
            assert len(profile.email) > len(email), "Encrypted token must be longer"
        finally:
            session.close()

    def test_update_existing_profile(self, client):
        """POST to an existing profile updates fields."""
        client.post(
            "/api/user/profile",
            json={
                "wallet_address": _W_UPDATE,
                "display_name": "Original",
            },
        )
        res = client.post(
            "/api/user/profile",
            json={
                "wallet_address": _W_UPDATE,
                "display_name": "Updated",
                "marketing_opt_in": True,
            },
        )
        assert res.status_code == 200
        assert res.json()["display_name"] == "Updated"
        assert res.json()["marketing_opt_in"] is True

    def test_create_profile_minimal_fields(self, client):
        """POST with only wallet address (all optional fields null)."""
        res = client.post(
            "/api/user/profile",
            json={
                "wallet_address": _W_MINIMAL,
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["display_name"] is None
        assert data["email"] is None
        assert data["marketing_opt_in"] is False

    def test_invalid_wallet_address_rejected(self, client):
        """POST with invalid wallet address returns validation error."""
        res = client.post(
            "/api/user/profile",
            json={
                "wallet_address": "not-a-wallet",
                "display_name": "Test",
            },
        )
        assert res.status_code == 422

    def test_get_profile_case_insensitive(self, client):
        """GET finds profile regardless of wallet case."""
        client.post(
            "/api/user/profile",
            json={
                "wallet_address": _W_CASE,
                "display_name": "CaseTest",
            },
        )
        # Query with lowercase + owner header
        res = client.get(
            f"/api/user/profile/{_W_CASE.lower()}",
            headers={"X-Wallet-Address": _W_CASE},
        )
        assert res.status_code == 200
        assert res.json()["display_name"] == "CaseTest"
        # Query with original mixed case + owner header
        res2 = client.get(
            f"/api/user/profile/{_W_CASE}",
            headers={"X-Wallet-Address": _W_CASE},
        )
        assert res2.status_code == 200
        assert res2.json()["display_name"] == "CaseTest"

    def test_anonymous_get_does_not_reveal_email(self, client):
        """Anonymous GET must not reveal email (Issue #181)."""
        client.post(
            "/api/user/profile",
            json={
                "wallet_address": _W_CHARLIE,
                "email": "secret@example.com",
            },
        )
        res = client.get(f"/api/user/profile/{_W_CHARLIE}")
        assert res.status_code == 200
        assert res.json()["email"] is None, "Anonymous GET must not reveal email"

    def test_owner_get_decrypts_email(self, client):
        """Owner GET with matching header decrypts email (Issue #181)."""
        client.post(
            "/api/user/profile",
            json={
                "wallet_address": _W_CHARLIE,
                "email": "owner@example.com",
            },
        )
        res = client.get(
            f"/api/user/profile/{_W_CHARLIE}",
            headers={"X-Wallet-Address": _W_CHARLIE},
        )
        assert res.status_code == 200
        assert res.json()["email"] == "owner@example.com"
