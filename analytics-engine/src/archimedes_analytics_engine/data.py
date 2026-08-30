from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]
_MAX_RETRIES = 3
_RETRY_DELAY_S = 2.0


def normalize_ohlcv(data: pd.DataFrame, *, symbol: str) -> pd.DataFrame:
    out = data.copy()

    if isinstance(out.columns, pd.MultiIndex):
        if symbol in out.columns.get_level_values(-1):
            out = out.xs(symbol, axis=1, level=-1)
        else:
            out = out.droplevel(-1, axis=1)

    missing = [c for c in REQUIRED_COLUMNS if c not in out.columns]
    if missing:
        raise ValueError(f"Missing required columns for {symbol}: {missing}")

    before = len(out)
    out = out[REQUIRED_COLUMNS].dropna()
    dropped = before - len(out)
    if dropped > 0:
        # A partial/garbled yfinance response can drop many bars here and
        # silently shorten the series, misaligning cross-strategy PBO splits.
        logger.warning("normalize_ohlcv(%s): dropped %d row(s) with NaN values", symbol, dropped)

    # Non-positive marks. Every runner in engine.py models a position as an
    # unlevered cash-equity holding: backtrader's stock-like commission scheme
    # marks it at `size * price`, and `broker.getvalue()` = cash + that mark is
    # the equity curve every metric is computed from. A non-positive price is
    # outside that model — the mark goes NEGATIVE, so mark-to-market "equity"
    # stops being the account's equity and can cross zero, which is what makes
    # an impossible >100% drawdown come out the other end. This is not
    # hypothetical for the declared universe: OIL resolves to `CL=F`, whose
    # front-month settled at -$37.63 on 2020-04-20. Note the PCA stat-arb
    # strategy already refuses to build a RETURN from a non-positive price
    # (`if prev <= 0: return None`) — the signal path was guarded and the
    # marking path was not. Drop the bars, loudly, so the two agree.
    price_cols = [c for c in ("Open", "High", "Low", "Close") if c in out.columns]
    if price_cols:
        positive = (out[price_cols] > 0).all(axis=1)
        n_bad = int((~positive).sum())
        if n_bad:
            bad_dates = [str(d) for d in out.index[~positive][:5]]
            logger.warning(
                "normalize_ohlcv(%s): dropped %d bar(s) with a non-positive price (first: %s) — "
                "the engine marks positions as unlevered cash equity (size * price), so a "
                "non-positive mark would corrupt portfolio value and every metric derived from it",
                symbol,
                n_bad,
                ", ".join(bad_dates),
            )
            out = out[positive]

    # yfinance occasionally returns duplicate or out-of-order timestamps (seen
    # on some corporate-action dates); a non-monotonic index makes backtrader's
    # PandasData feed advance incorrectly and introduces look-ahead bias.
    if not out.index.is_monotonic_increasing:
        dupes = int(out.index.duplicated().sum())
        if dupes > 0:
            logger.warning("normalize_ohlcv(%s): %d duplicate timestamp(s) — keeping last", symbol, dupes)
            out = out[~out.index.duplicated(keep="last")]
        out = out.sort_index()

    return out


def fetch_ohlcv(symbol: str, start: str, end: str) -> pd.DataFrame:
    """Fetch + normalize OHLCV for ``symbol`` over [start, end).

    A thin façade over the market-data provider seam (#1218:
    ``archimedes_analytics_engine.market_data``) — default provider is
    yfinance, and this function's retry/error contract (3 attempts, backoff,
    raises on a genuinely empty/unfetchable result) is unchanged from before
    the seam existed; that logic now lives in ``market_data.YFinanceProvider``.
    Vendor-swappable via the ``MARKET_DATA_PROVIDER`` env var.
    """
    from archimedes_analytics_engine.market_data import get_provider

    return get_provider().fetch_ohlcv(symbol, start, end)
