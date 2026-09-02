"""Per-seam market-data routing (#1798).

#1798's finding: ``MARKET_DATA_PROVIDER`` was ONE global variable read by every
seam, and Tiingo serves daily bars only — so the flip that was supposed to move
backtests onto licensed data would also have pointed the live oracle push, the
paper-marks loop and the Explore history modal at three
``NotImplementedError``s. The fix routes by seam: a ``daily`` vendor
(``MARKET_DATA_DAILY_PROVIDER``) that may be Tiingo, and an ``intraday`` vendor
(``MARKET_DATA_PROVIDER``) that stays on yfinance.

What this module pins, and the mutation that turns each red:

* **The default is unchanged.** Nothing set → both seams yfinance. Mutation:
  default either seam to ``"tiingo"`` → ``TestDefaultsAreUnchanged`` red.
* **The seams are actually separate.** The pre-#1798 global flip
  (``MARKET_DATA_PROVIDER=tiingo``, daily var unset) moves daily bars to Tiingo
  and leaves intraday on yfinance. Mutation: drop the ``_VENDOR_SEAMS``
  capability check in ``provider_name`` → ``TestTheSplitHolds`` red (intraday
  resolves to tiingo again, i.e. the #1798 breakage restored).
* **The daily seam refuses intraday methods.** Mutation: make
  ``SeamRoutedProvider._route`` return ``getattr(self._inner, method)``
  unconditionally → ``TestSeamDispatch`` red.
* **Tiingo refuses intraday before any network call.** Mutation: give
  ``TiingoProvider.get_series`` a body that fetches → ``TestTiingoIsDailyOnly``
  red on the "transport never touched" assertion.
* **Every call site names its seam.** Mutation: drop ``seam=`` anywhere →
  ``TestEveryCallSiteNamesItsSeam`` red (``get_provider`` is keyword-only on
  ``seam``, so it is a TypeError at runtime too).

Hermetic: stub providers and an ``httpx.MockTransport`` that fails the test if
it is ever asked for a request. No network, no DB, no vendor import.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

import httpx
import pandas as pd
import pytest
from archimedes.services.market_data_provider import (
    DAILY_SEAM,
    INTRADAY_SEAM,
    MarketDataProvider,
    MarketDataSeamError,
    SeamRoutedProvider,
    TiingoProvider,
    YFinanceProvider,
    get_provider,
    intraday_is_delayed,
    provider_name,
)


@pytest.fixture(autouse=True)
def _clean_market_data_env(monkeypatch):
    """Neither variable leaks in from the developer's shell or another test."""
    monkeypatch.delenv("MARKET_DATA_PROVIDER", raising=False)
    monkeypatch.delenv("MARKET_DATA_DAILY_PROVIDER", raising=False)


class _StubProvider(MarketDataProvider):
    """Records what it was asked for. Every method returns a sentinel the test
    can recognize, so "the router delegated" is an assertion about a value that
    could only have come from here."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_daily_close_batch(self, tickers: dict[str, str], period: str) -> dict[str, pd.Series]:
        self.calls.append("get_daily_close_batch")
        return {k: pd.Series([1.0], name=k) for k in tickers}

    def get_daily_ohlcv(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        self.calls.append("get_daily_ohlcv")
        return pd.DataFrame({"Close": [1.0]}, index=pd.to_datetime(["2024-01-02"]))

    def get_intraday_quote(self, ticker: str) -> tuple[float, datetime] | None:
        self.calls.append("get_intraday_quote")
        return (1.0, datetime(2024, 1, 2, tzinfo=UTC))

    def get_intraday_quotes_batch(self, tickers: dict[str, str]) -> dict[str, tuple[float, datetime]]:
        self.calls.append("get_intraday_quotes_batch")
        return {k: (1.0, datetime(2024, 1, 2, tzinfo=UTC)) for k in tickers}

    def get_series(self, ticker: str, period: str, interval: str) -> pd.Series:
        self.calls.append("get_series")
        return pd.Series([1.0], index=pd.to_datetime(["2024-01-02"]))


# ─── Vendor selection, per seam ─────────────────────────────────────────


class TestDefaultsAreUnchanged:
    """The whole point of shipping this dark: with nothing set, both seams
    resolve to yfinance exactly as they did before #1798."""

    def test_both_seams_default_to_yfinance(self):
        assert provider_name(DAILY_SEAM) == "yfinance"
        assert provider_name(INTRADAY_SEAM) == "yfinance"

    def test_get_provider_defaults_to_a_yfinance_vendor_on_both_seams(self):
        for seam in (DAILY_SEAM, INTRADAY_SEAM):
            provider = get_provider(seam=seam)
            assert isinstance(provider, SeamRoutedProvider)
            assert provider.vendor_name == "yfinance"
            assert isinstance(provider._inner._inner, YFinanceProvider)

    def test_a_blank_value_is_not_a_vendor_name(self, monkeypatch):
        """``MARKET_DATA_DAILY_PROVIDER=`` (the shape an unset terraform
        variable or an empty .env line produces) falls through to
        MARKET_DATA_PROVIDER, then to yfinance — it does not select a vendor
        called ''."""
        monkeypatch.setenv("MARKET_DATA_DAILY_PROVIDER", "   ")
        monkeypatch.setenv("MARKET_DATA_PROVIDER", "tiingo")
        assert provider_name(DAILY_SEAM) == "tiingo"

        monkeypatch.delenv("MARKET_DATA_PROVIDER", raising=False)
        assert provider_name(DAILY_SEAM) == "yfinance"


