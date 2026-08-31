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
import re
from abc import ABC, abstractmethod
from datetime import UTC, date, datetime, timedelta

import httpx
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


# ─── Tiingo provider (#1218 Part 1 — yfinance replacement) ─────────────


class TiingoProviderError(RuntimeError):
    """Base class for TiingoProvider failures. Deliberately loud (never
    caught-and-silently-substituted with yfinance) — see CLAUDE.md 'fail-soft
    is wrong for anything a claim depends on'."""


class TiingoAPIKeyMissingError(TiingoProviderError):
    """``TIINGO_API_KEY`` is unset/blank. Raised at ``TiingoProvider``
    construction — ``get_provider()`` builds a fresh instance on every call
    (no long-lived singleton in this seam), so this fires on the very next
    call site that routes through the seam with ``MARKET_DATA_PROVIDER=tiingo``:
    the closest thing this seam has to a "startup" check, since there is no
    separate eager app-boot validation of the market-data vendor today — AND
    again at every HTTP call (the key is never cached on the instance;
    re-read from the environment every time, so a rotated value takes effect
    as soon as the process's own environment carries the new one — no
    restart needed on THIS module's account). The message never contains the
    key itself — there is none to include."""


class TiingoUnsupportedSymbolError(TiingoProviderError, ValueError):
    """A ticker's shape identifies it as a commodity-future / index symbol
    (e.g. ``GC=F``, ``CL=F``, ``^N225``, ``^GSPC``) that none of Tiingo's
    three REST endpoint families (equities/ETFs, crypto, FX) can serve.
    Raised loud, naming the symbol and the reason — never silently swapped
    for a yfinance fallback, which would defeat the #1218 migration this
    provider exists to make. ``ValueError`` in the MRO keeps it catchable by
    any existing ``except ValueError`` call site (matches
    ``YFinanceProvider``'s / ``fetch_ohlcv``'s existing
    raise-on-unfetchable-symbol contract)."""


class TiingoEmptyResponseError(TiingoProviderError, ValueError):
    """Tiingo returned a syntactically valid but empty result (no rows) for a
    ticker/date-range that passed symbol-routing. Raised loud rather than
    handed back as an empty-but-"successful" DataFrame — an empty frame
    masquerading as a hit would look identical to "no data exists" at every
    downstream consumer (backtrader feed, portfolio panel), silently
    truncating whatever depends on it."""


_TIINGO_BASE_URL = "https://api.tiingo.com"
_TIINGO_TIMEOUT_S = 15.0

# Case-insensitive: crypto vendor tickers in this codebase are always
# "<BASE>-USD" (see backend/archimedes/data/synthetic_universe.json's 71
# `crypto` entries, e.g. "BTC-USD", "AAVE-USD") — the same shape yfinance
# uses. Tiingo's crypto endpoint wants the pair concatenated and lowercased
# ("btcusd").
_CRYPTO_TICKER_RE = re.compile(r"^[A-Z0-9]{1,10}-USD$")


