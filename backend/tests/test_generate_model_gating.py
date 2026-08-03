"""Server-side model allowlist gate on POST /api/generate/start (P1).

The Generate page may send an optional ``model``. The server honors it ONLY
when it is an allowlisted free-tier id (defense in depth — the UI also disables
premium rows). A non-allowlisted or absent model is dropped and the pipeline
falls back to the env default → behavior UNCHANGED.

Hermetic: the Redis-backed job store is mocked at the boundary, and the
fire-and-forget pipeline task is patched out so no LLM/network call happens.
"""

from __future__ import annotations

import gc
import warnings
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from tests.auth_helpers import auth_cookies


def _client() -> TestClient:
    from archimedes.main import app

    return TestClient(app)


def _mock_store() -> MagicMock:
    store = MagicMock()
    store.enqueue = AsyncMock(return_value="job-abc")
    return store


def _start(model_value):
    """POST /start with the given model field; return (status, captured_payload)."""
    store = _mock_store()

    def _close_background_coroutine(coro):
        coro.close()
        return MagicMock()

    body = {
        "brief": {"intent": "low-vol treasury alternative with crypto upside", "risk_appetite": "moderate"},
    }
    if model_value is not None:
        body["model"] = model_value

    with (
        patch("archimedes.api.generate_routes.get_job_store", return_value=store),
        # Close the coroutine handed to the mock so it cannot leak a never-awaited warning.
        patch("archimedes.api.generate_routes.asyncio.create_task", side_effect=_close_background_coroutine),
    ):
        resp = _client().post("/api/generate/start", json=body, cookies=auth_cookies())
    captured = store.enqueue.call_args.kwargs["payload"] if store.enqueue.call_args else {}
    return resp, captured


def test_allowlisted_model_is_honored() -> None:
    resp, payload = _start("zai.glm-4.7-flash")
    assert resp.status_code == 202, resp.text
    assert payload.get("model") == "zai.glm-4.7-flash"


def test_premium_model_anonymous_rejected_402() -> None:
    """A premium (Anthropic) id from a non-entitled (here anonymous) caller is
    REJECTED with HTTP 402 by the paid-tier entitlement gate (T1.8 / #723) —
    it is NOT silently dropped to the env default, and no job is enqueued.

    (Previously this asserted a 202 silent drop; once the #723 entitlement gate
    landed on top of the free-tier allowlist, an explicit premium request is
    rejected rather than downgraded. The allowlist still drops junk ids — see
    test_junk_model_is_dropped.)
    """
    resp, payload = _start("us.anthropic.claude-sonnet-4-6")
    assert resp.status_code == 402, resp.text
    assert payload == {}  # rejected before enqueue


def test_junk_model_is_dropped() -> None:
    resp, payload = _start("not-a-real-model")
    assert resp.status_code == 202, resp.text
    assert payload.get("model") is None


def test_absent_model_unchanged_behavior() -> None:
    """No model field → model None in payload → pipeline uses env default."""
    resp, payload = _start(None)
    assert resp.status_code == 202, resp.text
    assert payload.get("model") is None


def test_start_mock_does_not_leak_pipeline_coroutine() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _start("zai.glm-4.7-flash")
        gc.collect()

    assert not [warning for warning in caught if "was never awaited" in str(warning.message)]