class TestDailySeamSelection:
    def test_daily_var_selects_the_daily_vendor(self, monkeypatch):
        monkeypatch.setenv("MARKET_DATA_DAILY_PROVIDER", "tiingo")
        assert provider_name(DAILY_SEAM) == "tiingo"

    def test_daily_var_wins_over_the_legacy_global(self, monkeypatch):
        monkeypatch.setenv("MARKET_DATA_PROVIDER", "yfinance")
        monkeypatch.setenv("MARKET_DATA_DAILY_PROVIDER", "tiingo")
        assert provider_name(DAILY_SEAM) == "tiingo"
        assert provider_name(INTRADAY_SEAM) == "yfinance"

    def test_case_and_whitespace_insensitive(self, monkeypatch):
        monkeypatch.setenv("MARKET_DATA_DAILY_PROVIDER", "  TiinGo ")
        assert provider_name(DAILY_SEAM) == "tiingo"

    def test_unknown_daily_vendor_falls_back_to_yfinance(self, monkeypatch, caplog):
        import logging

        monkeypatch.setenv("MARKET_DATA_DAILY_PROVIDER", "some_unreleased_vendor")
        with caplog.at_level(logging.WARNING):
            assert provider_name(DAILY_SEAM) == "yfinance"
        assert any("some_unreleased_vendor" in rec.message for rec in caplog.records)


class TestTheSplitHolds:
    """The proof #1798 asked for: the pre-existing global flip must no longer
    be able to break intraday."""

    def test_global_flip_to_tiingo_moves_daily_and_leaves_intraday_alone(self, monkeypatch, caplog):
        import logging

        monkeypatch.setenv("MARKET_DATA_PROVIDER", "tiingo")  # daily var deliberately unset
        with caplog.at_level(logging.WARNING):
            assert provider_name(DAILY_SEAM) == "tiingo", "back-compat: the legacy var still moves daily bars"
            assert provider_name(INTRADAY_SEAM) == "yfinance", "#1798: the flip must not reach intraday"
        # Substituted, never silently: the log names the vendor and the seam.
        assert any("tiingo" in rec.message.lower() and "intraday" in rec.message for rec in caplog.records)

    def test_intraday_provider_under_a_global_tiingo_flip_is_a_working_yfinance_one(self, monkeypatch):
        """The failure this replaces: ``get_provider()`` used to build a
        ``TiingoProvider`` here — raising ``TiingoAPIKeyMissingError`` with no
        token, or ``NotImplementedError`` on the first quote with one."""
        monkeypatch.setenv("MARKET_DATA_PROVIDER", "tiingo")
        monkeypatch.delenv("TIINGO_API_TOKEN", raising=False)
        monkeypatch.delenv("TIINGO_API_KEY", raising=False)

        provider = get_provider(seam=INTRADAY_SEAM)

        assert provider.vendor_name == "yfinance"
        assert isinstance(provider._inner._inner, YFinanceProvider)

    def test_the_delayed_intraday_declaration_follows_the_intraday_seam(self, monkeypatch):
        monkeypatch.setenv("MARKET_DATA_PROVIDER", "tiingo")
        # yfinance's feed is declared delayed; a tiingo-shaped answer here would
        # mean intraday_is_delayed() had gone looking at the wrong seam.
        assert intraday_is_delayed() is True

    def test_marks_and_pushes_stamp_the_vendor_that_actually_served_them(self, monkeypatch):
        """Provenance stays true under the substitution: ``paper_marks`` and
        ``oracle_updater`` stamp ``provider_name("intraday")``, which is the
        vendor that really answered, not the name in the env var."""
        monkeypatch.setenv("MARKET_DATA_PROVIDER", "tiingo")
        assert provider_name(INTRADAY_SEAM) == get_provider(seam=INTRADAY_SEAM).vendor_name


