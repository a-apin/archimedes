from __future__ import annotations

import logging
import time

import pandas as pd
import yfinance as yf

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
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            # auto_adjust=True applies yfinance's built-in split/dividend back-adjustment so
            # corporate actions (e.g. KO's Aug-2012 2-for-1 split) don't show up as spurious
            # overnight price discontinuities that corrupt pairs-trading signals and PBO.
            data = yf.download(symbol, start=start, end=end, auto_adjust=True, progress=False)
        except Exception as exc:  # transient network/yfinance error — retry with backoff
            last_exc = exc
            if attempt == _MAX_RETRIES:
                break
            logger.warning("fetch_ohlcv(%s) attempt %d failed: %s — retrying", symbol, attempt, exc)
            time.sleep(_RETRY_DELAY_S * attempt)
            continue

        if data.empty:
            if attempt == _MAX_RETRIES:
                raise ValueError(f"No data returned for symbol={symbol} in range {start}..{end}")
            logger.warning("fetch_ohlcv(%s) returned empty — retrying (attempt %d)", symbol, attempt)
            time.sleep(_RETRY_DELAY_S * attempt)
            continue

        result = normalize_ohlcv(data, symbol=symbol)
        if len(result) == 0:
            raise ValueError(f"All rows dropped after normalization for {symbol} — check date range")
        return result

    raise RuntimeError(f"yfinance download failed for {symbol} after {_MAX_RETRIES} attempts") from last_exc
