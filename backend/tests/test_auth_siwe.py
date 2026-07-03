"""Tests for SIWE (Sign-In with Ethereum) session authentication.

Target: backend/archimedes/api/auth_siwe.py
Goal: ≥85% coverage on the target module.

Hermetic: no .env, no Redis, no Postgres, no network. All external deps mocked.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from archimedes.api.auth_siwe import (
    _COOKIE_NAME,
    _NONCE_TTL_SECONDS,
    _SESSION_TTL_SECONDS,
    _pending_nonces,
    _sign_session,
    _verify_session,
    get_verified_wallet,
    require_verified_wallet,
)
from httpx import ASGITransport, AsyncClient


def _now_iso() -> str:
    """Current UTC time as an EIP-4361 'Issued At' string (must be fresh: the
    verifier now rejects stale issued-at timestamps)."""
    return datetime.now(UTC).isoformat()


# ── Unit tests for session token functions ─────────────────────


class TestSignSession:
    def test_produces_pipe_separated_token(self):
        token = _sign_session("0xabc", time.time())
        assert "|" in token
        payload_str, sig = token.rsplit("|", 1)
        payload = json.loads(payload_str)
        assert payload["wallet"] == "0xabc"
        assert "iat" in payload
        assert len(sig) == 64  # SHA-256 hex

    def test_lowercases_wallet(self):
        token = _sign_session("0xABC", time.time())
        payload = json.loads(token.rsplit("|", 1)[0])
        assert payload["wallet"] == "0xabc"

    def test_different_wallets_different_tokens(self):
        now = time.time()
        t1 = _sign_session("0xaaa", now)
        t2 = _sign_session("0xbbb", now)
        assert t1 != t2


class TestVerifySession:
    def test_valid_token_returns_wallet(self):
        token = _sign_session("0xdef", time.time())
        assert _verify_session(token) == "0xdef"

    def test_tampered_payload_returns_none(self):
        token = _sign_session("0xdef", time.time())
        # Tamper with the payload
        payload_str, sig = token.rsplit("|", 1)
        tampered = payload_str.replace("0xdef", "0xevil")
        assert _verify_session(f"{tampered}|{sig}") is None

    def test_tampered_signature_returns_none(self):
        token = _sign_session("0xdef", time.time())
        payload_str, _ = token.rsplit("|", 1)
        assert _verify_session(f"{payload_str}|{'0' * 64}") is None

    def test_expired_token_returns_none(self):
        old_time = time.time() - _SESSION_TTL_SECONDS - 1
        token = _sign_session("0xdef", old_time)
        assert _verify_session(token) is None

    def test_garbage_token_returns_none(self):
        assert _verify_session("not-a-token") is None
        assert _verify_session("") is None
        assert _verify_session("a|b|c") is None

    def test_just_expired_returns_none(self):
        # Token issued exactly at the TTL boundary
        boundary = time.time() - _SESSION_TTL_SECONDS - 0.001
        token = _sign_session("0xdef", boundary)
        assert _verify_session(token) is None

    def test_fresh_token_returns_wallet(self):
        token = _sign_session("0xdef", time.time() - 60)  # 1 min ago
        assert _verify_session(token) == "0xdef"


# ── Unit tests for request-level functions ─────────────────────


class TestGetVerifiedWallet:
    def test_returns_none_without_cookie(self):
        request = MagicMock()
        request.cookies = {}
        assert get_verified_wallet(request) is None

    def test_returns_wallet_with_valid_cookie(self):
        token = _sign_session("0x1234", time.time())
        request = MagicMock()
        request.cookies = {_COOKIE_NAME: token}
        assert get_verified_wallet(request) == "0x1234"

    def test_returns_none_with_invalid_cookie(self):
        request = MagicMock()
        request.cookies = {_COOKIE_NAME: "garbage"}
        assert get_verified_wallet(request) is None


class TestRequireVerifiedWallet:
    def test_raises_401_without_session(self):
        request = MagicMock()
        request.cookies = {}
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            require_verified_wallet(request)
        assert exc_info.value.status_code == 401

    def test_returns_wallet_with_valid_session(self):
        token = _sign_session("0xabcd", time.time())
        request = MagicMock()
        request.cookies = {_COOKIE_NAME: token}
        assert require_verified_wallet(request) == "0xabcd"


# ── Endpoint integration tests ─────────────────────────────────


@pytest.mark.asyncio
async def test_nonce_endpoint_returns_nonce():
    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/auth/nonce")
    assert resp.status_code == 200
    data = resp.json()
    assert "nonce" in data
    assert len(data["nonce"]) == 32  # 16 bytes hex
    assert "domain" in data
    assert data["expiry_seconds"] == _NONCE_TTL_SECONDS


@pytest.mark.asyncio
async def test_session_endpoint_unauthenticated():
    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/auth/session")
    assert resp.status_code == 200
    data = resp.json()
    assert data["authenticated"] is False
    assert data["wallet"] is None


@pytest.mark.asyncio
async def test_session_endpoint_authenticated():
    from archimedes.main import app

    token = _sign_session("0xdeadbeef" + "0" * 32, time.time())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/auth/session", cookies={_COOKIE_NAME: token})
    assert resp.status_code == 200
    data = resp.json()
    assert data["authenticated"] is True
    assert data["wallet"] == "0xdeadbeef" + "0" * 32


@pytest.mark.asyncio
async def test_logout_clears_cookie():
    from archimedes.main import app

    token = _sign_session("0xdeadbeef" + "0" * 32, time.time())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/auth/logout", cookies={_COOKIE_NAME: token})
    assert resp.status_code == 200
    assert resp.json()["status"] == "logged_out"
    # Cookie should be deleted (set to empty or max_age=0)
    cookie_header = resp.headers.get("set-cookie", "")
    assert _COOKIE_NAME in cookie_header


@pytest.mark.asyncio
async def test_verify_rejects_missing_fields():
    from archimedes.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/auth/verify", json={"message": "", "signature": ""})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_verify_rejects_unknown_nonce():
    from archimedes.main import app

    # Full bindings (domain/chain/issued-at) present so we reach the nonce check.
    message = (
        "archimedes-arc.com wants you to sign in\n"
        "0xabcdef1234567890abcdef1234567890abcdef12\n"
        f"Chain ID: 5042002\nNonce: nonexistentnonce12345678\nIssued At: {_now_iso()}"
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/auth/verify", json={"message": message, "signature": "0xfake"})
    assert resp.status_code == 401
    assert "Nonce" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_verify_rejects_expired_nonce():
    from archimedes.main import app

    # Plant an expired nonce
    nonce = "expired_nonce_123456"
    _pending_nonces[nonce] = time.time() - 1  # already expired

    message = (
        "archimedes-arc.com wants you to sign in\n"
        "0xabcdef1234567890abcdef1234567890abcdef12\n"
        f"Chain ID: 5042002\nNonce: {nonce}\nIssued At: {_now_iso()}"
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/auth/verify", json={"message": message, "signature": "0xfake"})
    assert resp.status_code == 401
    assert "expired" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_verify_with_valid_signature():
    """Full SIWE flow: nonce → sign → verify → session cookie."""
    from archimedes.main import app
    from eth_account import Account
    from eth_account.messages import encode_defunct

    # Create a test wallet
    acct = Account.create()
    wallet = acct.address.lower()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Step 1: Get nonce
        nonce_resp = await client.get("/api/auth/nonce")
        assert nonce_resp.status_code == 200
        nonce_data = nonce_resp.json()

        # Step 2: Construct and sign SIWE message
        message = (
            f"{nonce_data['domain']} wants you to sign in with your Ethereum account:\n"
            f"{wallet}\n\n"
            f"Sign in to Archimedes.\n\n"
            f"URI: https://{nonce_data['domain']}\n"
            f"Version: 1\n"
            f"Chain ID: 5042002\n"
            f"Nonce: {nonce_data['nonce']}\n"
            f"Issued At: {_now_iso()}"
        )
        signable = encode_defunct(text=message)
        signed = acct.sign_message(signable)

        # Step 3: Verify
        verify_resp = await client.post(
            "/api/auth/verify",
            json={"message": message, "signature": signed.signature.hex()},
        )
        assert verify_resp.status_code == 200
        data = verify_resp.json()
        assert data["status"] == "authenticated"
        assert data["wallet"] == wallet
        assert data["expires_in"] == _SESSION_TTL_SECONDS

        # Step 4: Session cookie should be set
        cookie_header = verify_resp.headers.get("set-cookie", "")
        assert _COOKIE_NAME in cookie_header
        assert "httponly" in cookie_header.lower()
        assert "samesite=strict" in cookie_header.lower()


@pytest.mark.asyncio
async def test_verify_rejects_wrong_signer():
    """Signature from wallet A cannot authenticate as wallet B.

    Since the smart-wallet path landed (#869), an EOA mismatch falls through
    to ERC-1271/6492 verification of the CLAIMED wallet instead of hard-401ing
    on the recovery mismatch — so the RPC boundary is mocked to refuse here
    (a contract wallet that doesn't accept the signature), keeping the test
    hermetic and the wrong-signer guarantee pinned end-to-end."""
    from archimedes.main import app
    from eth_account import Account
    from eth_account.messages import encode_defunct

    acct_real = Account.create()
    acct_fake = "0x" + "1" * 40  # claimed wallet (not the signer)

    with patch("archimedes.api._erc6492._eth_call", new=AsyncMock(return_value="0x" + "00" * 32)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            nonce_resp = await client.get("/api/auth/nonce")
            nonce_data = nonce_resp.json()

            # Message claims acct_fake but signed by acct_real
            message = (
                f"{nonce_data['domain']} wants you to sign in with your Ethereum account:\n"
                f"{acct_fake}\n\n"
                f"Chain ID: 5042002\n"
                f"Nonce: {nonce_data['nonce']}\n"
                f"Issued At: {_now_iso()}"
            )
            signable = encode_defunct(text=message)
            signed = acct_real.sign_message(signable)

            verify_resp = await client.post(
                "/api/auth/verify",
                json={"message": message, "signature": signed.signature.hex()},
            )
            assert verify_resp.status_code == 401
            assert "Invalid signature" in verify_resp.json()["detail"]


@pytest.mark.asyncio
async def test_nonce_is_single_use():
    """A nonce consumed by one verify attempt cannot be reused."""
    from archimedes.main import app
    from eth_account import Account
    from eth_account.messages import encode_defunct

    acct = Account.create()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        nonce_resp = await client.get("/api/auth/nonce")
        nonce_data = nonce_resp.json()

        message = (
            f"{nonce_data['domain']} wants you to sign in with your Ethereum account:\n"
            f"{acct.address.lower()}\n\n"
            f"Chain ID: 5042002\n"
            f"Nonce: {nonce_data['nonce']}\n"
            f"Issued At: {_now_iso()}"
        )
        signable = encode_defunct(text=message)
        signed = acct.sign_message(signable)
        payload = {"message": message, "signature": signed.signature.hex()}

        # First verify: should succeed
        resp1 = await client.post("/api/auth/verify", json=payload)
        assert resp1.status_code == 200

        # Second verify with same nonce: should fail
        resp2 = await client.post("/api/auth/verify", json=payload)
        assert resp2.status_code == 401
        assert "Nonce" in resp2.json()["detail"]


# ── SIWE message binding: domain / chain-id / expiry (audit #9) ─────


@pytest.mark.asyncio
async def test_verify_rejects_wrong_domain():
    """A message signed for another dApp's domain must not authenticate here."""
    from archimedes.main import app

    message = (
        "evil-phish.example wants you to sign in with your Ethereum account:\n"
        "0xabcdef1234567890abcdef1234567890abcdef12\n\n"
        "Chain ID: 5042002\n"
        f"Nonce: somenonce123456\nIssued At: {_now_iso()}"
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/auth/verify", json={"message": message, "signature": "0xfake"})
    assert resp.status_code == 401
    assert "domain" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_verify_rejects_wrong_chain_id():
    from archimedes.main import app

    message = (
        "archimedes-arc.com wants you to sign in with your Ethereum account:\n"
        "0xabcdef1234567890abcdef1234567890abcdef12\n\n"
        "Chain ID: 1\n"  # Ethereum mainnet, not Arc testnet
        f"Nonce: somenonce123456\nIssued At: {_now_iso()}"
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/auth/verify", json={"message": message, "signature": "0xfake"})
    assert resp.status_code == 401
    assert "chain" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_verify_rejects_stale_issued_at():
    from archimedes.main import app

    message = (
        "archimedes-arc.com wants you to sign in with your Ethereum account:\n"
        "0xabcdef1234567890abcdef1234567890abcdef12\n\n"
        "Chain ID: 5042002\n"
        "Nonce: somenonce123456\nIssued At: 2020-01-01T00:00:00+00:00"  # ancient
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/auth/verify", json={"message": message, "signature": "0xfake"})
    assert resp.status_code == 401
    assert "issued-at" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_verify_rejects_message_missing_chain_id():
    """A message that omits Chain ID is rejected (400) — bindings are required,
    not skip-if-absent (self-audit hardening of finding #9)."""
    from archimedes.main import app

    message = (
        "archimedes-arc.com wants you to sign in\n"
        "0xabcdef1234567890abcdef1234567890abcdef12\n"
        f"Nonce: somenonce123456\nIssued At: {_now_iso()}"
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/auth/verify", json={"message": message, "signature": "0xfake"})
    assert resp.status_code == 400
    assert "Chain ID" in resp.json()["detail"]


# ── Redis-backed nonce store (multi-worker support) ─────────────


class _FakeRedisNonceStore:
    """In-memory stand-in for the Redis keyspace AgentStateStore.save_nonce /
    pop_nonce write to. A single instance shared across two separately
    constructed AgentStateStore patches simulates "two workers, one Redis" --
    the scenario the real fix is for."""

    def __init__(self):
        self._nonces: set[str] = set()

    async def save_nonce(self, nonce: str, ttl_seconds: int) -> None:
        self._nonces.add(nonce)

    async def pop_nonce(self, nonce: str) -> bool:
        if nonce in self._nonces:
            self._nonces.discard(nonce)
            return True
        return False


@pytest.mark.asyncio
async def test_nonce_survives_across_separate_store_instances():
    """A nonce issued through one AgentStateStore instance is verifiable
    through a *different* instance against the same backing store --
    the multi-worker scenario. Asserts the in-process `_pending_nonces`
    fallback dict is untouched, proving the Redis-backed path was used.
    """
    from archimedes.main import app
    from eth_account import Account
    from eth_account.messages import encode_defunct

    fake_store = _FakeRedisNonceStore()
    acct = Account.create()

    with (
        patch("archimedes.services.redis_state.AgentStateStore.save_nonce", fake_store.save_nonce),
        patch("archimedes.services.redis_state.AgentStateStore.pop_nonce", fake_store.pop_nonce),
        patch("archimedes.services.redis_state.AgentStateStore.close", AsyncMock(return_value=None)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            nonce_resp = await client.get("/api/auth/nonce")
            assert nonce_resp.status_code == 200
            nonce_data = nonce_resp.json()

            # The nonce landed in the shared fake-Redis store, not the
            # in-process fallback dict.
            assert nonce_data["nonce"] in fake_store._nonces
            assert nonce_data["nonce"] not in _pending_nonces

            message = (
                f"{nonce_data['domain']} wants you to sign in with your Ethereum account:\n"
                f"{acct.address.lower()}\n\n"
                f"Chain ID: 5042002\n"
                f"Nonce: {nonce_data['nonce']}\n"
                f"Issued At: {_now_iso()}"
            )
            signable = encode_defunct(text=message)
            signed = acct.sign_message(signable)

            verify_resp = await client.post(
                "/api/auth/verify",
                json={"message": message, "signature": signed.signature.hex()},
            )

    assert verify_resp.status_code == 200
    data = verify_resp.json()
    assert data["status"] == "authenticated"
    assert data["wallet"] == acct.address.lower()
    # Single-use: the nonce was popped from the shared store.
    assert nonce_data["nonce"] not in fake_store._nonces


@pytest.mark.asyncio
async def test_nonce_reuse_rejected_via_redis_backed_store():
    """A nonce already popped from the (fake) Redis store cannot be reused,
    even across separately-constructed AgentStateStore instances."""
    from archimedes.main import app
    from eth_account import Account
    from eth_account.messages import encode_defunct

    fake_store = _FakeRedisNonceStore()
    acct = Account.create()

    with (
        patch("archimedes.services.redis_state.AgentStateStore.save_nonce", fake_store.save_nonce),
        patch("archimedes.services.redis_state.AgentStateStore.pop_nonce", fake_store.pop_nonce),
        patch("archimedes.services.redis_state.AgentStateStore.close", AsyncMock(return_value=None)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            nonce_resp = await client.get("/api/auth/nonce")
            nonce_data = nonce_resp.json()

            message = (
                f"{nonce_data['domain']} wants you to sign in with your Ethereum account:\n"
                f"{acct.address.lower()}\n\n"
                f"Chain ID: 5042002\n"
                f"Nonce: {nonce_data['nonce']}\n"
                f"Issued At: {_now_iso()}"
            )
            signable = encode_defunct(text=message)
            signed = acct.sign_message(signable)
            payload = {"message": message, "signature": signed.signature.hex()}

            resp1 = await client.post("/api/auth/verify", json=payload)
            assert resp1.status_code == 200

            resp2 = await client.post("/api/auth/verify", json=payload)

    assert resp2.status_code == 401
    assert "Nonce" in resp2.json()["detail"]


@pytest.mark.asyncio
async def test_nonce_redis_down_falls_back_to_in_process_dict():
    """When the Redis-backed store raises (Redis unreachable), /nonce and
    /verify fall back to the in-process `_pending_nonces` dict -- single-worker
    local dev keeps working exactly as before this change.
    """
    from archimedes.main import app
    from eth_account import Account
    from eth_account.messages import encode_defunct

    acct = Account.create()

    with (
        patch(
            "archimedes.services.redis_state.AgentStateStore.save_nonce",
            AsyncMock(side_effect=ConnectionError("redis down")),
        ),
        patch(
            "archimedes.services.redis_state.AgentStateStore.pop_nonce",
            AsyncMock(side_effect=ConnectionError("redis down")),
        ),
        patch("archimedes.services.redis_state.AgentStateStore.close", AsyncMock(return_value=None)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            nonce_resp = await client.get("/api/auth/nonce")
            assert nonce_resp.status_code == 200
            nonce_data = nonce_resp.json()

            # Fell back to the in-process dict.
            assert nonce_data["nonce"] in _pending_nonces

            message = (
                f"{nonce_data['domain']} wants you to sign in with your Ethereum account:\n"
                f"{acct.address.lower()}\n\n"
                f"Chain ID: 5042002\n"
                f"Nonce: {nonce_data['nonce']}\n"
                f"Issued At: {_now_iso()}"
            )
            signable = encode_defunct(text=message)
            signed = acct.sign_message(signable)

            verify_resp = await client.post(
                "/api/auth/verify",
                json={"message": message, "signature": signed.signature.hex()},
            )

    assert verify_resp.status_code == 200
    data = verify_resp.json()
    assert data["status"] == "authenticated"
    assert data["wallet"] == acct.address.lower()
    # Single-use: popped from the in-process fallback dict.
    assert nonce_data["nonce"] not in _pending_nonces


@pytest.mark.asyncio
async def test_verify_redis_down_unknown_nonce_rejected():
    """With Redis down and no matching nonce in the in-process fallback dict,
    /verify still returns 401 (not a 500 from the unhandled ConnectionError)."""
    from archimedes.main import app

    with (
        patch(
            "archimedes.services.redis_state.AgentStateStore.pop_nonce",
            AsyncMock(side_effect=ConnectionError("redis down")),
        ),
        patch("archimedes.services.redis_state.AgentStateStore.close", AsyncMock(return_value=None)),
    ):
        message = (
            "archimedes-arc.com wants you to sign in\n"
            "0xabcdef1234567890abcdef1234567890abcdef12\n"
            f"Chain ID: 5042002\nNonce: nonexistentnonce_redisdown\nIssued At: {_now_iso()}"
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/auth/verify", json={"message": message, "signature": "0xfake"})

    assert resp.status_code == 401
    assert "Nonce" in resp.json()["detail"]