class TestUnknownSeam:
    def test_an_unknown_seam_name_is_a_loud_error(self):
        with pytest.raises(MarketDataSeamError) as exc:
            provider_name("weekly")
        assert "weekly" in str(exc.value)

    def test_get_provider_rejects_an_unknown_seam(self):
        with pytest.raises(MarketDataSeamError):
            get_provider(seam="weekly")

    def test_seam_is_keyword_only(self):
        with pytest.raises(TypeError):
            get_provider("daily")  # type: ignore[misc]


# ─── Method dispatch ────────────────────────────────────────────────────


class TestSeamDispatch:
    def test_daily_seam_serves_the_two_daily_bar_methods(self):
        stub = _StubProvider()
        routed = SeamRoutedProvider(stub, seam=DAILY_SEAM, vendor_name="tiingo")

        routed.get_daily_close_batch({"sSPY": "SPY"}, period="1y")
        routed.get_daily_ohlcv("SPY", "2024-01-02", "2024-01-05")

        assert stub.calls == ["get_daily_close_batch", "get_daily_ohlcv"]

    @pytest.mark.parametrize(
        "call",
        [
            lambda p: p.get_intraday_quote("SPY"),
            lambda p: p.get_intraday_quotes_batch({"sSPY": "SPY"}),
            lambda p: p.get_series("SPY", "1y", "1h"),
        ],
        ids=["get_intraday_quote", "get_intraday_quotes_batch", "get_series"],
    )
    def test_daily_seam_refuses_intraday_methods_without_touching_the_vendor(self, call):
        stub = _StubProvider()
        routed = SeamRoutedProvider(stub, seam=DAILY_SEAM, vendor_name="tiingo")

        with pytest.raises(MarketDataSeamError) as exc:
            call(routed)

        assert stub.calls == [], "the refusal must happen before the vendor is asked"
        assert "intraday" in str(exc.value), "the error must name the seam that CAN serve it"

    def test_the_refusal_does_not_depend_on_which_vendor_is_configured(self, monkeypatch):
        """Deterministic by design: the daily seam refuses ``get_series`` even
        when its vendor is yfinance, which could serve it. Otherwise the seam's
        shape would change under the operator's flag and a call site could pass
        review, ship, and only fail on the day of the flip."""
        provider = get_provider(seam=DAILY_SEAM)
        assert provider.vendor_name == "yfinance"
        with pytest.raises(MarketDataSeamError):
            provider.get_series("SPY", "1y", "1h")

    def test_intraday_seam_serves_daily_bars_too(self):
        """One run, one vendor (the ADR's rule, unchanged): the oracle snapshot
        reads ^VIX intraday and ^GSPC daily in a single run, so the intraday
        seam must answer both from the same vendor."""
        stub = _StubProvider()
        routed = SeamRoutedProvider(stub, seam=INTRADAY_SEAM, vendor_name="yfinance")

        routed.get_intraday_quote("^VIX")
        routed.get_daily_close_batch({"^GSPC": "^GSPC"}, period="1y")
        routed.get_series("SPY", "1y", "1h")
        routed.get_intraday_quotes_batch({"sSPY": "SPY"})
        routed.get_daily_ohlcv("SPY", "2024-01-02", "2024-01-05")

        assert stub.calls == [
            "get_intraday_quote",
            "get_daily_close_batch",
            "get_series",
            "get_intraday_quotes_batch",
            "get_daily_ohlcv",
        ]

    def test_the_router_returns_the_vendor_s_own_value_unaltered(self):
        stub = _StubProvider()
        routed = SeamRoutedProvider(stub, seam=INTRADAY_SEAM, vendor_name="yfinance")
        assert routed.get_intraday_quote("SPY") == (1.0, datetime(2024, 1, 2, tzinfo=UTC))


# ─── The vendor's own limit, independently ──────────────────────────────


class TestTiingoIsDailyOnly:
    """Belt and braces: even if ``_VENDOR_SEAMS`` were edited to put Tiingo on
    the intraday seam, the adapter itself refuses — and refuses BEFORE it opens
    a socket, so a mis-wired deploy costs an exception, not a vendor bill or a
    hung request."""

    @staticmethod
    def _no_network_client(requests: list[httpx.Request]) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must never run
            requests.append(request)
            raise AssertionError(f"network touched: {request.method} {request.url}")

        return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.tiingo.com")

    @pytest.mark.parametrize(
        "call",
        [
            lambda p: p.get_intraday_quote("SPY"),
            lambda p: p.get_intraday_quotes_batch({"sSPY": "SPY"}),
            lambda p: p.get_series("SPY", "1y", "1h"),
        ],
        ids=["get_intraday_quote", "get_intraday_quotes_batch", "get_series"],
    )
    def test_intraday_methods_raise_before_any_request(self, monkeypatch, call):
        monkeypatch.setenv("TIINGO_API_TOKEN", "test-token-not-a-real-one")
        requests: list[httpx.Request] = []
        provider = TiingoProvider(client=self._no_network_client(requests))

        with pytest.raises(NotImplementedError):
            call(provider)

        assert requests == []


