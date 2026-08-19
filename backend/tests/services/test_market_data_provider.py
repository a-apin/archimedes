"""Hermetic tests for the #1218 / #775 market-data vendor seam.

Covers: provider selection (default + unknown-value fail-safe), the
``asset_daily_bars`` Postgres cache's read-through/miss/freshness/coverage
behavior, and that the intraday methods (live oracle pushes, the #775
cross-check's secondary reading) are never routed through that cache.

Hermetic: an isolated, fresh SQLite engine (not the module-level
``archimedes.db`` singleton) is injected via ``session_factory`` — no real DB,
no network. The vendor boundary (``MarketDataProvider``) is a hand-rolled
fake, not real yfinance.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pandas as pd
import pytest
from archimedes.services.market_data_provider import (
    CachingMarketDataProvider,
    MarketDataProvider,
    YFinanceProvider,
    get_provider,
    provider_name,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# ─── Provider selection ────────────────────────────────────────────────


class TestProviderSelection:
    def test_default_is_yfinance(self, monkeypatch):
        monkeypatch.delenv("MARKET_DATA_PROVIDER", raising=False)
        assert provider_name() == "yfinance"

    def test_explicit_yfinance(self, monkeypatch):
        monkeypatch.setenv("MARKET_DATA_PROVIDER", "yfinance")
        assert provider_name() == "yfinance"

    def test_unknown_value_falls_back_to_yfinance(self, monkeypatch, caplog):
        import logging

        monkeypatch.setenv("MARKET_DATA_PROVIDER", "some_unreleased_vendor")
        with caplog.at_level(logging.WARNING):
            assert provider_name() == "yfinance"
        assert any("some_unreleased_vendor" in rec.message for rec in caplog.records)

    def test_case_and_whitespace_insensitive(self, monkeypatch):
        monkeypatch.setenv("MARKET_DATA_PROVIDER", "  YFinance  ")
        assert provider_name() == "yfinance"

    def test_get_provider_wraps_vendor_in_caching_provider(self):
        assert isinstance(get_provider(), CachingMarketDataProvider)
        # The inner vendor is the default (yfinance) implementation.
        assert isinstance(get_provider()._inner, YFinanceProvider)


# ─── Cache read-through / miss ──────────────────────────────────────────


@pytest.fixture()
def session_factory(tmp_path):
    """An isolated SQLite engine/session factory with asset_daily_bars
    created — independent of the app's module-level engine."""
    from archimedes.db import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'market_data.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


class _FakeVendor(MarketDataProvider):
    """A hand-rolled fake vendor — records every call it receives so tests
    can assert whether the cache actually skipped it."""

    def __init__(self, series_by_key: dict[str, pd.Series]) -> None:
        self.series_by_key = series_by_key
        self.daily_batch_calls: list[dict[str, str]] = []

    def get_daily_close_batch(self, tickers: dict[str, str], period: str) -> dict[str, pd.Series]:
        self.daily_batch_calls.append(dict(tickers))
        return {k: self.series_by_key[k] for k in tickers if k in self.series_by_key}

    def get_intraday_quote(self, ticker: str) -> tuple[float, datetime] | None:
        raise AssertionError("get_intraday_quote must never be called by the caching wrapper's own logic")

    def get_intraday_quotes_batch(self, tickers: dict[str, str]) -> dict[str, float]:
        raise AssertionError("get_intraday_quotes_batch must never be called by the caching wrapper's own logic")

    def get_series(self, ticker: str, period: str, interval: str) -> pd.Series:
        raise AssertionError("get_series must never be called by the caching wrapper's own logic")


def _series(n_days: int = 30, start_price: float = 100.0) -> pd.Series:
    idx = pd.date_range(end=pd.Timestamp.now("UTC").normalize(), periods=n_days, freq="D")
    return pd.Series([start_price + i for i in range(n_days)], index=idx, name="X")


