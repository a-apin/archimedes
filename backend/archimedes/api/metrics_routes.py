"""Metrics routes — public human-vs-agent traction counter (issue #428).

Exposes ``GET /api/metrics`` with the live cumulative human/agent request
counts maintained by the telemetry middleware. This is the read side of the
hackathon win-condition instrument; the write side is
``api/telemetry_middleware.py`` + ``services/telemetry_store.py``.

# TODO(T-observability): Phase 2 — Prometheus ``/metrics`` exposition endpoint
# and an arc-canteen async task that mirrors these counts onto the traction
# dashboard. Intentionally out of scope for this app-layer MVP to keep it tight.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Query, Request

from archimedes.api.funnel_middleware import record_funnel
from archimedes.api.visitor_insights import record_visitor_insight
from archimedes.models.telemetry import (
    CountryCount,
    FunnelEventRequest,
    FunnelResponse,
    FunnelStageCount,
    MetricsResponse,
    VisitorInsightsResponse,
)
from archimedes.services.funnel_store import CLIENT_EMITTABLE_STAGES, STAGES, FunnelStore
from archimedes.services.telemetry_store import TelemetryStore
from archimedes.services.user_stats import get_distinct_user_count
from archimedes.services.visitor_insights_store import VisitorInsightsStore

metrics_router = APIRouter(prefix="/api", tags=["metrics"])


def _build_funnel(counts: dict[str, int], window: str) -> FunnelResponse:
    """Turn an ordered stage->count map into a funnel with ratios.

    ``pct_of_landed`` is each stage vs the first stage; ``step_conversion`` is
    each stage vs the previous one. Both default to 0.0 when the denominator is
    zero so the response is always well-formed (no divide-by-zero).
    """
    landed = counts.get(STAGES[0], 0)
    stages: list[FunnelStageCount] = []
    prev = landed
    for i, stage in enumerate(STAGES):
        n = counts.get(stage, 0)
        pct = (n / landed) if landed else 0.0
        step = 1.0 if i == 0 else ((n / prev) if prev else 0.0)
        stages.append(
            FunnelStageCount(
                stage=stage,
                distinct_visitors=n,
                pct_of_landed=round(pct, 4),
                step_conversion=round(step, 4),
            )
        )
        prev = n
    return FunnelResponse(window=window, stages=stages, timestamp=datetime.now(UTC).isoformat())


@metrics_router.get("/metrics", response_model=MetricsResponse)
async def get_metrics() -> MetricsResponse:
    """Return the live human-vs-agent traction counts + the honest user count.

    ``human_count`` / ``agent_count`` / ``total_requests`` are cumulative
    per-request tallies (site traffic, NOT users). ``real_users`` is the distinct
    wallet count from ``user_profiles`` — the honest distinct-user number surfaced
    alongside so the tallies can't be mis-cited as users (issue #830).

    Fail-safe: ``TelemetryStore.get_counts`` returns ``(0, 0)`` and
    ``get_distinct_user_count`` returns ``0`` on error, so this endpoint always
    responds 200 with a well-formed shape.
    """
    store = TelemetryStore()
    try:
        human_count, agent_count = await store.get_counts()
    finally:
        await store.close()

    real_users = get_distinct_user_count()

    return MetricsResponse(
        human_count=human_count,
        agent_count=agent_count,
        total_requests=human_count + agent_count,
        real_users=real_users,
        timestamp=datetime.now(UTC).isoformat(),
    )


@metrics_router.get("/metrics/funnel", response_model=FunnelResponse)
async def get_funnel(
    day: str | None = Query(
        default=None,
        description="ISO date (YYYY-MM-DD) for a single-day window; omit for the all-time funnel.",
    ),
) -> FunnelResponse:
    """Return the distinct-visitor conversion funnel (issue #787).

    Stages: ``landed -> generation_started -> wallet_connected -> vault_deployed``.
    Fail-safe: ``FunnelStore`` returns zeros when Redis is unreachable, so this
    endpoint always responds 200 with a well-formed funnel.
    """
    store = FunnelStore()
    try:
        if day:
            counts = await store.get_day(day)
            window = day
        else:
            counts = await store.get_totals()
            window = "all-time"
    finally:
        await store.close()
    return _build_funnel(counts, window)


@metrics_router.post("/metrics/funnel/event")
async def record_funnel_event(req: FunnelEventRequest, request: Request) -> dict[str, bool]:
    """Client beacon for top-of-funnel stages (issue #787).

    Only ``CLIENT_EMITTABLE_STAGES`` (today: ``landed``) are accepted — every
    downstream stage is recorded server-side at its authoritative transition so a
    client cannot inflate it. Always 200 (``recorded`` flags whether it counted);
    the visitor id is read from the ``archimedes_vid`` cookie via request state.

    Issue #830: this JS-gated ``landed`` beacon is the single source of truth for
    "distinct visitor". We record geography + device here (same ``archimedes_vid``
    dedup key as the funnel) so geo/device count exactly the ``landed`` population
    — reconciling the funnel-vs-visitors discrepancy. Both writes are fail-safe.
    """
    if req.stage not in CLIENT_EMITTABLE_STAGES:
        return {"recorded": False}
    await record_funnel(request, req.stage)
    # Geography + device off the same landed beacon (one distinct-visitor source).
    await record_visitor_insight(request)
    return {"recorded": True}


@metrics_router.get("/metrics/visitors", response_model=VisitorInsightsResponse)
async def get_visitor_insights() -> VisitorInsightsResponse:
    """Where our human traffic comes from + on what device (issue #787).

    Distinct HUMAN visitors (agents excluded), country via CloudFront viewer-country,
    device via CloudFront device headers (UA fallback). Fail-safe: returns empty
    maps when Redis is unreachable, so this always responds 200.
    """
    store = VisitorInsightsStore()
    try:
        countries, devices = await store.get_insights()
    finally:
        await store.close()
    ranked = sorted(countries.items(), key=lambda kv: (-kv[1], kv[0]))
    return VisitorInsightsResponse(
        window="all-time",
        countries=[CountryCount(code=c, distinct_visitors=n) for c, n in ranked],
        devices=devices,
        timestamp=datetime.now(UTC).isoformat(),
    )
