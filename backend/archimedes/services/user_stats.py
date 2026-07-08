"""User stats — the honest "real users" count (issue #830, superseded by #1028).

The distinct-user number that is safe to cite anywhere. **Issue #1028 (D1/AC1)
supersedes the original #830 implementation**: this used to count rows in
``user_profiles`` (one row per wallet that completed the optional profile
form), which undercounted real users by design — a wallet that owns a
strategy, a passport, or a vault but never saved a profile was invisible to
it (the audit that opened #1028 found this was a 3x undercount: 2
``user_profiles`` rows vs. 6 distinct wallets actually holding product data).

The honest source of truth is now the identity ledger's anchor table:
``count(*) FROM wallet_identities WHERE actor_class = 'human'`` — every SIWE-
verified wallet, anchored at the one moment the backend holds a
cryptographically proven identity (``auth_siwe.verify_signature`` →
``services.identity_events.emit_identity_event``), independent of whether
that wallet ever filled in a profile. See ``services/identity_metrics.py``
for the enumerable form of this same count (AC1's "enumerating those wallets
is one query") and the ``identity_events``-derived funnel/connection-log
queries this same ledger backs.

This module's function name / call sites (``get_distinct_user_count``) are
kept stable across the #830→#1028 handoff so every existing caller (``/api/
metrics``, ``/api/metrics/private/cost``, ``/health``) picks up the honest
number with no signature change.

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
#
# Held in a MUTATED-IN-PLACE dict (never reassigned) so there is no module global to
# rebind — ``"ts" == 0`` means nothing is cached yet.
_CACHE_TTL_SECONDS = 30.0
_cache: dict[str, float] = {"value": 0.0, "ts": 0.0}


def _query_distinct_user_count() -> int | None:
    """Run the actual COUNT query — ``wallet_identities WHERE actor_class = 'human'`` (#1028 AC1).

    Returns the distinct human-wallet count, or ``None`` on any error (never raises) so the
    caller can tell a failed read apart from a genuine zero and avoid caching the failure.

    Deliberately does NOT delegate to ``identity_metrics.count_human_wallets``
    (which swallows its own errors to a bare ``0``) — this function needs to
    tell "genuinely zero" apart from "the query failed" so the TTL cache below
    only caches successful reads, matching this module's pre-#1028 contract.
    """
    try:
        from sqlalchemy import func

        from archimedes.db import get_session
        from archimedes.models.identity import WalletIdentity

        session = get_session()
        try:
            return int(
                session.query(func.count(WalletIdentity.wallet_address))
                .filter(WalletIdentity.actor_class == "human")
                .scalar()
                or 0
            )
        finally:
            session.close()
    except Exception as exc:
        logger.debug("distinct user count read failed: %s", exc)
        return None


def get_distinct_user_count() -> int:
    """Return the number of distinct real users = human wallets in ``wallet_identities``.

    Superseded query as of issue #1028 (see module docstring) — was ``count(*)
    FROM user_profiles``, now ``count(*) FROM wallet_identities WHERE
    actor_class = 'human'``. A successful result (including a genuine 0) is
    cached for ``_CACHE_TTL_SECONDS`` so highly-polled callers (``/health``,
    ``/api/metrics``) don't hammer Postgres; a failed read is NOT cached, so
    the next call retries. Returns 0 on any error (never raises).
    """
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
