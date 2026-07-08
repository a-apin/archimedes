"""In-app automated backtest refresh (services/backtest_scheduler.py).

The refresh must be a product behavior, not an operator ritual: stale or
missing curated backtests trigger the SAME run_backtests implementation the
CLI uses, from a background loop the app arms at startup. Hermetic: tmp-sqlite
DB, run_backtests monkeypatched (never yfinance), TESTING guard respected.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta

import archimedes.db as _db
import archimedes.services.backtest_scheduler as sched
import pytest


@pytest.fixture(autouse=True)
def _use_tmp_db(tmp_path, monkeypatch):
    import archimedes.db as db
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    url = f"sqlite:///{tmp_path / 'sched.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    eng = create_engine(url, connect_args={"check_same_thread": False})
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=eng, autocommit=False, autoflush=False))
    db.init_db()
    yield


@pytest.fixture(autouse=True)
def _reset_backoff_state():
    """Reset module-level backoff state before each test to prevent order-dependence."""
    sched._last_unresolved_missing = set()
    yield
    sched._last_unresolved_missing = set()


class _Strat:
    def __init__(self, sid):
        self.id = sid


def _patch_provider(monkeypatch, ids):
    provider = type("P", (), {"list_strategies": lambda self: [_Strat(i) for i in ids]})()
    monkeypatch.setattr("archimedes.services.strategy_provider.default_provider", lambda: provider)


def _insert_backtest(sid: str, created_at: datetime):
    import hashlib

    from archimedes.models.backtest_store import BacktestResultRecord

    content_hash = hashlib.sha256(sid.encode()).hexdigest()
    with _db.get_session() as session:
        session.add(
            BacktestResultRecord(
                strategy_id=sid,
                content_hash=content_hash,
                artifact_json="{}",
                created_at=created_at,
            )
        )
        session.commit()


# ── enable/disable semantics ──────────────────────────────────────────


def test_disabled_under_testing(monkeypatch):
    monkeypatch.setenv("TESTING", "1")
    assert sched.refresh_enabled() is False  # hermetic suites never hit yfinance


def test_enabled_by_default_outside_testing(monkeypatch):
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.delenv("BACKTEST_REFRESH_ENABLED", raising=False)
    assert sched.refresh_enabled() is True
    monkeypatch.setenv("BACKTEST_REFRESH_ENABLED", "0")
    assert sched.refresh_enabled() is False


# ── staleness detection ───────────────────────────────────────────────


def test_missing_rows_trigger_refresh(monkeypatch):
    _patch_provider(monkeypatch, ["s1", "s2"])
    _insert_backtest("s1", datetime.now(UTC))
    should, reason = sched.needs_refresh()
    assert should is True
    assert "no persisted backtest" in reason


def test_fresh_rows_do_not_trigger(monkeypatch):
    _patch_provider(monkeypatch, ["s1"])
    _insert_backtest("s1", datetime.now(UTC))
    should, reason = sched.needs_refresh()
    assert should is False
    assert "fresh" in reason


def test_stale_rows_trigger(monkeypatch):
    _patch_provider(monkeypatch, ["s1"])
    _insert_backtest("s1", datetime.now(UTC) - timedelta(days=30))
    monkeypatch.setenv("BACKTEST_MAX_AGE_HOURS", "168")
    should, reason = sched.needs_refresh()
    assert should is True
    assert "old" in reason


def test_staleness_check_fails_soft(monkeypatch):
    def _boom():
        raise RuntimeError("provider down")

    monkeypatch.setattr("archimedes.services.strategy_provider.default_provider", _boom)
    should, reason = sched.needs_refresh()
    assert should is False  # a broken check must not stampede refreshes
    assert "failed" in reason


# ── the loop actually refreshes via the shared implementation ─────────


async def test_loop_runs_refresh_when_stale(monkeypatch):
    monkeypatch.setenv("BACKTEST_REFRESH_STARTUP_DELAY_S", "0")
    monkeypatch.setenv("BACKTEST_REFRESH_INTERVAL_HOURS", "1")
    calls = []
    monkeypatch.setattr(sched, "needs_refresh", lambda: (True, "test"))
    monkeypatch.setattr(sched, "_run_refresh", lambda: calls.append(1) or {"inserted": 1})

    task = asyncio.create_task(sched.backtest_refresh_loop())
    for _ in range(200):
        if calls:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert calls, "the loop must invoke the shared run_backtests implementation when stale"


async def test_loop_survives_refresh_failure(monkeypatch):
    monkeypatch.setenv("BACKTEST_REFRESH_STARTUP_DELAY_S", "0")
    monkeypatch.setenv("BACKTEST_REFRESH_INTERVAL_HOURS", "1")
    checks = []

    def _needs():
        checks.append(1)
        return (True, "test")

    def _explode():
        raise RuntimeError("yfinance down")

    monkeypatch.setattr(sched, "needs_refresh", _needs)
    monkeypatch.setattr(sched, "_run_refresh", _explode)
    task = asyncio.create_task(sched.backtest_refresh_loop())
    for _ in range(200):
        if checks:
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)  # give the exception path time to run
    assert not task.done(), "a failed refresh must never kill the loop"
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def test_unresolved_missing_backs_off(monkeypatch):
    """Strategies that STILL have no rows after a refresh (permanently failing,
    e.g. the pairs family) must not re-trigger a missing-driven refresh every
    startup — that burned ~15 min of a 2-vCPU box per deploy, starving live
    generations. Once remembered, only the age cadence (or a CHANGED missing
    set) refreshes again."""
    _patch_provider(monkeypatch, ["ok1", "dead1"])
    _insert_backtest("ok1", datetime.now(UTC))

    should, reason = sched.needs_refresh()
    assert should is True  # first sighting → refresh attempt is correct

    sched._remember_unresolved_missing()  # refresh "ran"; dead1 still rowless
    should, reason = sched.needs_refresh()
    assert should is False, "identical unresolved-missing set must back off"
    assert "backing off" in reason, f"expected explicit backoff reason, got: {reason!r}"

    # A NEW missing strategy (set changed) re-arms the missing trigger.
    _patch_provider(monkeypatch, ["ok1", "dead1", "new1"])
    should, _ = sched.needs_refresh()
    assert should is True
