"""DB-level correctness tests for ``services/engagement_metrics.py`` (Insights dashboard v2).

Unlike the HTTP-layer gating tests in ``test_metrics_private_routes.py`` /
``test_metrics_routes.py`` (which mock the service boundary), these tests seed
real rows into a tmp-file SQLite via the ``redirect_to_tmp_sqlite`` precedent
(``tests/db_isolation.py`` / ``test_generation_cost_persistence.py``) and
assert against the actual ORM models + queries — the "verify each query
against the models first" requirement, made durable as a regression guard
rather than a one-time manual check.

Hermetic: tmp-file SQLite, no Postgres, no Redis, no network.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from archimedes.db import get_session
from archimedes.models.account import AuthUser, LinkedWallet
from archimedes.models.generation_cost import GenerationCostRecord
from archimedes.models.paper_store import STATUS_ACTIVE, STATUS_STOPPED, PaperDeployment
from archimedes.models.strategy_store import StrategyRecord
from archimedes.services import engagement_metrics

from tests.db_isolation import redirect_to_tmp_sqlite


@pytest.fixture
def tmp_db(tmp_path):
    yield from redirect_to_tmp_sqlite(tmp_path)


def _now() -> datetime:
    return datetime.now(UTC)


def _make_user(user_id: str, *, created_at: datetime) -> AuthUser:
    return AuthUser(
        id=user_id,
        name=f"user-{user_id}",
        email=f"{user_id}@example.test",
        email_verified=True,
        created_at=created_at,
        updated_at=created_at,
    )


def _make_strategy(strategy_id: str, *, owner_user_id: str | None, created_at: datetime) -> StrategyRecord:
    return StrategyRecord(
        id=strategy_id,
        content_hash=f"0x{uuid.uuid4().hex}{uuid.uuid4().hex}"[:66],
        generation_method="fusion",
        owner_user_id=owner_user_id,
        created_at=created_at,
        updated_at=created_at,
    )


# ── Accounts ────────────────────────────────────────────────────────────


def test_account_metrics_totals_and_windows(tmp_db):
    now = _now()
    with get_session() as session:
        session.add(_make_user("u-fresh", created_at=now - timedelta(hours=1)))
        session.add(_make_user("u-week", created_at=now - timedelta(days=5)))
        session.add(_make_user("u-month", created_at=now - timedelta(days=20)))
        session.add(_make_user("u-old", created_at=now - timedelta(days=90)))
        session.commit()

    result = engagement_metrics.get_account_metrics()
    assert result["total"] == 4
    assert result["new_7d"] == 2  # u-fresh, u-week
    assert result["new_30d"] == 3  # u-fresh, u-week, u-month


def test_account_metrics_empty_db_is_honest_zero(tmp_db):
    assert engagement_metrics.get_account_metrics() == {"total": 0, "new_7d": 0, "new_30d": 0}


# ── Linked wallets ──────────────────────────────────────────────────────


def test_linked_wallet_metrics_counts_rows(tmp_db):
    now = _now()
    with get_session() as session:
        session.add(_make_user("u1", created_at=now))
        session.add(_make_user("u2", created_at=now))
        session.flush()
        session.add(
            LinkedWallet(
                user_id="u1",
                normalized_identity="eip155:5042002:0x1111111111111111111111111111111111111111",
                address="0x1111111111111111111111111111111111111111",
                display_address="0x1111111111111111111111111111111111111111",
                chain_id=5042002,
                provider="metamask",
            )
        )
        session.add(
            LinkedWallet(
                user_id="u2",
                normalized_identity="eip155:5042002:0x2222222222222222222222222222222222222222",
                address="0x2222222222222222222222222222222222222222",
                display_address="0x2222222222222222222222222222222222222222",
                chain_id=5042002,
                provider="metamask",
            )
        )
        session.commit()

    assert engagement_metrics.get_linked_wallet_metrics() == {"total": 2}


# ── Strategies generated ────────────────────────────────────────────────


def test_strategy_metrics_total_and_daily_trend(tmp_db):
    now = _now()
    today = now.date()
    with get_session() as session:
        # Two today, one yesterday, one outside the 7-day trend window (but
        # still counted in the all-time total).
        session.add(_make_strategy("s-today-1", owner_user_id=None, created_at=now))
        session.add(_make_strategy("s-today-2", owner_user_id=None, created_at=now))
        session.add(_make_strategy("s-yesterday", owner_user_id=None, created_at=now - timedelta(days=1)))
        session.add(_make_strategy("s-ancient", owner_user_id=None, created_at=now - timedelta(days=40)))
        session.commit()

    result = engagement_metrics.get_strategy_metrics()
    assert result["total"] == 4
    assert result["new_7d"] == 3  # excludes s-ancient
    assert len(result["daily_new"]) == 7
    assert result["daily_new"][-1] == {"date": today.isoformat(), "count": 2}
    assert result["daily_new"][-2] == {"date": (today - timedelta(days=1)).isoformat(), "count": 1}
    # Zero-filled, not sparse.
    assert result["daily_new"][0]["count"] == 0


# ── Generation costs ────────────────────────────────────────────────────


def _cost_row(job_id: str, *, input_tokens: int, output_tokens: int) -> GenerationCostRecord:
    measurement = {
        "schema": "cost_v1",
        "job_id": job_id,
        "llm": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    }
    return GenerationCostRecord(
        job_id=job_id,
        strategy_id=f"strat-{job_id}",
        schema_version="cost_v1",
        measurement_json=json.dumps(measurement),
        recorded_at=_now(),
    )


def test_generation_cost_metrics_sums_tokens_and_skips_corrupt_rows(tmp_db):
    with get_session() as session:
        session.add(_cost_row("job-1", input_tokens=100, output_tokens=50))
        session.add(_cost_row("job-2", input_tokens=200, output_tokens=75))
        # Corrupt row: must be skipped, not crash the sum or inflate the count.
        session.add(
            GenerationCostRecord(
                job_id="job-corrupt",
                strategy_id="strat-corrupt",
                schema_version="cost_v1",
                measurement_json="{not valid json",
                recorded_at=_now(),
            )
        )
        session.commit()

    result = engagement_metrics.get_generation_cost_metrics()
    assert result["measured_count"] == 3  # the corrupt row still exists as a row
    assert result["total_input_tokens"] == 300
    assert result["total_output_tokens"] == 125
    assert result["total_tokens"] == 425


# ── Paper deployments ───────────────────────────────────────────────────


def test_paper_deployment_metrics_counts_by_status(tmp_db):
    today = date.today()
    with get_session() as session:
        session.add(PaperDeployment(strategy_id="s1", spec_json="{}", deployed_at=today, status=STATUS_ACTIVE))
        session.add(PaperDeployment(strategy_id="s2", spec_json="{}", deployed_at=today, status=STATUS_ACTIVE))
        session.add(PaperDeployment(strategy_id="s3", spec_json="{}", deployed_at=today, status=STATUS_STOPPED))
        session.commit()

    assert engagement_metrics.get_paper_deployment_metrics() == {"active": 2, "stopped": 1}


# ── Repeat-generation proxy ─────────────────────────────────────────────


def test_repeat_generation_metrics_counts_distinct_days_per_owner(tmp_db):
    now = _now()
    with get_session() as session:
        session.add(_make_user("repeat-user", created_at=now - timedelta(days=10)))
        session.add(_make_user("single-day-user", created_at=now - timedelta(days=10)))
        session.commit()
        # repeat-user: two strategies on two DIFFERENT days -> repeat.
        session.add(_make_strategy("r-1", owner_user_id="repeat-user", created_at=now - timedelta(days=3)))
        session.add(_make_strategy("r-2", owner_user_id="repeat-user", created_at=now - timedelta(days=1)))
        # single-day-user: two strategies on the SAME day -> not a repeat.
        session.add(_make_strategy("sd-1", owner_user_id="single-day-user", created_at=now.replace(hour=2)))
        session.add(_make_strategy("sd-2", owner_user_id="single-day-user", created_at=now.replace(hour=14)))
        # Pre-account (wallet-only) generation: no owner_user_id -> excluded entirely.
        session.add(_make_strategy("anon-1", owner_user_id=None, created_at=now))
        session.commit()

    result = engagement_metrics.get_repeat_generation_metrics()
    assert result["generating_users"] == 2
    assert result["repeat_users"] == 1


def test_repeat_generation_metrics_empty_db_is_honest_zero(tmp_db):
    result = engagement_metrics.get_repeat_generation_metrics()
    assert result["generating_users"] == 0
    assert result["repeat_users"] == 0


# ── Payments (dry-run marker, no query) ─────────────────────────────────


def test_payments_snapshot_is_dry_run_with_no_settled_volume(monkeypatch):
    monkeypatch.setenv("PAYMENTS_DRY_RUN", "true")
    result = engagement_metrics.get_payments_snapshot()
    assert result["dry_run"] is True
    assert result["settled_volume_usd"] is None
    assert "dry-run" in result["note"].lower() or "dry_run" in result["note"].lower()


def test_payments_snapshot_never_claims_a_settled_number_when_dry_run_flips_off(monkeypatch):
    """Negative control: even with PAYMENTS_DRY_RUN=false, there is still no
    durable settlement query wired — settled_volume_usd must NOT silently
    become 0 (a measured zero) just because the flag flipped. It stays None."""
    monkeypatch.setenv("PAYMENTS_DRY_RUN", "false")
    result = engagement_metrics.get_payments_snapshot()
    assert result["dry_run"] is False
    assert result["settled_volume_usd"] is None


# ── Composed snapshot ────────────────────────────────────────────────────


def test_engagement_snapshot_composes_every_tile(tmp_db):
    snapshot = engagement_metrics.get_engagement_snapshot()
    for key in (
        "accounts",
        "linked_wallets",
        "strategies",
        "generation_costs",
        "paper_deployments",
        "repeat_generation_users",
        "payments",
        "timestamp",
    ):
        assert key in snapshot, f"missing {key} in {list(snapshot.keys())}"
