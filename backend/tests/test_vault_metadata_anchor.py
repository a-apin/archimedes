"""Tests for strategy passport on-chain anchoring via POST /api/vaults/metadata.

Validates that store_vault_metadata triggers strategy_publisher.anchor()
for each strategy_id, handles missing passports/hashes gracefully, and
survives anchor failures without breaking the DB write.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

from archimedes.api.auth_siwe import _COOKIE_NAME, _sign_session
from archimedes.main import app
from fastapi.testclient import TestClient

client = TestClient(app)

_WALLET = "0x" + "a1" * 20


def _siwe_cookies(wallet: str = _WALLET) -> dict[str, str]:
    """A valid SIWE session cookie for `wallet` (the endpoints are now gated)."""
    return {_COOKIE_NAME: _sign_session(wallet.lower(), time.time())}


def _make_passport(sid: str, methodology_hash: str | None = "abcd1234" * 8):
    """Build a mock passport with the fields anchor() needs."""
    mock = MagicMock()
    mock.id = sid
    mock.methodology_hash = methodology_hash
    mock.regime_tag = "bull"
    paper = MagicMock()
    paper.arxiv_id = f"2301.{sid}"
    mock.papers = [paper]
    return mock


class TestVaultMetadataAnchor:
    @patch("archimedes.api.vaults_routes.strategy_publisher")
    @patch("archimedes.api.vaults_routes.strategy_provider")
    def test_metadata_post_calls_anchor_once_per_strategy_id(self, mock_provider, mock_publisher):
        # strategy_provider is now a lazily-cached accessor (strategy_provider()),
        # so the mocked callable's return_value stands in for the provider instance.
        mock_provider.return_value.get_strategy.side_effect = _make_passport
        mock_publisher.anchor = AsyncMock()

        resp = client.post(
            "/api/vaults/metadata",
            json={
                "vault_address": "0x" + "ab" * 20,
                "name": "Test Vault",
                "symbol": "tVLT",
                "strategy_ids": ["s1", "s2", "s3"],
            },
            cookies=_siwe_cookies(),
        )
        assert resp.status_code == 200

        # Give the fire-and-forget task time to execute
        asyncio.run(asyncio.sleep(0.1))

        assert mock_publisher.anchor.call_count == 3

    @patch("archimedes.api.vaults_routes.strategy_publisher")
    @patch("archimedes.api.vaults_routes.strategy_provider")
    def test_metadata_post_skips_passports_without_methodology_hash(self, mock_provider, mock_publisher):
        def get_strat(sid):
            if sid == "s2":
                return _make_passport(sid, methodology_hash=None)
            return _make_passport(sid)

        mock_provider.return_value.get_strategy.side_effect = get_strat
        mock_publisher.anchor = AsyncMock()

        resp = client.post(
            "/api/vaults/metadata",
            json={
                "vault_address": "0x" + "cd" * 20,
                "name": "Test Vault 2",
                "symbol": "tVL2",
                "strategy_ids": ["s1", "s2", "s3"],
            },
            cookies=_siwe_cookies(),
        )
        assert resp.status_code == 200

        asyncio.run(asyncio.sleep(0.1))
        # s2 skipped due to no methodology_hash
        assert mock_publisher.anchor.call_count == 2

    @patch("archimedes.api.vaults_routes.strategy_publisher")
    @patch("archimedes.api.vaults_routes.strategy_provider")
    def test_metadata_post_succeeds_when_anchor_raises(self, mock_provider, mock_publisher):
        mock_provider.return_value.get_strategy.side_effect = _make_passport
        mock_publisher.anchor = AsyncMock(side_effect=RuntimeError("simulated chain failure"))

        resp = client.post(
            "/api/vaults/metadata",
            json={
                "vault_address": "0x" + "ef" * 20,
                "name": "Test Vault 3",
                "symbol": "tVL3",
                "strategy_ids": ["s1"],
            },
            cookies=_siwe_cookies(),
        )
        # Handler still returns 200 — anchor failure is non-fatal
        assert resp.status_code == 200

    @patch("archimedes.api.vaults_routes.strategy_publisher")
    @patch("archimedes.api.vaults_routes.strategy_provider")
    def test_metadata_post_with_unknown_strategy_id_is_refused(self, mock_provider, mock_publisher):
        # The metadata route is the client-signed deploy path's rigor choke point:
        # an unknown/unverified strategy id can no longer be bound to a vault. It is
        # cleanly refused with 422 (not a crash) BEFORE any anchoring is attempted —
        # this is the server-side "you can never fully bypass the rigor gate" guarantee.
        mock_provider.return_value.get_strategy.return_value = None
        mock_publisher.anchor = AsyncMock()

        resp = client.post(
            "/api/vaults/metadata",
            json={
                "vault_address": "0x" + "11" * 20,
                "name": "Test Vault 4",
                "symbol": "tVL4",
                "strategy_ids": ["unknown_id"],
            },
            cookies=_siwe_cookies(),
        )
        assert resp.status_code == 422

        asyncio.run(asyncio.sleep(0.1))
        # Refused before persisting/anchoring — the unknown strategy is never anchored.
        mock_publisher.anchor.assert_not_called()


class TestVaultMetadataAuth:
    """Audit finding #8: /api/vaults/metadata triggers a backend-signed on-chain
    tx and was unauthenticated. It must require SIWE and be owner-scoped."""

    def test_metadata_post_requires_auth(self):
        resp = client.post(
            "/api/vaults/metadata",
            json={
                "vault_address": "0x" + "22" * 20,
                "name": "Unauthed",
                "symbol": "tUNA",
                "strategy_ids": [],
            },
        )
        assert resp.status_code == 401

    def test_metadata_post_rejects_non_owner_overwrite(self):
        vault = "0x" + "33" * 20
        owner = "0x" + "aa" * 20
        attacker = "0x" + "bb" * 20

        # Owner claims the vault metadata first.
        first = client.post(
            "/api/vaults/metadata",
            json={"vault_address": vault, "name": "Owned", "symbol": "tOWN", "strategy_ids": []},
            cookies=_siwe_cookies(owner),
        )
        assert first.status_code == 200

        # A different authenticated wallet cannot overwrite it.
        attack = client.post(
            "/api/vaults/metadata",
            json={"vault_address": vault, "name": "Hijacked", "symbol": "tHJK", "strategy_ids": []},
            cookies=_siwe_cookies(attacker),
        )
        assert attack.status_code == 403

        # The owner can still edit their own metadata.
        again = client.post(
            "/api/vaults/metadata",
            json={"vault_address": vault, "name": "Owned v2", "symbol": "tOWN", "strategy_ids": []},
            cookies=_siwe_cookies(owner),
        )
        assert again.status_code == 200


def test_create_vault_requires_auth():
    """Vault creation spends the backend signer's gas → must be SIWE-gated."""
    resp = client.post(
        "/api/vaults/create",
        json={
            "name": "Unauthed Vault",
            "symbol": "tUAV",
            "management_fee_bps": 100,
            "performance_fee_bps": 1000,
            "agent_assisted": True,
            "strategy_ids": [],
        },
    )
    assert resp.status_code == 401
