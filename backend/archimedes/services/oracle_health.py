"""On-chain oracle-freshness probe (#1371) — the first backend caller of
``PriceOracle.isFresh()`` / ``lastUpdated()``.

Every deployed ``PriceOracle`` on Arc testnet has been stale since the T3.2
redeploy (2026-07-09) — the on-chain updater address has nonce 0, no push has
ever landed (#1341 owns the cause). Nothing reported this: ``isFresh()`` is a
view function that exists precisely to answer "is this oracle current?" and
had zero callers anywhere in ``backend/`` or ``ui/src/``. This module is the
loud, visible-absence probe CLAUDE.md's fail-soft principle requires for
anything a claim depends on (docs/architectural-principles.md § fail-soft):
a chain-read failure must never be presented as "assume fresh".

**Design constraint — the trap this probe exists to avoid.** The signal MUST
come from an on-chain ``lastUpdated()`` / ``isFresh()`` read, never from
oracle-runner process liveness, a systemd unit, or a Redis "last fetch"
heartbeat. ``oracle_updater.push_prices_on_chain()`` can loop happily every
60s and log "Pushed sSPY" forever while the push itself reverts on-chain —
that is exactly #1341's failure mode, and a liveness-based probe would have
read green for all 42+ days of it. Only a direct chain read can catch it.

**The probed set is derived, never hard-coded.** ``_push_set_symbols()``
reads ``oracle_updater.YFINANCE_MAP`` / ``CRYPTO_MAP`` (read-only — this
module never imports anything that would let it touch those maps; owning them
is #1341's lane) and keeps the entries that are actually on-chain synths,
mirroring the same leading-``"s"`` filter ``oracle_updater.fetch_prices()``
already applies to ``YFINANCE_MAP`` at :184 (excluding the ``^GSPC``/``^VIX``
regime-signal index tickers, which are never pushed on-chain). Today this
resolves to ``{"sSPY", "sBTC"}`` — 2 of the 281 deployed oracles
(``universe.ON_CHAIN_SYNTHS``) — but the probe re-derives it on every call, so
a push-map change lands here automatically with no edit required.

**2-of-281-fresh must never read as globally healthy.** ``oracle_probed_count``
and ``oracle_universe_count`` are both always present on the returned
``OracleHealth`` so a caller can never collapse "the push set is 100% fresh"
into "the oracle subsystem is healthy" — the gap between the two counts is
the whole point of carrying both.

Hermetic test entry point: mock ``archimedes.chain.contracts.get_contract_loader``
(see ``backend/tests/chain/test_oracle_updater.py`` for the exact idiom this
module's tests mirror) — never patch internals.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# /health's total budget is hard-enforced twice at 5s — infra/alb.tf's
# target-group check and infra/ecs.tf's container HEALTHCHECK (which gates
# nginx's dependsOn HEALTHY condition) — and the handler already spends up to
# chain/client.py's rpc_timeout_seconds (3.0s) on is_connected() before this
# probe runs. The probe therefore gets a strict aggregate deadline of its own:
# per-symbol reads run concurrently, and a deadline overrun is a LOUD miss
# (oracle_fresh=False with a probe_timeout reason), never a slow success that
# lets ALB/ECS kill a healthy task for an RPC-layer problem. Same risk class
# asset_market_service._ORACLE_TOTAL_BUDGET_SECONDS already guards, tightened
# for the health path's smaller budget.
_PROBE_BUDGET_SECONDS = 1.5

# ── Weekend-blind freshness fix (#1525) ──────────────────────────────────
#
# ``PriceOracle.isFresh()`` enforces one flat ``MAX_STALENESS`` (24h) window
# on-chain, identically for every symbol — it has no notion of a trading
# calendar. That is correct for a 24/7 market (crypto) and wrong for an
# equity synth: the US equity market is closed ~64/168 hours a week (more
# with holidays), so an equity synth's on-chain price cannot update while
# closed, and the flat threshold flips `isFresh()` false every single
# weekend "by design" (the symptom this issue reports). A signal that fires
# every weekend trains operators to ignore it, which is exactly how a real
# weekday staleness incident goes unnoticed.
#
# Fix: for equity synths only, when the on-chain `isFresh()` says NOT fresh,
# apply a dep-free trading-calendar override — `zoneinfo` is stdlib, no new
# dependency — using the SAME `lastUpdated()` timestamp already read from
# chain (never a liveness/heartbeat signal; see the module docstring's trap
# note). A last update at/after the most recently completed US market
# session is still that session's valid closing price until the market
# reopens; only once the market has reopened without a fresh push does this
# fall through to the chain's own (already-computed) not-fresh verdict.
# Crypto synths are never touched by this override — they keep exactly the
# existing flat-threshold behavior via the raw on-chain `isFresh()` read.
_NY_TZ = ZoneInfo("America/New_York")
_MARKET_OPEN = dtime(9, 30)
_MARKET_CLOSE = dtime(16, 0)

# Static 2026 NYSE full-closure holiday list — dep-free approximation (no
# holidays/pandas_market_calendars dependency). Source: NYSE's published 2026
# holiday calendar. Only full-market closures are listed; NYSE has no
# early-close observance that matters here (this override only cares about
# whether the market is open AT ALL, not intraday half-day hours).
_NYSE_HOLIDAYS_2026: frozenset[date] = frozenset(
    {
        date(2026, 1, 1),  # New Year's Day
        date(2026, 1, 19),  # Martin Luther King, Jr. Day
        date(2026, 2, 16),  # Washington's Birthday
        date(2026, 4, 3),  # Good Friday
        date(2026, 5, 25),  # Memorial Day
        date(2026, 6, 19),  # Juneteenth National Independence Day
        date(2026, 7, 3),  # Independence Day (observed — July 4 falls on a Saturday)
        date(2026, 9, 7),  # Labor Day
        date(2026, 11, 26),  # Thanksgiving Day
        date(2026, 12, 25),  # Christmas Day
    }
)


def _is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in _NYSE_HOLIDAYS_2026


def _previous_trading_day(d: date) -> date:
    d -= timedelta(days=1)
    while not _is_trading_day(d):
        d -= timedelta(days=1)
    return d


def _next_trading_day(d: date) -> date:
    d += timedelta(days=1)
    while not _is_trading_day(d):
        d += timedelta(days=1)
    return d


def _most_recent_close_before(now_ny: datetime) -> datetime:
    """The most recent scheduled 16:00 ET close at/before ``now_ny``.

    On a trading day after close, that is today's close. On a trading day
    before close (market open or pre-open), or on a weekend/holiday, that is
    the previous trading day's close.
    """
    d = now_ny.date()
    if _is_trading_day(d) and now_ny.time() >= _MARKET_CLOSE:
        close_day = d
    else:
        close_day = _previous_trading_day(d)
    return datetime.combine(close_day, _MARKET_CLOSE, tzinfo=_NY_TZ)


def _next_open_after(close_dt: datetime) -> datetime:
    """The next scheduled 09:30 ET open strictly after ``close_dt``'s day."""
    open_day = _next_trading_day(close_dt.date())
    return datetime.combine(open_day, _MARKET_OPEN, tzinfo=_NY_TZ)