class TestDailyCloseBatchCache:
    def test_cold_cache_is_a_miss_and_primes(self, session_factory):
        vendor = _FakeVendor({"sSPY": _series(30)})
        provider = CachingMarketDataProvider(vendor, source_name="yfinance", session_factory=session_factory)

        result = provider.get_daily_close_batch({"sSPY": "SPY"}, period="1mo")

        assert vendor.daily_batch_calls == [{"sSPY": "SPY"}]  # vendor WAS hit (cold cache)
        assert "sSPY" in result
        assert len(result["sSPY"]) == 30

        # Verify it actually wrote through to the DB.
        from archimedes.models.asset_daily_bars import AssetDailyBar

        session = session_factory()
        try:
            rows = session.query(AssetDailyBar).filter(AssetDailyBar.symbol == "SPY").all()
            assert len(rows) == 30
            assert all(r.source == "yfinance" for r in rows)
        finally:
            session.close()

    def test_warm_cache_within_ttl_skips_vendor(self, session_factory):
        vendor = _FakeVendor({"sSPY": _series(30)})
        provider = CachingMarketDataProvider(vendor, source_name="yfinance", session_factory=session_factory)

        provider.get_daily_close_batch({"sSPY": "SPY"}, period="1mo")  # primes the cache
        vendor.daily_batch_calls.clear()

        result = provider.get_daily_close_batch({"sSPY": "SPY"}, period="1mo")

        assert vendor.daily_batch_calls == []  # cache hit — vendor NOT called
        assert len(result["sSPY"]) == 30

    def test_stale_cache_past_ttl_refetches(self, session_factory):
        vendor = _FakeVendor({"sSPY": _series(30)})
        provider = CachingMarketDataProvider(
            vendor, source_name="yfinance", session_factory=session_factory
        )
        provider._ttl = timedelta(hours=1)
        provider.get_daily_close_batch({"sSPY": "SPY"}, period="1mo")

        # Backdate every row's fetched_at past the TTL.
        from archimedes.models.asset_daily_bars import AssetDailyBar

        session = session_factory()
        try:
            for row in session.query(AssetDailyBar).all():
                row.fetched_at = datetime.now(UTC) - timedelta(hours=2)
            session.commit()
        finally:
            session.close()

        vendor.daily_batch_calls.clear()
        provider.get_daily_close_batch({"sSPY": "SPY"}, period="1mo")
        assert vendor.daily_batch_calls == [{"sSPY": "SPY"}]  # stale → refetched

    def test_insufficient_back_coverage_refetches(self, session_factory):
        """A cache primed for a SHORT period ("1mo") does not satisfy a later
        request for a LONGER period ("2y") over the same symbol — the earliest
        cached row doesn't reach back far enough."""
        vendor = _FakeVendor({"sSPY": _series(30)})
        provider = CachingMarketDataProvider(vendor, source_name="yfinance", session_factory=session_factory)
        provider.get_daily_close_batch({"sSPY": "SPY"}, period="1mo")  # only 30 days cached

        vendor.series_by_key["sSPY"] = _series(731)
        vendor.daily_batch_calls.clear()
        result = provider.get_daily_close_batch({"sSPY": "SPY"}, period="2y")

        assert vendor.daily_batch_calls == [{"sSPY": "SPY"}]  # coverage gap → refetched
        assert len(result["sSPY"]) == 731

    def test_partial_miss_only_fetches_missing_symbols(self, session_factory):
        vendor = _FakeVendor({"sSPY": _series(30), "sAGG": _series(30)})
        provider = CachingMarketDataProvider(vendor, source_name="yfinance", session_factory=session_factory)
        provider.get_daily_close_batch({"sSPY": "SPY"}, period="1mo")  # primes sSPY only
        vendor.daily_batch_calls.clear()

        result = provider.get_daily_close_batch({"sSPY": "SPY", "sAGG": "AGG"}, period="1mo")

        assert vendor.daily_batch_calls == [{"sAGG": "AGG"}]  # only the miss goes to the vendor
        assert set(result) == {"sSPY", "sAGG"}

    def test_empty_tickers_is_a_noop(self, session_factory):
        vendor = _FakeVendor({})
        provider = CachingMarketDataProvider(vendor, source_name="yfinance", session_factory=session_factory)
        assert provider.get_daily_close_batch({}, period="1mo") == {}
        assert vendor.daily_batch_calls == []

    def test_vendor_miss_leaves_symbol_absent_not_erroring(self, session_factory):
        """A vendor that returns nothing for a requested ticker (delisted,
        typo) must not raise — the caller (e.g. _fetch_price_histories)
        expects a silently-omitted key."""
        vendor = _FakeVendor({})  # returns nothing for anything
        provider = CachingMarketDataProvider(vendor, source_name="yfinance", session_factory=session_factory)
        result = provider.get_daily_close_batch({"sNOPE": "NOPE"}, period="1mo")
        assert result == {}


