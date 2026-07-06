"""Unit coverage for portfolio_backtester — the multi-asset weighted
backtester that fills in generated strategies' "Pending Backtest" gap.

Tests the pure functions (simulator + annualized metrics) directly. The
yfinance-fetch + DB-persist paths are exercised via a stubbed price panel
so the suite stays offline + DB-free per pytest.ini's unit profile.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
from archimedes.services.portfolio_backtester import (
    ANNUALIZATION,
    DEFAULT_REBALANCE_DAYS,
    _annualized_metrics,
    _correlation_to_benchmark,
    _simulate_portfolio,
    backtest_portfolio,
)


def _flat_panel(symbols: list[str], n_bars: int, daily_drift: float = 0.0005) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build a deterministic price and volume panel with mild upward drift."""
    idx = pd.bdate_range("2018-01-02", periods=n_bars)
    data = {}
    vols = {}
    for i, s in enumerate(symbols):
        # Distinct drifts and deterministic noise so variance > 0 for Almgren impact
        noise = (i + 1) * 0.005 * np.sin(np.arange(n_bars))
        prices = 100.0 * np.cumprod(1.0 + (daily_drift + i * 0.0001) + noise)
        data[s] = pd.Series(prices, index=idx)
        # Constant volume
        vols[s] = pd.Series(1_000_000.0 * np.ones(n_bars), index=idx)
    return pd.DataFrame(data), pd.DataFrame(vols)


