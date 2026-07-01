"""Hermetic tests for the telemetry request classifier + middleware (issue #428).

Covers the deterministic human-vs-agent classification and the middleware's
fail-safe behaviour:

  - valid SIWE session            → human
  - valid X-Internal-Agent-Key    → agent (internal)
  - bot/script User-Agent         → agent (external)
  - browser UA, no session        → human (open demo default)
  - Redis-down                    → middleware never raises (no 5xx)

No live Redis / Postgres / Anthropic / Arc RPC. The SIWE session is built with
the real ``_sign_session`` (the auth layer's own signer) so the human path is
exercised through production ``_verify_session`` rather than a mock. The Redis
boundary is mocked via ``patch.object`` on the store's client accessor.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest
from archimedes.api.auth_siwe import _COOKIE_NAME, _sign_session
from archimedes.api.telemetry_middleware import classify_request, telemetry_middleware
from starlette.requests import Request

_WALLET = "0x1111111111111111111111111111111111111111"


def _make_request(
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
    method: str = "GET",
    path: str = "/api/anything",
) -> Request:
    """Build a minimal ASGI ``Request`` with given headers/cookies (hermetic)."""
    raw_headers: list[tuple[bytes, bytes]] = []
    for k, v in (headers or {}).items():
        raw_headers.append((k.lower().encode(), v.encode()))
    if cookies:
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
        raw_headers.append((b"cookie", cookie_str.encode()))
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": raw_headers,
        "query_string": b"",
    }
    return Request(scope)


# ── classify_request ─────────────────────────────────────────────


def test_siwe_session_classifies_as_human():
    token = _sign_session(_WALLET, time.time())
    request = _make_request(headers={"user-agent": "curl/8.0"}, cookies={_COOKIE_NAME: token})
    # Even with a bot UA, a valid session wins → human.
    is_agent, agent_type = classify_request(request)
    assert is_agent is False
    assert agent_type == "human"


def test_internal_key_classifies_as_agent():
    with patch.dict("os.environ", {"INTERNAL_AGENT_API_KEY": "secret-key-123"}):
        request = _make_request(
            headers={"user-agent": "Mozilla/5.0", "x-internal-agent-key": "secret-key-123"},
        )
        is_agent, agent_type = classify_request(request)
    assert is_agent is True
    assert agent_type == "internal"


def test_wrong_internal_key_is_not_agent_internal():
    with patch.dict("os.environ", {"INTERNAL_AGENT_API_KEY": "secret-key-123"}):
        # Wrong key + browser UA + no session → falls through to human default.
        request = _make_request(
            headers={"user-agent": "Mozilla/5.0", "x-internal-agent-key": "wrong-key"},
        )
        is_agent, agent_type = classify_request(request)
    assert is_agent is False
    assert agent_type == "human"


def test_unset_internal_key_fails_closed():
    # No INTERNAL_AGENT_API_KEY configured → no request can be "internal".
    with patch.dict("os.environ", {}, clear=False):
        import os

        os.environ.pop("INTERNAL_AGENT_API_KEY", None)
        request = _make_request(headers={"user-agent": "Mozilla/5.0", "x-internal-agent-key": ""})
        is_agent, agent_type = classify_request(request)
    assert agent_type == "human"
    assert is_agent is False


@pytest.mark.parametrize(
    "ua",
    ["curl/8.0", "python-requests/2.31", "boto3/1.34", "axios/1.6", "Googlebot/2.1", ""],
)
def test_bot_user_agent_classifies_as_agent_external(ua):
    request = _make_request(headers={"user-agent": ua})
    is_agent, agent_type = classify_request(request)
    assert is_agent is True
    assert agent_type == "external"


def test_browser_no_session_classifies_as_human():
    request = _make_request(
        headers={"user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
    )
    is_agent, agent_type = classify_request(request)
    assert is_agent is False
    assert agent_type == "human"


def test_expired_session_is_not_human_session():
    # An expired session token fails _verify_session; with a bot UA it's external.
    expired = _sign_session(_WALLET, time.time() - 10**9)
    request = _make_request(headers={"user-agent": "curl/8.0"}, cookies={_COOKIE_NAME: expired})
    is_agent, agent_type = classify_request(request)
    assert is_agent is True
    assert agent_type == "external"


# ── _is_counted_request: read-exclusion is method-aware (issue #830) ──────────


def test_metrics_read_get_is_not_counted():
    """A GET metrics-read poll (the dashboard polling itself) is excluded."""
    from archimedes.api.telemetry_middleware import _is_counted_request

    for path in ("/api/metrics", "/api/metrics/funnel", "/api/metrics/visitors", "/health", "/api/health"):
        assert _is_counted_request(_make_request(method="GET", path=path)) is False


def test_funnel_event_beacon_post_is_counted():
    """The POST ``/api/metrics/funnel/event`` beacon is a real user action → counts.

    Regression for the read-exclusion bug: excluding the ``/api/metrics`` prefix for
    ALL methods wrongly stopped counting the beacon POST (issue #830 review).
    """
    from archimedes.api.telemetry_middleware import _is_counted_request

    assert _is_counted_request(_make_request(method="POST", path="/api/metrics/funnel/event")) is True


def test_options_preflight_is_not_counted():
    """OPTIONS is a CORS preflight, never a user action."""
    from archimedes.api.telemetry_middleware import _is_counted_request

    assert _is_counted_request(_make_request(method="OPTIONS", path="/api/metrics/funnel/event")) is False
    assert _is_counted_request(_make_request(method="OPTIONS", path="/api/strategies")) is False


def test_ordinary_request_is_counted():
    """A normal page/API GET (not a metrics-read/health path) still counts."""
    from archimedes.api.telemetry_middleware import _is_counted_request

    assert _is_counted_request(_make_request(method="GET", path="/api/strategies")) is True


# ── middleware fail-safe ─────────────────────────────────────────


async def _passthrough(_request):
    from starlette.responses import JSONResponse

    return JSONResponse({"ok": True})


@pytest.mark.asyncio
async def test_middleware_sets_state_and_header_for_agent():
    request = _make_request(headers={"user-agent": "curl/8.0"})
    # Mock the Redis boundary so no real Redis is touched.
    with patch(
        "archimedes.services.telemetry_store.TelemetryStore._get_redis",
        new=AsyncMock(return_value=AsyncMock()),
    ):
        response = await telemetry_middleware(request, _passthrough)
    assert request.state.is_agent is True
    assert request.state.agent_type == "external"
    assert response.headers["X-Telemetry-Agent"] == "true"


@pytest.mark.asyncio
async def test_middleware_sets_header_false_for_human():
    request = _make_request(headers={"user-agent": "Mozilla/5.0"})
    with patch(
        "archimedes.services.telemetry_store.TelemetryStore._get_redis",
        new=AsyncMock(return_value=AsyncMock()),
    ):
        response = await telemetry_middleware(request, _passthrough)
    assert request.state.is_agent is False
    assert response.headers["X-Telemetry-Agent"] == "false"


@pytest.mark.asyncio
async def test_middleware_does_not_raise_when_redis_down():
    """A Redis outage must never turn a request into a 5xx — middleware swallows it."""
    request = _make_request(headers={"user-agent": "curl/8.0"})
    with patch(
        "archimedes.services.telemetry_store.TelemetryStore._get_redis",
        new=AsyncMock(side_effect=ConnectionError("redis down")),
    ):
        response = await telemetry_middleware(request, _passthrough)
    # The request still completes; the header is still set.
    assert response.headers["X-Telemetry-Agent"] == "true"
    assert request.state.is_agent is True


@pytest.mark.asyncio
async def test_store_increment_swallows_redis_error():
    """TelemetryStore.increment_* must never raise even when Redis is unreachable."""
    from archimedes.services.telemetry_store import TelemetryStore

    store = TelemetryStore()
    with patch.object(
        TelemetryStore,
        "_get_redis",
        new=AsyncMock(side_effect=ConnectionError("redis down")),
    ):
        # Should not raise.
        await store.increment_human()
        await store.increment_agent()
        humans, agents = await store.get_counts()
    # Read also fails safe → zeros.
    assert humans == 0
    assert agents == 0
