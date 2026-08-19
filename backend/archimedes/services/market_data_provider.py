"""Market-data vendor seam (#775 / #1218).

#1218 priced yfinance as an unlicensed commercial dependency that scales with
strategies x symbols x re-run cadence, and named the seam it should be
substituted at. This module IS that seam for backend's live/request-path call
sites (analytics-engine's own choke point — ``fetch_ohlcv`` — gets the
equivalent treatment in ``archimedes_analytics_engine.market_data``; the two
are separate modules because analytics-engine is a standalone, DB-less
package, but both read the SAME ``MARKET_DATA_PROVIDER`` env var and default
to ``"yfinance"``, so a deploy of this change is a no-op until the flag
flips).

Three call sites route through here (grep-verified):
  - ``strategy_signal_evaluator._fetch_price_history(ies)`` — daily close
    series for backtests/Explore's universe sweep (the #1218 volume driver).
  - ``chain.oracle_updater`` — the live oracle-push equity fetch, the VIX /
    S&P-MA regime reads, and the #775 secondary-source cross-check's
    independent reading.
  - ``services.asset_market_service._fetch_yfinance_series`` — the Explore
    per-asset history-modal endpoint.

**#775 resolution, in one line:** the cross-check
(``oracle_updater._cross_check_secondary``) reads its independent secondary
through ``get_provider()`` and treats ``provider_name()`` (not a hardcoded
``"yfinance"``) as "same source, skip". Swap ``MARKET_DATA_PROVIDER`` to a
new vendor and the cross-check's secondary source swaps with it automatically
— no separate change needed at the guardrail.

**Caching, scoped intentionally.** Only ``get_daily_close_batch`` — the
DAILY-bar shape that matches ``asset_daily_bars`` — is cache-backed
(``CachingMarketDataProvider``). ``get_intraday_quote(s)`` (live oracle
pushes, VIX, the cross-check's secondary) and ``get_series`` (the Explore
history modal, any interval) pass straight through, uncached, on purpose: a
stale daily close must never masquerade as a live push/guardrail reading, and
a single-symbol drill-down isn't the volume driver #1218 identified — the
280-symbol universe sweep behind ``get_daily_close_batch`` is. Background
refresh is deliberately NOT built here (#1218 anti-goal: seam, not
migration) — the cache primes itself on the first miss.
"""

from __future__ import annotations

import logging
import math
import os
from abc import ABC, abstractmethod
from datetime import UTC, date, datetime, timedelta

import pandas as pd

logger = logging.getLogger(__name__)

# yfinance period vocabulary -> approximate calendar days, used to derive a
# cache coverage window. Approximate on purpose (trading days < calendar
# days); the +5-day tolerance in _read_cached_series absorbs the slack.
_PERIOD_DAYS: dict[str, int] = {
    "1d": 1,
    "5d": 5,
    "1mo": 31,
    "3mo": 93,
    "6mo": 186,
    "1y": 366,
    "2y": 731,
    "5y": 1827,
    "10y": 3653,
    "ytd": 366,
    "max": 3653 * 4,
}
_DEFAULT_PERIOD_DAYS = 731  # ~2y — matches the evaluator's own default period

# How much back-history the cache must already hold, relative to the
# requested window's start, to count as "covers this request". Trading-day
# gaps (weekends/holidays) plus vendor listing-date differences mean an exact
# match is too strict.
_COVERAGE_TOLERANCE_DAYS = 5

# How long a cached row is trusted before a request re-fetches it live.
# Configurable because "how often is it OK to be a day stale" is a product
# call, not a code constant — default errs toward freshness (daily bars
# genuinely change once a day; the point of the cache is to not re-fetch on
# every request within that window, not to go stale for a week).
DEFAULT_CACHE_TTL_HOURS = 12


