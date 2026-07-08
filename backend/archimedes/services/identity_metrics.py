"""Identity-ledger read queries — issue #1028 Phase C ("metrics-views" chunk).

Every function here is a thin, fail-safe SQL view over the two tables the
identity ledger writes to (``wallet_identities`` / ``identity_events``,
``archimedes.models.identity``). This module is the read side; the write side
is ``services/identity_events.py``. Together they make the Insights
endpoints (``api/metrics_routes.py``) honest-by-construction instead of
Redis-HLL-derived — per the epic's acceptance criteria:

  - ``count_human_wallets`` / ``list_human_wallets`` — AC1: "real users" is
    ``count(*) FROM wallet_identities WHERE actor_class = 'human'``, and
    enumerating those wallets is one query.
  - ``list_wallet_connections`` — AC2: "which wallets connected, and when" —
    ``SELECT wallet, min(occurred_at) FROM identity_events WHERE event_type =
    'auth_verified' GROUP BY wallet``.
  - ``get_identity_funnel`` — AC3: the funnel recomputed from
    ``identity_events`` alone, with an ``actor_class NOT IN
    ('operator_dogfood', 'agent')`` filter to exclude dogfooding — no Redis
    HLL involved.

Fail-safe by construction, matching every other instrument in this codebase
(``telemetry_store.py``, ``funnel_store.py``, ``user_stats.py``): any DB error
returns the empty/zero shape rather than raising, so a read here can never
turn a request into a 5xx.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TypedDict

logger = logging.getLogger(__name__)

# The post-auth funnel, in order. Mirrors the pre-#1028 visitor funnel's
# downstream stages (``services/funnel_store.STAGES`` minus the anonymous
# ``landed`` stage, which has no wallet to key on and stays Redis/HLL — D2a
# keeps pre-auth tracking anonymous). Order is load-bearing for ratio math in
# ``api/metrics_routes._build_funnel``.
IDENTITY_FUNNEL_EVENT_TYPES: tuple[str, ...] = ("auth_verified", "generation_started", "vault_created")

# actor_class values excluded when a caller asks for the "real" (non-dogfood,
# non-agent) funnel — AC3's exact clause.
_DOGFOOD_ACTOR_CLASSES: tuple[str, ...] = ("operator_dogfood", "agent")


class WalletIdentityRow(TypedDict):
    wallet_address: str
    actor_class: str
    first_seen_at: str
    last_auth_at: str | None


class WalletConnectionRow(TypedDict):
    wallet: str
    connected_at: str


def count_human_wallets() -> int:
    """AC1: distinct human-identity count — ``wallet_identities`` where actor_class='human'.

    This is THE "real users" number (superseding the ``user_profiles`` row
    count, which undercounted by design: a wallet that owns a strategy or
    passport but never saved a profile was invisible to it). Returns 0 on any
    error.
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
        logger.debug("count_human_wallets failed: %s", exc)
        return 0


def list_human_wallets(limit: int = 500) -> list[WalletIdentityRow]:
    """AC1: enumerate the human wallets behind ``count_human_wallets`` — one query.

    Ordered oldest-first (``first_seen_at``) so the list reads as an
    onboarding timeline. Returns an empty list on any error.
    """
    try:
        from archimedes.db import get_session
        from archimedes.models.identity import WalletIdentity

        session = get_session()
        try:
            rows = (
                session.query(WalletIdentity)
                .filter(WalletIdentity.actor_class == "human")
                .order_by(WalletIdentity.first_seen_at.asc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "wallet_address": row.wallet_address,
                    "actor_class": row.actor_class,
                    "first_seen_at": row.first_seen_at.isoformat(),
                    "last_auth_at": row.last_auth_at.isoformat() if row.last_auth_at else None,
                }
                for row in rows
            ]
        finally:
            session.close()
    except Exception as exc:
        logger.debug("list_human_wallets failed: %s", exc)
        return []


def list_wallet_connections(limit: int = 500) -> list[WalletConnectionRow]:
    """AC2: "which wallets connected, and when" — impossible before the ledger.

    ``SELECT wallet, min(occurred_at) FROM identity_events WHERE event_type =
    'auth_verified' GROUP BY wallet``, ordered earliest-first. Returns an
    empty list on any error.
    """
    try:
        from sqlalchemy import func

        from archimedes.db import get_session
        from archimedes.models.identity import IdentityEvent

        session = get_session()
        try:
            rows = (
                session.query(IdentityEvent.wallet, func.min(IdentityEvent.occurred_at).label("connected_at"))
                .filter(IdentityEvent.event_type == "auth_verified")
                .filter(IdentityEvent.wallet.isnot(None))
                .group_by(IdentityEvent.wallet)
                .order_by(func.min(IdentityEvent.occurred_at).asc())
                .limit(limit)
                .all()
            )
            return [{"wallet": wallet, "connected_at": _isoformat(connected_at)} for wallet, connected_at in rows]
        finally:
            session.close()
    except Exception as exc:
        logger.debug("list_wallet_connections failed: %s", exc)
        return []


def get_identity_funnel(exclude_dogfood: bool = True) -> dict[str, int]:
    """AC3: distinct-wallet funnel recomputed from ``identity_events`` alone.

    Returns ``{event_type: distinct_wallet_count}`` for
    ``IDENTITY_FUNNEL_EVENT_TYPES``, defaulting every stage to 0 so the shape
    is always well-formed. ``exclude_dogfood=True`` (the default — the
    "honest, human-only" reading) adds ``actor_class NOT IN
    ('operator_dogfood', 'agent')``, AC3's exact clause; pass ``False`` for
    the unfiltered "everyone, including agents/dogfooding" reading. No Redis
    involved — every count is a live ``COUNT(DISTINCT wallet)`` over the
    ledger. Returns all-zero on any error.
    """
    counts = dict.fromkeys(IDENTITY_FUNNEL_EVENT_TYPES, 0)
    try:
        from sqlalchemy import func

        from archimedes.db import get_session
        from archimedes.models.identity import IdentityEvent

        session = get_session()
        try:
            query = (
                session.query(IdentityEvent.event_type, func.count(func.distinct(IdentityEvent.wallet)))
                .filter(IdentityEvent.event_type.in_(IDENTITY_FUNNEL_EVENT_TYPES))
                .filter(IdentityEvent.wallet.isnot(None))
            )
            if exclude_dogfood:
                query = query.filter(IdentityEvent.actor_class.notin_(_DOGFOOD_ACTOR_CLASSES))
            for event_type, count in query.group_by(IdentityEvent.event_type).all():
                counts[event_type] = int(count or 0)
        finally:
            session.close()
    except Exception as exc:
        logger.debug("get_identity_funnel failed: %s", exc)
    return counts


def _isoformat(value: datetime | str) -> str:
    """Best-effort ISO-8601 string. SQLite can hand back a naive ``str`` for
    an aggregate ``min()`` column instead of a ``datetime`` — normalize both.
    """
    if isinstance(value, str):
        return value
    return value.isoformat()
