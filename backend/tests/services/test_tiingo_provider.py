"""Hermetic tests for ``TiingoProvider`` (#1218 Part 1 — yfinance replacement).

Mocks httpx at the transport boundary (``httpx.MockTransport`` + client
injection) with realistic canned Tiingo JSON per endpoint family — no
network, no real API key required for any test except the explicit
missing-key cases, which assert the loud-failure contract itself.

Covers (per the issue spec): happy path per family (equity/crypto/FX),
symbol routing, unsupported-symbol loud failure, missing-key loud failure,
adjustment-semantics parity (fixture deliberately distinguishes adjusted
from raw), empty-response handling (loud, not empty-frame-as-success), and
output-shape parity with ``YFinanceProvider``'s contract.

Mutation-check evidence (both the unsupported-symbol guard and the
adjustment-field mapping) is recorded in the PR body, not here — per this
repo's convention (see ``test_generation_market_data_seam.py``'s header).
"""

from __future__ import annotations

import httpx
import pandas as pd
import pytest
from archimedes.services.market_data_provider import (
    TiingoAPIKeyMissingError,
    TiingoEmptyResponseError,
    TiingoProvider,
    TiingoProviderError,
    TiingoUnsupportedSymbolError,
    _classify_tiingo_ticker,
    get_provider,
)

# ─── Canned Tiingo fixtures ──────────────────────────────────────────────
#
# Equity adjClose/adjOpen/... are deliberately DIFFERENT from close/open/...
# (as if a split/dividend occurred between the raw print and today) so the
# adjustment-semantics test can distinguish "mapped from adjClose" from
# "mapped from close" — a mapping bug (adjClose -> close) changes the
# asserted values, it doesn't just leave them coincidentally equal.

EQUITY_FIXTURE = [
    {
        "date": "2024-01-02T00:00:00.000Z",
        "close": 472.65,
        "high": 473.67,
        "low": 470.49,
        "open": 472.16,
        "volume": 123456,
        "adjClose": 468.20,
        "adjHigh": 469.21,
        "adjLow": 466.05,
        "adjOpen": 467.72,
        "adjVolume": 123400,
        "divCash": 0.0,
        "splitFactor": 1.0,
    },
    {
        "date": "2024-01-03T00:00:00.000Z",
        "close": 468.79,
        "high": 470.16,
        "low": 467.53,
        "open": 468.35,
        "volume": 234567,
        "adjClose": 464.37,
        "adjHigh": 465.72,
        "adjLow": 463.11,
        "adjOpen": 463.93,
        "adjVolume": 234500,
        "divCash": 0.0,
        "splitFactor": 1.0,
    },
]

CRYPTO_FIXTURE = [
    {
        "ticker": "btcusd",
        "baseCurrency": "btc",
        "quoteCurrency": "usd",
        "priceData": [
            {
                "date": "2024-01-02T00:00:00+00:00",
                "open": 44200.5,
                "high": 45200.75,
                "low": 44000.0,
                "close": 45000.25,
                "volume": 12345.6,
                "volumeNotional": 555555555.5,
                "tradesDone": 10000,
            },
            {
                "date": "2024-01-03T00:00:00+00:00",
                "open": 45000.25,
                "high": 46000.0,
                "low": 44500.0,
                "close": 45700.1,
                "volume": 15000.2,
                "volumeNotional": 600000000.0,
                "tradesDone": 12000,
            },
        ],
    }
]

FX_FIXTURE = [
    {
        "ticker": "eurusd",
        "date": "2024-01-02T00:00:00+00:00",
        "open": 1.1050,
        "high": 1.1090,
        "low": 1.1020,
        "close": 1.1075,
    },
    {
        "ticker": "eurusd",
        "date": "2024-01-03T00:00:00+00:00",
        "open": 1.1075,
        "high": 1.1100,
        "low": 1.1040,
        "close": 1.1060,
    },
]


