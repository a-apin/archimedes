"""Visitor-insight capture helper — geography + device from a request (#787).

Derives the visitor's country (CloudFront-Viewer-Country) and device class
(CloudFront device headers, falling back to a User-Agent sniff) and records them
via ``VisitorInsightsStore``.

Population (issue #830): called from the **JS-gated ``landed`` beacon path**
(``POST /api/metrics/funnel/event`` with ``stage="landed"``), the same trigger
and same ``archimedes_vid`` dedup key the conversion funnel uses. This is the one
source of truth for "distinct visitor": geography + device now count exactly the
population the funnel's ``landed`` stage counts, reconciling the 17-vs-50 gap that
came from recording on every server-side human-classified request (which leaked
browser-UA bots through the open-demo default). Non-JS crawlers don't run the
beacon, so they don't appear here — but this is still an UA/beacon-derived signal,
not a verified-identity count. Fail-safe — never raises into the request.
"""

from __future__ import annotations

import logging

from starlette.requests import Request

logger = logging.getLogger(__name__)


def _device_class(request: Request) -> str:
    """Best-effort device class: prefer CloudFront's device headers, else UA sniff."""
    h = request.headers
    if h.get("cloudfront-is-tablet-viewer", "").lower() == "true":
        return "tablet"
    if h.get("cloudfront-is-mobile-viewer", "").lower() == "true":
        return "mobile"
    if h.get("cloudfront-is-smarttv-viewer", "").lower() == "true":
        return "tv"
    if h.get("cloudfront-is-desktop-viewer", "").lower() == "true":
        return "desktop"
    # Fallback (no CloudFront headers — local/dev, or before the TF change lands):
    ua = h.get("user-agent", "").lower()
    if "ipad" in ua or "tablet" in ua:
        return "tablet"
    if "mobi" in ua or "android" in ua or "iphone" in ua:
        return "mobile"
    if ua:
        return "desktop"
    return "unknown"


async def record_visitor_insight(request: Request, is_agent: bool = False) -> None:
    """Record this visitor's geography + device. Never raises.

    Called from the JS-gated ``landed`` beacon path (issue #830), so the caller is
    a browser that actually rendered the page. ``is_agent`` is retained as a
    defense-in-depth skip (a beacon carrying an internal-agent key / bot UA is not
    a real visitor) and defaults to ``False`` for the ordinary beacon call.
    """
    if is_agent:
        return
    try:
        visitor_id = getattr(request.state, "visitor_id", "") or ""
        if not visitor_id:
            return
        country = request.headers.get("cloudfront-viewer-country")
        device = _device_class(request)

        from archimedes.services.visitor_insights_store import VisitorInsightsStore

        store = VisitorInsightsStore()
        try:
            await store.record(country, device, visitor_id)
        finally:
            await store.close()
    except Exception as exc:
        logger.debug("record_visitor_insight failed: %s", exc)
