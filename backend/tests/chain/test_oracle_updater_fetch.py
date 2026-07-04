"""OracleUpdater price-fetch + snapshot coverage (#738 Tier-A).

Target: backend/archimedes/chain/oracle_updater.py
Complements test_oracle_updater.py (which covers the sanity-bound / push-refusal
logic) by exercising the *fetch* surface: yfinance equity prices, CoinGecko
crypto prices, the market snapshot (VIX + S&P MAs), the cache, the Circle
public-key fetch, and the no-credentials push early-return.

Hermetic: yfinance is replaced with a fake module via sys.modules; the aiohttp
CoinGecko/Circle boundary is mocked. No network, no Arc RPC, no Circle.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from archimedes.chain.oracle_updater import OracleUpdater
from archimedes.models.asset import AssetPrice


@pytest.fixture
def updater() -> OracleUpdater:
    return OracleUpdater()


def _fake_yfinance_multi(prices_by_ticker: dict[str, float]):
    """A fake `yfinance` module whose download() returns a multi-ticker frame.

    The real code reads `data["Close"]` and indexes `.columns` per ticker, then
    takes `.dropna().iloc[-1]`. We mimic the pandas surface the code touches.
    """
    import pandas as pd

    close = pd.DataFrame({t: [p] for t, p in prices_by_ticker.items()})
    frame = MagicMock()
    frame.empty = False
    frame.__getitem__ = MagicMock(side_effect=lambda k: close if k == "Close" else None)
    fake = MagicMock()
    fake.download = MagicMock(return_value=frame)
    return fake


class TestFetchYfinance:
    def test_parses_multi_ticker_close(self, updater):
        fake_yf = _fake_yfinance_multi({"TSLA": 250.0, "SPY": 500.0})
        with patch.dict(sys.modules, {"yfinance": fake_yf}):
            results = updater._fetch_yfinance({"sTSLA": "TSLA", "sSPY": "SPY"}, datetime.now(UTC))
        by_symbol = {r.symbol: r.price_usd for r in results}
        assert by_symbol["sTSLA"] == 250.0
        assert by_symbol["sSPY"] == 500.0

    def test_import_error_returns_empty(self, updater):
        # Force the `import yfinance as yf` inside to raise (no mock object needed —
        # mapping the module to None makes the import statement itself raise).
        with patch.dict(sys.modules, {"yfinance": None}):
            results = updater._fetch_yfinance({"sTSLA": "TSLA"}, datetime.now(UTC))
        assert results == []


class TestFetchPrices:
    async def test_combines_equity_and_crypto(self, updater):
        now = datetime.now(UTC)
        equities = [AssetPrice(symbol="sTSLA", price_usd=250.0, timestamp=now, source="yfinance")]
        crypto = [AssetPrice(symbol="sBTC", price_usd=65000.0, timestamp=now, source="coingecko")]
        with (
            patch.object(updater, "_fetch_yfinance", return_value=equities),
            patch.object(updater, "_fetch_crypto", AsyncMock(return_value=crypto)),
        ):
            prices = await updater.fetch_prices()
        symbols = {p.symbol for p in prices}
        assert {"sTSLA", "sBTC"} <= symbols
        # fetch_prices populates the per-instance cache.
        assert updater.get_cached_price("sTSLA").price_usd == 250.0
        assert updater.get_cached_price("sBTC").price_usd == 65000.0

    def test_get_cached_price_miss_returns_none(self, updater):
        assert updater.get_cached_price("sNOPE") is None


class TestFetchCrypto:
    async def test_parses_coingecko_response(self, updater):
        resp = MagicMock(status=200)
        resp.json = AsyncMock(return_value={"bitcoin": {"usd": 64000.0}})
        get_cm = MagicMock()
        get_cm.__aenter__ = AsyncMock(return_value=resp)
        get_cm.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.get = MagicMock(return_value=get_cm)
        session_cm = MagicMock()
        session_cm.__aenter__ = AsyncMock(return_value=session)
        session_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("archimedes.chain.oracle_updater.aiohttp.ClientSession", return_value=session_cm):
            results = await updater._fetch_crypto(datetime.now(UTC))
        by_symbol = {r.symbol: r.price_usd for r in results}
        assert by_symbol["sBTC"] == 64000.0

    async def test_coingecko_error_is_swallowed(self, updater):
        # A non-200 / raising session must not crash — returns [] for that symbol.
        session_cm = MagicMock()
        session_cm.__aenter__ = AsyncMock(side_effect=RuntimeError("network down"))
        session_cm.__aexit__ = AsyncMock(return_value=False)
        with patch("archimedes.chain.oracle_updater.aiohttp.ClientSession", return_value=session_cm):
            results = await updater._fetch_crypto(datetime.now(UTC))
        assert results == []


class TestFetchMarketSnapshot:
    async def test_assembles_prices_vix_and_mas(self, updater):
        now = datetime.now(UTC)
        equities = [AssetPrice(symbol="sSPY", price_usd=500.0, timestamp=now, source="yfinance")]
        with (
            patch.object(updater, "fetch_prices", AsyncMock(return_value=equities)),
            patch.object(updater, "_fetch_yfinance_single", AsyncMock(return_value=(14.5, now))),
            patch.object(updater, "_fetch_sp500_moving_averages", return_value={"ma50": 4900.0, "ma200": 4800.0}),
        ):
            snap = await updater.fetch_market_snapshot()
        assert snap.vix == 14.5
        assert snap.sp500_ma50 == 4900.0
        assert snap.sp500_ma200 == 4800.0
        assert snap.prices["sSPY"] == 500.0
        # VIX + MAs present → the snapshot reports it carries regime signals.
        assert snap.has_regime_signals is True

    async def test_vix_none_when_yfinance_single_fails(self, updater):
        # _fetch_yfinance_single returns a bare None on failure (not a 2-tuple) —
        # fetch_market_snapshot must unpack that shape safely.
        now = datetime.now(UTC)
        equities = [AssetPrice(symbol="sSPY", price_usd=500.0, timestamp=now, source="yfinance")]
        with (
            patch.object(updater, "fetch_prices", AsyncMock(return_value=equities)),
            patch.object(updater, "_fetch_yfinance_single", AsyncMock(return_value=None)),
            patch.object(updater, "_fetch_sp500_moving_averages", return_value={}),
        ):
            snap = await updater.fetch_market_snapshot()
        assert snap.vix is None


class TestFetchYfinanceSingle:
    async def test_returns_last_close_and_bar_timestamp(self, updater):
        import pandas as pd

        idx = pd.date_range("2026-06-30 00:00", periods=2, freq="min", tz="UTC")
        close = pd.DataFrame({"^VIX": [13.2, 14.0]}, index=idx)
        frame = MagicMock()
        frame.empty = False
        frame.__getitem__ = MagicMock(side_effect=lambda k: close if k == "Close" else None)
        frame.index = idx
        fake_yf = MagicMock()
        fake_yf.download = MagicMock(return_value=frame)
        with patch.dict(sys.modules, {"yfinance": fake_yf}):
            result = await updater._fetch_yfinance_single("^VIX")
        assert result is not None
        price, bar_ts = result
        assert price == 14.0
        assert bar_ts == idx[-1].to_pydatetime()
        assert bar_ts.tzinfo is not None

    async def test_naive_bar_timestamp_normalized_to_utc(self, updater):
        import pandas as pd

        idx = pd.date_range("2026-06-30 00:00", periods=2, freq="min")  # tz-naive
        close = pd.DataFrame({"^VIX": [13.2, 14.0]}, index=idx)
        frame = MagicMock()
        frame.empty = False
        frame.__getitem__ = MagicMock(side_effect=lambda k: close if k == "Close" else None)
        frame.index = idx
        fake_yf = MagicMock()
        fake_yf.download = MagicMock(return_value=frame)
        with patch.dict(sys.modules, {"yfinance": fake_yf}):
            result = await updater._fetch_yfinance_single("^VIX")
        assert result is not None
        _, bar_ts = result
        assert bar_ts.tzinfo is not None
        assert bar_ts.utcoffset().total_seconds() == 0

    async def test_swallows_error_returns_none(self, updater):
        fake_yf = MagicMock()
        fake_yf.download = MagicMock(side_effect=RuntimeError("yf boom"))
        with patch.dict(sys.modules, {"yfinance": fake_yf}):
            assert await updater._fetch_yfinance_single("^VIX") is None


class TestSp500MovingAverages:
    def test_computes_rolling_means(self, updater):
        import pandas as pd

        hist = pd.DataFrame({"Close": list(range(1, 301))})
        ticker = MagicMock()
        ticker.history = MagicMock(return_value=hist)
        fake_yf = MagicMock()
        fake_yf.Ticker = MagicMock(return_value=ticker)
        with patch.dict(sys.modules, {"yfinance": fake_yf}):
            mas = updater._fetch_sp500_moving_averages()
        # rolling(50)/(200) means over 1..300 → finite numbers, ma200 < ma50.
        assert mas["ma50"] > mas["ma200"] > 0

    def test_empty_history_returns_empty_dict(self, updater):
        import pandas as pd

        ticker = MagicMock()
        ticker.history = MagicMock(return_value=pd.DataFrame())
        fake_yf = MagicMock()
        fake_yf.Ticker = MagicMock(return_value=ticker)
        with patch.dict(sys.modules, {"yfinance": fake_yf}):
            assert updater._fetch_sp500_moving_averages() == {}


class TestGetCirclePublicKey:
    async def test_fetches_and_caches(self, monkeypatch, updater):
        monkeypatch.setenv("CIRCLE_API_KEY", "key")
        upd = OracleUpdater()
        resp = MagicMock(status=200)
        resp.json = AsyncMock(return_value={"data": {"publicKey": "PEM"}})
        get_cm = MagicMock()
        get_cm.__aenter__ = AsyncMock(return_value=resp)
        get_cm.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.get = MagicMock(return_value=get_cm)
        key = await upd._get_circle_public_key(session)
        assert key == "PEM"
        # Cached → second call doesn't re-fetch.
        session.get.reset_mock()
        assert await upd._get_circle_public_key(session) == "PEM"
        session.get.assert_not_called()

    async def test_non_200_returns_none(self, updater):
        resp = MagicMock(status=500)
        get_cm = MagicMock()
        get_cm.__aenter__ = AsyncMock(return_value=resp)
        get_cm.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.get = MagicMock(return_value=get_cm)
        assert await updater._get_circle_public_key(session) is None


class TestPushNoCredentials:
    async def test_returns_none_without_creds(self, monkeypatch):
        for var in ("CIRCLE_API_KEY", "CIRCLE_ENTITY_SECRET", "WALLET_ID"):
            monkeypatch.delenv(var, raising=False)
        upd = OracleUpdater()
        price = AssetPrice(symbol="sTSLA", price_usd=100.0, timestamp=datetime.now(UTC), source="yfinance")
        # No creds → early return None, nothing submitted.
        assert await upd.push_prices_on_chain([price]) is None

    async def test_push_aborts_when_public_key_unavailable(self, monkeypatch):
        for var, val in (("CIRCLE_API_KEY", "k"), ("CIRCLE_ENTITY_SECRET", "ab" * 32), ("WALLET_ID", "w")):
            monkeypatch.setenv(var, val)
        upd = OracleUpdater()
        session = MagicMock()
        session_cm = MagicMock()
        session_cm.__aenter__ = AsyncMock(return_value=session)
        session_cm.__aexit__ = AsyncMock(return_value=False)
        price = AssetPrice(symbol="sTSLA", price_usd=100.0, timestamp=datetime.now(UTC), source="yfinance")
        with (
            patch("archimedes.chain.oracle_updater.aiohttp.ClientSession", return_value=session_cm),
            patch.object(upd, "_get_circle_public_key", AsyncMock(return_value=None)),
        ):
            assert await upd.push_prices_on_chain([price]) is None


class TestModuleConstants:
    def test_symbol_maps_present(self):
        from archimedes.chain.oracle_updater import CRYPTO_MAP, YFINANCE_MAP

        assert YFINANCE_MAP["sTSLA"] == "TSLA"
        assert CRYPTO_MAP["sBTC"] == "bitcoin"

    def test_circle_constants(self):
        from archimedes.chain.oracle_updater import CIRCLE_API_BASE, CIRCLE_BLOCKCHAIN

        assert CIRCLE_BLOCKCHAIN == "ARC-TESTNET"
        assert CIRCLE_API_BASE.startswith("https://")
