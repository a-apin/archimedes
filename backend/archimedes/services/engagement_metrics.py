"""Engagement/adoption metrics for the admin-only Insights dashboard v2.

Owner directive (2026-08-20, superseding issue #1028 D8 "public Insights
page" — see the ADMIN-ONLY gate on ``ui/src/components/Insights.jsx`` /
``ui/src/App.jsx`` and the server-side gate on
``api/metrics_private_routes.metrics_private_router``): ``/app/insights``
is now the owner traction dashboard, gated to ``PLATFORM_ADMIN_WALLETS``
holders. This module is the read side for its "dashboard v2" engagement
tiles — new admin-only queries against tables that already exist, no schema
change.

Every function is fail-safe by construction, matching every other read
instrument in this codebase (``user_stats.py``, ``identity_metrics.py``,
``funnel_store.py``): a DB error is logged and degrades to an honest empty/zero
shape, never a raised exception that would 5xx the dashboard. A subsystem
failing to read must not blank the tiles that DID read successfully — each
metric group is queried, and fails, independently (mirrors
``Insights.jsx``'s per-endpoint resilience for the same reason).

**What is and is not joinable today (verified against the ORM models, not
assumed):**

- Accounts (``auth_users.created_at``), linked wallets (``linked_wallets``
  row count), strategies generated (``strategy_store.created_at``),
  generation-cost measurements (``generation_costs`` — count + the ``llm``
  token block inside ``measurement_json``), and paper deployments
  (``paper_deployments.status``) are all single-table current-schema reads.
- The "repeat generator" proxy — accounts with more than one distinct
  generation DAY — is joinable: ``strategy_store.owner_user_id`` carries a
  real ``ForeignKey("auth_users.id")`` (the #1028 D1 FK retrofit), so grouping
  generated strategies by owner and counting distinct
  ``date(created_at)`` values per owner is a real join, not an approximation.
  It is scoped to rows with a non-NULL ``owner_user_id`` — pre-account
  (SIWE-era, wallet-only) generations have no account to attribute to and are
  excluded, which the response says explicitly rather than silently rounding
  them into either bucket.
- Money paid / settled volume has **no query at all** — ``PAYMENTS_DRY_RUN``
  gates every settlement path today (``services/generation_payment.py``,
  ``marketplace/settlement.py``), so there is no durable settled-volume
  record for any query to read. The response says ``dry_run`` and leaves
  ``settled_volume_usd`` as ``None`` rather than reporting a $0 that would
  read as "measured and zero" instead of "not yet metered".

See the PR body for the Phase 2 list of metrics this module does NOT attempt
(the schema-relations work that would make them possible is in flight
separately).
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# Daily-bucket time series window for strategies-generated trend. Small and
# fixed deliberately — this is a cheap read over existing timestamps, not a
# general-purpose analytics range picker.
_TREND_DAYS = 7


def _now() -> datetime:
    return datetime.now(UTC)


def _daily_buckets(timestamps: list[datetime | None], *, days: int) -> list[dict[str, Any]]:
    """Zero-filled UTC daily counts for the last ``days`` days, oldest first.

    Buckets in Python rather than a dialect-specific ``GROUP BY date(...)`` —
    the caller already fetched a bounded window of rows (see
    ``get_strategy_metrics``), and bucketing client-side keeps this file
    portable across the sqlite-in-test / Postgres-in-prod split without
    leaning on a SQL date-truncation function neither test suite exercises
    today.
    """
    today = _now().date()
    counts: Counter[date] = Counter()
    for ts in timestamps:
        if ts is None:
            continue
        counts[ts.date()] += 1
    return [
        {"date": (today - timedelta(days=offset)).isoformat(), "count": counts.get(today - timedelta(days=offset), 0)}
        for offset in range(days - 1, -1, -1)
    ]


def get_account_metrics() -> dict[str, Any]:
    """Canonical Better Auth accounts: total, new in the last 7d / 30d."""
    try:
        from sqlalchemy import func

        from archimedes.db import get_session
        from archimedes.models.account import AuthUser

        now = _now()
        session = get_session()
        try:
            total = int(session.query(func.count(AuthUser.id)).scalar() or 0)
            new_7d = int(
                session.query(func.count(AuthUser.id)).filter(AuthUser.created_at >= now - timedelta(days=7)).scalar()
                or 0
            )
            new_30d = int(
                session.query(func.count(AuthUser.id)).filter(AuthUser.created_at >= now - timedelta(days=30)).scalar()
                or 0
            )
        finally:
            session.close()
        return {"total": total, "new_7d": new_7d, "new_30d": new_30d}
    except Exception as exc:
        logger.debug("engagement_metrics.get_account_metrics failed: %s", exc)
        return {"total": 0, "new_7d": 0, "new_30d": 0}


def get_linked_wallet_metrics() -> dict[str, Any]:
    """Total account-linked wallets (``linked_wallets`` row count)."""
    try:
        from sqlalchemy import func

        from archimedes.db import get_session
        from archimedes.models.account import LinkedWallet

        session = get_session()
        try:
            total = int(session.query(func.count(LinkedWallet.id)).scalar() or 0)
        finally:
            session.close()
        return {"total": total}
    except Exception as exc:
        logger.debug("engagement_metrics.get_linked_wallet_metrics failed: %s", exc)
        return {"total": 0}


def get_strategy_metrics() -> dict[str, Any]:
    """Strategies generated: all-time total + a zero-filled 7-day daily trend."""
    try:
        from sqlalchemy import func

        from archimedes.db import get_session
        from archimedes.models.strategy_store import StrategyRecord

        now = _now()
        window_start = now - timedelta(days=_TREND_DAYS - 1)
        session = get_session()
        try:
            total = int(session.query(func.count(StrategyRecord.id)).scalar() or 0)
            recent = (
                session.query(StrategyRecord.created_at)
                .filter(StrategyRecord.created_at >= window_start.replace(hour=0, minute=0, second=0, microsecond=0))
                .all()
            )
        finally:
            session.close()
        timestamps = [row[0] for row in recent]
        return {
            "total": total,
            "new_7d": len(timestamps),
            "daily_new": _daily_buckets(timestamps, days=_TREND_DAYS),
        }
    except Exception as exc:
        logger.debug("engagement_metrics.get_strategy_metrics failed: %s", exc)
        return {"total": 0, "new_7d": 0, "daily_new": []}


def get_generation_cost_metrics() -> dict[str, Any]:
    """``generation_costs`` measured-run count + summed LLM token usage.

    Sums straight out of each row's ``measurement_json["llm"]`` block
    (``services/cost_meter.py``'s ``cost_v1`` shape) — money never lives in
    this table (``assert_measurement_only`` enforces that at write time), so
    there is nothing to convert here, only to add up. A row whose JSON fails
    to decode is skipped (logged, not counted) rather than crashing the sum —
    the same "corrupt row is an absence, not a zero baked into the total"
    posture ``GenerationCostRecord.to_payload`` already uses.
    """
    try:
        from sqlalchemy import func

        from archimedes.db import get_session
        from archimedes.models.generation_cost import GenerationCostRecord

        session = get_session()
        try:
            measured_count = int(session.query(func.count(GenerationCostRecord.id)).scalar() or 0)
            rows = session.query(GenerationCostRecord.measurement_json).all()
        finally:
            session.close()
        input_tokens = 0
        output_tokens = 0
        for (raw,) in rows:
            try:
                measurement = json.loads(raw) if raw else None
            except (json.JSONDecodeError, TypeError):
                logger.warning("engagement_metrics: corrupt generation_costs.measurement_json — skipping row")
                continue
            llm = measurement.get("llm") if isinstance(measurement, dict) else None
            if not isinstance(llm, dict):
                continue
            input_tokens += int(llm.get("input_tokens") or 0)
            output_tokens += int(llm.get("output_tokens") or 0)
        return {
            "measured_count": measured_count,
            "total_input_tokens": input_tokens,
            "total_output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }
    except Exception as exc:
        logger.debug("engagement_metrics.get_generation_cost_metrics failed: %s", exc)
        return {"measured_count": 0, "total_input_tokens": 0, "total_output_tokens": 0, "total_tokens": 0}


def get_paper_deployment_metrics() -> dict[str, Any]:
    """Paper-trading deployments by status (``paper_deployments.status``)."""
    try:
        from sqlalchemy import func

        from archimedes.db import get_session
        from archimedes.models.paper_store import STATUS_ACTIVE, STATUS_STOPPED, PaperDeployment

        session = get_session()
        try:
            active = int(
                session.query(func.count(PaperDeployment.id)).filter(PaperDeployment.status == STATUS_ACTIVE).scalar()
                or 0
            )
            stopped = int(
                session.query(func.count(PaperDeployment.id)).filter(PaperDeployment.status == STATUS_STOPPED).scalar()
                or 0
            )
        finally:
            session.close()
        return {"active": active, "stopped": stopped}
    except Exception as exc:
        logger.debug("engagement_metrics.get_paper_deployment_metrics failed: %s", exc)
        return {"active": 0, "stopped": 0}


def get_repeat_generation_metrics() -> dict[str, Any]:
    """Accounts with generations on more than one distinct calendar day.

    Scoped to ``strategy_store`` rows with a non-NULL ``owner_user_id`` — the
    real FK to ``auth_users.id`` (#1028 D1). Pre-account, wallet-only
    generations carry no account to attribute to and are excluded from both
    the numerator and denominator, which is why ``generating_users`` (the
    denominator) is reported alongside ``repeat_users`` rather than leaving a
    bare percentage that would silently imply full population coverage.
    """
    try:
        from archimedes.db import get_session
        from archimedes.models.strategy_store import StrategyRecord

        session = get_session()
        try:
            rows = (
                session.query(StrategyRecord.owner_user_id, StrategyRecord.created_at)
                .filter(StrategyRecord.owner_user_id.isnot(None))
                .all()
            )
        finally:
            session.close()
        days_by_user: dict[str, set[date]] = {}
        for owner_user_id, created_at in rows:
            if not owner_user_id or created_at is None:
                continue
            days_by_user.setdefault(owner_user_id, set()).add(created_at.date())
        generating_users = len(days_by_user)
        repeat_users = sum(1 for days in days_by_user.values() if len(days) > 1)
        return {
            "generating_users": generating_users,
            "repeat_users": repeat_users,
            "note": (
                "Accounts with strategy generations on more than one distinct calendar day. "
                "Scoped to strategy_store rows with a linked account (owner_user_id); "
                "pre-account wallet-only generations are excluded from both counts."
            ),
        }
    except Exception as exc:
        logger.debug("engagement_metrics.get_repeat_generation_metrics failed: %s", exc)
        return {"generating_users": 0, "repeat_users": 0, "note": "unavailable"}


def get_payments_snapshot() -> dict[str, Any]:
    """Money-paid tile — DRY-RUN only today; no settled-volume query exists.

    Mirrors ``main.py`` / ``services/generation_payment.py``'s exact
    ``PAYMENTS_DRY_RUN`` parse (the two must never disagree). This is the real
    field name settlement wiring will populate — ``settled_volume_usd`` stays
    ``None`` rather than ``0`` so a reader can't mistake "not yet metered" for
    "metered at zero".
    """
    import os

    dry_run = os.getenv("PAYMENTS_DRY_RUN", "true").lower() in ("1", "true", "yes")
    return {
        "dry_run": dry_run,
        "settled_volume_usd": None,
        "note": (
            "Payments are DRY-RUN — no real value has moved, so there is no settled volume to "
            "report. This is the real slot for it: when PAYMENTS_DRY_RUN=false and settlement "
            "is durably recorded, this field reads from that record instead of staying null."
            if dry_run
            else "PAYMENTS_DRY_RUN is off, but no durable settled-volume record exists yet to read from."
        ),
    }


def get_engagement_snapshot() -> dict[str, Any]:
    """Compose every engagement/adoption tile for the admin dashboard.

    Each sub-metric already fails soft to its own zero shape (see the
    functions above), so one subsystem's DB error degrades that tile without
    blanking the rest — the dashboard-level analogue of ``Insights.jsx``'s
    existing per-endpoint resilience.
    """
    return {
        "accounts": get_account_metrics(),
        "linked_wallets": get_linked_wallet_metrics(),
        "strategies": get_strategy_metrics(),
        "generation_costs": get_generation_cost_metrics(),
        "paper_deployments": get_paper_deployment_metrics(),
        "repeat_generation_users": get_repeat_generation_metrics(),
        "payments": get_payments_snapshot(),
        "timestamp": _now().isoformat(),
    }