def _equity_weekend_override_fresh(last_updated_ts: int, now_ts: float) -> bool:
    """True iff an equity synth's on-chain-stale verdict should be overridden.

    Only ever called to relax an on-chain ``isFresh() == False`` read for an
    equity synth — never to make a genuinely-stale weekday miss look fresh.
    Returns ``False`` (no override; defer to the chain's own verdict) unless
    ``now`` is currently within a closed-market gap (weekend, holiday, or
    overnight) that opened at/after ``last_updated``'s trading session — i.e.
    the market has not reopened since the last known price was pushed, so
    that price is still the operative truth.

    Compares by NY-local calendar DATE, not exact clock time, against the
    most recently completed session: a push landing at 15:59 ET (one push
    cycle before the nominal 16:00 close, or any other time that trading
    day — including the market-open push) is still that session's price, not
    a stale earlier one.
    """
    now_ny = datetime.fromtimestamp(now_ts, tz=_NY_TZ)
    last_ny = datetime.fromtimestamp(last_updated_ts, tz=_NY_TZ)
    close_before_now = _most_recent_close_before(now_ny)
    next_open = _next_open_after(close_before_now)
    if now_ny >= next_open:
        # The market has reopened at least once since that close — a fresh
        # push should have landed by now; a still-stale on-chain read is a
        # genuine miss, not a weekend/holiday artifact.
        return False
    return last_ny.date() >= close_before_now.date()


