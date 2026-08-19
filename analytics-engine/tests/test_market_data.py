"""Hermetic tests for the #1218 market-data vendor seam (analytics-engine side).

Covers provider selection (default + unknown-value fail-safe) and that
``data.fetch_ohlcv`` is a pure façade over ``get_provider().fetch_ohlcv`` —
same retry/error contract as before the seam existed. No network: the
``yfinance`` boundary is mocked.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from archimedes_analytics_engine import data, market_data
from archimedes_analytics_engine.market_data import (
    MarketDataProvider,
    YFinanceProvider,
    get_provider,
    provider_name,
)


class TestProviderSelection:
    def test_default_is_yfinance(self, monkeypatch):
        monkeypatch.delenv("MARKET_DATA_PROVIDER", raising=False)
        assert provider_name() == "yfinance"

    def test_unknown_value_falls_back_to_yfinance(self, monkeypatch, caplog):
        import logging

        monkeypatch.setenv("MARKET_DATA_PROVIDER", "some_unreleased_vendor")
        with caplog.at_level(logging.WARNING):
            assert provider_name() == "yfinance"
        assert any("some_unreleased_vendor" in rec.message for rec in caplog.records)

    def test_get_provider_returns_yfinance_provider_by_default(self, monkeypatch):
        monkeypatch.delenv("MARKET_DATA_PROVIDER", raising=False)
        assert isinstance(get_provider(), YFinanceProvider)
        assert isinstance(get_provider(), MarketDataProvider)


def _multiindex_ohlcv(symbol: str, n: int = 3) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    columns = pd.MultiIndex.from_tuples([(field, symbol) for field in ("Open", "High", "Low", "Close", "Volume")])
    return pd.DataFrame(
        [[100 + i, 101 + i, 99 + i, 100 + i, 1000 + i] for i in range(n)],
        index=idx,
        columns=columns,
    )


class TestYFinanceProviderFetchOhlcv:
    def test_normal_fetch_normalizes_and_returns(self):
        provider = YFinanceProvider()
        raw = _multiindex_ohlcv("SPY")
        with patch.object(market_data, "yf") as fake_yf:
            fake_yf.download = MagicMock(return_value=raw)
            out = provider.fetch_ohlcv("SPY", "2024-01-01", "2024-01-04")
        assert list(out.columns) == ["Open", "High", "Low", "Close", "Volume"]
        assert len(out) == 3

    def test_empty_result_retries_then_raises(self):
        provider = YFinanceProvider()
        with (
            patch.object(market_data, "yf") as fake_yf,
            patch.object(market_data.time, "sleep"),  # no real backoff delay in tests
        ):
            fake_yf.download = MagicMock(return_value=pd.DataFrame())
            with pytest.raises(ValueError, match="No data returned"):
                provider.fetch_ohlcv("NOPE", "2024-01-01", "2024-01-04")
        assert fake_yf.download.call_count == market_data._MAX_RETRIES

    def test_download_exception_retries_then_raises(self):
        provider = YFinanceProvider()
        with (
            patch.object(market_data, "yf") as fake_yf,
            patch.object(market_data.time, "sleep"),
        ):
            fake_yf.download = MagicMock(side_effect=RuntimeError("network down"))
            with pytest.raises(RuntimeError, match="yfinance download failed"):
                provider.fetch_ohlcv("SPY", "2024-01-01", "2024-01-04")
        assert fake_yf.download.call_count == market_data._MAX_RETRIES

    def test_transient_failure_then_success_returns_normalized(self):
        """First attempt raises, second attempt returns real data — the retry
        loop's success path, not just its two failure paths."""
        provider = YFinanceProvider()
        raw = _multiindex_ohlcv("SPY")
        with (
            patch.object(market_data, "yf") as fake_yf,
            patch.object(market_data.time, "sleep"),
        ):
            fake_yf.download = MagicMock(side_effect=[RuntimeError("transient"), raw])
            out = provider.fetch_ohlcv("SPY", "2024-01-01", "2024-01-04")
        assert len(out) == 3
        assert fake_yf.download.call_count == 2


class TestDataFetchOhlcvFacade:
    def test_delegates_to_active_provider(self):
        """data.fetch_ohlcv must be a pure façade: patching the provider
        module's get_provider changes what data.fetch_ohlcv returns."""
        fake_provider = MagicMock()
        fake_provider.fetch_ohlcv = MagicMock(return_value="SENTINEL")
        with patch.object(market_data, "get_provider", return_value=fake_provider):
            assert data.fetch_ohlcv("SPY", "2024-01-01", "2024-01-04") == "SENTINEL"
        fake_provider.fetch_ohlcv.assert_called_once_with("SPY", "2024-01-01", "2024-01-04")
