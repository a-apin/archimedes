import pandas as pd
from archimedes_analytics_engine.data import normalize_ohlcv


def test_normalize_ohlcv_handles_yfinance_multiindex() -> None:
    idx = pd.date_range("2024-01-01", periods=3, freq="D")
    columns = pd.MultiIndex.from_tuples(
        [
            ("Open", "SPY"),
            ("High", "SPY"),
            ("Low", "SPY"),
            ("Close", "SPY"),
            ("Volume", "SPY"),
        ]
    )
    raw = pd.DataFrame(
        [
            [100, 101, 99, 100, 1000],
            [101, 102, 100, 101, 1100],
            [102, 103, 101, 102, 1200],
        ],
        index=idx,
        columns=columns,
    )

    out = normalize_ohlcv(raw, symbol="SPY")

    assert list(out.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert len(out) == 3
