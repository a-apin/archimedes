"""Per-request server-side timing, so the slow endpoint is identifiable (#1436).

The ALB reported p50 0.046 s against p95 6.2 s and p99 16.8 s. That gap says
most requests are static assets and the API calls that actually render a page
live in the tail — but ALB metrics are per-target, not per-route, so finding
*which* call is slow meant reaching for curl. `/app/library` issues three calls
on mount and there was no way to tell from logs which of the three was the one
costing seconds.

This records the duration of every request, exposes it on the response as
``X-Response-Time-Ms``, and logs a line only when a request crosses
``SLOW_REQUEST_MS``.

**Streaming responses are exempt, and that exemption is the point.** #1436's
alarm was re-tuned on 2026-08-21 after 36 state transitions in one night,
because p95 of ``TargetResponseTime`` is structurally contaminated: SSE
generation streams legitimately run 300 s+, so at low traffic p95 *equals* the
stream duration. A slow-request log that treated those the same way would
reproduce exactly that failure in the logs instead of the alarm — every
generation emitting a "slow request" warning until nobody reads them. A
300-second SSE stream is the feature working.

Fail-safe, like the telemetry and funnel middleware it sits alongside: timing
never turns a request into a 5xx.
"""

from __future__ import annotations

import logging
import os
import time

from fastapi import Request

logger = logging.getLogger(__name__)

#: Default slow-request threshold in milliseconds. 1000 ms is well under the
#: 2 s the ALB alarm used to trip on and well over the 46 ms p50, so it catches
#: the tail this issue is about without logging ordinary traffic.
_DEFAULT_SLOW_MS = 1000.0

#: Content types whose duration measures how long a client stayed connected
#: rather than how long the server took. See the module docstring.
_STREAMING_TYPES = ("text/event-stream",)


def _slow_request_ms() -> float:
    """Threshold in ms. Fails SAFE to the default on a malformed value — a typo
    must not silently disable the logging or flood it."""
    raw = os.getenv("SLOW_REQUEST_MS", "").strip()
    if not raw:
        return _DEFAULT_SLOW_MS
    try:
        value = float(raw)
    except ValueError:
        logger.warning("invalid SLOW_REQUEST_MS=%r — using default %s", raw, _DEFAULT_SLOW_MS)
        return _DEFAULT_SLOW_MS
    return value if value > 0 else _DEFAULT_SLOW_MS


def _route_template(request: Request) -> str:
    """The matched route's template, e.g. ``/api/strategies/{strategy_id}``.

    The template rather than the raw path for two reasons: it groups every hit
    on one endpoint into one identifiable line instead of scattering them
    across ids, and it keeps path parameters — which include wallet addresses
    and job ids — out of the logs.
    """
    route = request.scope.get("route")
    path_format = getattr(route, "path_format", None) or getattr(route, "path", None)
    if path_format:
        return str(path_format)
    return "<unmatched>"


def _is_streaming(response) -> bool:
    content_type = response.headers.get("content-type", "")
    return any(content_type.startswith(t) for t in _STREAMING_TYPES)


async def timing_middleware(request: Request, call_next):
    """Time the request, tag the response, log only the slow ones."""
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    try:
        response.headers["X-Response-Time-Ms"] = f"{elapsed_ms:.1f}"

        if _is_streaming(response):
            # A long stream is the feature working, not a slow request.
            return response

        threshold = _slow_request_ms()
        if elapsed_ms >= threshold:
            logger.warning(
                "slow request: %s %s status=%s duration_ms=%.1f threshold_ms=%.0f",
                request.method,
                _route_template(request),
                response.status_code,
                elapsed_ms,
                threshold,
            )
    except Exception:  # pragma: no cover — defensive, mirrors the sibling middleware
        logger.debug("timing middleware failed", exc_info=True)

    return response