class TestSimulate:
    def test_two_asset_rebalance_produces_dense_return_series(self) -> None:
        panel, vols = _flat_panel(["SPY", "TLT"], n_bars=500)
        rets, eq = _simulate_portfolio(
            panel=panel,
            volume_panel=vols,
            target_weights={"SPY": 0.6, "TLT": 0.4},
            rebalance_days=21,
            initial_cash=100_000.0,
            gamma=0.1,
        )
        assert len(rets) == 500
        assert len(eq) == 500
        # Equity strictly positive (no nonsense leverage / shorting)
        assert all(v > 0 for v in eq)
        # First bar uses no prior return, so |r_0| ≈ 0
        assert abs(rets[0]) < 1e-6

    def test_negative_weights_clamped_to_zero(self) -> None:
        panel, vols = _flat_panel(["SPY", "TLT"], n_bars=120)
        # SPY has negative weight; should be treated as 0 → 100% TLT
        rets_neg, _ = _simulate_portfolio(
            panel=panel,
            volume_panel=vols,
            target_weights={"SPY": -0.5, "TLT": 1.0},
            rebalance_days=21,
            initial_cash=100_000.0,
            gamma=0.1,
        )
        rets_pure_tlt, _ = _simulate_portfolio(
            panel=panel,
            volume_panel=vols,
            target_weights={"SPY": 0.0, "TLT": 1.0},
            rebalance_days=21,
            initial_cash=100_000.0,
            gamma=0.1,
        )
        # Long-only enforcement: -0.5 SPY is dropped, TLT renormalizes to 1.0
        np.testing.assert_allclose(rets_neg, rets_pure_tlt, atol=1e-12)

    def test_rebalance_charges_turnover_cost(self) -> None:
        panel, vols = _flat_panel(["SPY", "TLT"], n_bars=120, daily_drift=0.001)
        _, eq_with_cost = _simulate_portfolio(
            panel=panel,
            volume_panel=vols,
            target_weights={"SPY": 0.5, "TLT": 0.5},
            rebalance_days=21,
            initial_cash=100_000.0,
            gamma=0.1,  # Market impact cost
        )
        _, eq_no_cost = _simulate_portfolio(
            panel=panel,
            volume_panel=vols,
            target_weights={"SPY": 0.5, "TLT": 0.5},
            rebalance_days=21,
            initial_cash=100_000.0,
            gamma=0.0,  # Zero impact
        )
        # Cost must drag terminal equity strictly below the zero-cost run.
        assert eq_with_cost[-1] < eq_no_cost[-1]

    def test_linear_cost_is_round_trip_on_turnover(self) -> None:
        """Pin the round-trip convention: ``tx_cost_bps`` is a one-way (per-leg)
        rate, and the linear cost is charged on the two-sided turnover
        ``sum(|Δw|)`` — i.e. on both the sell leg and the buy leg of each
        rebalance. This locks the intentional 2×-per-rebalance semantics so it
        cannot silently drift to a one-way charge (issue #936).

        Deterministic single-rebalance scenario built to make ``sum(|Δw|)``
        exactly ``1/11`` with zero Almgren impact (``gamma=0``):

          - 3 bars, ``rebalance_days=2`` → the only rebalance is at bar i=2.
          - Prices flat bar0→bar1; on bar1→bar2 SPY jumps +20%, TLT flat.
          - Held weights entering bar 2 are the static target [0.5, 0.5].
            After the +20% SPY move they drift to [0.6, 0.5] → normalized to
            [6/11, 5/11], so ``Δw = [1/22, 1/22]`` and ``sum(|Δw|) = 1/11``.
          - Pre-cost bar-2 return is 0.5 * 0.20 = 0.10; the realized (post-cost)
            return is ``0.10 - sum(|Δw|) * (tx_cost_bps / 10_000)``.
        """
        idx = pd.bdate_range("2020-01-02", periods=3)
        # SPY: flat, flat, then +20%. TLT: flat throughout.
        spy = pd.Series([100.0, 100.0, 120.0], index=idx)
        tlt = pd.Series([100.0, 100.0, 100.0], index=idx)
        panel = pd.DataFrame({"SPY": spy, "TLT": tlt})
        vols = pd.DataFrame({"SPY": pd.Series([1e6] * 3, index=idx), "TLT": pd.Series([1e6] * 3, index=idx)})

        tx_cost_bps = 30
        rets, _eq = _simulate_portfolio(
            panel=panel,
            volume_panel=vols,
            target_weights={"SPY": 0.5, "TLT": 0.5},
            rebalance_days=2,
            initial_cash=100_000.0,
            tx_cost_bps=tx_cost_bps,
            gamma=0.0,  # isolate the linear bps cost from Almgren impact
        )

        turnover = 1.0 / 11.0  # sum(|Δw|) — two-sided, sell leg + buy leg
        pre_cost_return = 0.10  # 0.5 SPY weight * +20% move
        round_trip_cost_fraction = turnover * (tx_cost_bps / 10_000.0)

        # Realized return on the rebalance bar equals pre-cost minus the
        # round-trip linear cost fraction, to machine precision.
        assert rets[2] == pytest.approx(pre_cost_return - round_trip_cost_fraction, abs=1e-12)

        # And it must NOT match the one-way (halved) charge — the convention is
        # round-trip, so a per-leg-only cost is the wrong model here.
        one_way_cost_fraction = turnover * (tx_cost_bps / 2 / 10_000.0)
        assert rets[2] != pytest.approx(pre_cost_return - one_way_cost_fraction, abs=1e-12)

    def test_all_zero_weights_raises(self) -> None:
        panel, vols = _flat_panel(["SPY", "TLT"], n_bars=120)
        with pytest.raises(ValueError, match="non-positive"):
            _simulate_portfolio(
                panel=panel,
                volume_panel=vols,
                target_weights={"SPY": 0.0, "TLT": 0.0},
                rebalance_days=21,
                initial_cash=100_000.0,
                gamma=0.1,
            )

    def test_negative_equity_stops_trading(self) -> None:
        panel, vols = _flat_panel(["SPY", "TLT"], n_bars=120)
        rets, eq = _simulate_portfolio(
            panel=panel,
            volume_panel=vols,
            target_weights={"SPY": 0.5, "TLT": 0.5},
            rebalance_days=21,
            initial_cash=10.0,  # Tiny starting cash
            tx_cost_bps=10_000_000,  # Massive transaction costs to trigger bankruptcy on first rebalance
            gamma=1.0,
        )
        assert min(eq) == 0.0
        assert -1.0 in rets

    def test_zero_volume_fallback(self) -> None:
        panel, vols = _flat_panel(["SPY", "TLT"], n_bars=120)
        vols["SPY"] = 0.0
        vols["TLT"] = 0.0
        _rets, eq = _simulate_portfolio(
            panel=panel,
            volume_panel=vols,
            target_weights={"SPY": 0.5, "TLT": 0.5},
            rebalance_days=21,
            initial_cash=100_000.0,
            tx_cost_bps=0,
            gamma=0.1,
        )
        assert len(eq) == 120

    def test_zero_volatility_fallback(self) -> None:
        panel, vols = _flat_panel(["SPY", "TLT"], n_bars=120, daily_drift=0.0)
        panel["SPY"] = 100.0
        panel["TLT"] = 100.0
        _rets, eq = _simulate_portfolio(
            panel=panel,
            volume_panel=vols,
            target_weights={"SPY": 0.5, "TLT": 0.5},
            rebalance_days=21,
            initial_cash=100_000.0,
            tx_cost_bps=0,
            gamma=0.1,
        )
        assert len(eq) == 120
        assert eq[-1] == 100_000.0


