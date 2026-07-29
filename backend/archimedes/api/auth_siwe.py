"""Legacy test-only SIWE session router and reusable EIP-4361 helpers.

Production does not mount ``auth_router``. Better Auth supplies application
sessions; ``api.wallet_routes`` uses signature helpers here only to verify
optional linked-wallet control. Routes and cookie helpers remain solely for
legacy regression tests during migration.

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
import re
import secrets
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, Response

logger = logging.getLogger(__name__)

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])


def _resolve_session_secret() -> str:
    """Resolve the session-cookie signing secret (issue #933).

    Prefers SIWE_SESSION_KEY (dedicated), then EMAIL_ENCRYPTION_KEY (back-compat).
    If neither is set, falls back to a per-process random secret AND logs a loud
    WARNING: that fallback is only safe in single-worker local dev — across
    multiple uvicorn workers each worker would get a DIFFERENT random secret, so
    a cookie signed by one worker fails verification on another and users are
    logged out at random. Production can't reach the random path: main.py raises
    at boot when PUBLIC_DOMAIN is set (production) and EMAIL_ENCRYPTION_KEY is
    unset. The warning makes a misconfigured multi-worker deploy visible instead
    of silently churning sessions.
    """
    configured = os.getenv("SIWE_SESSION_KEY") or os.getenv("EMAIL_ENCRYPTION_KEY")
    if configured:
        return configured
    logger.warning(
        "No SIWE_SESSION_KEY / EMAIL_ENCRYPTION_KEY set — using a per-process random "
        "session secret. Safe ONLY for single-worker local dev; a multi-worker deploy "
        "will log users out at random (set SIWE_SESSION_KEY or EMAIL_ENCRYPTION_KEY)."
    )
    return secrets.token_hex(32)


_SESSION_SECRET = _resolve_session_secret()
_SESSION_TTL_SECONDS = 24 * 60 * 60  # 24 hours
_NONCE_TTL_SECONDS = 300  # 5 minutes
_CLOCK_SKEW_SECONDS = 120  # tolerate small client/server clock drift on Issued At

# SIWE message binding — a valid signature must be for THIS site and chain, not
# merely carry a live nonce. Retained for legacy test-only challenge routes.
#
# No hardcoded literal fallback here (#940): a fallback like
# "https://archimedes-arc.com" would mean every deployment that forgets to set
# PUBLIC_DOMAIN — including a phishing clone — advertises and verifies against
# the SAME domain string, defeating the entire point of domain binding (a
# signature scoped to one site being replayed against another). May be None;
# callers must go through _require_configured_domain() before using it.
_EXPECTED_DOMAIN = os.getenv("PUBLIC_DOMAIN")
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


def _require_configured_domain() -> str:
    """Return the configured SIWE domain, or fail closed if PUBLIC_DOMAIN is unset.

    (#940) Both /api/auth/nonce and /api/auth/verify must call this — a single
    source of truth kills the drift risk of two independent os.getenv() reads
    going out of sync, and the missing-config case now fails closed (503)
    instead of silently falling back to a spoofable hardcoded domain.

    This deliberately does NOT raise at module-import time: main.py's
    `_is_production = bool(os.getenv("PUBLIC_DOMAIN"))` switch treats an unset
    PUBLIC_DOMAIN as the intentional local-dev mode (per .env.example), and the
    app must still boot cleanly in that mode (test_docs_enabled_in_local_dev).
    Raising here only at first use of the two SIWE endpoints keeps that
    contract intact while still closing the domain-binding hole at request time.
    """
    if not _EXPECTED_DOMAIN:
        raise HTTPException(status_code=503, detail="SIWE auth requires PUBLIC_DOMAIN to be configured")
    return _EXPECTED_DOMAIN


def _address_from_siwe_message(message: str) -> str | None:
    """Return the lowercased signer address from an EIP-4361 SIWE message.

    The address is line 2 (index 1), immediately after the
    ``"<domain> wants you to sign in with your Ethereum account:"`` preamble.
    Read it positionally: scanning for "the last 42-char 0x line" (the previous
    behavior) let a 0x address appearing later in the message — e.g. inside a
    ``Resources:`` URI — overwrite the real signer. On the EOA-recovery-fail →
    ERC-1271 smart-wallet path that misplaced address is the wallet we verify
    against, so a crafted message could point verification at the wrong account
    (#946). Returns ``None`` if line 2 is absent or not a 42-char 0x string.
    """
    lines = message.split("\n")
    if len(lines) >= 2:
        addr = lines[1].strip()
        if addr.startswith("0x") and len(addr) == 42:
            return addr.lower()
    return None


def _canonical_wallet_or_none(wallet: str) -> str | None:
    """Return strict lowercase 0x-prefixed 40-hex wallet, else None."""
    if not isinstance(wallet, str):
        return None
    w = wallet.strip().lower()
    if re.fullmatch(r"0x[a-f0-9]{40}", w):
        return w
    return None


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
    """Return selected wallet only when linked to current Better Auth user."""
    from archimedes.api.wallet_routes import get_linked_wallet_address

    return get_linked_wallet_address(request)


def require_verified_wallet(request: Request) -> str:
    """Require account session plus verified linked wallet."""
    from archimedes.api.wallet_routes import require_linked_wallet

    return require_linked_wallet(request)


def _generation_auth_required() -> bool:
    """Legacy test-only generation gate retained for compatibility coverage.

    Secure by default (flipped 2026-07-01, agent-auth slice 2): with
    REQUIRE_SIWE_FOR_GENERATION unset, paid LLM endpoints require a verified
    SIWE session — closing the unauthenticated budget-drain vector (audit
    2026-06-13 B12). Opt out explicitly with REQUIRE_SIWE_FOR_GENERATION=false
    (local dev / demo — documented in .env.example).

    TESTING carve-out: hermetic suite runs with TESTING=1 and exercises this
    retired path; when flag is unset under TESTING gate stays off. An explicit
    true/false always wins over carve-out,
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
    """Legacy test-only FastAPI dependency for paid generation endpoints.

    When REQUIRE_SIWE_FOR_GENERATION is enabled (the default — see
    ``_generation_auth_required``), behaves like ``require_verified_wallet``
    (401 without a session). When explicitly disabled, returns the best-effort
    wallet for attribution without enforcing — the old open behavior, kept as
    a local-dev/demo opt-out. This closes the unauthenticated-LLM budget-drain
    vector (audit 2026-06-13).
    """
    wallet = get_verified_wallet(request)
    if _generation_auth_required() and wallet is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return wallet


# ── Endpoints ─────────────────────────────────────────────────


@auth_router.get("/nonce")
async def get_nonce():
    """Issue a challenge nonce for SIWE signing.

    Fails closed (503) if PUBLIC_DOMAIN isn't configured (#940) — see
    _require_configured_domain for why. Otherwise: stored in Redis (via
    AgentStateStore) with a TTL so /verify on a different worker can find it --
    Redis handles expiry via SETEX, so no manual sweep is needed on that path.
    Falls back to the in-process `_pending_nonces` dict if Redis is unreachable
    (single-worker local dev).
    """
    domain = _normalize_domain(_require_configured_domain())

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
        "domain": domain,
        "issued_at": int(now),
        "expiry_seconds": _NONCE_TTL_SECONDS,
    }


@auth_router.post("/verify")
async def verify_signature(request: Request, response: Response):
    """Verify a signed SIWE message and issue a session cookie.

    Body: { "message": "<SIWE message text>", "signature": "0x..." }

    Fails closed (503) if PUBLIC_DOMAIN isn't configured (#940), before any
    domain comparison happens — see _require_configured_domain.
    """
    expected_domain = _require_configured_domain()

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
    # Read the signer address positionally from EIP-4361 line 2 — see
    # _address_from_siwe_message for why "the last 0x line" was wrong (#946).
    wallet_from_message = _address_from_siwe_message(message)
    for line in lines:
        line = line.strip()
        if line.startswith("Nonce: "):
            nonce = line[7:].strip()
        elif line.startswith("Chain ID: "):
            chain_id_from_message = line[len("Chain ID: ") :].strip()
        elif line.startswith("Issued At: "):
            issued_at_from_message = line[len("Issued At: ") :].strip()

    if not nonce:
        raise HTTPException(status_code=400, detail="Nonce not found in message")

    # The three EIP-4361 bindings below are REQUIRED, not best-effort: a message
    # that simply omits the domain / chain-id / issued-at lines must not slip
    # through unbound. Legacy regression fixtures always emit all three.

    # Domain binding — a signature for another dApp's domain must not authenticate
    # here, even if it happens to carry a live Archimedes nonce. Compare on the
    # bare authority (scheme/trailing-slash stripped) so "archimedes-arc.com" and
    # "https://archimedes-arc.com/" are treated as the same site.
    if domain_from_message is None:
        raise HTTPException(status_code=400, detail="SIWE message is missing the domain line")
    if _normalize_domain(domain_from_message) != _normalize_domain(expected_domain):
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

    recovered_lower = _canonical_wallet_or_none(verified_wallet)
    if not recovered_lower:
        raise HTTPException(status_code=401, detail="Invalid signature")

    # Re-derive the address via a hex round-trip rather than trusting the
    # regex-validated string as-is (CodeQL py/cookie-injection, #8). The
    # value below is ultimately traceable back to user-supplied SIWE message
    # text, and a custom regex check isn't recognized as a taint sanitizer;
    # decoding to bytes and re-encoding produces a value CodeQL's dataflow
    # analysis treats as freshly constructed, breaking that flow.
    recovered_lower = "0x" + bytes.fromhex(recovered_lower[2:]).hex()

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
    # we most need to move. Stays anonymous (vid only, no wallet) — D2a keeps
    # this pre-auth instrument unchanged. Fail-safe; never blocks the response.
    from archimedes.api.funnel_middleware import record_funnel

    await record_funnel(request, "wallet_connected")

    # The identity ledger (#1028, D2/D2a): this is THE missed write — the one
    # moment the backend holds a cryptographically verified wallet, and until
    # now nothing durable was written for it. Upserts wallet_identities(wallet,
    # last_auth_at) and appends an identity_events `auth_verified` row. The
    # browser vid is captured HERE — post-auth — deliberately reversing #787's
    # blanket "no wallet linkage": D2a scopes that rule to pre-auth traffic
    # only; a vid↔wallet link at the exact moment of proof is standard
    # analytics practice, not the over-extension #787 shipped. Fail-safe;
    # never blocks the auth response.
    from archimedes.services.identity_events import emit_identity_event

    emit_identity_event(
        wallet=recovered_lower,
        event_type="auth_verified",
        actor_class="human",
        vid=getattr(request.state, "visitor_id", None),
        touch_last_auth=True,
    )

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