def _mock_client(routes: dict[str, object], *, capture: list[httpx.Request] | None = None) -> httpx.Client:
    """A hermetic httpx.Client wired to a MockTransport — the "transport
    boundary" mock this repo's testing conventions call for. ``routes`` maps
    an EXACT request path (``request.url.path``) to a JSON body returned
    with HTTP 200; anything unmatched is a 404 (so a routing bug shows up as
    a loud HTTP error, not a silently-wrong endpoint)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture.append(request)
        if request.url.path in routes:
            return httpx.Response(200, json=routes[request.url.path], request=request)
        return httpx.Response(404, json={"detail": f"no mock route for {request.url.path}"}, request=request)

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.tiingo.com")


@pytest.fixture(autouse=True)
def _tiingo_key(monkeypatch):
    """Every test gets a key by default; the missing-key tests explicitly
    delete it after construction (see TestMissingApiKey)."""
    monkeypatch.setenv("TIINGO_API_KEY", "test-key-do-not-log-me")


@pytest.fixture
def session_factory(tmp_path):
    """An isolated SQLite engine/session factory with ``asset_daily_bars``
    created — same construction as ``test_market_data_provider.py``'s
    fixture of the same name, independent of the app's module-level engine.
    Used only by the cold/warm cache round-trip in ``TestShapeParity``."""
    from archimedes.db import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{tmp_path / 'tiingo_market_data.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


# ─── Symbol classification (ticker-shape heuristic) ─────────────────────


class TestTickerClassification:
    def test_equity_ticker(self):
        assert _classify_tiingo_ticker("SPY") == "equity"

    def test_crypto_ticker(self):
        assert _classify_tiingo_ticker("BTC-USD") == "crypto"

    def test_fx_ticker(self):
        assert _classify_tiingo_ticker("EURUSD=X") == "fx"

    def test_index_ticker_is_unsupported(self):
        with pytest.raises(TiingoUnsupportedSymbolError, match=r"\^GSPC"):
            _classify_tiingo_ticker("^GSPC")

    def test_future_ticker_is_unsupported(self):
        with pytest.raises(TiingoUnsupportedSymbolError, match="GC=F"):
            _classify_tiingo_ticker("GC=F")

    @pytest.mark.parametrize("ticker", ["CL=F", "^N225", "^VIX", "SI=F", "HG=F", "PA=F", "PL=F"])
    def test_unsupported_shape_examples(self, ticker):
        """The task's illustrative unsupported set (CL=F, ^N225) plus the
        5 real metal_spot SSOT tickers — all must fail loud."""
        with pytest.raises(TiingoUnsupportedSymbolError):
            _classify_tiingo_ticker(ticker)


# ─── Happy path per endpoint family ──────────────────────────────────────


class TestHappyPathPerFamily:
    def test_equity_daily_ohlcv(self):
        client = _mock_client({"/tiingo/daily/SPY/prices": EQUITY_FIXTURE})
        provider = TiingoProvider(client=client)

        result = provider.get_daily_ohlcv("SPY", "2024-01-02", "2024-01-03")

        assert list(result.columns) == ["Open", "High", "Low", "Close", "Volume"]
        assert len(result) == 2
        assert isinstance(result.index, pd.DatetimeIndex)

    def test_crypto_daily_ohlcv(self):
        client = _mock_client({"/tiingo/crypto/prices": CRYPTO_FIXTURE})
        provider = TiingoProvider(client=client)

        result = provider.get_daily_ohlcv("BTC-USD", "2024-01-02", "2024-01-03")

        assert list(result.columns) == ["Open", "High", "Low", "Close", "Volume"]
        assert result["Close"].tolist() == [45000.25, 45700.1]
        assert result["Volume"].tolist() == [12345.6, 15000.2]

    def test_fx_daily_ohlcv(self):
        client = _mock_client({"/tiingo/fx/eurusd/prices": FX_FIXTURE})
        provider = TiingoProvider(client=client)

        result = provider.get_daily_ohlcv("EURUSD=X", "2024-01-02", "2024-01-03")

        assert list(result.columns) == ["Open", "High", "Low", "Close", "Volume"]
        assert result["Close"].tolist() == [1.1075, 1.1060]
        # FX has no vendor volume on either side (yfinance's EURUSD=X is also
        # all-zero) — documented deviation, not a bug.
        assert result["Volume"].tolist() == [0.0, 0.0]


# ─── Symbol routing: correct path + ticker translation per family ──────


class TestSymbolRouting:
    def test_equity_ticker_passed_through_unchanged(self):
        captured: list[httpx.Request] = []
        client = _mock_client({"/tiingo/daily/AGG/prices": EQUITY_FIXTURE}, capture=captured)
        TiingoProvider(client=client).get_daily_ohlcv("AGG", "2024-01-02", "2024-01-03")
        assert captured[0].url.path == "/tiingo/daily/AGG/prices"

    def test_crypto_ticker_translated_to_tiingo_shape(self):
        """BTC-USD (yfinance shape) -> tickers=btcusd (Tiingo shape) —
        hyphen stripped, lowercased."""
        captured: list[httpx.Request] = []
        client = _mock_client({"/tiingo/crypto/prices": CRYPTO_FIXTURE}, capture=captured)
        TiingoProvider(client=client).get_daily_ohlcv("BTC-USD", "2024-01-02", "2024-01-03")
        assert captured[0].url.path == "/tiingo/crypto/prices"
        assert dict(captured[0].url.params)["tickers"] == "btcusd"

    def test_fx_ticker_translated_to_tiingo_shape(self):
        """EURUSD=X (yfinance shape) -> /tiingo/fx/eurusd/prices (Tiingo
        shape) — =X suffix stripped, lowercased."""
        captured: list[httpx.Request] = []
        client = _mock_client({"/tiingo/fx/eurusd/prices": FX_FIXTURE}, capture=captured)
        TiingoProvider(client=client).get_daily_ohlcv("EURUSD=X", "2024-01-02", "2024-01-03")
        assert captured[0].url.path == "/tiingo/fx/eurusd/prices"

    def test_authorization_via_header_not_query_param(self):
        """The API key must never appear in the URL (query-string params
        routinely end up in access logs / debug traces) — only in the
        Authorization header."""
        captured: list[httpx.Request] = []
        client = _mock_client({"/tiingo/daily/SPY/prices": EQUITY_FIXTURE}, capture=captured)
        TiingoProvider(client=client).get_daily_ohlcv("SPY", "2024-01-02", "2024-01-03")
        assert captured[0].headers.get("authorization") == "Token test-key-do-not-log-me"
        assert "test-key-do-not-log-me" not in str(captured[0].url)


# ─── Unsupported-symbol loud failure ──────────────────────────────────────


class TestUnsupportedSymbolLoudFailure:
    def test_get_daily_ohlcv_raises_for_index_ticker(self):
        client = _mock_client({})  # no route registered — a real HTTP call would 404
        provider = TiingoProvider(client=client)
        with pytest.raises(TiingoUnsupportedSymbolError, match=r"\^GSPC"):
            provider.get_daily_ohlcv("^GSPC", "2024-01-02", "2024-01-03")

    def test_get_daily_ohlcv_raises_for_futures_ticker(self):
        client = _mock_client({})
        provider = TiingoProvider(client=client)
        with pytest.raises(TiingoUnsupportedSymbolError, match="GC=F"):
            provider.get_daily_ohlcv("GC=F", "2024-01-02", "2024-01-03")

    def test_unsupported_symbol_never_hits_the_network(self):
        """The guard must fire BEFORE any HTTP call — an unsupported ticker
        that somehow reached the network would 404, not raise
        TiingoUnsupportedSymbolError, which would defeat the point of a
        typed, nameable failure."""
        captured: list[httpx.Request] = []
        client = _mock_client({}, capture=captured)
        provider = TiingoProvider(client=client)
        with pytest.raises(TiingoUnsupportedSymbolError):
            provider.get_daily_ohlcv("CL=F", "2024-01-02", "2024-01-03")
        assert captured == []

    def test_batch_skips_unsupported_symbol_but_keeps_others_and_logs_loud(self, caplog):
        """get_daily_close_batch's contract (inherited from the ABC, matched
        by YFinanceProvider) is per-item skip, not whole-batch failure — but
        the skip must be LOUD in the log (full symbol named) and must never
        silently substitute yfinance data (it doesn't call yfinance at all)."""
        import logging

        client = _mock_client({"/tiingo/daily/SPY/prices": EQUITY_FIXTURE})
        provider = TiingoProvider(client=client)
        with caplog.at_level(logging.ERROR):
            result = provider.get_daily_close_batch({"sXAU": "GC=F", "sSPY": "SPY"}, period="1mo")
        assert "sXAU" not in result
        assert "sSPY" in result
        assert any("GC=F" in rec.message for rec in caplog.records)


# ─── Missing API key: loud failure, never in the message ────────────────


class TestMissingApiKey:
    def test_construction_without_key_raises(self, monkeypatch):
        monkeypatch.delenv("TIINGO_API_KEY", raising=False)
        with pytest.raises(TiingoAPIKeyMissingError):
            TiingoProvider()

    def test_key_removed_after_construction_still_fails_at_call_time(self, monkeypatch):
        """The key is read fresh on every call, never cached on the
        instance — deleting it after construction must still fail the next
        call (proves 'read at call time', not just at construction)."""
        client = _mock_client({"/tiingo/daily/SPY/prices": EQUITY_FIXTURE})
        provider = TiingoProvider(client=client)  # key present here
        monkeypatch.delenv("TIINGO_API_KEY", raising=False)
        with pytest.raises(TiingoAPIKeyMissingError):
            provider.get_daily_ohlcv("SPY", "2024-01-02", "2024-01-03")

    def test_missing_key_error_never_contains_a_key_value(self, monkeypatch):
        monkeypatch.setenv("TIINGO_API_KEY", "super-secret-value-12345")
        client = _mock_client({"/tiingo/daily/SPY/prices": EQUITY_FIXTURE})
        provider = TiingoProvider(client=client)
        monkeypatch.delenv("TIINGO_API_KEY", raising=False)
        with pytest.raises(TiingoAPIKeyMissingError) as excinfo:
            provider.get_daily_ohlcv("SPY", "2024-01-02", "2024-01-03")
        assert "super-secret-value-12345" not in str(excinfo.value)

    def test_batch_propagates_missing_key_loudly_not_as_empty_result(self, monkeypatch):
        """A missing key must NOT be swallowed by the batch's per-ticker
        skip-and-log path (that would silently degrade
        MARKET_DATA_PROVIDER=tiingo-with-no-key into 'every symbol empty'
        instead of a loud, diagnosable failure)."""
        client = _mock_client({"/tiingo/daily/SPY/prices": EQUITY_FIXTURE})
        provider = TiingoProvider(client=client)
        monkeypatch.delenv("TIINGO_API_KEY", raising=False)
        with pytest.raises(TiingoAPIKeyMissingError):
            provider.get_daily_close_batch({"sSPY": "SPY"}, period="1mo")


# ─── Adjustment-semantics parity (the load-bearing correctness test) ────


class TestAdjustmentSemantics:
    def test_equity_close_is_mapped_from_adjclose_not_close(self):
        """Matches yfinance's auto_adjust=True contract (see
        market_data_provider.py:173/275 and
        archimedes_analytics_engine/market_data.py:63): the returned Close
        must be Tiingo's adjClose, NEVER the raw close. If the mapping were
        flipped (adjClose -> close), these values would be
        [472.65, 468.79] instead — a real, silent split/dividend
        discontinuity re-introduced into every backtest."""
        client = _mock_client({"/tiingo/daily/SPY/prices": EQUITY_FIXTURE})
        provider = TiingoProvider(client=client)

        result = provider.get_daily_ohlcv("SPY", "2024-01-02", "2024-01-03")

        assert result["Close"].tolist() == [468.20, 464.37]
        assert result["Open"].tolist() == [467.72, 463.93]
        assert result["High"].tolist() == [469.21, 465.72]
        assert result["Low"].tolist() == [466.05, 463.11]
        assert result["Volume"].tolist() == [123400.0, 234500.0]
        # The raw (unadjusted) values must NOT appear anywhere in the output.
        assert 472.65 not in result["Close"].tolist()
        assert 468.79 not in result["Close"].tolist()

    def test_crypto_has_no_adjustment_distinction(self):
        """Crypto carries only one field set (no corporate actions apply);
        Close must equal the raw `close` field directly."""
        client = _mock_client({"/tiingo/crypto/prices": CRYPTO_FIXTURE})
        provider = TiingoProvider(client=client)
        result = provider.get_daily_ohlcv("BTC-USD", "2024-01-02", "2024-01-03")
        assert result["Close"].tolist() == [45000.25, 45700.1]

    def test_fx_has_no_adjustment_distinction(self):
        client = _mock_client({"/tiingo/fx/eurusd/prices": FX_FIXTURE})
        provider = TiingoProvider(client=client)
        result = provider.get_daily_ohlcv("EURUSD=X", "2024-01-02", "2024-01-03")
        assert result["Close"].tolist() == [1.1075, 1.1060]


# ─── Empty-response handling: loud, not empty-frame-as-success ─────────


class TestEmptyResponseIsLoud:
    def test_equity_empty_list_raises(self):
        """Also the "never returns an empty DataFrame" regression guard:
        ``pytest.raises`` fails the test if the call returns instead of
        raising, so a regression to an empty-but-truthy-shaped sentinel is
        caught right here. (A separate try/except-shaped test asserting the
        same thing was removed as a duplicate — it exercised the identical
        route, call, and exception type.)"""
        client = _mock_client({"/tiingo/daily/NOPE/prices": []})
        provider = TiingoProvider(client=client)
        with pytest.raises(TiingoEmptyResponseError, match="NOPE"):
            provider.get_daily_ohlcv("NOPE", "2024-01-02", "2024-01-03")

    def test_crypto_empty_pricedata_raises(self):
        client = _mock_client(
            {
                "/tiingo/crypto/prices": [
                    {"ticker": "nopeusd", "baseCurrency": "nope", "quoteCurrency": "usd", "priceData": []}
                ]
            }
        )
        provider = TiingoProvider(client=client)
        with pytest.raises(TiingoEmptyResponseError):
            provider.get_daily_ohlcv("NOPE-USD", "2024-01-02", "2024-01-03")

    def test_crypto_empty_top_level_list_raises(self):
        client = _mock_client({"/tiingo/crypto/prices": []})
        provider = TiingoProvider(client=client)
        with pytest.raises(TiingoEmptyResponseError):
            provider.get_daily_ohlcv("NOPE-USD", "2024-01-02", "2024-01-03")

    def test_fx_empty_list_raises(self):
        client = _mock_client({"/tiingo/fx/nopeusd/prices": []})
        provider = TiingoProvider(client=client)
        with pytest.raises(TiingoEmptyResponseError):
            provider.get_daily_ohlcv("NOPEUSD=X", "2024-01-02", "2024-01-03")

    def test_batch_skips_empty_response_symbol_and_logs(self, caplog):
        import logging

        client = _mock_client({"/tiingo/daily/NOPE/prices": [], "/tiingo/daily/SPY/prices": EQUITY_FIXTURE})
        provider = TiingoProvider(client=client)
        with caplog.at_level(logging.ERROR):
            result = provider.get_daily_close_batch({"sNope": "NOPE", "sSPY": "SPY"}, period="1mo")
        assert "sNope" not in result
        assert "sSPY" in result


# ─── HTTP error surface ──────────────────────────────────────────────────


class TestHttpErrorSurface:
    def test_http_500_raises_tiingo_provider_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"detail": "server error"}, request=request)

        client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.tiingo.com")
        provider = TiingoProvider(client=client)
        with pytest.raises(TiingoProviderError, match="500"):
            provider.get_daily_ohlcv("SPY", "2024-01-02", "2024-01-03")

    def test_network_error_raises_tiingo_provider_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.tiingo.com")
        provider = TiingoProvider(client=client)
        with pytest.raises(TiingoProviderError):
            provider.get_daily_ohlcv("SPY", "2024-01-02", "2024-01-03")