# ─── Intraday / generic passthrough — never cache-backed ───────────────


class TestUncachedPassthrough:
    def test_intraday_quote_never_touches_the_cache(self, session_factory):
        vendor = _FakeVendorPassthroughSpy()
        provider = CachingMarketDataProvider(vendor, source_name="yfinance", session_factory=session_factory)
        assert provider.get_intraday_quote("SPY") == (123.0, vendor.ts)
        assert vendor.quote_calls == ["SPY"]

    def test_intraday_quotes_batch_never_touches_the_cache(self, session_factory):
        vendor = _FakeVendorPassthroughSpy()
        provider = CachingMarketDataProvider(vendor, source_name="yfinance", session_factory=session_factory)
        assert provider.get_intraday_quotes_batch({"sSPY": "SPY"}) == {"sSPY": 123.0}
        assert vendor.batch_calls == [{"sSPY": "SPY"}]

    def test_get_series_never_touches_the_cache(self, session_factory):
        vendor = _FakeVendorPassthroughSpy()
        provider = CachingMarketDataProvider(vendor, source_name="yfinance", session_factory=session_factory)
        out = provider.get_series("SPY", "1y", "1wk")
        assert list(out) == [1.0, 2.0]
        assert vendor.series_calls == [("SPY", "1y", "1wk")]


class _FakeVendorPassthroughSpy(MarketDataProvider):
    def __init__(self) -> None:
        self.ts = datetime(2026, 8, 19, tzinfo=UTC)
        self.quote_calls: list[str] = []
        self.batch_calls: list[dict[str, str]] = []
        self.series_calls: list[tuple[str, str, str]] = []

    def get_daily_close_batch(self, tickers: dict[str, str], period: str) -> dict[str, pd.Series]:
        raise AssertionError("not exercised in this test class")

    def get_intraday_quote(self, ticker: str) -> tuple[float, datetime] | None:
        self.quote_calls.append(ticker)
        return (123.0, self.ts)

    def get_intraday_quotes_batch(self, tickers: dict[str, str]) -> dict[str, float]:
        self.batch_calls.append(dict(tickers))
        return dict.fromkeys(tickers, 123.0)

    def get_series(self, ticker: str, period: str, interval: str) -> pd.Series:
        self.series_calls.append((ticker, period, interval))
        return pd.Series([1.0, 2.0])


# ─── YFinanceProvider — the default vendor's own boundary ──────────────


class TestYFinanceProviderBoundary:
    def test_get_daily_close_batch_import_error_returns_empty(self):
        import sys

        provider = YFinanceProvider()
        with patch.dict(sys.modules, {"yfinance": None}):
            assert provider.get_daily_close_batch({"sSPY": "SPY"}, period="1mo") == {}

    def test_get_intraday_quotes_batch_import_error_returns_empty(self):
        import sys

        provider = YFinanceProvider()
        with patch.dict(sys.modules, {"yfinance": None}):
            assert provider.get_intraday_quotes_batch({"sSPY": "SPY"}) == {}