def _classify_tiingo_ticker(ticker: str) -> str:
    """Route a vendor-ticker string to one of Tiingo's three REST endpoint
    families by ITS SHAPE — the same shape yfinance's own ticker convention
    already encodes (``=X`` FX suffix, ``-USD`` crypto suffix, bare symbol
    for equities/ETFs), since every call site into this seam still hands
    ``TiingoProvider`` a yfinance-formatted ticker string (the ``tickers``
    dict's VALUES / the ``ticker`` positional arg — see the ABC method
    docstrings), not a synth symbol or an ``asset_class`` label.

    A ``archimedes.universe.SYNTHETIC_UNIVERSE`` lookup was considered
    instead of a shape heuristic, and rejected: several real call sites hand
    this seam tickers that are NOT in the on-chain synthetic universe at all
    — ``chain.oracle_updater``'s ``^GSPC``/``^VIX`` regime-signal tickers,
    and arbitrary Explore/backtest tickers passed straight through by
    ``asset_market_service`` / ``fusion_market_data``. A universe lookup
    would return nothing useful for exactly the tickers most likely to need
    the unsupported-symbol guard (index tickers aren't synths at all).
    The shape heuristic covers every one of the 281 SSOT entries'
    ``yfinance_ticker`` values correctly (verified against
    ``synthetic_universe.json`` while building this provider) plus the
    out-of-universe ``^`` / ``=F`` cases the guard exists for.

    Returns ``"equity"``, ``"crypto"``, or ``"fx"``. Raises
    ``TiingoUnsupportedSymbolError`` for an index (``^``-prefixed) or
    commodity-future/continuous-contract (``=F``-suffixed) ticker — e.g. the
    5 ``metal_spot`` SSOT entries (``GC=F``/``SI=F``/``HG=F``/``PA=F``/
    ``PL=F``) and index regime tickers like ``^GSPC``/``^VIX``/``^N225``/
    ``CL=F``. These have no Tiingo REST equivalent on the endpoint families
    this provider targets.
    """
    t = ticker.strip()
    if t.startswith("^"):
        raise TiingoUnsupportedSymbolError(
            f"{ticker!r} is an index ticker (^-prefixed, e.g. ^GSPC/^VIX/^N225) — "
            "Tiingo's REST API has no index-level endpoint; TiingoProvider cannot "
            "serve this symbol. Never silently falls back to yfinance."
        )
    if t.upper().endswith("=F"):
        raise TiingoUnsupportedSymbolError(
            f"{ticker!r} is a futures/commodity-continuous-contract ticker (=F suffix, "
            "e.g. GC=F/SI=F/CL=F) — Tiingo has no futures endpoint on the "
            "equities/crypto/FX REST families TiingoProvider targets. Never silently "
            "falls back to yfinance."
        )
    if t.upper().endswith("=X"):
        return "fx"
    if _CRYPTO_TICKER_RE.match(t.upper()):
        return "crypto"
    return "equity"


def _tiingo_api_key() -> str:
    """Read ``TIINGO_API_KEY`` fresh from the environment — never cached on a
    ``TiingoProvider`` instance, so a rotated value takes effect on the next
    call as soon as the process's environment carries it. Raises loud
    (``TiingoAPIKeyMissingError``) rather than proceeding with an
    unauthenticated request Tiingo would reject anyway; the message never
    includes the key (there is none to include)."""
    key = os.getenv("TIINGO_API_KEY", "").strip()
    if not key:
        raise TiingoAPIKeyMissingError(
            "TIINGO_API_KEY is not set. Required whenever MARKET_DATA_PROVIDER=tiingo "
            "(see .env.example). NOT wired into infra/ecs.tf's task-definition secrets yet — "
            "seeding /archimedes/prod/TIINGO_API_KEY and adding the ecs.tf entry are cutover "
            "follow-ups, deliberately not in this PR."
        )
    return key


def _tiingo_rows_to_ohlcv(
    rows: list[dict],
    ticker: str,
    *,
    open_key: str,
    high_key: str,
    low_key: str,
    close_key: str,
    volume_key: str | None,
) -> pd.DataFrame:
    """Shared row->frame mapper for all three Tiingo endpoint families —
    shape contract match with ``YFinanceProvider`` (same columns, a
    tz-naive ``DatetimeIndex``, float64 throughout). ``volume_key=None``
    (Tiingo's FX endpoint carries no volume field — FX is OTC, there is no
    consolidated tape) fills ``Volume`` with 0.0, matching yfinance's own
    FX-ticker behavior (``EURUSD=X`` etc. return an all-zero ``Volume``
    column from yfinance too — forex has no exchange-reported volume for
    either vendor, so this is not a Tiingo-specific gap)."""
    if not rows:
        raise TiingoEmptyResponseError(f"Tiingo returned zero rows for {ticker!r} in the requested range")
    index = pd.DatetimeIndex(pd.to_datetime([r["date"] for r in rows], utc=True)).tz_localize(None)
    volume = [float(r[volume_key]) for r in rows] if volume_key else [0.0] * len(rows)
    frame = pd.DataFrame(
        {
            "Open": [float(r[open_key]) for r in rows],
            "High": [float(r[high_key]) for r in rows],
            "Low": [float(r[low_key]) for r in rows],
            "Close": [float(r[close_key]) for r in rows],
            "Volume": volume,
        },
        index=index,
    )
    return frame.sort_index()