class TestAnnualizedMetrics:
    def test_constant_returns_give_zero_sharpe(self) -> None:
        # std=0 → Sharpe collapses to 0 by guard; max DD is 0
        rets = [0.0] * 300
        eq = [100_000.0] * 300
        m = _annualized_metrics(rets, eq)
        assert m["sharpe_ratio"] == 0.0
        assert m["max_drawdown"] == 0.0
        assert m["calmar_ratio"] == 0.0

    def test_drift_positive_sharpe(self) -> None:
        # Drift of 0.1%/day clearly exceeds the 5% annual rf (≈0.0198%/day),
        # so the rf-adjusted Sharpe is reliably positive even with seed noise.
        # (loc=0.0005 was used before rf subtraction was added; with rf=5% that
        # mean can fall below the rf threshold in a 2520-bar sample with seed=42.)
        rng = np.random.default_rng(42)
        rets = list(rng.normal(loc=0.001, scale=0.01, size=2520))  # ~10y daily
        eq = [100_000.0]
        for r in rets:
            eq.append(eq[-1] * (1 + r))
        m = _annualized_metrics(rets, eq[1:])
        assert m["sharpe_ratio"] > 0
        assert m["cagr"] > 0
        # CAGR / max_drawdown >= 0 (max_dd may be very small but not negative)
        assert m["max_drawdown"] >= 0

    def test_too_few_observations_returns_zeros(self) -> None:
        m = _annualized_metrics([], [])
        assert m["sharpe_ratio"] == 0.0
        assert m["cagr"] == 0.0
        m1 = _annualized_metrics([0.01], [100_000])
        assert m1["sharpe_ratio"] == 0.0


class TestCorrelation:
    def test_perfect_correlation(self) -> None:
        a = [0.01, -0.02, 0.005, 0.03]
        assert _correlation_to_benchmark(a, a) == pytest.approx(1.0, abs=1e-9)

    def test_zero_variance_returns_zero(self) -> None:
        flat = [0.0, 0.0, 0.0, 0.0]
        varying = [0.01, -0.01, 0.02, -0.02]
        assert _correlation_to_benchmark(flat, varying) == 0.0

    def test_short_series_returns_zero(self) -> None:
        assert _correlation_to_benchmark([0.01], [0.01]) == 0.0