def _int_env(name: str, default: int) -> int:
    """Parse an integer env var, failing SAFE to ``default`` (mirrors
    ``chain.oracle_updater._int_env`` — same fail-safe convention)."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError:
        logger.warning("invalid %s=%r (not an integer) — falling back to %d", name, raw, default)
        return default


# ─── Provider interface ─────────────────────────────────────────────────


class MarketDataProvider(ABC):
    """Vendor abstraction for market data. Default implementation (below) is
    yfinance, unchanged in behavior from what each call site did before this
    seam existed. A new vendor implements this interface and registers in
    ``_VENDOR_PROVIDERS``; ``MARKET_DATA_PROVIDER`` selects it."""

    @abstractmethod
    def get_daily_close_batch(self, tickers: dict[str, str], period: str) -> dict[str, pd.Series]:
        """Batch daily close-price series. ``tickers`` maps caller-chosen keys
        (our synth symbols) to vendor tickers; the result is keyed the same
        way. Missing/failed tickers are simply absent from the result."""

    @abstractmethod
    def get_intraday_quote(self, ticker: str) -> tuple[float, datetime] | None:
        """Latest intraday (price, bar_timestamp) for one vendor ticker, or
        None on any failure. Never cached — see module docstring."""

    @abstractmethod
    def get_intraday_quotes_batch(self, tickers: dict[str, str]) -> dict[str, float]:
        """Latest intraday price for many tickers in one vendor call. Keyed
        like ``get_daily_close_batch``. Never cached."""

    @abstractmethod
    def get_series(self, ticker: str, period: str, interval: str) -> pd.Series:
        """Generic close-price series at an arbitrary (period, interval) —
        the Explore history-modal shape. Never cached."""


class YFinanceProvider(MarketDataProvider):
    """Default provider — today's yfinance behavior, unchanged. Each method
    body is a straight move of the logic that used to live inline at its
    call site (see the docstring in each caller for the #1218 provenance)."""

    def get_daily_close_batch(self, tickers: dict[str, str], period: str) -> dict[str, pd.Series]:
        if not tickers:
            return {}
        try:
            import yfinance as yf
        except ImportError:
            logger.warning("yfinance not installed — returning empty histories")
            return {}

        yf_tickers = list(tickers.values())
        try:
            data = yf.download(
                tickers=" ".join(yf_tickers),
                period=period,
                interval="1d",
                progress=False,
                auto_adjust=True,
                group_by="ticker",
                threads=True,
            )
        except Exception as exc:
            logger.warning("Batched yfinance fetch failed: %s", exc)
            return {}

        if data is None or len(data) == 0:
            return {}

        result: dict[str, pd.Series] = {}

        if len(yf_tickers) == 1:
            sole_key = next(iter(tickers))
            try:
                close = data["Close"]
                if isinstance(close, pd.DataFrame):
                    close = close.iloc[:, 0]
                close = close.dropna()
                close.name = sole_key
                if not close.empty:
                    result[sole_key] = close
            except Exception as exc:
                logger.warning("Failed to extract Close for %s: %s", sole_key, exc)
            return result

        for key, yf_ticker in tickers.items():
            try:
                if isinstance(data.columns, pd.MultiIndex):
                    if yf_ticker not in data.columns.get_level_values(0):
                        continue
                    close = data[yf_ticker]["Close"]
                else:
                    close = data["Close"]
                if isinstance(close, pd.DataFrame):
                    close = close.iloc[:, 0]
                close = close.dropna()
                if close.empty:
                    continue
                close.name = key
                result[key] = close
            except Exception as exc:
                logger.warning("Failed to extract %s (%s): %s", key, yf_ticker, exc)
        return result

    def get_intraday_quote(self, ticker: str) -> tuple[float, datetime] | None:
        try:
            import yfinance as yf

            data = yf.download(ticker, period="1d", interval="1m", progress=False)
            if not data.empty:
                close = data["Close"]
                if hasattr(close, "columns"):
                    close = close.iloc[:, 0]
                bar_ts = data.index[-1]
                bar_ts = bar_ts.tz_convert("UTC") if bar_ts.tzinfo is not None else bar_ts.tz_localize("UTC")
                return float(close.iloc[-1]), bar_ts.to_pydatetime()
        except Exception as exc:
            logger.warning("Failed to fetch %s: %s", ticker, exc)
        return None

    def get_intraday_quotes_batch(self, tickers: dict[str, str]) -> dict[str, float]:
        try:
            import yfinance as yf
        except ImportError:
            logger.warning("yfinance not installed — returning empty prices")
            return {}

        tickers_str = " ".join(tickers.values())
        data = yf.download(tickers_str, period="1d", interval="1m", progress=False)

        results: dict[str, float] = {}
        for key, yf_ticker in tickers.items():
            try:
                if data.empty:
                    continue
                if len(tickers) == 1:
                    close = data["Close"]
                    if hasattr(close, "columns"):
                        close = close.iloc[:, 0]
                    price = float(close.iloc[-1])
                else:
                    close = data["Close"]
                    if yf_ticker in close.columns:
                        price = float(close[yf_ticker].dropna().iloc[-1])
                    else:
                        continue
                results[key] = price
            except Exception as exc:
                logger.warning("Failed to fetch %s: %s", key, exc)
        return results

    def get_series(self, ticker: str, period: str, interval: str) -> pd.Series:
        try:
            import yfinance as yf

            data = yf.download(
                ticker,
                period=period,
                interval=interval,
                progress=False,
                auto_adjust=True,
                threads=False,
            )
        except Exception as exc:
            logger.warning("Failed to fetch series for %s (%s/%s): %s", ticker, period, interval, exc)
            return pd.Series(dtype=float)

        if data is None or len(data) == 0:
            return pd.Series(dtype=float)
        try:
            close = data["Close"]
            if hasattr(close, "columns"):
                close = close.iloc[:, 0]
            return close.dropna()
        except Exception as exc:
            logger.warning("Failed to extract Close for %s: %s", ticker, exc)
            return pd.Series(dtype=float)


_VENDOR_PROVIDERS: dict[str, type[MarketDataProvider]] = {
    "yfinance": YFinanceProvider,
}


def provider_name() -> str:
    """The active vendor name: ``MARKET_DATA_PROVIDER`` env, default
    ``"yfinance"``. An unrecognized value fails SAFE to the default (logged),
    matching this codebase's other mode switches (e.g.
    ``price_source.price_source_mode``, ``oracle_updater._int_env``) rather
    than crashing a live process over a config typo."""
    raw = os.getenv("MARKET_DATA_PROVIDER", "yfinance").strip().lower()
    if raw not in _VENDOR_PROVIDERS:
        logger.warning("unknown MARKET_DATA_PROVIDER=%r — falling back to yfinance", raw)
        return "yfinance"
    return raw


# ─── Postgres read-through cache (asset_daily_bars) ────────────────────


def _period_to_start_date(period: str, now: datetime) -> date:
    days = _PERIOD_DAYS.get(period, _DEFAULT_PERIOD_DAYS)
    return (now - timedelta(days=days)).date()


def _default_session_factory():
    from archimedes.db import get_session

    return get_session()


def _read_cached_series(session, ticker: str, start_date: date, ttl: timedelta) -> pd.Series | None:
    """Return a cached close-price ``Series`` for ``ticker`` if the cache both
    covers back to (approximately) ``start_date`` and was written within
    ``ttl``. ``None`` on a coverage or freshness miss (caller re-fetches)."""
    from archimedes.models.asset_daily_bars import AssetDailyBar

    rows = (
        session.query(AssetDailyBar)
        .filter(AssetDailyBar.symbol == ticker, AssetDailyBar.trade_date >= start_date)
        .order_by(AssetDailyBar.trade_date)
        .all()
    )
    if not rows:
        return None

    earliest = rows[0].trade_date
    if earliest > start_date + timedelta(days=_COVERAGE_TOLERANCE_DAYS):
        return None  # cache doesn't reach back far enough for this request

    newest_fetch = max(r.fetched_at for r in rows)
    if newest_fetch.tzinfo is None:
        newest_fetch = newest_fetch.replace(tzinfo=UTC)
    if datetime.now(UTC) - newest_fetch > ttl:
        return None  # cache is past its freshness window

    idx = pd.to_datetime([r.trade_date for r in rows])
    return pd.Series([r.close for r in rows], index=idx, name=ticker)


def _write_cached_series(session, ticker: str, series: pd.Series, source: str) -> None:
    """Upsert ``series`` (close prices indexed by date) into ``asset_daily_bars``
    for ``ticker``, dialect-agnostic (works on SQLite in tests and Postgres in
    prod) via select-then-add/update rather than a dialect-specific ON
    CONFLICT clause."""
    from archimedes.models.asset_daily_bars import AssetDailyBar

    now = datetime.now(UTC)
    to_write: list[tuple[date, float]] = []
    for ts, close in series.items():
        try:
            close_f = float(close)
        except (TypeError, ValueError):
            continue
        if math.isnan(close_f):
            continue
        trade_date = ts.date() if hasattr(ts, "date") else ts
        to_write.append((trade_date, close_f))
    if not to_write:
        return

    dates = [d for d, _ in to_write]
    existing = {
        row.trade_date: row
        for row in session.query(AssetDailyBar)
        .filter(AssetDailyBar.symbol == ticker, AssetDailyBar.trade_date.in_(dates))
        .all()
    }
    for trade_date, close_f in to_write:
        row = existing.get(trade_date)
        if row is not None:
            row.close = close_f
            row.source = source
            row.fetched_at = now
        else:
            session.add(
                AssetDailyBar(
                    symbol=ticker,
                    trade_date=trade_date,
                    close=close_f,
                    source=source,
                    fetched_at=now,
                )
            )


class CachingMarketDataProvider(MarketDataProvider):
    """Wraps another provider with the Postgres ``asset_daily_bars``
    read-through cache for ``get_daily_close_batch`` only. Every other method
    passes straight through, uncached — see the module docstring for why."""

    def __init__(self, inner: MarketDataProvider, source_name: str, session_factory=None) -> None:
        self._inner = inner
        self._source_name = source_name
        self._session_factory = session_factory or _default_session_factory
        self._ttl = timedelta(hours=_int_env("MARKET_DATA_CACHE_TTL_HOURS", DEFAULT_CACHE_TTL_HOURS))

    def get_daily_close_batch(self, tickers: dict[str, str], period: str) -> dict[str, pd.Series]:
        if not tickers:
            return {}
        start_date = _period_to_start_date(period, datetime.now(UTC))

        result: dict[str, pd.Series] = {}
        missing: dict[str, str] = {}
        session = self._session_factory()
        try:
            for key, ticker in tickers.items():
                series = _read_cached_series(session, ticker, start_date, self._ttl)
                if series is not None:
                    result[key] = series
                else:
                    missing[key] = ticker
        finally:
            session.close()

        if not missing:
            return result

        fetched = self._inner.get_daily_close_batch(missing, period)
        if fetched:
            session = self._session_factory()
            try:
                for key, series in fetched.items():
                    ticker = missing.get(key)
                    if ticker is None or series is None or series.empty:
                        continue
                    _write_cached_series(session, ticker, series, self._source_name)
                session.commit()
            finally:
                session.close()

        result.update(fetched)
        return result

    def get_intraday_quote(self, ticker: str) -> tuple[float, datetime] | None:
        return self._inner.get_intraday_quote(ticker)

    def get_intraday_quotes_batch(self, tickers: dict[str, str]) -> dict[str, float]:
        return self._inner.get_intraday_quotes_batch(tickers)

    def get_series(self, ticker: str, period: str, interval: str) -> pd.Series:
        return self._inner.get_series(ticker, period, interval)


def get_provider() -> MarketDataProvider:
    """The active provider, cache-wrapped. Call sites use this — never
    ``YFinanceProvider`` (or ``yfinance``) directly — so a vendor swap via
    ``MARKET_DATA_PROVIDER`` changes every choke point (including the #775
    cross-check's secondary source) in one place."""
    name = provider_name()
    vendor = _VENDOR_PROVIDERS[name]()
    return CachingMarketDataProvider(vendor, source_name=name)
