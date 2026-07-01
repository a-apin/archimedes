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

logger = logging.getLogger(__name__)


def get_distinct_user_count() -> int:
    """Return the number of distinct users = wallet rows in ``user_profiles``.

    ``wallet_address`` is the table's primary key, so a plain row count is already
    a distinct-wallet count. Returns 0 on any error (never raises).
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
        return 0
