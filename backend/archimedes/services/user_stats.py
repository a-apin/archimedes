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
# from running a Postgres COUNT on every request. The cache is per-process and
# fail-safe: a query error caches nothing (so the next call retries) and still
# returns 0.
_CACHE_TTL_SECONDS = 30.0
_cache_value: int = 0
_cache_ts: float = 0.0


def _query_distinct_user_count() -> int:
    """Run the actual COUNT query. Returns 0 on any error (never raises)."""
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
        return 0


def get_distinct_user_count() -> int:
    """Return the number of distinct users = wallet rows in ``user_profiles``.

    ``wallet_address`` is the table's primary key, so a plain row count is already
    a distinct-wallet count. Result is cached in-process for ``_CACHE_TTL_SECONDS``
    so highly-polled callers (``/health``, ``/api/metrics``) don't hammer Postgres.
    Returns 0 on any error (never raises).
    """
    global _cache_value, _cache_ts
    now = time.monotonic()
    if _cache_ts and (now - _cache_ts) < _CACHE_TTL_SECONDS:
        return _cache_value
    _cache_value = _query_distinct_user_count()
    _cache_ts = now
    return _cache_value


def _reset_cache() -> None:
    """Clear the TTL cache. Test hook so cached state can't leak between tests."""
    global _cache_value, _cache_ts
    _cache_value = 0
    _cache_ts = 0.0
