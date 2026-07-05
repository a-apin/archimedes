"""Tests for asset_market_service — on-chain oracle reads + stale guards.

Per issue #168: the service reads on-chain PriceOracle via chain_client as
the primary price source, falls back to yfinance for change/vol, and marks
stale when oracle data is >5 min old.

Universe alignment (#759 follow-up to PR #842): the listing iterates the
deploy-eligible SSOT universe (``archimedes.universe.ON_CHAIN_SYNTHS`` — the
same set the Generate picker uses), NOT the legacy ``DEFAULT_SCAN_UNIVERSE``
scan subset; card names come from the SSOT display name; and a cold /
budget-exhausted fetch degrades to an honest partial result (all universe
symbols listed, unfetched ones as ``price_source="none"``) instead of a
timeout. All tests are hermetic — yfinance / chain boundaries are mocked.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from archimedes.services.asset_market_service import (
    AssetMarketService,
    _explore_universe,
    _pct_change,
    _realized_vol_annual,
    _ssot_display_name,
)

# ── Unit tests for stat math ──────────────────────────────────────────────


class TestStatMath:
    def test_pct_change_basic(self):
        assert _pct_change([100, 105], 1) == pytest.approx(5.0)

    def test_pct_change_insufficient_data(self):
        assert _pct_change([100], 1) is None

    def test_pct_change_zero_start(self):
        assert _pct_change([0, 105], 1) is None

    def test_realized_vol_basic(self):
        # Constant prices → zero vol
        prices = [100.0] * 32
        assert _realized_vol_annual(prices, 30) == pytest.approx(0.0)

    def test_realized_vol_insufficient(self):
        assert _realized_vol_annual([100, 101], 30) is None


# ── Oracle read tests (mocked chain_client) ────────────────────────────────


@pytest.fixture
def mock_chain_client():
    """Mock chain_client with oracle and synth addresses + ABI."""
    from pathlib import Path

    mock_settings = MagicMock()
    mock_settings.oracle_addresses = {
        "sSPY": "0xd8161a8eeab7c7100e2863abe3d5f346b5ff9e52",
        "sBTC": "0x6cc5f621c4e3b46152e69e5c9873689cbb4a85e8",
    }
    mock_settings.synth_addresses = {
        "sSPY": "0x6fea38dedea0c6bb66ce93e5383c34385d8b889f",
        "sBTC": "0x317e82be8f7cba6c162ab968fcf695d88e8e0359",
    }
    # Point to real ABI directory so Path resolution works
    abi_dir = str(Path(__file__).resolve().parents[2] / "contracts" / "abis")
    mock_settings.abi_dir = abi_dir

    mock_client = MagicMock()
    mock_client.settings = mock_settings
    mock_client.to_checksum = lambda addr: addr
    return mock_client


class TestOracleReads:
    @pytest.mark.asyncio
    async def test_read_oracle_prices_success(self, mock_chain_client):
        """On-chain getPrice returns price + timestamp; parsed correctly."""
        now = time.time()
        service = AssetMarketService()

        mock_contract = MagicMock()
        mock_contract.functions.getPrice.return_value.call = AsyncMock(
            return_value=(550_000_000, int(now)),  # $550 in 6 decimals
        )
        mock_client_w3 = MagicMock()
        mock_client_w3.eth.contract.return_value = mock_contract
        mock_chain_client.w3 = mock_client_w3

        with patch("archimedes.chain.client.chain_client", mock_chain_client):
            result = await service._read_oracle_prices(["sSPY"])

        assert "sSPY" in result
        assert result["sSPY"]["price"] == pytest.approx(550.0)
        assert result["sSPY"]["stale"] is False

    @pytest.mark.asyncio
    async def test_read_oracle_prices_stale(self, mock_chain_client):
        """Oracle timestamp >5 min old → stale=True."""
        old_ts = time.time() - 600  # 10 minutes ago
        service = AssetMarketService()

        mock_contract = MagicMock()
        mock_contract.functions.getPrice.return_value.call = AsyncMock(
            return_value=(100_000_000, int(old_ts)),  # $100, stale
        )
        mock_client_w3 = MagicMock()
        mock_client_w3.eth.contract.return_value = mock_contract
        mock_chain_client.w3 = mock_client_w3

        with patch("archimedes.chain.client.chain_client", mock_chain_client):
            result = await service._read_oracle_prices(["sSPY"])

        assert "sSPY" in result
        assert result["sSPY"]["stale"] is True

    @pytest.mark.asyncio
    async def test_read_oracle_prices_missing_symbol(self, mock_chain_client):
        """Symbol not in oracle_addresses → skipped, not error."""
        service = AssetMarketService()
        mock_client_w3 = MagicMock()
        mock_chain_client.w3 = mock_client_w3

        with patch("archimedes.chain.client.chain_client", mock_chain_client):
            result = await service._read_oracle_prices(["sUNKNOWN"])

        assert "sUNKNOWN" not in result
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_read_oracle_prices_chain_failure(self, mock_chain_client):
        """Chain read throws → symbol skipped, no crash."""
        service = AssetMarketService()

        mock_contract = MagicMock()
        mock_contract.functions.getPrice.return_value.call = AsyncMock(
            side_effect=Exception("RPC timeout"),
        )
        mock_client_w3 = MagicMock()
        mock_client_w3.eth.contract.return_value = mock_contract
        mock_chain_client.w3 = mock_client_w3

        with patch("archimedes.chain.client.chain_client", mock_chain_client):
            result = await service._read_oracle_prices(["sSPY"])

        assert "sSPY" not in result  # Failed read → omitted


class TestListAssets:
    @pytest.mark.asyncio
    async def test_oracle_primary_price_yfinance_fallback(self, mock_chain_client):
        """When oracle returns a price, it takes priority over yfinance."""
        now = time.time()
        service = AssetMarketService()

        mock_contract = MagicMock()
        mock_contract.functions.getPrice.return_value.call = AsyncMock(
            return_value=(550_000_000, int(now)),  # $550 from oracle
        )
        mock_client_w3 = MagicMock()
        mock_client_w3.eth.contract.return_value = mock_contract
        mock_chain_client.w3 = mock_client_w3

        mock_histories = {
            "sSPY": {
                "close": [540.0, 545.0, 548.0],  # yfinance shows ~$548
                "dates": ["2026-05-22", "2026-05-23", "2026-05-24"],
            }
        }

        with (
            patch("archimedes.chain.client.chain_client", mock_chain_client),
            patch("archimedes.services.strategy_signal_evaluator._fetch_price_histories", return_value=mock_histories),
            patch("archimedes.services.asset_market_service._explore_universe", return_value=["sSPY"]),
            patch(
                "archimedes.services.strategy_signal_evaluator.GLOBAL_ASSETS",
                {"sSPY": ("SPY", "SPY", "us_equity_etf", "NYSE")},
            ),
        ):
            resp = await service.list_assets()

        assert len(resp.assets) >= 1
        spy = next((a for a in resp.assets if a.symbol == "sSPY"), None)
        assert spy is not None
        assert spy.current_price == pytest.approx(550.0)  # Oracle price, not yfinance
        assert spy.is_stale is False

    @pytest.mark.asyncio
    async def test_no_oracle_falls_back_to_yfinance_not_stale(self):
        """When the on-chain oracle has no price, the service should fall back
        to yfinance and surface ``price_source="yfinance"``. The displayed
        price isn't stale just because the unused oracle slot has no value —
        ``is_stale`` reflects the *displayed* price's freshness, not the oracle
        slot. (Semantics changed during the 2026-05-25 Explore-page rebuild —
        see ``asset_market_service.py`` module docstring for full rationale.)"""
        service = AssetMarketService()
        # Use the current UTC date as the latest bar so the staleness check
        # (yfinance window of 4 days) doesn't drift the test as time passes.
        from datetime import UTC, datetime, timedelta

        today = datetime.now(UTC).date()
        mock_histories = {
            "sSPY": {
                "close": [540.0, 545.0],
                "dates": [(today - timedelta(days=1)).isoformat(), today.isoformat()],
            }
        }

        with (
            patch.object(service, "_read_oracle_prices", return_value={}),
            patch("archimedes.services.strategy_signal_evaluator._fetch_price_histories", return_value=mock_histories),
            patch("archimedes.services.asset_market_service._explore_universe", return_value=["sSPY"]),
            patch(
                "archimedes.services.strategy_signal_evaluator.GLOBAL_ASSETS",
                {"sSPY": ("SPY", "SPY", "us_equity_etf", "NYSE")},
            ),
        ):
            resp = await service.list_assets()

        spy = next((a for a in resp.assets if a.symbol == "sSPY"), None)
        assert spy is not None
        assert spy.current_price == pytest.approx(545.0)  # yfinance fallback
        assert spy.price_source == "yfinance"
        assert spy.is_stale is False  # displayed price is fresh; only the unused oracle slot is empty

    @pytest.mark.asyncio
    async def test_list_assets_survives_none_in_price_series(self):
        """A None in a raw (object-dtype) price series must not abort the whole
        /explore/assets response — the bad element is dropped, the card still
        renders (#928). Uses dtype=object so the None stays a None (a float64
        series would coerce it to NaN, which math.isnan handles fine and would
        not reproduce the TypeError this guards against)."""
        import pandas as pd

        service = AssetMarketService()
        bad_series = pd.Series([540.0, None, 548.0], dtype=object)

        with (
            patch.object(service, "_read_oracle_prices", return_value={}),
            patch.object(
                service,
                "_fetch_histories_budgeted",
                AsyncMock(return_value={"sSPY": bad_series}),
            ),
            patch("archimedes.services.asset_market_service._explore_universe", return_value=["sSPY"]),
            patch(
                "archimedes.services.strategy_signal_evaluator.GLOBAL_ASSETS",
                {"sSPY": ("SPY", "SPY", "us_equity_etf", "NYSE")},
            ),
        ):
            resp = await service.list_assets()  # must not raise TypeError

        spy = next((a for a in resp.assets if a.symbol == "sSPY"), None)
        assert spy is not None  # card served, not dropped by a single bad element

    @pytest.mark.asyncio
    async def test_cache_ttl(self):
        """Second call within TTL returns cached result."""
        service = AssetMarketService()

        with (
            patch.object(service, "_read_oracle_prices", return_value={}),
            patch("archimedes.services.strategy_signal_evaluator._fetch_price_histories", return_value={}),
            patch("archimedes.services.asset_market_service._explore_universe", return_value=[]),
            patch("archimedes.services.strategy_signal_evaluator.GLOBAL_ASSETS", {}),
        ):
            resp1 = await service.list_assets()
            resp2 = await service.list_assets()

        assert resp1 is resp2  # Same object (cached)


# ── Universe alignment (#759 follow-up to #842) ────────────────────────────


class TestExploreUniverseAlignment:
    def test_explore_universe_is_the_deploy_eligible_ssot(self):
        """_explore_universe() must be ON_CHAIN_SYNTHS (the Generate-picker set),
        not DEFAULT_SCAN_UNIVERSE and not the single-stock-carrying GLOBAL_ASSETS."""
        from archimedes.services.strategy_signal_evaluator import DEFAULT_SCAN_UNIVERSE, GLOBAL_ASSETS
        from archimedes.universe import ON_CHAIN_SYNTHS

        universe = _explore_universe()
        assert universe == list(ON_CHAIN_SYNTHS)
        assert set(universe) != set(DEFAULT_SCAN_UNIVERSE)  # not the legacy ~74-name scan subset
        assert len(universe) > len(DEFAULT_SCAN_UNIVERSE)
        assert set(universe) <= set(GLOBAL_ASSETS)  # every card resolvable to a yfinance ticker

    @pytest.mark.asyncio
    async def test_list_assets_iterates_ssot_universe_not_scan_universe(self):
        """Every SSOT-universe symbol gets a card — including ones with no data —
        and the counts disclose data coverage honestly."""
        service = AssetMarketService()
        mock_histories = {
            "sAAA": {"close": [10.0, 11.0], "dates": ["2026-06-29", "2026-06-30"]},
        }

        with (
            patch.object(service, "_read_oracle_prices", return_value={}),
            patch("archimedes.services.strategy_signal_evaluator._fetch_price_histories", return_value=mock_histories),
            patch("archimedes.services.asset_market_service._explore_universe", return_value=["sAAA", "sBBB"]),
            patch(
                "archimedes.services.strategy_signal_evaluator.GLOBAL_ASSETS",
                {
                    "sAAA": ("AAA", "AAA", "us_equity_etf", "NYSE"),
                    "sBBB": ("BBB", "BBB", "us_equity_etf", "NYSE"),
                },
            ),
        ):
            resp = await service.list_assets()

        symbols = {a.symbol for a in resp.assets}
        assert symbols == {"sAAA", "sBBB"}  # full universe listed, not just fetched symbols
        assert resp.universe_size == 2
        assert resp.priced_count == 1
        bbb = next(a for a in resp.assets if a.symbol == "sBBB")
        assert bbb.current_price is None
        assert bbb.price_source == "none"
        assert bbb.is_stale is True  # honestly stale: no source at all

    @pytest.mark.asyncio
    async def test_card_name_uses_ssot_display_name_with_ticker_fallback(self):
        """Card names come from the SSOT (synthetic_universe.json), not a
        blanket f"Synthetic {ticker}"; symbols absent from the SSOT fall back
        to the ticker."""
        service = AssetMarketService()

        fake_spec = MagicMock()
        fake_spec.name = "Synthetic TLT (20+yr)"

        with (
            patch.object(service, "_read_oracle_prices", return_value={}),
            patch("archimedes.services.strategy_signal_evaluator._fetch_price_histories", return_value={}),
            patch("archimedes.services.asset_market_service._explore_universe", return_value=["sTLT", "sZZZ"]),
            patch("archimedes.universe.SYNTHETIC_UNIVERSE", {"sTLT": fake_spec}),
            patch(
                "archimedes.services.strategy_signal_evaluator.GLOBAL_ASSETS",
                {
                    "sTLT": ("TLT", "TLT", "us_bond_long", "NASDAQ"),
                    "sZZZ": ("ZZZ", "ZZZ", "us_equity_etf", "NYSE"),
                },
            ),
        ):
            resp = await service.list_assets()

        by_symbol = {a.symbol: a for a in resp.assets}
        assert by_symbol["sTLT"].name == "Synthetic TLT (20+yr)"  # SSOT display name
        assert by_symbol["sZZZ"].name == "ZZZ"  # ticker fallback (not in SSOT)

    def test_ssot_display_name_real_ssot(self):
        """Against the real SSOT: sTLT carries a richer name than 'Synthetic TLT'."""
        assert _ssot_display_name("sTLT", "TLT") == "Synthetic TLT (20+yr)"
        assert _ssot_display_name("sNOT_A_SYNTH", "FALLBACK") == "FALLBACK"


# ── Partial-result degradation (budgeted history fetch) ─────────────────────


class TestBudgetedHistoryFetch:
    @pytest.mark.asyncio
    async def test_budget_exhausted_serves_partial_not_timeout(self):
        """With a zero fetch budget, list_assets still answers: every universe
        symbol is listed, prices are honestly null, priced_count == 0."""
        service = AssetMarketService()
        fetch_mock = MagicMock()

        with (
            patch.object(service, "_read_oracle_prices", return_value={}),
            patch("archimedes.services.strategy_signal_evaluator._fetch_price_histories", fetch_mock),
            patch("archimedes.services.asset_market_service._HISTORY_FETCH_BUDGET_SECONDS", 0.0),
            patch("archimedes.services.asset_market_service._explore_universe", return_value=["sAAA", "sBBB"]),
            patch(
                "archimedes.services.strategy_signal_evaluator.GLOBAL_ASSETS",
                {
                    "sAAA": ("AAA", "AAA", "us_equity_etf", "NYSE"),
                    "sBBB": ("BBB", "BBB", "us_equity_etf", "NYSE"),
                },
            ),
        ):
            resp = await service.list_assets()

        fetch_mock.assert_not_called()  # budget gone before the first chunk
        assert {a.symbol for a in resp.assets} == {"sAAA", "sBBB"}
        assert resp.universe_size == 2
        assert resp.priced_count == 0
        assert all(a.price_source == "none" for a in resp.assets)

    @pytest.mark.asyncio
    async def test_chunked_fetch_merges_all_chunks(self):
        """Chunk size 1 over 3 symbols → 3 evaluator calls, results merged."""
        service = AssetMarketService()
        per_chunk = {
            "sAAA": {"sAAA": {"close": [1.0, 2.0], "dates": ["d1", "d2"]}},
            "sBBB": {"sBBB": {"close": [3.0, 4.0], "dates": ["d1", "d2"]}},
            "sCCC": {"sCCC": {"close": [5.0, 6.0], "dates": ["d1", "d2"]}},
        }
        calls: list[list[str]] = []

        def fake_fetch(symbols, period):
            calls.append(list(symbols))
            return per_chunk[symbols[0]]

        with (
            patch("archimedes.services.strategy_signal_evaluator._fetch_price_histories", side_effect=fake_fetch),
            patch("archimedes.services.asset_market_service._HISTORY_CHUNK_SIZE", 1),
        ):
            histories = await service._fetch_histories_budgeted(["sAAA", "sBBB", "sCCC"])

        assert calls == [["sAAA"], ["sBBB"], ["sCCC"]]
        assert set(histories) == {"sAAA", "sBBB", "sCCC"}

    @pytest.mark.asyncio
    async def test_slow_chunk_times_out_and_serves_what_it_has(self):
        """A chunk that overruns the remaining budget is cut off; earlier
        chunks' data is still served (honest partial, not an exception)."""
        service = AssetMarketService()
        calls: list[list[str]] = []

        def fake_fetch(symbols, period):
            calls.append(list(symbols))
            if symbols[0] == "sBBB":
                time.sleep(0.5)  # overruns the 0.2s budget
            return {symbols[0]: {"close": [1.0], "dates": ["d1"]}}

        with (
            patch("archimedes.services.strategy_signal_evaluator._fetch_price_histories", side_effect=fake_fetch),
            patch("archimedes.services.asset_market_service._HISTORY_CHUNK_SIZE", 1),
            patch("archimedes.services.asset_market_service._HISTORY_FETCH_BUDGET_SECONDS", 0.2),
        ):
            histories = await service._fetch_histories_budgeted(["sAAA", "sBBB", "sCCC"])

        assert "sAAA" in histories  # fetched before the budget ran out
        assert "sCCC" not in histories  # never attempted after the timeout
        assert [c[0] for c in calls] == ["sAAA", "sBBB"]

    @pytest.mark.asyncio
    async def test_failed_chunk_does_not_kill_later_chunks(self):
        """A chunk raising (upstream hiccup) is logged and skipped; later
        chunks still fetch."""
        service = AssetMarketService()

        def fake_fetch(symbols, period):
            if symbols[0] == "sAAA":
                raise RuntimeError("yfinance hiccup")
            return {symbols[0]: {"close": [1.0], "dates": ["d1"]}}

        with (
            patch("archimedes.services.strategy_signal_evaluator._fetch_price_histories", side_effect=fake_fetch),
            patch("archimedes.services.asset_market_service._HISTORY_CHUNK_SIZE", 1),
        ):
            histories = await service._fetch_histories_budgeted(["sAAA", "sBBB"])

        assert set(histories) == {"sBBB"}
