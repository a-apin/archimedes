"""SIWE message construction in the agent journey harness (#788 slice 2).

Target: scripts/agent_journey.py (repo root — loaded via importlib, it isn't a
package). Two layers:
  1. unit — ``build_siwe_message`` embeds the exact EIP-4361 bindings the backend
     verifier enforces (domain first-line, wallet line, Chain ID 5042002, Nonce,
     Issued At) and ``_domain_from_base`` derives a bare authority from --base;
  2. round-trip — a message built by the harness's builder, signed with a real
     throwaway ``eth_account`` key, is ACCEPTED by the real ``/api/auth/verify``
     (mirrors test_auth_siwe.py::test_verify_with_valid_signature, but through
     the harness's code path — proving the recipe matches the verifier).

Hermetic: ASGI transport, in-process nonce fallback, no network / Redis / .env.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_agent_journey():
    """Import scripts/agent_journey.py from the repo root by file path."""
    path = _REPO_ROOT / "scripts" / "agent_journey.py"
    spec = importlib.util.spec_from_file_location("agent_journey", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["agent_journey"] = module
    spec.loader.exec_module(module)
    return module


aj = _load_agent_journey()


# ── Unit: message shape carries every server-enforced binding ─────────


def test_message_embeds_domain_wallet_chain_nonce_issued_at():
    msg = aj.build_siwe_message(
        domain="archimedes-arc.com",
        wallet="0x" + "ab" * 20,
        nonce="deadbeefdeadbeefdeadbeefdeadbeef",
        issued_at="2026-07-01T12:00:00+00:00",
    )
    lines = msg.split("\n")
    # Domain binding: bare authority on the first line, EIP-4361 phrasing.
    assert lines[0] == "archimedes-arc.com wants you to sign in with your Ethereum account:"
    # Wallet: the first 42-char 0x line (how the verifier extracts it).
    assert lines[1] == "0x" + "ab" * 20
    # Chain binding: Arc testnet, exactly the id the verifier compares.
    assert "Chain ID: 5042002" in msg
    assert f"Chain ID: {aj.ARC_CHAIN_ID}" in msg
    # Nonce + freshness bindings, with the exact "Key: " prefixes parsed server-side.
    assert "Nonce: deadbeefdeadbeefdeadbeefdeadbeef" in msg
    assert "Issued At: 2026-07-01T12:00:00+00:00" in msg


def test_message_chain_id_override_is_respected():
    msg = aj.build_siwe_message(domain="d.example", wallet="0x" + "cd" * 20, nonce="n1", issued_at="t", chain_id=1)
    assert "Chain ID: 1" in msg
    assert "Chain ID: 5042002" not in msg


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        ("http://localhost:8000", "localhost:8000"),
        ("https://archimedes-arc.com", "archimedes-arc.com"),
        ("https://archimedes-arc.com/", "archimedes-arc.com"),
        ("archimedes-arc.com", "archimedes-arc.com"),
    ],
)
def test_domain_from_base_bare_authority(base, expected):
    assert aj._domain_from_base(base) == expected


# ── Round-trip: the harness recipe satisfies the real verifier ────────


@pytest.mark.asyncio
async def test_builder_message_accepted_by_real_verifier():
    """nonce → build_siwe_message → sign → /api/auth/verify → authenticated.

    Uses the server-advertised domain + issued_at exactly as step_auth does,
    proving no binding (domain/chain/nonce/freshness) was weakened client-side.
    """
    from archimedes.main import app
    from eth_account import Account
    from eth_account.messages import encode_defunct

    acct = Account.create()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        nonce_resp = await client.get("/api/auth/nonce")
        assert nonce_resp.status_code == 200
        nonce_data = nonce_resp.json()

        issued_at = datetime.fromtimestamp(int(nonce_data["issued_at"]), tz=UTC).isoformat()
        message = aj.build_siwe_message(
            domain=nonce_data["domain"],
            wallet=acct.address.lower(),
            nonce=nonce_data["nonce"],
            issued_at=issued_at,
        )
        signed = acct.sign_message(encode_defunct(text=message))

        verify_resp = await client.post(
            "/api/auth/verify",
            json={"message": message, "signature": signed.signature.hex()},
        )

    assert verify_resp.status_code == 200, verify_resp.text
    data = verify_resp.json()
    assert data["status"] == "authenticated"
    assert data["wallet"] == acct.address.lower()
