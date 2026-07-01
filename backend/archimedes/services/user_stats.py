"""User stats — the honest "real users" count (issue #830).

The distinct-user number that is safe to cite anywhere: the count of rows in the
``user_profiles`` table (one row per wallet, wallet address is the primary key).
This is the ONLY distinct-user instrument that is actually distinct — the
``/api/metrics`` human/agent counters are cumulative per-request tallies (site
traffic, NOT users), and the JS-gated distinct-visitor HLL counts browsers, not
verified identities. Surfacing this number alongside those keeps the honest count
adjacent so no surface can conflate traffic with users.

Fail-safe by construction: any DB error returns 0 rather than raising, mirroring
the telemetry/funnel stores — an instrument read must never turn a request into a
5xx.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

# Small in-process TTL cache. ``get_distinct_user_count`` is called from the
# highly-polled ``/health`` + ``/api/metrics`` endpoints, and the wallet count is
# a slowly-changing stat — caching it for a few seconds keeps frequent polling
# from running a Postgres COUNT on every request. Per-process and fail-safe: only a
# SUCCESSFUL read (a genuine count, including a real 0) is cached; a query error is
# NOT cached (the next call retries) and still returns 0.
_CACHE_TTL_SECONDS = 30.0
# (last successful count, monotonic timestamp of that read). ts == 0 → nothing cached yet.
_cache: tuple[int, float] = (0, 0.0)


def _query_distinct_user_count() -> int | None:
    """Run the actual COUNT query.

    Returns the distinct-wallet count, or ``None`` on any error (never raises) so the
    caller can tell a failed read apart from a genuine zero and avoid caching the failure.
    """
    try:
        from sqlalchemy import func

        from archimedes.db import get_session
        from archimedes.models.user_profile import UserProfile

        session = get_session()
        try:
            return int(session.query(func.count(UserProfile.wallet_address)).scalar() or 0)
        finally:
            session.close()
    except Exception as exc:
        logger.debug("distinct user count read failed: %s", exc)
        return None


def get_distinct_user_count() -> int:
    """Return the number of distinct users = wallet rows in ``user_profiles``.

    ``wallet_address`` is the table's primary key, so a plain row count is already a
    distinct-wallet count. A successful result (including a genuine 0) is cached for
    ``_CACHE_TTL_SECONDS`` so highly-polled callers (``/health``, ``/api/metrics``)
    don't hammer Postgres; a failed read is NOT cached, so the next call retries.
    Returns 0 on any error (never raises).
    """
    global _cache
    now = time.monotonic()
    value, ts = _cache
    if ts and (now - ts) < _CACHE_TTL_SECONDS:
        return value
    result = _query_distinct_user_count()
    if result is None:
        return 0  # query error → don't cache; the next call retries
    _cache = (result, now)
    return result


def _reset_cache() -> None:
    """Clear the TTL cache. Test hook so cached state can't leak between tests."""
    global _cache
    _cache = (0, 0.0)
