"""SIWE (Sign-In with Ethereum) session authentication — EIP-4361.

Implements wallet-signature-based auth so the X-Wallet-Address header
is no longer trusted. Users prove wallet ownership by signing a nonce;
the backend verifies the signature and issues a session cookie.

Endpoints:
  GET  /api/auth/nonce          — request a challenge nonce
  POST /api/auth/verify         — submit signed message → session cookie
  POST /api/auth/logout         — clear session cookie

Session middleware:
  get_verified_wallet(request)  — extract wallet from session cookie
                                  Returns None if not authenticated.

References:
  - EIP-4361: https://eips.ethereum.org/EIPS/eip-4361
  - eth_account: https://eth-account.readthedocs.io/
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, Response

logger = logging.getLogger(__name__)

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])

# Session signing key — prefer SIWE_SESSION_KEY (dedicated session-signing secret);
# falls back to EMAIL_ENCRYPTION_KEY for backwards compatibility. Production enforces
# EMAIL_ENCRYPTION_KEY at boot (main.py RuntimeError), so the secrets.token_hex(32)
# path is local-dev only.
# Multi-worker note: main.py:~127 raises RuntimeError at boot if PUBLIC_DOMAIN is set
# (production) and EMAIL_ENCRYPTION_KEY is unset, so the secrets.token_hex(32) fallback
# is only reachable in single-worker local dev -- per-worker secret divergence (which
# would break cross-worker session-cookie verification) can't occur there.
_SESSION_SECRET = os.getenv("SIWE_SESSION_KEY") or os.getenv("EMAIL_ENCRYPTION_KEY", secrets.token_hex(32))
_SESSION_TTL_SECONDS = 24 * 60 * 60  # 24 hours
_NONCE_TTL_SECONDS = 300  # 5 minutes
_CLOCK_SKEW_SECONDS = 120  # tolerate small client/server clock drift on Issued At

# SIWE message binding — a valid signature must be for THIS site and chain, not
# merely carry a live nonce. Must match what GET /api/auth/nonce advertises and
# what the UI puts in the message (see ui/src/siwe.js).
_EXPECTED_DOMAIN = os.getenv("PUBLIC_DOMAIN", "https://archimedes-arc.com")
_EXPECTED_CHAIN_ID = int(os.getenv("ARC_CHAIN_ID", "5042002"))

# Pending-nonce store: Redis-backed (via AgentStateStore) so /nonce and /verify
# agree across Uvicorn workers, with this in-memory dict as a same-process
# fallback when Redis is unreachable (single-worker local dev keeps working
# exactly as before). nonce → expiry timestamp.
_pending_nonces: dict[str, float] = {}  # nonce → expiry timestamp

_COOKIE_NAME = "archimedes_session"


def _normalize_domain(domain: str) -> str:
    """Bare authority for domain comparison — drop scheme and trailing slash."""
    return domain.strip().removeprefix("https://").removeprefix("http://").rstrip("/").lower()


def _sign_session(wallet: str, issued_at: float) -> str:
    """Create an HMAC-signed session token."""
    payload = json.dumps({"wallet": wallet.lower(), "iat": issued_at})
    sig = hmac.new(_SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    # Base64 would be cleaner but hex+json is simpler to debug
    return f"{payload}|{sig}"


def _verify_session(token: str) -> str | None:
    """Verify session token and return wallet address, or None if invalid."""
    try:
        payload_str, sig = token.rsplit("|", 1)
        expected = hmac.new(_SESSION_SECRET.encode(), payload_str.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(payload_str)
        if time.time() - payload["iat"] > _SESSION_TTL_SECONDS:
            return None  # expired
        return payload["wallet"]
    except Exception:
        return None


def get_verified_wallet(request: Request) -> str | None:
    """Extract the authenticated wallet address from the session cookie.

    Returns the lowercase wallet address if the session is valid, None otherwise.
    This replaces the old X-Wallet-Address header trust model.
    """
    token = request.cookies.get(_COOKIE_NAME)
    if not token:
        return None
    return _verify_session(token)


def require_verified_wallet(request: Request) -> str:
    """FastAPI dependency: require a valid SIWE session. Raises 401 if not authenticated."""
    wallet = get_verified_wallet(request)
    if not wallet:
        raise HTTPException(status_code=401, detail="Authentication required. Connect your wallet and sign in.")
    return wallet


def _generation_auth_required() -> bool:
    """Whether expensive LLM-generation endpoints require a SIWE session.

    Secure by default (flipped 2026-07-01, agent-auth slice 2): with
    REQUIRE_SIWE_FOR_GENERATION unset, paid LLM endpoints require a verified
    SIWE session — closing the unauthenticated budget-drain vector (audit
    2026-06-13 B12). Opt out explicitly with REQUIRE_SIWE_FOR_GENERATION=false
    (local dev / demo — documented in .env.example).

    TESTING carve-out: the hermetic suite runs with TESTING=1 (conftest) and
    exercises the open path throughout; when the flag is UNSET under TESTING the
    gate stays off, mirroring how the slowapi limiter and the wallet-less quota
    already treat TESTING. An explicit true/false always wins over the carve-out,
    so gating-on tests simply set REQUIRE_SIWE_FOR_GENERATION=true.
    """
    raw = os.getenv("REQUIRE_SIWE_FOR_GENERATION", "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    # Unset/unrecognized → secure by default, except under the hermetic test env.
    return not os.getenv("TESTING")


def gate_generation(request: Request) -> str | None:
    """FastAPI dependency for paid LLM-generation endpoints.

    When REQUIRE_SIWE_FOR_GENERATION is enabled (the default — see
    ``_generation_auth_required``), behaves like ``require_verified_wallet``
    (401 without a session). When explicitly disabled, returns the best-effort
    wallet for attribution without enforcing — the old open behavior, kept as
    a local-dev/demo opt-out. This closes the unauthenticated-LLM budget-drain
    vector (audit 2026-06-13).
    """
    if _generation_auth_required():
        return require_verified_wallet(request)
    return get_verified_wallet(request)


# ── Endpoints ─────────────────────────────────────────────────


@auth_router.get("/nonce")
async def get_nonce():
    """Issue a challenge nonce for SIWE signing.

    Stored in Redis (via AgentStateStore) with a TTL so /verify on a
    different worker can find it -- Redis handles expiry via SETEX, so no
    manual sweep is needed on that path. Falls back to the in-process
    `_pending_nonces` dict if Redis is unreachable (single-worker local dev).
    """
    now = time.time()
    nonce = secrets.token_hex(16)

    from archimedes.services.redis_state import AgentStateStore

    state = AgentStateStore()
    try:
        await state.save_nonce(nonce, _NONCE_TTL_SECONDS)
    except Exception as exc:
        logger.debug("SIWE nonce store unavailable, using in-process fallback: %s", exc)
        # Clean expired nonces from the in-memory fallback before adding a new one.
        expired = [n for n, exp in _pending_nonces.items() if exp < now]
        for n in expired:
            del _pending_nonces[n]
        _pending_nonces[nonce] = now + _NONCE_TTL_SECONDS
    finally:
        await state.close()

    return {
        "nonce": nonce,
        "domain": os.getenv("PUBLIC_DOMAIN", "https://archimedes-arc.com")
        .replace("https://", "")
        .replace("http://", ""),
        "issued_at": int(now),
        "expiry_seconds": _NONCE_TTL_SECONDS,
    }


@auth_router.post("/verify")
async def verify_signature(request: Request, response: Response):
    """Verify a signed SIWE message and issue a session cookie.

    Body: { "message": "<SIWE message text>", "signature": "0x..." }
    """
    from eth_account import Account
    from eth_account.messages import encode_defunct

    body = await request.json()
    message = body.get("message", "")
    signature = body.get("signature", "")

    if not message or not signature:
        raise HTTPException(status_code=400, detail="message and signature are required")

    # Parse the SIWE message fields (EIP-4361). The first line is
    # "<domain> wants you to sign in with your Ethereum account:".
    nonce = None
    wallet_from_message = None
    domain_from_message = None
    chain_id_from_message = None
    issued_at_from_message = None
    lines = message.split("\n")
    if lines and " wants you to sign in" in lines[0]:
        domain_from_message = lines[0].split(" wants you to sign in")[0].strip()
    for line in lines:
        line = line.strip()
        if line.startswith("Nonce: "):
            nonce = line[7:].strip()
        elif line.startswith("Chain ID: "):
            chain_id_from_message = line[len("Chain ID: ") :].strip()
        elif line.startswith("Issued At: "):
            issued_at_from_message = line[len("Issued At: ") :].strip()
        elif line.startswith("0x") and len(line) == 42:
            wallet_from_message = line.lower()

    if not nonce:
        raise HTTPException(status_code=400, detail="Nonce not found in message")

    # The three EIP-4361 bindings below are REQUIRED, not best-effort: a message
    # that simply omits the domain / chain-id / issued-at lines must not slip
    # through unbound. The UI always emits all three (see ui/src/siwe.js).

    # Domain binding — a signature for another dApp's domain must not authenticate
    # here, even if it happens to carry a live Archimedes nonce. Compare on the
    # bare authority (scheme/trailing-slash stripped) so "archimedes-arc.com" and
    # "https://archimedes-arc.com/" are treated as the same site.
    if domain_from_message is None:
        raise HTTPException(status_code=400, detail="SIWE message is missing the domain line")
    if _normalize_domain(domain_from_message) != _normalize_domain(_EXPECTED_DOMAIN):
        raise HTTPException(status_code=401, detail="SIWE message domain does not match this site")

    # Chain-id binding — reject messages signed for the wrong chain (or with none).
    if chain_id_from_message is None:
        raise HTTPException(status_code=400, detail="SIWE message is missing the Chain ID")
    if chain_id_from_message != str(_EXPECTED_CHAIN_ID):
        raise HTTPException(status_code=401, detail="SIWE message chain-id mismatch")

    # Expiry — require a fresh "Issued At"; a message without one is not bound in time.
    if not issued_at_from_message:
        raise HTTPException(status_code=400, detail="SIWE message is missing Issued At")
    try:
        issued_dt = datetime.fromisoformat(issued_at_from_message.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid Issued At timestamp") from exc
    age = datetime.now(issued_dt.tzinfo) - issued_dt
    if age > timedelta(seconds=_NONCE_TTL_SECONDS) or age < timedelta(seconds=-_CLOCK_SKEW_SECONDS):
        raise HTTPException(status_code=401, detail="SIWE message issued-at outside the valid window")

    # Verify nonce is pending and not expired. Redis (GETDEL) makes this
    # read-and-delete atomic and shared across workers; Redis-managed TTL means
    # a "found" result is by construction unexpired, so no separate expiry
    # check is needed on that path. Falls back to the in-process dict (with
    # its own expiry timestamp) if Redis is unreachable.
    from archimedes.services.redis_state import AgentStateStore

    state = AgentStateStore()
    try:
        found = await state.pop_nonce(nonce)
    except Exception as exc:
        logger.debug("SIWE nonce store unavailable, using in-process fallback: %s", exc)
        found = False
        redis_unreachable = True
    else:
        redis_unreachable = False
    finally:
        await state.close()

    if not found and redis_unreachable:
        expiry = _pending_nonces.pop(nonce, None)
        if expiry is None:
            raise HTTPException(status_code=401, detail="Nonce not found or already used")
        if time.time() > expiry:
            raise HTTPException(status_code=401, detail="Nonce expired")
    elif not found:
        raise HTTPException(status_code=401, detail="Nonce not found or already used")

    # Recover the signer address from the signature (EOA fast path — no RPC).
    # Smart-contract wallets (Circle passkey MSCAs, #869) sign with a WebAuthn
    # P-256 owner, so secp256k1 recovery either fails outright or recovers an
    # unrelated address — those fall through to the ERC-1271/6492 path below.
    recovered_lower = None
    try:
        signable = encode_defunct(text=message)
        recovered = Account.recover_message(signable, signature=signature)
        recovered_lower = recovered.lower()
    except Exception as exc:
        logger.info("SIWE EOA recovery failed (may be a smart-wallet signature): %s", exc)

    if recovered_lower and (not wallet_from_message or recovered_lower == wallet_from_message):
        verified_wallet = recovered_lower
    elif wallet_from_message:
        # ERC-1271 (deployed) / ERC-6492 (counterfactual) smart-account
        # verification via one deployless eth_call — see _erc6492.py. The
        # claimed wallet is what gets verified: only a signature its contract
        # (or its 6492 factory-deployed instance) accepts authenticates it.
        from archimedes.api._erc6492 import verify_smart_wallet_signature

        rpc_url = os.getenv("ARC_ARC_RPC_URL", "https://rpc.testnet.arc.network")
        if not await verify_smart_wallet_signature(wallet_from_message, message, signature, rpc_url):
            raise HTTPException(
                status_code=401,
                detail="Invalid signature (EOA recovery and smart-wallet verification both failed)",
            )
        verified_wallet = wallet_from_message
        logger.info("SIWE smart-wallet (ERC-1271/6492) verification succeeded for %s…", verified_wallet[:10])
    else:
        raise HTTPException(status_code=401, detail="Invalid signature")

    recovered_lower = verified_wallet

    # Issue session cookie
    now = time.time()
    token = _sign_session(recovered_lower, now)

    response.set_cookie(
        key=_COOKIE_NAME,
        value=token,
        httponly=True,  # Not accessible via JavaScript (XSS-safe)
        secure=True,  # HTTPS only
        samesite="strict",  # CSRF protection
        max_age=_SESSION_TTL_SECONDS,
        path="/",
    )

    logger.info("SIWE session issued for wallet %s", recovered_lower[:10])

    # Conversion funnel (#787): the visitor connected a wallet — the trust step
    # we most need to move. Fail-safe; never blocks the auth response.
    from archimedes.api.funnel_middleware import record_funnel

    await record_funnel(request, "wallet_connected")

    return {
        "status": "authenticated",
        "wallet": recovered_lower,
        "expires_in": _SESSION_TTL_SECONDS,
    }


@auth_router.post("/logout")
async def logout(response: Response):
    """Clear the session cookie."""
    response.delete_cookie(key=_COOKIE_NAME, path="/")
    return {"status": "logged_out"}


@auth_router.get("/session")
async def get_session(request: Request):
    """Check current session status."""
    wallet = get_verified_wallet(request)
    if wallet:
        return {"authenticated": True, "wallet": wallet}
    return {"authenticated": False, "wallet": None}
