"""Route-level contract of the generation payment gate (Dan's directive).

Hermetic — same harness as test_generate_model_gating.py (mocked job store,
pipeline task patched out, signed auth cookies). The paywall module's own laws
live in services/test_generation_payment.py; THIS file pins the route wiring:

  * flag off (the shipped default) → /start behaves exactly as before;
  * flag on + no linked wallet → 409 wallet_link_required (with the faucet
    guidance — #1294's human-only step now bites agents here);
  * flag on + wallet + no payment → 402 quoting via PAYMENT-REQUIRED;
  * dry-run + payment header → 202, the request proceeds to enqueue;
  * the QUOTA runs BEFORE the paywall — a quota-blocked caller is 429'd
    without ever being asked to pay;
  * GET /api/generate/quote is public and matches the 402's quote.
"""

from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from tests.auth_helpers import auth_cookies

RECIPIENT = "0x00000000000000000000000000000000000000a1"

_BODY = {"brief": {"intent": "low-vol treasury alternative", "risk_appetite": "moderate"}}


def _client() -> TestClient:
    from archimedes.main import app

    return TestClient(app)


def _mock_store() -> MagicMock:
    store = MagicMock()
    store.enqueue = AsyncMock(return_value="job-pay")
    return store


def _close_background_coroutine(coro):
    coro.close()
    return MagicMock()


def _harness(store):
    return (
        patch("archimedes.api.generate_routes.get_job_store", return_value=store),
        patch("archimedes.api.generate_routes.asyncio.create_task", side_effect=_close_background_coroutine),
    )


def _payment_header(payer: str) -> str:
    payload = {"payload": {"authorization": {"from": payer, "value": "150000"}}}
    return base64.b64encode(json.dumps(payload).encode()).decode()


def test_flag_off_start_behaves_exactly_as_before(monkeypatch) -> None:
    monkeypatch.delenv("GENERATION_PAYMENT_REQUIRED", raising=False)
    store = _mock_store()
    p1, p2 = _harness(store)
    with p1, p2:
        resp = _client().post("/api/generate/start", json=_BODY, cookies=auth_cookies())
    assert resp.status_code == 202
    store.enqueue.assert_awaited_once()


def test_flag_on_without_linked_wallet_is_409_with_faucet_guidance(monkeypatch) -> None:
    monkeypatch.setenv("GENERATION_PAYMENT_REQUIRED", "true")
    monkeypatch.setenv("GENERATION_PAYMENT_RECIPIENT", RECIPIENT)
    store = _mock_store()
    p1, p2 = _harness(store)
    with (
        p1,
        p2,
        patch("archimedes.api.generate_routes.get_linked_wallet_address", return_value=None),
    ):
        resp = _client().post("/api/generate/start", json=_BODY, cookies=auth_cookies())
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["reason"] == "wallet_link_required"
    assert "faucet" in detail["message"].lower()
    store.enqueue.assert_not_called()


def test_flag_on_without_payment_is_402_with_requirements(monkeypatch) -> None:
    monkeypatch.setenv("GENERATION_PAYMENT_REQUIRED", "true")
    monkeypatch.setenv("GENERATION_PAYMENT_RECIPIENT", RECIPIENT)
    monkeypatch.setenv("PAYMENTS_DRY_RUN", "false")
    store = _mock_store()
    p1, p2 = _harness(store)
    with p1, p2:
        resp = _client().post("/api/generate/start", json=_BODY, cookies=auth_cookies())
    assert resp.status_code == 402
    assert "PAYMENT-REQUIRED" in resp.headers
    detail = resp.json()["detail"]
    assert detail["reason"] == "payment_required"
    # The 402's quote and the public quote endpoint can never disagree.
    quote = _client().get("/api/generate/quote").json()
    assert detail["quote"]["price"] == quote["price"]
    store.enqueue.assert_not_called()


def test_dry_run_payment_header_proceeds_to_enqueue(monkeypatch) -> None:
    monkeypatch.setenv("GENERATION_PAYMENT_REQUIRED", "true")
    monkeypatch.setenv("GENERATION_PAYMENT_RECIPIENT", RECIPIENT)
    monkeypatch.setenv("PAYMENTS_DRY_RUN", "true")
    store = _mock_store()
    p1, p2 = _harness(store)
    cookies = auth_cookies()
    with p1, p2:
        # The conftest legacy adapter links the cookie's wallet; any payer works
        # in dry-run because verify/settle are skipped (module tests pin that).
        resp = _client().post(
            "/api/generate/start",
            json=_BODY,
            cookies=cookies,
            headers={"Payment-Signature": _payment_header("0x" + "ab" * 20)},
        )
    assert resp.status_code == 202
    store.enqueue.assert_awaited_once()


def test_quota_runs_before_the_paywall(monkeypatch) -> None:
    """A quota-blocked caller must be refused 429 BEFORE being asked to pay —
    never pay-then-blocked. TESTING is unset so the quota branch runs; the
    quota itself is stubbed to raise, and the paywall is spied."""
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.setenv("GENERATION_PAYMENT_REQUIRED", "true")
    monkeypatch.setenv("GENERATION_PAYMENT_RECIPIENT", RECIPIENT)

    async def _blocked(request, user_id):
        raise HTTPException(status_code=429, detail="daily cap reached")

    paywall_spy = AsyncMock(side_effect=AssertionError("paywall must not run after a quota block"))
    store = _mock_store()
    p1, p2 = _harness(store)
    with (
        p1,
        p2,
        patch("archimedes.api.generate_routes.enforce_generation_quota", _blocked),
        patch("archimedes.api.generate_routes.generation_payment.enforce_generation_payment", paywall_spy),
    ):
        resp = _client().post("/api/generate/start", json=_BODY, cookies=auth_cookies())
    assert resp.status_code == 429
    paywall_spy.assert_not_called()
    store.enqueue.assert_not_called()


def test_quote_endpoint_is_public_and_reports_the_flag(monkeypatch) -> None:
    monkeypatch.delenv("GENERATION_PAYMENT_REQUIRED", raising=False)
    resp = _client().get("/api/generate/quote")  # no cookies — public by design
    assert resp.status_code == 200
    body = resp.json()
    assert body["payment_required"] is False
    assert body["price"] == "$0.150000"
    assert body["pricing_model"] == "flat_v1"
