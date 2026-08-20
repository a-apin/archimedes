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
``funnel_store.py``): a DB error is logged and never raises out to 5xx the
dashboard. A subsystem failing to read must not blank the tiles that DID read
successfully — each metric group is queried, and fails, independently
(mirrors ``Insights.jsx``'s per-endpoint resilience for the same reason).

**The degraded shape on failure is ``None`` per numeric field, never ``0``**
(round 2 correction — CLAUDE.md's fail-soft principle: "for measured values,
the correct degraded state is a loud, visible absence... never a plausible
substitute"). A count of zero is a CLAIM ("the query ran and found nothing"),
not an absence, and is indistinguishable on the page from a real zero once
the route still returns 200 — ``Insights.jsx``'s ``Stat`` component already
renders ``None`` as an honest "—" and only a real number as a number, so
returning ``0`` here converted a database outage into a plausible-looking
"zero users generated anything today" instead of a visible gap. Every
``except`` branch below returns ``None`` for each numeric/list field plus
``unavailable: True`` so a caller can distinguish "queried, got zero" from
"could not query" even where a field's own value alone would be ambiguous.

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
- Money paid / settled volume has **no query at all** — not because no
  settlement record exists (round 2 correction: it does — ``settlement_intents``
  is a real, durable table with a ``status`` column that reaches
  ``"settled"``/``"failed"`` via ``marketplace/service.py``'s
  ``_finalize_settlement_intent``), but because its ``amount_usdc`` column is
  declared and **never written** anywhere in this codebase (verified:
  ``grep -rn amount_usdc backend/archimedes/`` returns only the column
  declaration itself) — so ``sum(amount_usdc) WHERE status='settled'`` would
  be a real query returning a meaningless ``NULL`` even against live rows,
  not a "no table to query" gap. ``PAYMENTS_DRY_RUN`` gates every settlement
  path before an intent is even claimed (``services/generation_payment.py``),
  which is a second, independent reason no volume exists yet — either one
  alone would already block this metric. The response says ``dry_run`` and
  leaves ``settled_volume_usd`` as ``None`` rather than reporting a $0 that
  would read as "measured and zero" instead of "not yet metered".

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


def _to_naive_utc(dt: datetime) -> datetime:
    """Coerce a tz-aware or naive datetime to naive-UTC wall-clock time.

    ``strategy_store.created_at`` is a bare, timezone-NAIVE ``DateTime``
    column always written via ``datetime.now(UTC)`` (models/strategy_store.py)
    — i.e. naive-UTC BY CONVENTION, not by the column type. ``auth_users
    .createdAt``, by contrast, is a genuine ``DateTime(timezone=True)``
    column (models/account.py). Binding a tz-aware Python value straight
    against the naive column pushes the aware/naive cast onto the DB
    session's ``TimeZone`` setting on Postgres — a non-UTC session would
    silently shift both the trend window boundary in ``get_strategy_metrics``
    and the day a row buckets into below. Converting to UTC first (a no-op
    for an already-UTC value) before stripping ``tzinfo`` keeps this file's
    own bucketing correct regardless of session timezone.
    """
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC)
    return dt.replace(tzinfo=None)


def _daily_buckets(timestamps: list[datetime | None], *, days: int) -> list[dict[str, Any]]:
    """Zero-filled UTC daily counts for the last ``days`` days, oldest first.

    Buckets in Python rather than a dialect-specific ``GROUP BY date(...)`` —
    the caller already fetched a bounded window of rows (see
    ``get_strategy_metrics``), and bucketing client-side keeps this file
    portable across the sqlite-in-test / Postgres-in-prod split without
    leaning on a SQL date-truncation function neither test suite exercises
    today. Every timestamp is coerced through ``_to_naive_utc`` before
    ``.date()`` so bucketing is stable regardless of whether a caller's rows
    happen to come back tz-aware or naive.
    """
    today = _to_naive_utc(_now()).date()
    counts: Counter[date] = Counter()
    for ts in timestamps:
        if ts is None:
            continue
        counts[_to_naive_utc(ts).date()] += 1
    return [
        {"date": (today - timedelta(days=offset)).isoformat(), "count": counts.get(today - timedelta(days=offset), 0)}
        for offset in range(days - 1, -1, -1)
    ]


def get_account_metrics() -> dict[str, Any]:
    """Canonical Better Auth accounts: total, new in the last 7d / 30d.

    ``new_7d`` uses the SAME calendar-day-anchored window as
    ``get_strategy_metrics``'s ``new_7d`` — i.e. midnight UTC, ``_TREND_DAYS``
    days ago, through now (round 2 fix: this used to be a plain rolling
    168-hour window while strategies used calendar days, so the two "(7d)"
    tiles ``Insights.jsx`` renders side by side — "New accounts (7d)" and
    "Generated (7d)" — covered spans that differed by up to a day, for
    reasons unrelated to actual activity, even though nothing about their
    presentation suggested the two windows meant different things).
    ``new_30d`` keeps a simple rolling 720-hour window — it has no adjacent
    "(30d)" metric it's implicitly compared against, so there is no
    definitional drift to fix there.
    """
    try:
        from sqlalchemy import func

        from archimedes.db import get_session
        from archimedes.models.account import AuthUser

        now = _now()
        window_7d_start = (now - timedelta(days=_TREND_DAYS - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
        session = get_session()
        try:
            total = int(session.query(func.count(AuthUser.id)).scalar() or 0)
            new_7d = int(
                session.query(func.count(AuthUser.id)).filter(AuthUser.created_at >= window_7d_start).scalar() or 0
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
        return {"total": None, "new_7d": None, "new_30d": None, "unavailable": True}


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
        return {"total": None, "unavailable": True}


def get_strategy_metrics() -> dict[str, Any]:
    """Strategies generated: all-time total + a zero-filled 7-day daily trend.

    Excludes ``is_example`` rows (the hand-curated set ``main.py`` seeds into
    ``strategy_store`` on every startup, ``generation_method="curated"``) —
    same exclusion ``strategies_routes.py``'s library listing already applies
    (``StrategyRecord.is_example.is_(False)``). Without it, "strategies
    generated" counted platform-seeded rows no user ever generated, inflating
    both the total and every day's entry in the trend that happens to fall on
    or after a seed run.

    The window-start bind is coerced through ``_to_naive_utc`` before the
    query (see that function) — ``StrategyRecord.created_at`` is a naive
    column; binding a tz-aware value against it directly would push the
    aware/naive cast onto the DB session's timezone setting.
    """
    try:
        from sqlalchemy import func

        from archimedes.db import get_session
        from archimedes.models.strategy_store import StrategyRecord

        now = _now()
        window_start = _to_naive_utc(now - timedelta(days=_TREND_DAYS - 1))
        session = get_session()
        try:
            total = int(
                session.query(func.count(StrategyRecord.id)).filter(StrategyRecord.is_example.is_(False)).scalar() or 0
            )
            recent = (
                session.query(StrategyRecord.created_at)
                .filter(StrategyRecord.is_example.is_(False))
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
        return {"total": None, "new_7d": None, "daily_new": None, "unavailable": True}


def get_generation_cost_metrics() -> dict[str, Any]:
    """``generation_costs`` measured-run count + summed LLM token usage.

    Sums straight out of each row's ``measurement_json["llm"]`` block
    (``services/cost_meter.py``'s ``cost_v1`` shape) — money never lives in
    this table (``assert_measurement_only`` enforces that at write time), so
    there is nothing to convert here, only to add up. A row whose JSON fails
    to decode, or decodes but carries no ``llm`` block, is skipped rather than
    crashing the sum — the same "corrupt row is an absence, not a zero baked
    into the total" posture ``GenerationCostRecord.to_payload`` already uses.

    ``measured_count`` counts ROWS THAT ACTUALLY CONTRIBUTED to the token
    sums below it — i.e. it is incremented in the SAME pass that decodes and
    sums, not taken as a separate ``COUNT(*)`` up front (round 2 fix: a
    ``COUNT(*)`` counts every row regardless of whether its JSON decodes,
    which made a corrupt row both "skipped from the sum" and "counted as
    measured" at once — a reader computing total_tokens / measured_count as
    an average would silently understate it whenever any row was corrupt).
    ``rows_total`` is the separate, honest "how many generation_costs rows
    exist at all" figure for anyone who wants it.
    """
    try:
        from archimedes.db import get_session
        from archimedes.models.generation_cost import GenerationCostRecord

        session = get_session()
        try:
            rows = session.query(GenerationCostRecord.measurement_json).all()
        finally:
            session.close()
        rows_total = len(rows)
        measured_count = 0
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
            measured_count += 1
            input_tokens += int(llm.get("input_tokens") or 0)
            output_tokens += int(llm.get("output_tokens") or 0)
        return {
            "measured_count": measured_count,
            "rows_total": rows_total,
            "total_input_tokens": input_tokens,
            "total_output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }
    except Exception as exc:
        logger.debug("engagement_metrics.get_generation_cost_metrics failed: %s", exc)
        return {
            "measured_count": None,
            "rows_total": None,
            "total_input_tokens": None,
            "total_output_tokens": None,
            "total_tokens": None,
            "unavailable": True,
        }


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
        return {"active": None, "stopped": None, "unavailable": True}


def get_repeat_generation_metrics() -> dict[str, Any]:
    """Accounts owning content-hash-distinct strategy rows spanning >1 day.

    Scoped to ``strategy_store`` rows with a non-NULL ``owner_user_id`` — the
    real FK to ``auth_users.id`` (#1028 D1). Pre-account, wallet-only
    generations carry no account to attribute to and are excluded from both
    the numerator and denominator, which is why ``generating_users`` (the
    denominator) is reported alongside ``repeat_users`` rather than leaving a
    bare percentage that would silently imply full population coverage.

    This is a PROXY for "generated on more than one calendar day", not an
    exact measure, because ``created_at`` tracks row creation, not per-user
    activity, and ``upsert_strategy``'s content-hash dedup can attach an
    account to a row it did not create on the day it was created:
    regenerating byte-identical content returns the EXISTING row (no new
    ``created_at``, so a second real day of activity is silently not
    counted), and an ownerless legacy row that gets backfilled with
    ``owner_user_id`` keeps its original (pre-account) ``created_at`` (so a
    day that predates the account can count toward that account's span). Both
    directions of drift are accepted as the cost of a single-table proxy
    rather than an activity-log join that does not exist yet — the ``note``
    below states the proxy's actual definition rather than the informal
    "days a user generated" framing the field name suggests.
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
                "Accounts owning content-hash-distinct strategy rows whose creation dates span "
                "more than one calendar day. Scoped to strategy_store rows with a linked account "
                "(owner_user_id); pre-account wallet-only generations are excluded from both "
                "counts. Content-identical regenerations dedupe to the original row and are not "
                "counted as a second day, and a legacy row backfilled with an owner keeps its "
                "original creation date — this is a proxy for multi-day usage, not an exact count."
            ),
        }
    except Exception as exc:
        logger.debug("engagement_metrics.get_repeat_generation_metrics failed: %s", exc)
        return {"generating_users": None, "repeat_users": None, "note": "unavailable", "unavailable": True}


def get_payments_snapshot() -> dict[str, Any]:
    """Money-paid tile — no summable settled-volume figure exists today.

    Mirrors ``main.py`` / ``services/generation_payment.py``'s exact
    ``PAYMENTS_DRY_RUN`` parse (the two must never disagree). This is the real
    field name settlement wiring will populate — ``settled_volume_usd`` stays
    ``None`` rather than ``0`` so a reader can't mistake "not yet metered" for
    "metered at zero".

    Round 2 correction: when ``PAYMENTS_DRY_RUN`` is off, ``settlement_intents``
    (``models/marketplace.py``) IS a durable, real table that reaches
    ``status="settled"`` on the live path (``marketplace/service.py``'s
    ``_finalize_settlement_intent``) — the record exists. What is missing is
    narrower: its ``amount_usdc`` column is declared but never written by any
    code path, so there is no per-settlement amount to sum even though the
    settlement event itself is durably recorded.
    """
    import os

    dry_run = os.getenv("PAYMENTS_DRY_RUN", "true").lower() in ("1", "true", "yes")
    return {
        "dry_run": dry_run,
        "settled_volume_usd": None,
        "note": (
            "Payments are DRY-RUN — no real value has moved, so there is no settled volume to "
            "report. This is the real slot for it: when PAYMENTS_DRY_RUN=false and a settled "
            "amount is durably recorded, this field reads from that record instead of staying null."
            if dry_run
            else (
                "PAYMENTS_DRY_RUN is off and settlement_intents durably records that charges "
                "settled, but its amount_usdc column is never populated by any code path today, "
                "so there is no amount to sum yet."
            )
        ),
    }


def get_engagement_snapshot() -> dict[str, Any]:
    """Compose every engagement/adoption tile for the admin dashboard.

    Each sub-metric already fails soft to its own ``None``-per-field shape
    with ``unavailable: True`` (see the functions above), so one subsystem's
    DB error degrades that tile to an honest "—" without blanking the rest
    or reading as a measured zero — the dashboard-level analogue of
    ``Insights.jsx``'s existing per-endpoint resilience.
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
