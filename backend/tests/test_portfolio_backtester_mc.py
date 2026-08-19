"""Tests for portfolio_backtester's sensitivity sweep.

Hermetic: no network, no DB, no .env — mocked at the yfinance boundary.

History: this file also covered monte_carlo_portfolio, walk_forward_validate
and run_parallel_backtest until the 2026-08-18 test-suite audit established
all three had zero production callers ("Phase 2.2" exists nowhere but the
module banner); they were deleted WITH their code. sensitivity_sweep is the
one function of that era with a live caller (POST /api/portfolio/parameter-
sweep), so its tests stay.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestSensitivitySweep:
    """Tests for sensitivity_sweep using mocked backtest calls."""

    def _mock_backtest_result(self, sharpe: float) -> MagicMock:
        r = MagicMock()
        r.sharpe_ratio = sharpe
        return r

    def test_raises_on_empty_param_grid(self):
        from archimedes.services.portfolio_backtester import sensitivity_sweep

        with pytest.raises(ValueError, match="param_grid must contain"):
            sensitivity_sweep(
                strategy_id="s1",
                weights={"SPY": 1.0},
                param_grid={"unknown_param": [1, 2]},
            )

    def test_grid_length_matches_combinations(self):
        from archimedes.services.portfolio_backtester import sensitivity_sweep

        with patch("archimedes.services.portfolio_backtester.backtest_portfolio") as mock_bp:
            mock_bp.return_value = (self._mock_backtest_result(0.8), {})
            result = sensitivity_sweep(
                strategy_id="s1",
                weights={"SPY": 1.0},
                param_grid={"rebalance_days": [10, 21, 42], "tx_cost_bps": [5, 15]},
            )
        # 3 × 2 = 6 combinations
        assert len(result["grid"]) == 6

    def test_best_and_worst_params_present(self):
        from archimedes.services.portfolio_backtester import sensitivity_sweep

        sharpes = [0.3, 0.9, 0.5, 1.2]
        call_count = [0]

        def fake_backtest(**kw):
            s = sharpes[call_count[0] % len(sharpes)]
            call_count[0] += 1
            return (self._mock_backtest_result(s), {})

        with patch("archimedes.services.portfolio_backtester.backtest_portfolio", side_effect=fake_backtest):
            result = sensitivity_sweep(
                strategy_id="s1",
                weights={"SPY": 1.0},
                param_grid={"rebalance_days": [10, 21, 42, 63]},
            )

        assert result["best_params"] is not None
        assert result["worst_params"] is not None
        assert result["metric_range"][0] <= result["metric_range"][1]

    def test_sensitivity_ratio_computed(self):
        from archimedes.services.portfolio_backtester import sensitivity_sweep

        # Two values far apart → large sensitivity ratio
        call_sharpes = [2.0, 0.1]
        call_count = [0]

        def fake_backtest(**kw):
            s = call_sharpes[call_count[0] % 2]
            call_count[0] += 1
            return (self._mock_backtest_result(s), {})

        with patch("archimedes.services.portfolio_backtester.backtest_portfolio", side_effect=fake_backtest):
            result = sensitivity_sweep(
                strategy_id="s1",
                weights={"SPY": 1.0},
                param_grid={"rebalance_days": [10, 63]},
            )

        assert result["sensitivity_ratio"] > 0.5, "Wide spread should yield sensitivity_ratio > 0.5"


# ─── walk_forward_validate (mocked) ──────────────────────────────────