@dataclass(frozen=True)
class OracleHealth:
    """Health diagnostic for the on-chain price-oracle push set (#1371).

    - ``status``: ``"fresh"`` (every probed oracle read succeeded and reported
      ``isFresh() == True``), ``"stale"`` (at least one probed oracle is not
      fresh, but at least one chain read succeeded), or ``"unknown"`` (every
      chain read failed — the state is genuinely unobtainable, not
      confirmed-stale; mirrors the confirmed-absent vs. unobtainable
      distinction in ``oracle_updater._get_reference_price_int``, #587 part 2).
    - ``oracle_fresh``: ``True`` iff EVERY probed oracle read succeeded AND
      reported fresh. Never ``True`` on a partial or total read failure —
      fail-soft ("assume fresh when we can't tell") is wrong here.
    - ``oracle_oldest_age_s``: the oldest ``now - lastUpdated()`` in seconds
      across the oracles that were successfully read; ``None`` only when zero
      reads succeeded (there is no age to report).
    - ``oracle_probed_count``: size of the derived push set (today 2 —
      sSPY + sBTC), independent of how many reads actually succeeded.
    - ``oracle_universe_count``: the full on-chain-deploy-eligible universe
      size (281-class), derived from ``universe.ON_CHAIN_SYNTHS`` — never a
      literal, so a universe change is picked up automatically.
    - ``reason``: human-readable detail, always carrying an explicit
      ``chain_read_failed`` / ``probe_error`` marker on failure so a probe
      outage can never be mistaken for "all oracles fresh" downstream.
    """

    status: str  # "fresh" | "stale" | "unknown"
    oracle_fresh: bool
    oracle_oldest_age_s: int | None
    oracle_probed_count: int
    oracle_universe_count: int
    reason: str = ""


def _push_set_symbols() -> list[str]:
    """Derive the currently-pushed oracle symbols from oracle_updater's maps.

    Read-only: imports ``YFINANCE_MAP`` / ``CRYPTO_MAP`` but never mutates or
    re-exports them. Filters to real on-chain synths the same way
    ``oracle_updater.fetch_prices()`` does at :184 (``k.startswith("s")``),
    which excludes the ``^GSPC`` / ``^VIX`` regime-signal index tickers that
    also live in ``YFINANCE_MAP`` but are not synths and are never pushed.
    """
    from archimedes.chain.oracle_updater import CRYPTO_MAP, YFINANCE_MAP

    equity_synths = {s for s in YFINANCE_MAP if s.startswith("s")}
    crypto_synths = set(CRYPTO_MAP)
    return sorted(equity_synths | crypto_synths)


def _is_equity_symbol(symbol: str) -> bool:
    """True iff ``symbol`` is one of the equity synths in the push set (#1525).

    Same source of truth and same filter as ``_push_set_symbols()`` —
    ``YFINANCE_MAP`` keys with the leading-``"s"`` synth filter (excluding the
    ``^GSPC``/``^VIX`` regime-signal tickers). Crypto symbols (``CRYPTO_MAP``)
    are never equities and get no calendar override.
    """
    from archimedes.chain.oracle_updater import YFINANCE_MAP

    return symbol in YFINANCE_MAP and symbol.startswith("s")


