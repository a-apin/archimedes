"""Canonical account count used by metrics and health surfaces.

Better Auth ``auth_users`` is source of truth. Wallet/profile counts are separate
metrics. Reads stay fail-safe: DB errors return 0 and are not cached.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

# Small in-process TTL cache. ``get_distinct_user_count`` is called from the
# highly-polled ``/health`` + ``/api/metrics`` endpoints, and account count is
# a slowly-changing stat — caching it for a few seconds keeps frequent polling
# from running a Postgres COUNT on every request. Per-process and fail-safe: only a
# SUCCESSFUL read (a genuine count, including a real 0) is cached; a query error is
# NOT cached (the next call retries) and still returns 0.
#
# Held in a MUTATED-IN-PLACE dict (never reassigned) so there is no module global to
# rebind — ``"ts" == 0`` means nothing is cached yet.
_CACHE_TTL_SECONDS = 30.0
_cache: dict[str, float] = {"value": 0.0, "ts": 0.0}


def _query_distinct_user_count() -> int | None:
    """Count canonical Better Auth users, distinguishing failure from real zero."""
    try:
        from sqlalchemy import func

        from archimedes.db import get_session
        from archimedes.models.account import AuthUser

        session = get_session()
        try:
            return int(session.query(func.count(AuthUser.id)).scalar() or 0)
        finally:
            session.close()
    except Exception as exc:
        logger.debug("distinct user count read failed: %s", exc)
        return None


def get_distinct_user_count() -> int:
    """Return canonical Better Auth account count, cached after successful reads."""
    now = time.monotonic()
    if _cache["ts"] and (now - _cache["ts"]) < _CACHE_TTL_SECONDS:
        return int(_cache["value"])
    result = _query_distinct_user_count()
    if result is None:
        return 0  # query error → don't cache; the next call retries
    _cache["value"] = result
    _cache["ts"] = now
    return result


def _reset_cache() -> None:
    """Clear the TTL cache. Test hook so cached state can't leak between tests."""
    _cache["value"] = 0.0
    _cache["ts"] = 0.0