class TestBacktestPortfolioIntegration:
    """Integration-style test that stubs the fetcher to avoid yfinance hits."""

    def test_end_to_end_with_stubbed_panel(self) -> None:
        panel, vols = _flat_panel(["SPY", "TLT"], n_bars=2520)  # ~10y of daily bars

        with patch(
            "archimedes.services.portfolio_backtester._fetch_price_panel",
            return_value=(panel, vols),
        ):
            result, artifact = backtest_portfolio(
                strategy_id="test-strategy-1",
                weights={"SPY": 0.6, "TLT": 0.4},
                start_date="2016-01-04",
                end_date="2026-01-02",
                num_trials_for_dsr=6,
            )

        # Hard contract checks the strategies_routes wiring depends on
        assert result.strategy_id == "test-strategy-1"
        assert result.sharpe_ratio > 0  # mild drift → positive Sharpe
        assert result.cagr > 0
        assert result.max_drawdown >= 0
        assert result.deflated_sharpe_ratio is not None
        assert result.dsr_p_value is not None
        assert result.out_of_sample_sharpe is not None
        assert result.look_ahead_audit_passed is True
        assert result.backtest_engine == "portfolio-simulator-v1"
        assert result.backtest_code_hash  # non-empty
        assert isinstance(result.backtest_start, date)
        assert isinstance(result.backtest_end, date)
        assert result.num_trials_in_selection == 6

        # Artifact shape mirrors analytics-engine JSON so existing rigor
        # consumers (backtest_repository, mapper) stay generic.
        assert artifact["operations"] == ["SPY", "TLT"]
        assert artifact["assumptions"]["data_source"] == "yfinance"
        assert artifact["assumptions"]["rebalance_days"] == DEFAULT_REBALANCE_DAYS
        assert artifact["assumptions"]["weights"] == {"SPY": 0.6, "TLT": 0.4}
        assert len(artifact["results"]) == 1
        metrics = artifact["results"][0]["metrics"]
        assert len(metrics["daily_returns"]) == 2520
        assert len(metrics["equity_curve"]) == 2520
        assert metrics["num_bars"] == 2520

    def test_no_weights_raises(self) -> None:
        with pytest.raises(ValueError, match="No positive weights"):
            backtest_portfolio(strategy_id="x", weights={})

    def test_all_zero_weights_raises(self) -> None:
        with pytest.raises(ValueError, match="No positive weights"):
            backtest_portfolio(strategy_id="x", weights={"SPY": 0.0, "TLT": 0.0})

    def test_empty_dataframe_vector(self) -> None:
        import sys
        from unittest.mock import MagicMock

        from archimedes.services.portfolio_backtester import _fetch_price_panel

        mock_data = MagicMock()
        mock_data.fetch_ohlcv.return_value = pd.DataFrame()

        sys.modules["archimedes_analytics_engine"] = MagicMock()
        sys.modules["archimedes_analytics_engine.data"] = mock_data

        with pytest.raises(ValueError, match="Insufficient overlapping history"):
            _fetch_price_panel(["SPY", "TLT"], "2020-01-01", "2021-01-01")

    def test_rigor_metrics_match_evaluator(self) -> None:
        """DSR/OOS values returned by the backtester must come from the
        canonical rigor_evaluator — same functions the curated strategies'
        rigor gate uses. This locks in the contract that generated and
        curated strategies are graded on the same scale."""
        from archimedes.services.rigor_evaluator import compute_dsr, compute_oos_sharpe

        panel, vols = _flat_panel(["SPY"], n_bars=1500)

        with patch(
            "archimedes.services.portfolio_backtester._fetch_price_panel",
            return_value=(panel, vols),
        ):
            result, artifact = backtest_portfolio(
                strategy_id="rigor-test",
                weights={"SPY": 1.0},
                start_date="2018-01-02",
                end_date="2024-01-02",
                num_trials_for_dsr=1,
            )

        daily_rets = artifact["results"][0]["metrics"]["daily_returns"]
        expected_dsr, expected_p = compute_dsr(daily_rets, 1)
        expected_oos = compute_oos_sharpe(daily_rets)

        assert result.deflated_sharpe_ratio == pytest.approx(expected_dsr)
        assert result.dsr_p_value == pytest.approx(expected_p)
        assert result.out_of_sample_sharpe == pytest.approx(expected_oos)


class TestAnnualizationConstant:
    def test_constant_matches_trading_days(self) -> None:
        # Locked at 252 — drift here cascades into every downstream metric.
        assert ANNUALIZATION == 252
