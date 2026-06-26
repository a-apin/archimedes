"""Hermetic tests for the x402 nanopayment paywall (T1.2 / issue #713).

Covers the four states of the generic `x402_paywall` dependency:
  1. 402 when no PAYMENT-SIGNATURE header.
  2. 200 passthrough when PAYMENT-SIGNATURE present + facilitator settles (mocked
     at the aiohttp boundary).
  3. 429 when the per-payer daily spend cap is exceeded (SpendCapStore mocked).
  4. Flag off → the dependency is a no-op (route serves normally).

Hermetic per CLAUDE.md "Testing conventions" §1: no live Redis, no live Circle —
both boundaries are mocked. Mirrors the boundary-mocking precedent in
`test_api_routes.py` (AgentStateStore / httpx.ASGITransport) but uses a tiny
purpose-built FastAPI app so the test exercises the dependency in isolation,
independent of the heavyweight /construct handler (architect/LLM) wiring.
"""

from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, patch

import pytest
from archimedes.api import x402_middleware
from archimedes.api.x402_middleware import x402_paywall
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

RECIPIENT = "0x1111111111111111111111111111111111111111"
PRICE = 0.001


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_app():
    """Tiny app with one paywalled route, mirroring how /construct wires it."""
    app = FastAPI()

    @app.post("/paid")
    async def paid(_payment: dict | None = Depends(x402_paywall(PRICE, resource_path="/paid"))):
        return {"ok": True, "payment": _payment}

    return app


def _sig_header() -> dict[str, str]:
    """A base64 PAYMENT-SIGNATURE with an EIP-3009 `from` payer."""
    payload = {"payload": {"authorization": {"from": "0xPAYER000000000000000000000000000000beef"}}}
    raw = base64.b64encode(json.dumps(payload).encode()).decode()
    return {"PAYMENT-SIGNATURE": raw}


class _FakeResp:
    """Minimal aiohttp-style response context manager."""

    def __init__(self, status: int, body: dict):
        self.status = status
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._body

    async def text(self):
        return json.dumps(self._body)


@pytest.fixture(autouse=True)
def _arm_paywall(monkeypatch):
    """Arm the paywall + point the recipient at a fixed address for every test
    except the explicit flag-off case (which clears the flag itself)."""
    monkeypatch.setenv("ARCHIMEDES_X402_ENABLED", "1")
    monkeypatch.setenv("REVENUE_SPLIT_ADDRESS", RECIPIENT)
    monkeypatch.setenv("ARCHIMEDES_X402_MAX_USDC_PER_DAY", "0.10")
    yield


# ── 1. 402 when no payment signature ──────────────────────────────────────────


def test_402_when_no_payment_signature():
    client = TestClient(_make_app())
    r = client.post("/paid")
    assert r.status_code == 402
    # The PAYMENT-REQUIRED header names Arc + the recipient + the price.
    header = r.headers.get("PAYMENT-REQUIRED")
    assert header
    decoded = json.loads(base64.b64decode(header).decode())
    req = decoded["accepts"][0]
    assert req["network"] == "eip155:5042002"
    assert req["payTo"] == RECIPIENT
    assert req["amount"] == "1000"  # 0.001 USDC * 1e6 base units


# ── 2. 200 passthrough on a settled payment ───────────────────────────────────


def test_200_passthrough_when_settled():
    settle_body = {"success": True, "payer": "0xPAYER", "transaction": "tx-uuid", "network": "eip155:5042002"}
    with (
        patch(
            "archimedes.api.x402_middleware.aiohttp.ClientSession",
            return_value=_FakeSessionCM(settle_body, 200),
        ),
        patch.object(x402_middleware._spend_store, "amount_spent", AsyncMock(return_value=0.0)),
        patch.object(x402_middleware._spend_store, "record", AsyncMock()),
    ):
        client = TestClient(_make_app())
        r = client.post("/paid", headers=_sig_header())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["payment"]["payer"] == "0xPAYER"
    assert body["payment"]["transaction"] == "tx-uuid"


# ── 3. 429 when over the per-payer spend cap ──────────────────────────────────


def test_429_when_spend_cap_exceeded():
    settle_body = {"success": True, "payer": "0xPAYER", "transaction": "tx-uuid", "network": "eip155:5042002"}
    with (
        patch(
            "archimedes.api.x402_middleware.aiohttp.ClientSession",
            return_value=_FakeSessionCM(settle_body, 200),
        ),
        # Already spent 0.10 (== cap); the next 0.001 pushes over → 429.
        patch.object(x402_middleware._spend_store, "amount_spent", AsyncMock(return_value=0.10)),
        patch.object(x402_middleware._spend_store, "record", AsyncMock()) as rec,
    ):
        client = TestClient(_make_app())
        r = client.post("/paid", headers=_sig_header())
    assert r.status_code == 429, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "Daily spend cap exceeded"
    assert detail["cap_usdc"] == 0.10
    # We must NOT record a charge that was rejected.
    rec.assert_not_called()


