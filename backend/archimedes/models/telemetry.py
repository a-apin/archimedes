"""Telemetry data models — human-vs-agent traction counter (issue #428).

The traction counter is the hackathon win-condition instrument: it measures
how much of the live traffic is *humans* (browser sessions) vs *agents*
(internal agent runner + external bots/scripts) so the "agents make markets"
narrative is backed by a real, live number rather than a claim.

Identity model (today, single-user MVP):
  - HUMAN  = a valid SIWE wallet session cookie (``archimedes_session``).
  - AGENT  = a valid ``X-Internal-Agent-Key`` header (internal), OR no session
             plus a non-browser User-Agent (external bot/script).
  - Default (browser UA, no session) = HUMAN — the demo is open, so an
    un-signed-in browser visitor still counts as a human.

Only response *shapes* live here; classification logic lives in the telemetry
middleware and the counter persistence lives in ``services/telemetry_store.py``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class MetricsResponse(BaseModel):
    """Public traction counter — humans vs agents over the live deployment.

    Served by ``GET /api/metrics``. The ``*_count`` / ``total_requests`` fields are
    **cumulative per-request tallies (site traffic, NOT users)** — monotonic totals
    since the Redis counters were last reset (deploy / flush). ``real_users`` is the
    honest distinct-user number (wallet rows in ``user_profiles``) surfaced adjacent
    so the traffic tallies can never be read as a user count (issue #830).
    """

    human_count: int = Field(..., description="Human-UA REQUESTS (cumulative, site traffic — NOT users).")
    agent_count: int = Field(..., description="Agent/bot REQUESTS (cumulative, site traffic — NOT users).")
    total_requests: int = Field(..., description="human_count + agent_count (cumulative requests — NOT users).")
    real_users: int = Field(
        default=0,
        description="Distinct users = wallet rows in user_profiles. The honest distinct-user count (issue #830).",
    )
    timestamp: str = Field(..., description="ISO-8601 UTC timestamp this snapshot was read.")


class FunnelStageCount(BaseModel):
    """One stage of the conversion funnel (issue #787).

    ``distinct_visitors`` is an HLL estimate of unique visitors that reached this
    stage. ``pct_of_landed`` is this stage vs the top of funnel; ``step_conversion``
    is this stage vs the immediately preceding stage — the drop-off we care about.
    """

    stage: str = Field(..., description="Funnel stage name.")
    distinct_visitors: int = Field(..., description="Distinct visitors that reached this stage (HLL estimate).")
    pct_of_landed: float = Field(..., description="distinct_visitors / landed, as a fraction (0.0-1.0).")
    step_conversion: float = Field(..., description="distinct_visitors / previous-stage count (0.0-1.0).")


class FunnelResponse(BaseModel):
    """Distinct-visitor conversion funnel over the live deployment (issue #787).

    Served by ``GET /api/metrics/funnel``. Stages are ordered
    ``landed -> generation_started -> wallet_connected -> vault_deployed``.
    """

    window: str = Field(..., description='"all-time" or the ISO date (YYYY-MM-DD) the counts are for.')
    stages: list[FunnelStageCount] = Field(..., description="Ordered funnel stages with counts + ratios.")
    timestamp: str = Field(..., description="ISO-8601 UTC timestamp this snapshot was read.")


class FunnelEventRequest(BaseModel):
    """Client beacon body for ``POST /api/metrics/funnel/event`` (issue #787).

    Only top-of-funnel stages are client-emittable (see
    ``services.funnel_store.CLIENT_EMITTABLE_STAGES``); every downstream stage is
    recorded server-side at its authoritative transition so a client can't
    inflate it.
    """

    stage: str = Field(..., description="The funnel stage the browser is reporting (e.g. 'landed').")


class CountryCount(BaseModel):
    """Distinct human visitors from one country (issue #787)."""

    code: str = Field(..., description="ISO-3166 alpha-2 country code, or 'ZZ' when unknown.")
    distinct_visitors: int = Field(..., description="Distinct human visitors from this country (HLL estimate).")


class VisitorInsightsResponse(BaseModel):
    """Where our (un-promoted) human traffic comes from + on what device (issue #787).

    Served by ``GET /api/metrics/visitors``. Counts are distinct HUMAN visitors
    (agents/crawlers excluded) keyed on the anonymous visitor id. Country comes
    from CloudFront's viewer-country geolocation; device from CloudFront device
    headers (UA fallback).
    """

    window: str = Field(..., description='"all-time" snapshot window.')
    countries: list[CountryCount] = Field(..., description="Countries, sorted by distinct visitors (desc).")
    devices: dict[str, int] = Field(
        ..., description="Distinct visitors per device class (mobile/tablet/desktop/tv, or 'unknown' if unclassified)."
    )
    timestamp: str = Field(..., description="ISO-8601 UTC timestamp this snapshot was read.")