# ─── Call sites ─────────────────────────────────────────────────────────


class TestEveryCallSiteNamesItsSeam:
    """#1798's rule: no implicit default at a call site. ``get_provider`` is
    keyword-only on ``seam``, so a dropped argument is a TypeError — but WHICH
    seam a call site asks for is a decision, and this table is where it is
    reviewed. Source-level on purpose: several of these functions need a live
    DB session or an event loop to reach, and the question here is which
    vendor a feature is wired to, which the source answers exactly."""

    @pytest.mark.parametrize(
        ("import_path", "func_name", "seam"),
        [
            # Daily bars — the seam Tiingo may serve.
            ("archimedes.services.strategy_signal_evaluator", "_fetch_price_history", "daily"),
            ("archimedes.services.strategy_signal_evaluator", "_fetch_price_histories", "daily"),
            ("archimedes.services.fusion_market_data", "_fetch_one", "daily"),
            ("archimedes.services.portfolio_backtester", "_fetch_price_panel", "daily"),
            # Live/interactive — stays on yfinance.
            ("archimedes.services.paper_marks", "mark_all", "intraday"),
            ("archimedes.services.asset_market_service", "_fetch_yfinance_series", "intraday"),
        ],
    )
    def test_function_asks_for_its_seam(self, import_path, func_name, seam):
        import importlib

        module = importlib.import_module(import_path)
        source = inspect.getsource(getattr(module, func_name))
        assert f'get_provider(seam="{seam}")' in source

    @pytest.mark.parametrize(
        ("method_name", "seam"),
        [
            ("_fetch_yfinance", "intraday"),
            ("_fetch_yfinance_single", "intraday"),
            ("_fetch_crypto_provider", "intraday"),
            # ^GSPC daily bars, on the INTRADAY seam deliberately: one run,
            # one vendor (fetch_market_snapshot reads ^VIX alongside it).
            ("_fetch_sp500_moving_averages", "intraday"),
        ],
    )
    def test_oracle_updater_is_entirely_on_the_intraday_seam(self, method_name, seam):
        from archimedes.chain.oracle_updater import OracleUpdater

        source = inspect.getsource(getattr(OracleUpdater, method_name))
        assert f'get_provider(seam="{seam}")' in source

    def test_no_backend_call_site_asks_without_a_seam(self):
        """Structural: a ``get_provider(...)`` call anywhere in backend source
        that names no seam would be a call site picking a vendor by accident.

        Parsed with ``ast`` rather than grepped, so the many prose mentions of
        ``get_provider()`` in this package's docstrings cannot make it pass or
        fail for the wrong reason."""
        import ast
        import pathlib

        # The ``archimedes`` package itself — runtime source, not tests (which
        # legitimately call it wrong on purpose, e.g. test_seam_is_keyword_only).
        root = pathlib.Path(inspect.getsourcefile(get_provider)).parents[1]
        offenders: list[str] = []
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                if name != "get_provider":
                    continue
                if not any(kw.arg == "seam" for kw in node.keywords):
                    offenders.append(f"{path.relative_to(root)}:{node.lineno}")
        assert offenders == []


class TestCallSiteSeamsAtRuntime:
    """Two of the source-level rows above, re-checked by actually calling the
    function with ``get_provider`` monkeypatched — so the table cannot pass on
    a string that no longer runs."""

    def test_fusion_fetch_one_asks_the_daily_seam(self, monkeypatch):
        import archimedes.services.fusion_market_data as fmd
        from archimedes.services import market_data_provider as mdp

        seen: list[str] = []
        stub = _StubProvider()
        monkeypatch.setattr(mdp, "get_provider", lambda *, seam: (seen.append(seam), stub)[1])

        fmd._fetch_one("SPY", "2024-01-02", "2024-01-05")

        assert seen == ["daily"]
        assert stub.calls == ["get_daily_ohlcv"]

    def test_oracle_sp500_moving_averages_asks_the_intraday_seam(self, monkeypatch):
        from archimedes.chain.oracle_updater import OracleUpdater
        from archimedes.services import market_data_provider as mdp

        seen: list[str] = []

        class _Closes(_StubProvider):
            def get_daily_close_batch(self, tickers, period):
                self.calls.append("get_daily_close_batch")
                return {"^GSPC": pd.Series(range(250), dtype=float)}

        stub = _Closes()
        monkeypatch.setattr(mdp, "get_provider", lambda *, seam: (seen.append(seam), stub)[1])

        out = OracleUpdater._fetch_sp500_moving_averages(object())

        assert seen == ["intraday"]
        assert set(out) == {"ma50", "ma200"}
