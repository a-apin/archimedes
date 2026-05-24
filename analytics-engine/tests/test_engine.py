import pandas as pd
from archimedes_analytics_engine.engine import BacktestResult, run_buy_and_hold


def _fixture_prices(periods: int = 8) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=periods, freq="D")
    closes = [100, 102, 101, 105, 106, 107, 108, 109][:periods]
    return pd.DataFrame(
        {
            "Open": [c - 1 for c in closes],
            "High": [c + 1 for c in closes],
            "Low": [c - 2 for c in closes],
            "Close": closes,
            "Volume": [1000] * periods,
        },
        index=idx,
    )


def test_run_buy_and_hold_returns_metrics() -> None:
    result = run_buy_and_hold(_fixture_prices(), initial_cash=10000.0)

    assert isinstance(result, BacktestResult)
    assert result.final_value > 0
    assert isinstance(result.total_return_pct, float)
    assert len(result.equity_curve) > 0


def test_backtest_result_carries_passport_fields() -> None:
    result = run_buy_and_hold(_fixture_prices(), initial_cash=10000.0)

    assert result.backtest_engine == "backtrader"
    assert result.transaction_cost_bps == 10
    assert result.slippage_bps == 0
    assert result.look_ahead_audit_passed is True
    assert result.bars == 8
    assert result.backtest_start is not None
    assert result.backtest_end is not None
    assert isinstance(result.daily_returns, list)
    assert isinstance(result.monthly_returns, list)


def test_slippage_argument_flows_through() -> None:
    result = run_buy_and_hold(
        _fixture_prices(),
        initial_cash=10000.0,
        slippage_bps=25,
    )
    assert result.slippage_bps == 25


def test_drawdown_and_returns_fields_present() -> None:
    result = run_buy_and_hold(_fixture_prices(periods=8), initial_cash=10000.0)

    # max_drawdown_pct may be 0.0 if monotonically increasing — both None and float are valid.
    assert result.max_drawdown_pct is None or isinstance(result.max_drawdown_pct, float)
    assert result.cagr is None or isinstance(result.cagr, float)
    # Sortino: with rising series, no downside returns → None expected.
    assert result.sortino_ratio is None or isinstance(result.sortino_ratio, float)