class TiingoProvider(MarketDataProvider):
    """Yfinance-replacement vendor (#1218 Part 1) backed by Tiingo's REST
    API. Scope of THIS PR: ``get_daily_close_batch`` and ``get_daily_ohlcv``
    only — the two methods ``CachingMarketDataProvider`` cache-backs, and the
    #1218 cost driver (the universe sweep + generation-path OHLCV fetches).
    ``get_intraday_quote`` / ``get_intraday_quotes_batch`` / ``get_series``
    are intentionally NOT implemented — see their docstrings below for why,
    and the PR body for the cutover implication (call sites depending on
    them must stay on ``MARKET_DATA_PROVIDER=yfinance`` until a follow-up
    covers Tiingo's IEX/top-of-book endpoints).

    Three REST endpoint families, routed per-ticker by
    ``_classify_tiingo_ticker`` (ticker-SHAPE heuristic — see that function's
    docstring for why a universe lookup was rejected):
      - equities/ETFs → ``GET /tiingo/daily/{ticker}/prices``
      - crypto        → ``GET /tiingo/crypto/prices?tickers=...``
      - FX            → ``GET /tiingo/fx/{ticker}/prices``

    **Adjustment semantics — the load-bearing part (#1218 point 3).** The
    yfinance path this replaces calls ``yf.download(..., auto_adjust=True,
    ...)`` at every equity/ETF call site that feeds these two methods:
    ``YFinanceProvider.get_daily_close_batch`` (this file, line ~173),
    ``YFinanceProvider.get_series`` (this file, line ~275), and — via
    delegation from ``YFinanceProvider.get_daily_ohlcv`` — ``archimedes_analytics_engine
    .market_data.YFinanceProvider.fetch_ohlcv`` (``analytics-engine/src/
    archimedes_analytics_engine/market_data.py:63``). ``auto_adjust=True``
    means yfinance's ``Close``/``Open``/``High``/``Low`` columns are ALREADY
    split/dividend back-adjusted — yfinance folds what would otherwise be a
    separate ``Adj Close`` column into ``Close`` itself under this flag, so
    there is no unadjusted column left in the frame at all. Every graded
    backtest, PBO run, and the #775 cross-check compares against that
    adjusted series today.

    TiingoProvider therefore maps its EQUITY rows from Tiingo's
    ``adjOpen``/``adjHigh``/``adjLow``/``adjClose``/``adjVolume`` fields —
    NEVER the sibling raw ``open``/``high``/``low``/``close``/``volume``
    fields Tiingo also returns in the same payload. Using the raw fields
    would silently reintroduce split/dividend discontinuities into every
    backtest run against this provider (a KO-style 2-for-1 split would show
    up as a ~50% overnight price cliff) with no error and no log — exactly
    the corruption #1218 point 3 warns about. Mutation-checked: flipping
    ``adjClose`` -> ``close`` (etc.) in this mapping makes
    ``TestAdjustmentSemantics`` in ``test_tiingo_provider.py`` fail (before/
    after run recorded in the PR body).

    Crypto and FX have no adjusted/raw distinction to make: Tiingo's crypto
    and FX payloads carry only ONE set of OHLC fields each (no corporate
    actions apply to either asset class), so those two families map their
    single raw field set directly — there is nothing to get wrong there.
    """

    def __init__(self, client: httpx.Client | None = None) -> None:
        # Presence-check (not value-cache) at construction — see
        # TiingoAPIKeyMissingError's docstring for why this is the closest
        # thing this seam has to a "startup" gate. The key itself is
        # re-read fresh (never reused from here) by every _request() call.
        _tiingo_api_key()
        self._client = client

    # ─── HTTP boundary ───────────────────────────────────────────────

    def _request(self, path: str, params: dict[str, str]) -> object:
        key = _tiingo_api_key()  # re-read fresh — never cached on self
        headers = {"Authorization": f"Token {key}"}  # header, never a query param: never lands in a logged URL
        client = self._client
        owns_client = client is None
        if owns_client:
            client = httpx.Client(base_url=_TIINGO_BASE_URL, timeout=_TIINGO_TIMEOUT_S)
        try:
            response = client.get(path, params=params, headers=headers)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise TiingoProviderError(f"Tiingo API returned HTTP {exc.response.status_code} for {path}") from exc
        except httpx.RequestError as exc:
            raise TiingoProviderError(f"Tiingo API request failed for {path}: {type(exc).__name__}") from exc
        finally:
            if owns_client:
                client.close()

    # ─── Per-family fetch ────────────────────────────────────────────

    def _fetch_equity_rows(self, ticker: str, start: str, end: str) -> list[dict]:
        params: dict[str, str] = {"format": "json", "startDate": start}
        if end:
            params["endDate"] = end
        data = self._request(f"/tiingo/daily/{ticker}/prices", params)
        if not isinstance(data, list):
            raise TiingoProviderError(f"Unexpected Tiingo equity response shape for {ticker!r}")
        return data

    def _fetch_crypto_rows(self, ticker: str, start: str, end: str) -> list[dict]:
        tiingo_ticker = ticker.replace("-", "").lower()
        params: dict[str, str] = {"tickers": tiingo_ticker, "startDate": start, "resampleFreq": "1day"}
        if end:
            params["endDate"] = end
        data = self._request("/tiingo/crypto/prices", params)
        if not isinstance(data, list) or not data:
            return []
        return data[0].get("priceData") or []

    def _fetch_fx_rows(self, ticker: str, start: str, end: str) -> list[dict]:
        tiingo_ticker = ticker[:-2].lower() if ticker.upper().endswith("=X") else ticker.lower()
        params: dict[str, str] = {"startDate": start, "resampleFreq": "1day"}
        if end:
            params["endDate"] = end
        data = self._request(f"/tiingo/fx/{tiingo_ticker}/prices", params)
        if not isinstance(data, list):
            raise TiingoProviderError(f"Unexpected Tiingo FX response shape for {ticker!r}")
        return data

    def _fetch_ohlcv_for_ticker(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        """Shared by both public methods below — one routing + adjustment-
        mapping implementation, not two, so the two public methods can never
        drift into inconsistent semantics for the same ticker."""
        asset_class = _classify_tiingo_ticker(ticker)
        if asset_class == "equity":
            rows = self._fetch_equity_rows(ticker, start, end)
            return _tiingo_rows_to_ohlcv(
                rows,
                ticker,
                open_key="adjOpen",
                high_key="adjHigh",
                low_key="adjLow",
                close_key="adjClose",
                volume_key="adjVolume",
            )
        if asset_class == "crypto":
            rows = self._fetch_crypto_rows(ticker, start, end)
            return _tiingo_rows_to_ohlcv(
                rows, ticker, open_key="open", high_key="high", low_key="low", close_key="close", volume_key="volume"
            )
        rows = self._fetch_fx_rows(ticker, start, end)  # asset_class == "fx"
        return _tiingo_rows_to_ohlcv(
            rows, ticker, open_key="open", high_key="high", low_key="low", close_key="close", volume_key=None
        )

    # ─── ABC surface — IN SCOPE for this PR ─────────────────────────

    def get_daily_close_batch(self, tickers: dict[str, str], period: str) -> dict[str, pd.Series]:
        """Per the ABC contract, a failed/unsupported/empty ticker is simply
        ABSENT from the result (never raises the whole batch) — matching
        ``YFinanceProvider``'s existing behavior for this method and the
        cache-wrapper's own invariant
        (``test_vendor_miss_leaves_symbol_absent_not_erroring``). An
        unsupported symbol is still LOUD in the log (full symbol + reason,
        at ERROR level): "loud" here means "never silently substitutes
        yfinance data", not "raises out of a 280-symbol universe sweep over
        one bad ticker". ``get_daily_ohlcv`` below is the method whose
        contract is to raise on a single unfetchable ticker; this one's
        contract (inherited from the ABC, unchanged by this PR) is per-item
        skip.

        A missing API key is the one exception that DOES propagate out of
        the batch immediately rather than being skipped per-ticker: it is a
        configuration problem affecting every ticker, not a per-symbol data
        issue, and skipping it per-item would silently degrade
        ``MARKET_DATA_PROVIDER=tiingo`` with no key into "every symbol
        empty" instead of a loud startup-shaped failure.
        """
        if not tickers:
            return {}
        start_date = _period_to_start_date(period, datetime.now(UTC))
        start = start_date.isoformat()
        end = datetime.now(UTC).date().isoformat()

        result: dict[str, pd.Series] = {}
        for key, ticker in tickers.items():
            try:
                frame = self._fetch_ohlcv_for_ticker(ticker, start, end)
            except TiingoAPIKeyMissingError:
                raise  # configuration problem, not a per-symbol issue — loud, no partial batch
            except TiingoProviderError as exc:
                logger.error("TiingoProvider: skipping %s (%s) in batch — %s", key, ticker, exc)
                continue
            close = frame["Close"].dropna()
            if close.empty:
                continue
            close.name = key
            result[key] = close
        return result

    def get_daily_ohlcv(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        """Raises on a genuinely unfetchable/unsupported symbol or an empty
        result — matches the ABC's documented contract (and
        ``YFinanceProvider.get_daily_ohlcv``'s delegate,
        ``archimedes_analytics_engine.data.fetch_ohlcv``): callers
        (``fusion_market_data``, ``portfolio_backtester``) rely on the
        exception for their own fail-closed / per-symbol-skip handling, not
        a sentinel empty frame."""
        return self._fetch_ohlcv_for_ticker(ticker, start, end)

    # ─── ABC surface — OUT OF SCOPE for this PR ─────────────────────
    # See the class docstring's "Scope of THIS PR" note. Loud
    # NotImplementedError, never a silent wrong-data fallback: a stubbed
    # intraday quote or arbitrary-interval series would be indistinguishable
    # from a working one at every call site until it produced a visibly
    # wrong number in production (the live oracle push, the VIX/S&P regime
    # reads, or the Explore history modal) — see CLAUDE.md "fail-soft is
    # wrong for anything a claim depends on".

    def get_intraday_quote(self, ticker: str) -> tuple[float, datetime] | None:
        raise NotImplementedError(
            "TiingoProvider.get_intraday_quote is out of scope for #1218 Part 1 (daily "
            "batch + OHLCV only). chain.oracle_updater's live oracle push and VIX/S&P "
            "regime reads must stay on MARKET_DATA_PROVIDER=yfinance until a follow-up "
            "wires Tiingo's IEX top-of-book endpoint."
        )

    def get_intraday_quotes_batch(self, tickers: dict[str, str]) -> dict[str, float]:
        raise NotImplementedError(
            "TiingoProvider.get_intraday_quotes_batch is out of scope for #1218 Part 1 "
            "(daily batch + OHLCV only). Call sites needing a live intraday batch quote "
            "must stay on MARKET_DATA_PROVIDER=yfinance until a follow-up wires Tiingo's "
            "IEX top-of-book endpoint."
        )

    def get_series(self, ticker: str, period: str, interval: str) -> pd.Series:
        raise NotImplementedError(
            "TiingoProvider.get_series is out of scope for #1218 Part 1 (daily batch + "
            "OHLCV only). services.asset_market_service's Explore history modal must "
            "stay on MARKET_DATA_PROVIDER=yfinance until a follow-up wires this method."
        )


_VENDOR_PROVIDERS: dict[str, type[MarketDataProvider]] = {
    "yfinance": YFinanceProvider,
    "tiingo": TiingoProvider,
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
