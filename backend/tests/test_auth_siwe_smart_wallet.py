"""SIWE smart-wallet (ERC-1271 / ERC-6492) verification — the passkey path (#869).

Circle Modular Wallets are smart CONTRACT accounts with a WebAuthn P-256 owner:
EOA ecrecover can never validate their signatures, so `/api/auth/verify` falls
through to a deployless universal-validator `eth_call` (see `api/_erc6492.py`).

Hermetic: the RPC boundary (`_erc6492._eth_call`) is mocked — no network, no
Arc RPC. The EOA fast path is covered by test_auth_siwe.py and re-pinned here
only where its interaction with the fallback changed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from archimedes.api._erc6492 import (
    ERC6492_VALIDATOR_BYTECODE,
    _eip191_hash,
    verify_smart_wallet_signature,
)
from httpx import ASGITransport, AsyncClient

SMART_WALLET = "0x3de2000000000000000000000000000000084fa1"
RPC = "http://rpc.invalid"  # never contacted — _eth_call is mocked everywhere


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@pytest.fixture(autouse=True)
def _configured_domain(monkeypatch):
    """Default PUBLIC_DOMAIN to configured for this module (#940).

    _EXPECTED_DOMAIN is read once at archimedes.api.auth_siwe import time, and
    the hermetic test process never sets PUBLIC_DOMAIN -- without this, every
    /api/auth/nonce and /api/auth/verify call below 503s under the new
    fail-closed behavior instead of exercising the smart-wallet path this file
    is actually testing. See test_auth_siwe.py's fixture of the same name for
    the full rationale.
    """
    monkeypatch.setattr("archimedes.api.auth_siwe._EXPECTED_DOMAIN", "archimedes-arc.com")


async def _nonce_and_message(client: AsyncClient, wallet: str) -> str:
    nonce_data = (await client.get("/api/auth/nonce")).json()
    return (
        f"{nonce_data['domain']} wants you to sign in with your Ethereum account:\n"
        f"{wallet}\n\n"
        f"Sign in to Archimedes.\n\n"
        f"URI: https://{nonce_data['domain']}\n"
        f"Version: 1\n"
        f"Chain ID: 5042002\n"
        f"Nonce: {nonce_data['nonce']}\n"
        f"Issued At: {_now_iso()}"
    )


# ── Unit: the verifier helper ─────────────────────────────────────────


class TestVerifySmartWalletSignature:
    async def test_valid_result_accepts(self):
        with patch("archimedes.api._erc6492._eth_call", new=AsyncMock(return_value="0x01")) as call:
            ok = await verify_smart_wallet_signature(SMART_WALLET, "msg", "0x" + "ab" * 65, RPC)
        assert ok is True
        # The deployless call carries the validator bytecode + ABI-encoded
        # (signer, hash, sig) constructor args — pin the calldata prefix.
        data = call.call_args.args[1]
        assert data.startswith(ERC6492_VALIDATOR_BYTECODE)
        assert SMART_WALLET.removeprefix("0x") in data  # encoded signer address

    async def test_invalid_result_rejects(self):
        with patch("archimedes.api._erc6492._eth_call", new=AsyncMock(return_value="0x" + "00" * 32)):
            ok = await verify_smart_wallet_signature(SMART_WALLET, "msg", "0x" + "ab" * 65, RPC)
        assert ok is False

    async def test_rpc_error_fails_closed(self):
        with patch("archimedes.api._erc6492._eth_call", new=AsyncMock(side_effect=ConnectionError("rpc down"))):
            ok = await verify_smart_wallet_signature(SMART_WALLET, "msg", "0x" + "ab" * 65, RPC)
        assert ok is False

    async def test_malformed_signature_fails_closed(self):
        with patch("archimedes.api._erc6492._eth_call", new=AsyncMock(return_value="0x01")):
            ok = await verify_smart_wallet_signature(SMART_WALLET, "msg", "0xnothex", RPC)
        assert ok is False

    def test_eip191_hash_matches_eth_account(self):
        """The digest handed to isValidSignature must equal eth_account's own
        EIP-191 encoding — the two implementations may never drift."""
        from eth_account.messages import _hash_eip191_message, encode_defunct

        for msg in ("hello", "multi\nline\nsiwe message", "ünïcödé 🎯"):
            assert _eip191_hash(msg) == _hash_eip191_message(encode_defunct(text=msg))


# ── Endpoint: /api/auth/verify falls through to the smart-wallet path ─


@pytest.mark.asyncio
async def test_smart_wallet_signature_authenticates():
    """A signature EOA recovery can't handle authenticates the CLAIMED wallet
    when the on-chain validator accepts it — the Circle passkey login path."""
    from archimedes.main import app

    with patch("archimedes.api._erc6492._eth_call", new=AsyncMock(return_value="0x01")):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            message = await _nonce_and_message(client, SMART_WALLET)
            # A WebAuthn-style blob: not ecrecoverable, longer than 65 bytes.
            resp = await client.post("/api/auth/verify", json={"message": message, "signature": "0x" + "cd" * 200})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "authenticated"
    assert data["wallet"] == SMART_WALLET
    assert "set-cookie" in {k.lower() for k in resp.headers}


@pytest.mark.asyncio
async def test_smart_wallet_invalid_signature_rejected():
    from archimedes.main import app

    with patch("archimedes.api._erc6492._eth_call", new=AsyncMock(return_value="0x" + "00" * 32)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            message = await _nonce_and_message(client, SMART_WALLET)
            resp = await client.post("/api/auth/verify", json={"message": message, "signature": "0x" + "cd" * 200})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_smart_wallet_rpc_down_fails_closed():
    from archimedes.main import app

    with patch("archimedes.api._erc6492._eth_call", new=AsyncMock(side_effect=TimeoutError("rpc timeout"))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            message = await _nonce_and_message(client, SMART_WALLET)
            resp = await client.post("/api/auth/verify", json={"message": message, "signature": "0x" + "cd" * 200})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_eoa_mismatch_still_rejected_when_validator_refuses():
    """An ECDSA signature recovering to a DIFFERENT address must not
    authenticate the claimed wallet unless the claimed wallet's contract
    accepts it — with the validator refusing, this stays a 401 (the old
    hard-mismatch behavior, now routed through the smart-wallet check)."""
    from archimedes.main import app
    from eth_account import Account
    from eth_account.messages import encode_defunct

    attacker = Account.create()  # recovers to attacker.address, not SMART_WALLET

    with patch("archimedes.api._erc6492._eth_call", new=AsyncMock(return_value="0x" + "00" * 32)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            message = await _nonce_and_message(client, SMART_WALLET)
            signed = attacker.sign_message(encode_defunct(text=message))
            resp = await client.post(
                "/api/auth/verify",
                json={"message": message, "signature": signed.signature.hex()},
            )
    assert resp.status_code == 401