# ─── Output-shape parity with YFinanceProvider's contract ───────────────


def _assert_ohlcv_shape_contract(frame: pd.DataFrame) -> None:
    """The shape ``TiingoProvider.get_daily_ohlcv`` output must satisfy.

    **Scope of this helper, stated precisely because an earlier revision
    overclaimed it.** This is a LOCAL contract for this file — it is NOT
    shared with, imported by, or applied to ``YFinanceProvider`` anywhere.
    ``test_market_data_provider.py``'s ``_ohlcv_frame`` is a different
    fixture that is deliberately not reused here: it builds a **tz-AWARE**
    (UTC) index (``pd.date_range(end=pd.Timestamp.now("UTC")...)``), so it
    would FAIL the tz-naive assertion below. Reusing it would mean either a
    false parity claim or silently weakening the contract.

    The tz-naive requirement is not arbitrary and not a guess about what
    yfinance returns over the network (which these hermetic tests cannot
    observe). It comes from production code on ``main``:
    ``_read_cached_ohlcv`` rebuilds a warm-cache frame as
    ``pd.to_datetime([r.trade_date ...])`` over ``date`` objects — always
    tz-naive. So a tz-aware vendor frame would make the SAME
    ``CachingMarketDataProvider.get_daily_ohlcv(...)`` call return a
    tz-aware index cold and a tz-naive one warm. That invariant is pinned
    directly by ``test_cold_and_warm_cache_frames_agree_on_shape`` below,
    against the real cache, not against a hand-built reference frame.
    """
    assert list(frame.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert isinstance(frame.index, pd.DatetimeIndex)
    assert frame.index.tz is None
    for col in frame.columns:
        assert frame[col].dtype == "float64", f"{col} dtype is {frame[col].dtype}, expected float64"


class TestShapeParity:
    """``TiingoProvider``'s own frames satisfy the ``get_daily_ohlcv`` shape
    contract for all three endpoint families, and — the part that is
    genuinely cross-component rather than self-referential — a Tiingo frame
    round-trips through the real ``CachingMarketDataProvider`` without
    changing shape.

    Deliberately NOT claimed here: byte-compatibility with
    ``YFinanceProvider``'s live output. That would need a real yfinance
    response, which these hermetic tests do not have; mocking ``yf.download``
    would only assert the tz of our own fixture back to us. See the
    cutover-follow-up list in the PR body — a recorded-response parity check
    against both vendors is the honest way to close that gap, and it is not
    in this PR."""

    def test_tiingo_equity_output_satisfies_the_contract(self):
        client = _mock_client({"/tiingo/daily/SPY/prices": EQUITY_FIXTURE})
        result = TiingoProvider(client=client).get_daily_ohlcv("SPY", "2024-01-02", "2024-01-03")
        _assert_ohlcv_shape_contract(result)

    def test_tiingo_crypto_output_satisfies_the_contract(self):
        client = _mock_client({"/tiingo/crypto/prices": CRYPTO_FIXTURE})
        result = TiingoProvider(client=client).get_daily_ohlcv("BTC-USD", "2024-01-02", "2024-01-03")
        _assert_ohlcv_shape_contract(result)

    def test_tiingo_fx_output_satisfies_the_contract(self):
        client = _mock_client({"/tiingo/fx/eurusd/prices": FX_FIXTURE})
        result = TiingoProvider(client=client).get_daily_ohlcv("EURUSD=X", "2024-01-02", "2024-01-03")
        _assert_ohlcv_shape_contract(result)

    def test_get_daily_close_batch_returns_named_float_series(self):
        client = _mock_client({"/tiingo/daily/SPY/prices": EQUITY_FIXTURE})
        result = TiingoProvider(client=client).get_daily_close_batch({"sSPY": "SPY"}, period="1mo")
        assert set(result) == {"sSPY"}
        series = result["sSPY"]
        assert series.name == "sSPY"
        assert series.dtype == "float64"
        assert isinstance(series.index, pd.DatetimeIndex)

    def test_cold_and_warm_cache_frames_agree_on_shape(self, session_factory):
        """The real cross-component check: the SAME
        ``CachingMarketDataProvider.get_daily_ohlcv`` call must return the
        same-shaped frame on a cold cache (Tiingo vendor fetch) and on a warm
        one (``_read_cached_ohlcv`` rebuilding it from ``asset_daily_bars``).

        This is where the tz-naive requirement in
        ``_assert_ohlcv_shape_contract`` actually comes from: the warm path
        is production code on ``main`` and builds its index from ``date``
        objects, so it is unconditionally tz-naive. A tz-aware Tiingo frame
        would pass every Tiingo-only test above and still make this call
        return two differently-typed indexes depending on cache state —
        exactly the kind of state-dependent shape drift a downstream
        backtrader feed would hit intermittently.
        """
        from archimedes.services.market_data_provider import CachingMarketDataProvider

        seen: list[httpx.Request] = []
        client = _mock_client({"/tiingo/daily/SPY/prices": EQUITY_FIXTURE}, capture=seen)
        provider = CachingMarketDataProvider(
            TiingoProvider(client=client), source_name="tiingo", session_factory=session_factory
        )

        cold = provider.get_daily_ohlcv("SPY", "2024-01-02", "2024-01-03")
        assert len(seen) == 1, "cold call must reach the vendor"
        warm = provider.get_daily_ohlcv("SPY", "2024-01-02", "2024-01-03")
        assert len(seen) == 1, "warm call must be served by asset_daily_bars, not a second vendor fetch"

        _assert_ohlcv_shape_contract(cold)
        _assert_ohlcv_shape_contract(warm)
        assert list(cold.columns) == list(warm.columns)
        assert cold.index.tz == warm.index.tz
        # ...and the adjusted values survived the asset_daily_bars round-trip
        # intact (same adjClose-derived numbers TestAdjustmentSemantics pins).
        assert warm["Close"].tolist() == cold["Close"].tolist() == [468.20, 464.37]
        assert warm.index.tolist() == cold.index.tolist()


# ─── Out-of-scope ABC methods: loud NotImplementedError, not silent wrong data ──


class TestOutOfScopeMethodsFailLoud:
    def test_get_intraday_quote_raises_not_implemented(self):
        client = _mock_client({})
        provider = TiingoProvider(client=client)
        with pytest.raises(NotImplementedError):
            provider.get_intraday_quote("SPY")

    def test_get_intraday_quotes_batch_raises_not_implemented(self):
        client = _mock_client({})
        provider = TiingoProvider(client=client)
        with pytest.raises(NotImplementedError):
            provider.get_intraday_quotes_batch({"sSPY": "SPY"})

    def test_get_series_raises_not_implemented(self):
        client = _mock_client({})
        provider = TiingoProvider(client=client)
        with pytest.raises(NotImplementedError):
            provider.get_series("SPY", "1y", "1d")


# ─── Provider-selection wiring (MARKET_DATA_PROVIDER=tiingo) ────────────


class TestProviderSelectionWiring:
    def test_market_data_provider_tiingo_constructs_caching_tiingo_provider(self, monkeypatch):
        from archimedes.services.market_data_provider import CachingMarketDataProvider

        monkeypatch.setenv("MARKET_DATA_PROVIDER", "tiingo")
        provider = get_provider()
        assert isinstance(provider, CachingMarketDataProvider)
        assert isinstance(provider._inner, TiingoProvider)
        assert provider._source_name == "tiingo"

    def test_market_data_provider_tiingo_without_key_fails_loud(self, monkeypatch):
        monkeypatch.setenv("MARKET_DATA_PROVIDER", "tiingo")
        monkeypatch.delenv("TIINGO_API_KEY", raising=False)
        with pytest.raises(TiingoAPIKeyMissingError):
            get_provider()
