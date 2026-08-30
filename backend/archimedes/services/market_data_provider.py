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

Five call sites route through here (grep-verified):
  - ``strategy_signal_evaluator._fetch_price_history(ies)`` — daily close
    series for backtests/Explore's universe sweep (the #1218 volume driver).
  - ``chain.oracle_updater`` — the live oracle-push equity fetch, the VIX /
    S&P-MA regime reads, and the #775 secondary-source cross-check's
    independent reading.
  - ``services.asset_market_service._fetch_yfinance_series`` — the Explore
    per-asset history-modal endpoint.
  - ``services.fusion_market_data._fetch_one`` — the GENERATION path's
    fusion/debate real-data panel (#1218 generation-path seam fix).
  - ``services.portfolio_backtester._fetch_price_panel`` — the GENERATION
    path's portfolio-weights backtester (same fix).

**#775 resolution, in one line:** the cross-check
(``oracle_updater._cross_check_secondary``) reads its independent secondary
through ``get_provider()`` and treats ``provider_name()`` (not a hardcoded
``"yfinance"``) as "same source, skip". Swap ``MARKET_DATA_PROVIDER`` to a
new vendor and the cross-check's secondary source swaps with it automatically
— no separate change needed at the guardrail.

**Caching, scoped intentionally.** ``get_daily_close_batch`` — the DAILY-bar
close-only shape that matches ``asset_daily_bars`` — and ``get_daily_ohlcv``
— the DAILY full-bar (Open/High/Low/Close/Volume) shape the generation path's
backtrader feeds and Close+Volume panel need — are both cache-backed
(``CachingMarketDataProvider``), and both read/write the SAME
``asset_daily_bars`` table (a row primed by one method's writer that lacks a
full bar is treated as a miss by the other, so the two never hand back a
partially-populated frame as if it were complete). ``get_intraday_quote(s)``
(live oracle pushes, VIX, the cross-check's secondary) and ``get_series`` (the
Explore history modal, any interval) pass straight through, uncached, on
purpose: a stale daily close must never masquerade as a live push/guardrail
reading. Background refresh is deliberately NOT built here (#1218 anti-goal:
seam, not migration) — the cache primes itself on the first miss.
"""

from __future__ import annotations

import logging
import math
import os
from abc import ABC, abstractmethod
from datetime import UTC, date, datetime, timedelta

import pandas as pd
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

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


def _bar_ts_to_utc(ts) -> datetime:
    """Normalize a pandas bar-index entry to a tz-aware UTC ``datetime``.

    Extracted from ``YFinanceProvider.get_intraday_quote`` so the single- and
    batch-quote siblings normalize identically — the batch method's widened
    return is only interchangeable with the single one if "UTC" means the
    same thing on both. A naive index (yfinance returns one for some daily
    frames) is LOCALIZED to UTC rather than converted, matching what the
    single-quote path has always done.
    """
    ts = ts.tz_convert("UTC") if ts.tzinfo is not None else ts.tz_localize("UTC")
    return ts.to_pydatetime()


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
    def get_intraday_quotes_batch(self, tickers: dict[str, str]) -> dict[str, tuple[float, datetime]]:
        """Latest intraday ``(price, bar_timestamp)`` for many tickers in one
        vendor call. Keyed like ``get_daily_close_batch``. Never cached.

        The bar timestamp is part of the contract, not a nicety — same shape
        ``get_intraday_quote`` already returns for a single ticker, and
        ``bar_timestamp`` is always tz-aware UTC. Two consumers need it and
        neither can be honest without it:

          - ``oracle_updater._validate_for_push`` gates an on-chain push on
            ``now - price.timestamp``. When this method returned price only,
            ``_fetch_yfinance`` had nothing to stamp but the POLL time, so on
            the yfinance leg that gate compared now against now and could
            never reject a stale bar (the Pyth cascade always carried a real
            observation time; this leg did not).
          - the paper-marks loop (``services.paper_marks``) stores the
            UPSTREAM observation time on every mark and writes NO row when
            the newest bar is stale. Both rules are unbuildable on a bare
            float.

        A symbol whose bar time is genuinely old (an equity outside market
        hours) is still returned with its true, old timestamp — deciding what
        counts as too stale belongs to the caller's policy, not to the vendor
        seam.
        """

    @abstractmethod
    def get_series(self, ticker: str, period: str, interval: str) -> pd.Series:
        """Generic close-price series at an arbitrary (period, interval) —
        the Explore history-modal shape. Never cached."""

    @abstractmethod
    def get_daily_ohlcv(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        """Full daily OHLCV history for one vendor ticker over ``[start, end)``
        (ISO date strings) — the shape the GENERATION path needs: backtrader
        feeds (``fusion_market_data.feed_factory``) and the portfolio
        simulator's Close+Volume panel (``portfolio_backtester._fetch_price_panel``)
        both consume a full OHLCV frame, not a close-only series, so this is a
        separate method from ``get_daily_close_batch`` rather than a batch
        variant of it. Columns ``Open``/``High``/``Low``/``Close``/``Volume``,
        a ``DatetimeIndex``, normalized (dropna, monotonic — see
        ``archimedes_analytics_engine.data.normalize_ohlcv``). Raises on a
        genuinely unfetchable symbol/range rather than returning an empty
        frame — callers already handle that (fail-closed to synthetic,
        per-symbol skip) and rely on the exception, not a sentinel value."""


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
                return float(close.iloc[-1]), _bar_ts_to_utc(data.index[-1])
        except Exception as exc:
            logger.warning("Failed to fetch %s: %s", ticker, exc)
        return None

    def get_intraday_quotes_batch(self, tickers: dict[str, str]) -> dict[str, tuple[float, datetime]]:
        """One ``yf.download`` for the whole list; ``(price, bar_ts)`` per key.

        The bar timestamp was always in hand here and thrown away: the frame
        this method already holds is indexed by bar time. It is read
        PER SYMBOL (``col.dropna().index[-1]``), not once off the frame's own
        last index, because a mixed universe's legs do not share a last bar
        — an equity outside the session and a 24/7 crypto pair sit in the same
        frame with the equity column NaN across the tail. Taking the frame's
        last index for both would stamp the equity leg with the crypto leg's
        time, i.e. exactly the "stale price wearing a fresh timestamp" defect
        the widened signature exists to make impossible.
        """
        try:
            import yfinance as yf
        except ImportError:
            logger.warning("yfinance not installed — returning empty prices")
            return {}

        tickers_str = " ".join(tickers.values())
        data = yf.download(tickers_str, period="1d", interval="1m", progress=False)

        results: dict[str, tuple[float, datetime]] = {}
        for key, yf_ticker in tickers.items():
            try:
                if data.empty:
                    continue
                if len(tickers) == 1:
                    close = data["Close"]
                    if hasattr(close, "columns"):
                        close = close.iloc[:, 0]
                else:
                    close = data["Close"]
                    if yf_ticker not in close.columns:
                        continue
                    close = close[yf_ticker]
                close = close.dropna()
                if close.empty:
                    continue
                results[key] = (float(close.iloc[-1]), _bar_ts_to_utc(close.index[-1]))
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

    def get_daily_ohlcv(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        """Delegates to analytics-engine's ``data.fetch_ohlcv`` — the exact
        fetch/retry/normalize contract ``fusion_market_data`` and
        ``portfolio_backtester`` already depended on directly before this
        seam existed (#1282). Reusing it here (rather than re-implementing
        the fetch against yfinance a second time) is what guarantees the
        cold-cache output is byte-identical to the pre-seam direct-fetch
        path — the two are now the same function call."""
        from archimedes.services.fusion_market_data import _ensure_analytics_import

        _ensure_analytics_import()
        from archimedes_analytics_engine.data import fetch_ohlcv

        return fetch_ohlcv(ticker, start, end)


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


# Whether a vendor's INTRADAY feed is a delayed tape rather than a real-time
# one. A property of the vendor contract, declared here once, so a consumer
# that has to label a number for a user ("delayed") reads a stated fact
# instead of guessing from a timestamp at render time.
#
# yfinance: True. Its 1-minute bars come off a consolidated feed with a lag
# Yahoo does not contract to bound — the same property `oracle_updater`'s
# DEFAULT_MAX_UPSTREAM_STALENESS_SECONDS already treats as first-class.
_INTRADAY_DELAYED_BY_PROVIDER: dict[str, bool] = {
    "yfinance": True,
}


def intraday_is_delayed() -> bool:
    """Does the ACTIVE provider's intraday feed carry a delay?

    Fails toward ``True`` for a vendor that has not declared otherwise:
    claiming real-time for a feed nobody verified is the dishonest
    direction, and an unnecessary "delayed" badge costs nothing. A new
    provider that genuinely serves real-time intraday adds itself to
    ``_INTRADAY_DELAYED_BY_PROVIDER`` with ``False`` and a note saying what
    contract backs that claim.
    """
    return _INTRADAY_DELAYED_BY_PROVIDER.get(provider_name(), True)


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


def _read_cached_ohlcv(session, ticker: str, start_date: date, end_date: date, ttl: timedelta) -> pd.DataFrame | None:
    """Return a cached OHLCV ``DataFrame`` for ``ticker`` if the cache covers
    back to (approximately) ``start_date``, holds through ``end_date``, every
    row carries a full bar (not a close-only row written by
    ``get_daily_close_batch``'s writer), and was written within ``ttl``.
    ``None`` on any miss (caller re-fetches the whole range)."""
    from archimedes.models.asset_daily_bars import AssetDailyBar

    rows = (
        session.query(AssetDailyBar)
        .filter(
            AssetDailyBar.symbol == ticker,
            AssetDailyBar.trade_date >= start_date,
            AssetDailyBar.trade_date <= end_date,
        )
        .order_by(AssetDailyBar.trade_date)
        .all()
    )
    if not rows:
        return None

    # A row primed by get_daily_close_batch's writer only carries `close` —
    # open/high/low/volume are NULL. Treat that as a miss for THIS method:
    # a partial bar is not a valid OHLCV cache entry, and re-fetching the
    # whole range fills it in properly (via _write_cached_ohlcv below).
    if any(r.open is None or r.high is None or r.low is None or r.volume is None for r in rows):
        return None

    earliest = rows[0].trade_date
    if earliest > start_date + timedelta(days=_COVERAGE_TOLERANCE_DAYS):
        return None  # cache doesn't reach back far enough for this request

    # Forward-coverage is load-bearing, not symmetry for its own sake: both
    # call sites default `end` to "today" on every call, so a cache primed
    # through day D is routinely re-read after the date rolls to D+1 while
    # still inside the TTL. Without this check that read is a "hit" on a
    # silently shorter frame — a backtest window truncation that moves every
    # graded number with no signal. Known tradeoff: a symbol whose vendor
    # data genuinely ends before `end_date` (delisted/halted) now misses and
    # re-fetches every call; correctness over cache hits — recording a
    # vendor-end marker at write time is the fix for that, out of scope here.
    latest = rows[-1].trade_date
    if latest < end_date - timedelta(days=_COVERAGE_TOLERANCE_DAYS):
        return None  # cache doesn't reach forward to the requested end

    newest_fetch = max(r.fetched_at for r in rows)
    if newest_fetch.tzinfo is None:
        newest_fetch = newest_fetch.replace(tzinfo=UTC)
    if datetime.now(UTC) - newest_fetch > ttl:
        return None  # cache is past its freshness window

    idx = pd.to_datetime([r.trade_date for r in rows])
    return pd.DataFrame(
        {
            "Open": [r.open for r in rows],
            "High": [r.high for r in rows],
            "Low": [r.low for r in rows],
            "Close": [r.close for r in rows],
            "Volume": [r.volume for r in rows],
        },
        index=idx,
    )


def _write_cached_ohlcv(session, ticker: str, df: pd.DataFrame, source: str) -> None:
    """Upsert a full OHLCV frame (indexed by date, columns
    Open/High/Low/Close/Volume — ``fetch_ohlcv``'s output shape) into
    ``asset_daily_bars`` for ``ticker``. Mirrors ``_write_cached_series`` but
    persists the whole bar, not close-only, so the row is a valid cache entry
    for ``get_daily_ohlcv`` as well as ``get_daily_close_batch``."""
    from archimedes.models.asset_daily_bars import AssetDailyBar

    def _float_or_none(value: object) -> float | None:
        try:
            v = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        return None if math.isnan(v) else v

    now = datetime.now(UTC)
    to_write: list[tuple[date, float | None, float | None, float | None, float, float | None]] = []
    for ts, row in df.iterrows():
        close_f = _float_or_none(row.get("Close"))
        if close_f is None:
            continue
        trade_date = ts.date() if hasattr(ts, "date") else ts
        to_write.append(
            (
                trade_date,
                _float_or_none(row.get("Open")),
                _float_or_none(row.get("High")),
                _float_or_none(row.get("Low")),
                close_f,
                _float_or_none(row.get("Volume")),
            )
        )
    if not to_write:
        return

    dates = [d for d, *_ in to_write]
    existing = {
        row.trade_date: row
        for row in session.query(AssetDailyBar)
        .filter(AssetDailyBar.symbol == ticker, AssetDailyBar.trade_date.in_(dates))
        .all()
    }
    for trade_date, open_f, high_f, low_f, close_f, volume_f in to_write:
        row = existing.get(trade_date)
        if row is not None:
            row.open = open_f
            row.high = high_f
            row.low = low_f
            row.close = close_f
            row.volume = volume_f
            row.source = source
            row.fetched_at = now
        else:
            session.add(
                AssetDailyBar(
                    symbol=ticker,
                    trade_date=trade_date,
                    open=open_f,
                    high=high_f,
                    low=low_f,
                    close=close_f,
                    volume=volume_f,
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
        # Merge BEFORE priming: the fetch succeeded, and the caller gets that
        # data no matter what happens to the best-effort cache write below.
        result.update(fetched)
        if fetched:
            session = self._session_factory()
            try:
                for key, series in fetched.items():
                    ticker = missing.get(key)
                    if ticker is None or series is None or series.empty:
                        continue
                    _write_cached_series(session, ticker, series, self._source_name)
                session.commit()
            except IntegrityError:
                # Concurrent writers priming the same cold window (two tasks,
                # or the refresh loop overlapping a request sweep): the loser
                # trips uq_asset_daily_bars_symbol_trade_date at commit. The
                # winner's rows are equivalent vendor data, so this is benign —
                # roll back and serve the fetch; the next read is warm.
                session.rollback()
                logger.info("asset_daily_bars prime lost a concurrent-writer race (benign); next read is warm")
            except SQLAlchemyError:
                # Any other cache-write failure is still only a cache failure —
                # loud, but never allowed to discard a successful fetch.
                session.rollback()
                logger.warning("asset_daily_bars cache write failed (fetch still served)", exc_info=True)
            finally:
                session.close()

        return result

    def get_intraday_quote(self, ticker: str) -> tuple[float, datetime] | None:
        return self._inner.get_intraday_quote(ticker)

    def get_intraday_quotes_batch(self, tickers: dict[str, str]) -> dict[str, tuple[float, datetime]]:
        # Pass-through, and it must STAY a pass-through: intraday is uncached
        # by design (module docstring) — a cached quote handed to the on-chain
        # push gate or to a paper mark is a stale reading wearing a fresh
        # label, which is the failure both consumers' staleness rules exist
        # to prevent.
        return self._inner.get_intraday_quotes_batch(tickers)

    def get_series(self, ticker: str, period: str, interval: str) -> pd.Series:
        return self._inner.get_series(ticker, period, interval)

    def get_daily_ohlcv(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        """Read-through cache for the generation-path (fusion / portfolio
        backtester) OHLCV fetch — the #1218 volume driver these two call
        sites contribute alongside the universe sweep behind
        ``get_daily_close_batch``. On a coverage/freshness miss, the whole
        ``[start, end)`` range is fetched from the vendor and returned
        UNCHANGED (only a best-effort cache write happens afterward) — a
        fetch failure never gets a chance to corrupt the returned frame, and
        a cold cache produces the exact same object the pre-seam direct call
        would have."""
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end) if end else date.today()

        session = self._session_factory()
        try:
            cached = _read_cached_ohlcv(session, ticker, start_date, end_date, self._ttl)
        finally:
            session.close()
        if cached is not None:
            return cached

        fetched = self._inner.get_daily_ohlcv(ticker, start, end)
        if fetched is None or fetched.empty:
            return fetched

        session = self._session_factory()
        try:
            _write_cached_ohlcv(session, ticker, fetched, self._source_name)
            session.commit()
        except IntegrityError:
            # Same benign concurrent-writer race as get_daily_close_batch's
            # prime — the loser rolls back; the fetch is still served.
            session.rollback()
            logger.info("asset_daily_bars OHLCV prime lost a concurrent-writer race (benign); next read is warm")
        except SQLAlchemyError:
            session.rollback()
            logger.warning("asset_daily_bars OHLCV cache write failed (fetch still served)", exc_info=True)
        finally:
            session.close()

        return fetched


def get_provider() -> MarketDataProvider:
    """The active provider, cache-wrapped. Call sites use this — never
    ``YFinanceProvider`` (or ``yfinance``) directly — so a vendor swap via
    ``MARKET_DATA_PROVIDER`` changes every choke point (including the #775
    cross-check's secondary source) in one place."""
    name = provider_name()
    vendor = _VENDOR_PROVIDERS[name]()
    return CachingMarketDataProvider(vendor, source_name=name)
