from __future__ import annotations

import pandas as pd
import yfinance as yf

REQUIRED_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


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

    return out[REQUIRED_COLUMNS].dropna()


def fetch_ohlcv(symbol: str, start: str, end: str) -> pd.DataFrame:
    data = yf.download(symbol, start=start, end=end, auto_adjust=False, progress=False)
    if data.empty:
        raise ValueError(f"No data returned for symbol={symbol} in range {start}..{end}")

    return normalize_ohlcv(data, symbol=symbol)
