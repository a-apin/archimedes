"""Per-request timing, and the exemption that keeps it readable (#1436).

The ALB reported p50 0.046 s against p99 16.8 s, but its metrics are
per-target rather than per-route, so identifying which of `/app/library`'s
three mount-time calls owned the tail meant reaching for curl.

The exemption is the load-bearing part. #1436's own alarm was re-tuned on
2026-08-21 after 36 state transitions in one night, because p95 of
`TargetResponseTime` is structurally contaminated — SSE generation streams
legitimately run 300 s+, so at low traffic p95 equals the stream duration. A
slow-request log that treated a stream the same way would rebuild that failure
in the logs: every generation emitting a warning until nobody reads any of
them.

Hermetic: a bare Starlette app exercises the middleware directly, so nothing
here depends on the real route table, Redis, or a database.
"""

from __future__ import annotations

import logging

import pytest
from archimedes.api.timing_middleware import _route_template, _slow_request_ms, timing_middleware
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _app() -> FastAPI:
    app = FastAPI()
    app.middleware("http")(timing_middleware)

    @app.get("/fast")
    async def fast():
        return {"ok": True}

    @app.get("/slow")
    async def slow():
        import asyncio

        await asyncio.sleep(0.05)
        return {"ok": True}

    @app.get("/items/{item_id}")
    async def item(item_id: str):
        return {"id": item_id}

    @app.get("/stream")
    async def stream():
        # The sleep is BEFORE the response is returned, not inside the
        # generator. Under BaseHTTPMiddleware `call_next` resolves as soon as
        # the response starts, so a slow generator never reaches the timer —
        # a test that slept inside gen() would pass with the exemption
        # deleted, proving nothing.
        import asyncio

        await asyncio.sleep(0.05)

        async def gen():
            yield "data: hello\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    return app


async def _get(path: str, **kwargs):
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as client:
        return await client.get(path, **kwargs)


async def test_every_response_carries_its_duration():
    resp = await _get("/fast")
    assert resp.status_code == 200
    assert float(resp.headers["X-Response-Time-Ms"]) >= 0


async def test_a_slow_request_is_logged_with_its_route_and_duration(monkeypatch, caplog):
    monkeypatch.setenv("SLOW_REQUEST_MS", "1")
    with caplog.at_level(logging.WARNING, logger="archimedes.api.timing_middleware"):
        await _get("/slow")
    slow_lines = [r.getMessage() for r in caplog.records if "slow request" in r.getMessage()]
    assert len(slow_lines) == 1, slow_lines
    assert "/slow" in slow_lines[0]
    assert "duration_ms=" in slow_lines[0]


async def test_an_ordinary_request_is_not_logged(monkeypatch, caplog):
    monkeypatch.setenv("SLOW_REQUEST_MS", "10000")
    with caplog.at_level(logging.WARNING, logger="archimedes.api.timing_middleware"):
        await _get("/fast")
    assert not [r for r in caplog.records if "slow request" in r.getMessage()]


async def test_a_stream_is_never_reported_slow(monkeypatch, caplog):
    """The exemption. A 300s SSE stream is the feature working.

    Without this the log rebuilds the alarm-fatigue failure that forced the
    latency alarm off p95 on 2026-08-21.
    """
    monkeypatch.setenv("SLOW_REQUEST_MS", "1")
    with caplog.at_level(logging.WARNING, logger="archimedes.api.timing_middleware"):
        resp = await _get("/stream")
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert not [r for r in caplog.records if "slow request" in r.getMessage()], (
        "an SSE stream was logged as a slow request — long streams are the feature working, "
        "and warning on each one is how a signal stops being read"
    )


async def test_the_log_names_the_route_template_not_the_id(monkeypatch, caplog):
    """Path parameters carry wallet addresses and job ids. They stay out of logs,
    and templating also groups every hit on one endpoint into one line."""
    monkeypatch.setenv("SLOW_REQUEST_MS", "0.0001")
    with caplog.at_level(logging.WARNING, logger="archimedes.api.timing_middleware"):
        await _get("/items/0xdeadbeefcafe")
    line = next(r.getMessage() for r in caplog.records if "slow request" in r.getMessage())
    assert "/items/{item_id}" in line
    assert "0xdeadbeefcafe" not in line


class TestThresholdFailsSafe:
    def test_unset_uses_the_default(self, monkeypatch):
        monkeypatch.delenv("SLOW_REQUEST_MS", raising=False)
        assert _slow_request_ms() == 1000.0

    @pytest.mark.parametrize("bad", ["abc", "", "   ", "0", "-5"])
    def test_a_malformed_or_useless_value_falls_back(self, monkeypatch, bad):
        """A typo must not silently disable the logging or flood it with every request."""
        monkeypatch.setenv("SLOW_REQUEST_MS", bad)
        assert _slow_request_ms() == 1000.0

    def test_a_valid_value_is_honoured(self, monkeypatch):
        monkeypatch.setenv("SLOW_REQUEST_MS", "2500")
        assert _slow_request_ms() == 2500.0


def test_route_template_falls_back_when_nothing_matched():
    """A 404 has no matched route; the log must still be writable."""

    class _Req:
        def __init__(self) -> None:
            self.scope: dict = {}

    assert _route_template(_Req()) == "<unmatched>"


async def test_a_slow_handler_returning_a_stream_is_still_exempt(monkeypatch, caplog):
    """Belt and braces, and the case that actually distinguishes the exemption.

    Server-side timing measures until the response STARTS, so a long-running
    stream body never reaches this timer at all — unlike the ALB's
    TargetResponseTime, which measures the whole connection and is why p95 had
    to be abandoned for the latency alarm. The exemption covers the remaining
    case: a handler that is genuinely slow to produce a streaming response.
    """
    monkeypatch.setenv("SLOW_REQUEST_MS", "1")
    with caplog.at_level(logging.WARNING, logger="archimedes.api.timing_middleware"):
        resp = await _get("/stream")
    assert float(resp.headers["X-Response-Time-Ms"]) >= 50, "the handler really was slow"
    assert not [r for r in caplog.records if "slow request" in r.getMessage()]
