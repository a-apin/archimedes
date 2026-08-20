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

import asyncio
import math
import random
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from archimedes.api.explore_schemas import ExploreAssetsResponse
from archimedes.services.asset_market_service import (
    _CACHE_TTL_SECONDS,
    _CRYPTO_TRADING_DAYS_PER_YEAR,
    _EQUITY_TRADING_DAYS_PER_YEAR,
    AssetMarketService,
    _explanations_for,
    _explore_universe,
    _is_24_7_asset_class,
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


# ── Plausibility guard (#1322 — sJUP's +1483.08% "24h" move) ───────────────


class TestPctChangePlausibilityGuard:
    """An arithmetically-impossible pct change must come back None, not a
    fabricated number — honest absence per docs/architectural-principles.md
    § fail-soft, not a clamp/winsorize (the issue's anti-goal)."""

    def test_pct_change_rejects_implausible_24h_move(self):
        """The exact defect class from the issue: a tiny prior close (a bad
        tick / decimal-placement error) implies a >100%/day move. This
        mirrors sJUP's real prior close of 0.0003166165."""
        prices = [0.0003166165, 0.005]
        implied_pct = (0.005 - 0.0003166165) / 0.0003166165 * 100.0
        assert implied_pct > 100.0  # sanity: this genuinely trips the bound
        assert _pct_change(prices, 1) is None

    def test_pct_change_accepts_plausible_large_move(self):
        """A legitimate outsized daily move — well inside the bound — is NOT
        rejected. The guard targets impossible values, not merely large
        ones; the anti-goal forbids hiding real moves behind the guard."""
        assert _pct_change([100.0, 180.0], 1) == pytest.approx(80.0)

    def test_pct_change_exactly_at_the_bound_is_kept(self):
        """Exactly doubling in one bar (100% for n=1) sits ON the bound and
        is kept, not rejected — the guard is a strict '>' check."""
        assert _pct_change([100.0, 200.0], 1) == pytest.approx(100.0)

    def test_pct_change_multiday_window_tolerates_a_wider_move(self):
        """A 21-bar window's bound is wider than a 1-bar window's (vol scales
        with sqrt(time)) — the same cumulative move that would be rejected
        over 1 bar is plausible compounded over 21."""
        prices = [10.0] * 21 + [30.0]  # +200% over 21 bars
        assert _pct_change(prices, 21) == pytest.approx(200.0)
        # But the 1-bar version of the same magnitude is still rejected.
        assert _pct_change([10.0, 30.0], 1) is None


# ── Asset-class-aware trading-day convention (#1322) ────────────────────────


class TestIs247AssetClass:
    def test_crypto_is_24_7(self):
        assert _is_24_7_asset_class("crypto") is True

    def test_equity_etf_is_not_24_7(self):
        assert _is_24_7_asset_class("us_equity_etf") is False

    def test_empty_or_none_asset_class_is_not_24_7(self):
        assert _is_24_7_asset_class("") is False
        assert _is_24_7_asset_class(None) is False


class TestAssetClassAwareAnnualization:
    """Crypto trades 365 days/year with no weekend/holiday gaps; equities
    trade ~252. Annualizing crypto's realized vol with the equity constant
    understates it by sqrt(365/252) ≈ 1.20 — about 17% too low."""

    def _series(self, seed: int) -> list[float]:
        rng = random.Random(seed)
        prices = [100.0]
        for _ in range(31):
            prices.append(prices[-1] * (1 + rng.uniform(-0.02, 0.02)))
        return prices

    def test_identical_daily_returns_annualize_differently_by_asset_class(self):
        prices = self._series(1322)
        equity_vol = _realized_vol_annual(prices, 30, _EQUITY_TRADING_DAYS_PER_YEAR)
        crypto_vol = _realized_vol_annual(prices, 30, _CRYPTO_TRADING_DAYS_PER_YEAR)

        assert equity_vol is not None
        assert crypto_vol is not None
        assert crypto_vol != equity_vol
        assert crypto_vol > equity_vol
        assert crypto_vol / equity_vol == pytest.approx(math.sqrt(365 / 252), rel=1e-9)

    def test_realized_vol_annual_defaults_to_equity_252_unspecified(self):
        """Backward-compatible default: a caller that omits
        trading_days_per_year keeps the pre-#1322 (equity) behavior."""
        prices = self._series(7)
        assert _realized_vol_annual(prices, 30) == _realized_vol_annual(prices, 30, 252)


class TestExplanationsAssetClassAware:
    def test_period_labels_match_asset_class(self):
        stat_dict = {
            "current_price": 100.0,
            "change_24h_pct": 1.0,
            "change_7d_pct": 2.0,
            "change_30d_pct": 3.0,
            "realized_vol_30d": 0.4,
        }
        equity_expl = _explanations_for(stat_dict, is_247=False)
        crypto_expl = _explanations_for(stat_dict, is_247=True)

        assert "5 trading days" in equity_expl["change_7d_pct"]
        assert "≈21 trading days" in equity_expl["change_30d_pct"]
        assert "7 calendar days" in crypto_expl["change_7d_pct"]
        assert "30 calendar days" in crypto_expl["change_30d_pct"]

    def test_daily_vol_copy_uses_the_matching_annualization_factor(self):
        """The de-annualized 'typical daily move' figure in the copy must be
        computed with the SAME trading-day count the vol was actually
        annualized with — otherwise the copy asserts a number the
        computation didn't produce (#1322 honesty requirement: a hard-coded
        wrong-convention explanation is the same defect class as a fabricated
        statistic)."""
        rng = random.Random(7)
        prices = [100.0]
        for _ in range(31):
            prices.append(prices[-1] * (1 + rng.uniform(-0.03, 0.03)))
        crypto_vol = _realized_vol_annual(prices, 30, _CRYPTO_TRADING_DAYS_PER_YEAR)
        assert crypto_vol is not None

        expl = _explanations_for({"realized_vol_30d": crypto_vol}, is_247=True)
        expected_daily_pct = (crypto_vol / math.sqrt(365)) * 100.0
        assert f"{expected_daily_pct:.1f}%" in expl["realized_vol_30d"]


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
    async def test_read_oracle_prices_future_timestamp_stale(self, mock_chain_client):
        """An updated_at ahead of the host clock reads as stale, not fresh (#934).

        A future block timestamp otherwise leaves (now_ts - updated_at) negative
        forever, so a frozen price could never trip the staleness gate."""
        future_ts = time.time() + 1  # 1 second in the future
        service = AssetMarketService()

        mock_contract = MagicMock()
        mock_contract.functions.getPrice.return_value.call = AsyncMock(
            return_value=(100_000_000, int(future_ts) + 1),  # ceil past now to stay in the future
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

    @pytest.mark.asyncio
    async def test_stale_cache_served_immediately_not_blocked_on_rebuild(self):
        """Stale-while-revalidate (explore-latency fix): once ANY cache exists,
        a request past the TTL must never await the rebuild inline — it gets
        the last-good (stale) response immediately, and a background task
        does the rebuild. This is what turns the measured 12.7s-42.9s prod
        stall (cache-miss request pays the full oracle+yfinance cost) into a
        sub-millisecond cache read for every request except the very first.
        """
        service = AssetMarketService()

        # Seed a cache directly — skip the cold-start path entirely.
        stale_resp = ExploreAssetsResponse(
            assets=[],
            cache_ttl_seconds=_CACHE_TTL_SECONDS,
            generated_at="t0",
            universe_size=0,
            priced_count=0,
        )
        service._cache = stale_resp
        service._cache_ts = time.time() - (_CACHE_TTL_SECONDS + 1)  # already expired

        # A rebuild that would hang forever if this test ever awaited it inline.
        rebuild_started = asyncio.Event()
        rebuild_may_finish = asyncio.Event()

        async def slow_refresh():
            rebuild_started.set()
            await rebuild_may_finish.wait()
            return ExploreAssetsResponse(
                assets=[],
                cache_ttl_seconds=_CACHE_TTL_SECONDS,
                generated_at="t1",
                universe_size=0,
                priced_count=0,
            )

        with patch.object(service, "_refresh", side_effect=slow_refresh):
            t0 = time.monotonic()
            resp = await asyncio.wait_for(service.list_assets(), timeout=1.0)
            elapsed = time.monotonic() - t0

            assert resp is stale_resp  # served the stale cache, not the rebuild
            assert elapsed < 0.5  # never blocked on the (hung) rebuild
            await asyncio.wait_for(rebuild_started.wait(), timeout=1.0)  # rebuild WAS kicked off

            rebuild_may_finish.set()
            await service._refresh_task  # let the background task finish cleanly

    @pytest.mark.asyncio
    async def test_stale_cache_concurrent_requests_dedupe_background_refresh(self):
        """N requests landing in the same stale window must not each start
        their own oracle+yfinance rebuild — only one refresh in flight."""
        service = AssetMarketService()

        stale_resp = ExploreAssetsResponse(
            assets=[],
            cache_ttl_seconds=_CACHE_TTL_SECONDS,
            generated_at="t0",
            universe_size=0,
            priced_count=0,
        )
        service._cache = stale_resp
        service._cache_ts = time.time() - (_CACHE_TTL_SECONDS + 1)

        call_count = 0
        finish = asyncio.Event()

        async def counting_refresh():
            nonlocal call_count
            call_count += 1
            await finish.wait()
            return stale_resp

        with patch.object(service, "_refresh", side_effect=counting_refresh):
            results = await asyncio.gather(*(service.list_assets() for _ in range(5)))
            assert all(r is stale_resp for r in results)
            finish.set()
            # Bind the result rather than awaiting for the side effect alone:
            # a bare ``await task`` reads to a linter as a statement with no
            # effect, and asserting on what the background refresh actually
            # returned is a strictly stronger check than merely draining it.
            refreshed = await service._refresh_task
            assert refreshed is stale_resp

        assert call_count == 1  # deduplicated, not 5 separate rebuilds


# ── End-to-end #1322 repro: impossible values never reach the API ──────────


class TestExploreAssetsPlausibilityAndAssetClassAwareness:
    @pytest.mark.asyncio
    async def test_list_assets_change_24h_pct_none_for_impossible_move(self):
        """Full repro of the issue: a corrupted history (tiny prior close,
        mirroring sJUP's real 0.0003166165) must surface as
        change_24h_pct=None on the served AssetExploreItem — never as a
        fabricated triple-digit percentage. The price itself still shows."""
        service = AssetMarketService()
        mock_histories = {
            "sJUP": {
                "close": [0.0003166165, 0.005],
                "dates": ["2026-08-19", "2026-08-20"],
            }
        }

        with (
            patch.object(service, "_read_oracle_prices", return_value={}),
            patch("archimedes.services.strategy_signal_evaluator._fetch_price_histories", return_value=mock_histories),
            patch("archimedes.services.asset_market_service._explore_universe", return_value=["sJUP"]),
            patch(
                "archimedes.services.strategy_signal_evaluator.GLOBAL_ASSETS",
                {"sJUP": ("JUP-USD", "JUP", "crypto", "Coinbase")},
            ),
        ):
            resp = await service.list_assets()

        jup = next(a for a in resp.assets if a.symbol == "sJUP")
        assert jup.change_24h_pct is None  # honest absence, not +1479%
        assert jup.current_price == pytest.approx(0.005)  # price still shown

    @pytest.mark.asyncio
    async def test_list_assets_period_offsets_are_asset_class_aware(self):
        """Same input series for a crypto symbol and an equity symbol must
        produce DIFFERENT change_7d_pct / change_30d_pct: crypto indexes 7
        and 30 bars back (24/7, one bar per calendar day); equity indexes 5
        and 21 (trading days only)."""
        service = AssetMarketService()
        closes = [100.0 + i for i in range(35)]  # monotonic → offsets diverge
        mock_histories = {
            "sBTC": {"close": closes, "dates": [f"d{i}" for i in range(35)]},
            "sSPY": {"close": closes, "dates": [f"d{i}" for i in range(35)]},
        }

        with (
            patch.object(service, "_read_oracle_prices", return_value={}),
            patch("archimedes.services.strategy_signal_evaluator._fetch_price_histories", return_value=mock_histories),
            patch("archimedes.services.asset_market_service._explore_universe", return_value=["sBTC", "sSPY"]),
            patch(
                "archimedes.services.strategy_signal_evaluator.GLOBAL_ASSETS",
                {
                    "sBTC": ("BTC-USD", "BTC", "crypto", "Coinbase"),
                    "sSPY": ("SPY", "SPY", "us_equity_etf", "NYSE"),
                },
            ),
        ):
            resp = await service.list_assets()

        btc = next(a for a in resp.assets if a.symbol == "sBTC")
        spy = next(a for a in resp.assets if a.symbol == "sSPY")
        assert btc.change_7d_pct != spy.change_7d_pct
        assert btc.change_30d_pct != spy.change_30d_pct
        # Exact expected values pin the offsets down (7/30 vs 5/21 bars back).
        assert btc.change_7d_pct == pytest.approx((closes[-1] - closes[-1 - 7]) / closes[-1 - 7] * 100.0)
        assert spy.change_7d_pct == pytest.approx((closes[-1] - closes[-1 - 5]) / closes[-1 - 5] * 100.0)
        assert btc.change_30d_pct == pytest.approx((closes[-1] - closes[-1 - 30]) / closes[-1 - 30] * 100.0)
        assert spy.change_30d_pct == pytest.approx((closes[-1] - closes[-1 - 21]) / closes[-1 - 21] * 100.0)


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
