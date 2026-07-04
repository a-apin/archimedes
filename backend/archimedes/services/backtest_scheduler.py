"""In-app automated backtest refresh — no more operator-invoked backtests.

``archimedes.scripts.run_backtests`` existed only as a manual CLI (invoked by
hand over SSM), so the curated library's real backtests refreshed exactly as
often as a human remembered to run them — which is how prod sat at "pending"
for weeks. A product whose wedge is *autonomous* rigor cannot depend on an
operator for its evidence. This scheduler makes the backend keep its own
backtests fresh:

- On startup (after a settle delay) and then on a fixed cadence, it checks
  staleness: any curated strategy with NO persisted backtest, or whose latest
  run is older than ``BACKTEST_MAX_AGE_HOURS``, triggers a refresh.
- The refresh IS ``run_backtests()`` — the exact same code path as the CLI
  (content-hash dedup included), run on a worker thread so the event loop
  stays responsive. One implementation, two entry points.
- Fail-soft everywhere: a refresh failure logs loudly and the loop continues;
  the scheduler can never take the app down. ``TESTING`` disables it outright
  (hermetic tests must never hit yfinance).

Env knobs:
  BACKTEST_REFRESH_ENABLED          default on   ("0"/"false" disables)
  BACKTEST_REFRESH_INTERVAL_HOURS   default 24   (cadence of staleness checks)
  BACKTEST_MAX_AGE_HOURS            default 168  (7 days — refresh when older)
  BACKTEST_REFRESH_STARTUP_DELAY_S  default 180  (let the app settle first)

Single-writer note: prod runs one backend replica; if that ever changes, the
content-hash dedup in ``insert_backtest_if_missing`` keeps concurrent runs
idempotent (wasted compute, never corrupt data).
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

_FALSY = {"0", "false", "no", "off"}

# Backoff memory: if a refresh ran and the SAME strategies are still missing
# afterwards, they are permanently failing (e.g. the pairs family needs code
# fixes, not re-runs) — re-triggering on every startup/tick just burns the
# box's 2 vCPUs for ~15 min right when users are generating. Missing-driven
# refreshes only fire again once the missing SET changes; age-driven
# refreshes keep their normal cadence.
_last_unresolved_missing: set[str] = set()


def refresh_enabled() -> bool:
    """On by default; off under TESTING (hermetic) or via the env kill switch."""
    if os.getenv("TESTING"):
        return False
    return os.getenv("BACKTEST_REFRESH_ENABLED", "1").strip().lower() not in _FALSY


def _hours(name: str, default: float, lo: float, hi: float) -> float:
    try:
        return max(lo, min(hi, float(os.getenv(name, str(default)))))
    except ValueError:
        return default


def needs_refresh() -> tuple[bool, str]:
    """(should_refresh, reason) from the curated library's persisted backtests.

    Missing rows or a latest run older than ``BACKTEST_MAX_AGE_HOURS`` → stale.
    Fail-soft: any error reports (False, reason) — the next tick retries; a
    broken staleness check must not stampede refreshes.
    """
    max_age = timedelta(hours=_hours("BACKTEST_MAX_AGE_HOURS", 168.0, 1.0, 24 * 90.0))
    try:
        from archimedes.db import get_session
        from archimedes.services.backtest_repository import latest_backtests_by_strategy
        from archimedes.services.strategy_provider import default_provider

        strategies = default_provider().list_strategies()
        ids = [s.id for s in strategies]
        if not ids:
            return False, "no curated strategies"
        with get_session() as session:
            latest = latest_backtests_by_strategy(session, ids)
        missing = [sid for sid in ids if sid not in latest]
        if missing:
            if set(missing) == _last_unresolved_missing:
                # A previous refresh already failed to produce rows for exactly
                # this set — they need code fixes, not compute. Fall through to
                # the age check instead of thrashing.
                pass
            else:
                return True, f"{len(missing)}/{len(ids)} strategies have no persisted backtest"
        now = datetime.now(UTC)
        oldest = None
        for row in latest.values():
            created = getattr(row, "created_at", None)
            if created is None:
                return True, "a backtest row lacks created_at"
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            oldest = created if oldest is None or created < oldest else oldest
        if oldest is not None and (now - oldest) > max_age:
            return True, f"oldest backtest is {(now - oldest).days}d old (max {max_age.days}d)"
        return False, "all curated backtests fresh"
    except Exception as exc:
        return False, f"staleness check failed ({type(exc).__name__}: {exc})"


def _run_refresh() -> dict:
    """One refresh — the SAME implementation as the manual CLI."""
    from archimedes.scripts.run_backtests import run_backtests

    return run_backtests()


def _remember_unresolved_missing() -> None:
    """After a refresh, record which strategies STILL have no rows (backoff set)."""
    global _last_unresolved_missing
    try:
        from archimedes.db import get_session
        from archimedes.services.backtest_repository import latest_backtests_by_strategy
        from archimedes.services.strategy_provider import default_provider

        ids = [st.id for st in default_provider().list_strategies()]
        with get_session() as session:
            latest = latest_backtests_by_strategy(session, ids)
        _last_unresolved_missing = {sid for sid in ids if sid not in latest}
        if _last_unresolved_missing:
            logger.warning(
                "backtest refresh: %d strategies still have no rows after a refresh "
                "(likely need code fixes, e.g. the pairs family) — backing off to the age cadence",
                len(_last_unresolved_missing),
            )
    except Exception:
        _last_unresolved_missing = set()


async def backtest_refresh_loop() -> None:
    """Long-lived task: settle → check staleness → refresh when needed → sleep."""
    delay = _hours("BACKTEST_REFRESH_STARTUP_DELAY_S", 180.0, 0.0, 3600.0)
    interval_s = _hours("BACKTEST_REFRESH_INTERVAL_HOURS", 24.0, 1.0, 168.0) * 3600.0
    await asyncio.sleep(delay)
    while True:
        try:
            should, reason = needs_refresh()
            if should:
                logger.info("backtest refresh: starting (%s)", reason)
                summary = await asyncio.to_thread(_run_refresh)
                logger.info("backtest refresh: done — %s", summary)
                _remember_unresolved_missing()
            else:
                logger.info("backtest refresh: skipped (%s)", reason)
        except Exception as exc:
            # Fail-soft by contract: the scheduler must never take the app down.
            logger.warning("backtest refresh: cycle failed (%s: %s) — will retry next tick", type(exc).__name__, exc)
        await asyncio.sleep(interval_s)