def _universe_count() -> int:
    """The full 281-class on-chain-deploy-eligible universe size, never a literal."""
    from archimedes.universe import ON_CHAIN_SYNTHS

    return len(ON_CHAIN_SYNTHS)


async def oracle_health(budget_seconds: float | None = None) -> OracleHealth:
    """Probe on-chain freshness for the currently-pushed oracle symbols.

    Reads each probed oracle's ``isFresh()`` and ``lastUpdated()`` through the
    existing chain client / contract loader
    (``chain.contracts.get_contract_loader().oracle_for(symbol)``) — the same
    call shape ``oracle_updater._get_reference_price_int`` uses for ``price()``.
    All per-symbol reads run concurrently under one aggregate deadline
    (``budget_seconds``, default ``_PROBE_BUDGET_SECONDS``) so the probe's
    worst case is one bounded round-trip window, not reads × rpc_timeout —
    see the constant's comment for the /health 5s budget arithmetic.

    Never raises. Every failure path — including a deadline overrun — returns
    an ``OracleHealth`` with ``oracle_fresh=False`` and an explicit marker in
    ``reason`` — see the module docstring's fail-soft note. A read failure is
    NEVER silently absent and NEVER reported as fresh.
    """
    universe_count = _universe_count()

    try:
        symbols = _push_set_symbols()
    except Exception as exc:
        logger.warning("oracle_health: could not derive push set: %s", exc)
        return OracleHealth(
            status="unknown",
            oracle_fresh=False,
            oracle_oldest_age_s=None,
            oracle_probed_count=0,
            oracle_universe_count=universe_count,
            reason=f"oracle_health probe_error: could not derive push set ({exc})",
        )

    probed_count = len(symbols)
    if probed_count == 0:
        logger.warning("oracle_health: push set is empty — nothing to probe")
        return OracleHealth(
            status="unknown",
            oracle_fresh=False,
            oracle_oldest_age_s=None,
            oracle_probed_count=0,
            oracle_universe_count=universe_count,
            reason="oracle_health probe_error: no push-set symbols configured",
        )

    from archimedes.chain.contracts import get_contract_loader

    now = time.time()
    ages: list[int] = []
    all_fresh_flags: list[bool] = []
    errors: list[str] = []

    try:
        loader = get_contract_loader()
    except Exception as exc:
        logger.warning("oracle_health: could not obtain contract loader: %s", exc)
        return OracleHealth(
            status="unknown",
            oracle_fresh=False,
            oracle_oldest_age_s=None,
            oracle_probed_count=probed_count,
            oracle_universe_count=universe_count,
            reason=f"oracle_health chain_read_failed: contract loader unavailable ({exc})",
        )

    async def _read_one(symbol: str) -> tuple[bool, int]:
        oracle = loader.oracle_for(symbol)
        # The two view reads for one symbol are independent — run them
        # concurrently too, so a symbol costs one round-trip window, not two.
        is_fresh, last_updated = await asyncio.gather(
            oracle.functions.isFresh().call(),
            oracle.functions.lastUpdated().call(),
        )
        is_fresh = bool(is_fresh)
        last_updated = int(last_updated)
        if not is_fresh and _is_equity_symbol(symbol):
            # Weekend-blind freshness (#1525): the on-chain isFresh() enforces
            # one flat MAX_STALENESS window with no trading-calendar
            # awareness, so an equity synth reads stale every weekend by
            # design. Override with the dep-free calendar check below, using
            # the SAME lastUpdated() timestamp already read from chain — this
            # can only turn a chain-verdict False into True (never the
            # reverse), and only during a genuine closed-market gap; a real
            # weekday miss still reports not-fresh. Crypto symbols never
            # reach this branch — they keep exactly the existing flat
            # threshold via the raw on-chain isFresh() read above.
            is_fresh = _equity_weekend_override_fresh(last_updated, now)
            if is_fresh:
                # Surface the override instead of silently massaging the
                # verdict — the reason string names calendar-fresh symbols so
                # an operator can always tell chain-fresh from calendar-fresh
                # (fail-soft principle: transformations stay visible).
                return is_fresh, last_updated, True
        return is_fresh, last_updated, False

    budget = _PROBE_BUDGET_SECONDS if budget_seconds is None else budget_seconds
    try:
        outcomes = await asyncio.wait_for(
            asyncio.gather(*(_read_one(s) for s in symbols), return_exceptions=True),
            timeout=budget,
        )
    except TimeoutError:
        # Deadline overrun is a LOUD miss, never a slow success: a degraded
        # (not down) RPC must not push /health past the 5s ALB/ECS budget and
        # get a healthy task killed — the exact failure mode
        # chain/client.py's rpc_timeout_seconds comment guards against.
        logger.warning(
            "oracle_health: probe exceeded %.1fs budget for %d oracle(s)",
            budget,
            probed_count,
        )
        return OracleHealth(
            status="unknown",
            oracle_fresh=False,
            oracle_oldest_age_s=None,
            oracle_probed_count=probed_count,
            oracle_universe_count=universe_count,
            reason=f"oracle_health probe_timeout: exceeded {budget:.1f}s budget",
        )

    calendar_fresh_symbols: list[str] = []
    for symbol, outcome in zip(symbols, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            errors.append(f"{symbol}: {outcome}")
            continue
        is_fresh, last_updated, calendar_override = outcome
        ages.append(max(0, int(now - last_updated)))
        all_fresh_flags.append(is_fresh)
        if calendar_override:
            calendar_fresh_symbols.append(symbol)

    if not ages:
        # Every probed read failed — unobtainable, not confirmed-stale (mirrors
        # oracle_updater._get_reference_price_int's confirmed-absent vs.
        # unobtainable distinction). Fail loud: oracle_fresh stays False.
        # Note: the loud, greppable HEALTH_ORACLE_STALE marker (metric-filtered
        # in infra/cloudwatch.tf) is emitted once by the /health handler in
        # main.py, not here — this WARNing is the probe-level detail log.
        logger.warning(
            "oracle_health: chain read failed for all %d probed oracle(s): %s",
            probed_count,
            "; ".join(errors),
        )
        return OracleHealth(
            status="unknown",
            oracle_fresh=False,
            oracle_oldest_age_s=None,
            oracle_probed_count=probed_count,
            oracle_universe_count=universe_count,
            reason=f"oracle_health chain_read_failed: {'; '.join(errors)}",
        )

    oldest_age = max(ages)
    fresh_count = sum(1 for f in all_fresh_flags if f)
    all_fresh = fresh_count == probed_count and not errors

    if all_fresh:
        status = "fresh"
        reason = f"{fresh_count}/{probed_count} probed oracle(s) fresh (of {universe_count} in the universe)"
        if calendar_fresh_symbols:
            reason += (
                f"; {', '.join(sorted(calendar_fresh_symbols))} calendar-fresh (market closed since last update, #1525)"
            )
    else:
        status = "stale"
        reason = (
            f"{fresh_count}/{probed_count} probed oracle(s) fresh, oldest age {oldest_age}s "
            f"(of {universe_count} in the universe)"
        )
        if errors:
            reason += f"; {len(errors)} read error(s): {'; '.join(errors)}"

    return OracleHealth(
        status=status,
        oracle_fresh=all_fresh,
        oracle_oldest_age_s=oldest_age,
        oracle_probed_count=probed_count,
        oracle_universe_count=universe_count,
        reason=reason,
    )