# ── 4. Flag off → no-op passthrough ───────────────────────────────────────────


def test_flag_off_is_noop(monkeypatch):
    monkeypatch.setenv("ARCHIMEDES_X402_ENABLED", "0")
    client = TestClient(_make_app())
    # No payment header, but the paywall is disarmed → route serves 200.
    r = client.post("/paid")
    assert r.status_code == 200
    assert r.json()["payment"] is None


# ── 5. Settlement failure (facilitator success=false) → 402 ───────────────────


def test_402_when_settlement_fails():
    settle_body = {"success": False, "errorReason": "insufficient_balance", "transaction": "", "network": ""}
    with (
        patch(
            "archimedes.api.x402_middleware.aiohttp.ClientSession",
            return_value=_FakeSessionCM(settle_body, 200),
        ),
        patch.object(x402_middleware._spend_store, "amount_spent", AsyncMock(return_value=0.0)),
        patch.object(x402_middleware._spend_store, "record", AsyncMock()),
    ):
        client = TestClient(_make_app())
        r = client.post("/paid", headers=_sig_header())
    assert r.status_code == 402
    assert "insufficient_balance" in r.json()["detail"]


# ── 6. Misc edge cases ────────────────────────────────────────────────────────


def test_402_on_malformed_signature():
    """A non-base64 / non-JSON PAYMENT-SIGNATURE → 402, not a 500."""
    client = TestClient(_make_app())
    r = client.post("/paid", headers={"PAYMENT-SIGNATURE": "!!!not-base64!!!"})
    assert r.status_code == 402
    assert "Malformed" in r.json()["detail"]


def test_402_when_facilitator_non_200():
    """Facilitator HTTP error (e.g. 400) maps to a clean 402."""
    with (
        patch(
            "archimedes.api.x402_middleware.aiohttp.ClientSession",
            return_value=_FakeSessionCM({"error": "bad request"}, 400),
        ),
        patch.object(x402_middleware._spend_store, "amount_spent", AsyncMock(return_value=0.0)),
    ):
        client = TestClient(_make_app())
        r = client.post("/paid", headers=_sig_header())
    assert r.status_code == 402


def test_503_when_armed_but_recipient_unset(monkeypatch):
    """Flag on but no REVENUE_SPLIT_ADDRESS → fail loud (503), never free passage."""
    monkeypatch.delenv("REVENUE_SPLIT_ADDRESS", raising=False)
    client = TestClient(_make_app())
    r = client.post("/paid")
    assert r.status_code == 503


class _FakeRedis:
    """Minimal in-memory sorted-set double for the SpendCapStore Redis boundary."""

    def __init__(self):
        self.store: dict[str, dict[str, float]] = {}

    async def zremrangebyscore(self, key, lo, hi):
        members = self.store.get(key, {})
        self.store[key] = {m: s for m, s in members.items() if not (lo <= s <= hi)}

    async def zrange(self, key, start, end):
        members = sorted(self.store.get(key, {}).items(), key=lambda kv: kv[1])
        return [m for m, _ in members]

    async def zadd(self, key, mapping):
        self.store.setdefault(key, {}).update(mapping)

    async def expire(self, key, ttl):
        return True


async def test_spend_cap_store_records_and_reads():
    """Exercise the real SpendCapStore logic against an in-memory redis double."""
    store = x402_middleware.SpendCapStore()
    fake = _FakeRedis()
    store._redis = fake  # inject the boundary double

    now = 1_000_000.0
    assert await store.amount_spent("0xABC", now=now) == 0.0

    await store.record("0xABC", 0.03, now=now)
    await store.record("0xABC", 0.02, now=now + 1)
    assert abs(await store.amount_spent("0xABC", now=now + 2) - 0.05) < 1e-9

    # An entry older than the 24h window is trimmed on read.
    await store.record("0xABC", 0.10, now=now - x402_middleware.SPEND_WINDOW_SECONDS - 10)
    assert abs(await store.amount_spent("0xABC", now=now + 3) - 0.05) < 1e-9


# ── aiohttp.ClientSession test double ──────────────────────────────────────────


class _FakeSessionCM:
    """Stand-in for `aiohttp.ClientSession()` used as an async context manager.

    `_settle_with_facilitator` does `async with aiohttp.ClientSession() as s, s.post(...) as resp`.
    This double yields itself as the session and returns a `_FakeResp` from .post().
    """

    def __init__(self, body: dict, status: int = 200):
        self._body = body
        self._status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, *_a, **_k):
        return _FakeResp(self._status, self._body)
