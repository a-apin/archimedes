from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any

import backtrader as bt
import pandas as pd

ANNUALIZATION = 252
BACKTEST_ENGINE_TAG = "backtrader"


@dataclass
class BacktestResult:
    final_value: float
    total_return_pct: float
    equity_curve: list[float]
    sharpe_ratio: float | None = None
    sortino_ratio: float | None = None
    calmar_ratio: float | None = None
    max_drawdown_pct: float | None = None
    max_drawdown_duration_bars: int | None = None
    cagr: float | None = None
    total_trades: int = 0
    win_rate: float | None = None
    profit_factor: float | None = None
    avg_holding_period_days: float | None = None
    correlation_to_spy: float | None = None
    correlation_to_btc: float | None = None
    monthly_returns: list[float] = field(default_factory=list)
    daily_returns: list[float] = field(default_factory=list)
    transaction_cost_bps: int = 10
    slippage_bps: int = 0
    walk_forward_split: float | None = None
    out_of_sample_sharpe: float | None = None
    look_ahead_audit_passed: bool = False
    backtest_engine: str = BACKTEST_ENGINE_TAG
    bars: int = 0
    backtest_start: str | None = None
    backtest_end: str | None = None


class BuyAndHoldStrategy(bt.Strategy):
    PAPER_ARXIV_ID: str | None = None
    PAPER_TITLE = "Buy-and-Hold Baseline"
    METHODOLOGY_TEXT = (
        "Allocate the full available cash to the single asset on the first bar "
        "where no position is open; hold the position to the end of the period."
    )
    PAPER_CLAIMED_SHARPE: float | None = None

    def next(self) -> None:
        if not self.position:
            size = int(self.broker.getcash() / self.data.close[0])
            if size > 0:
                self.buy(size=size)


def _safe_get(d: Any, *keys: str, default: Any = None) -> Any:
    cur = d
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def _build_equity_curve(initial_cash: float, daily_pairs: list[tuple]) -> list[float]:
    equity = initial_cash
    curve = [initial_cash]
    for _, value in daily_pairs:
        equity *= 1.0 + float(value)
        curve.append(float(equity))
    return curve


def _compute_sortino(daily_returns: list[float]) -> float | None:
    if not daily_returns:
        return None
    mean = statistics.fmean(daily_returns)
    downside = [r for r in daily_returns if r < 0]
    if not downside:
        return None
    dd_std = math.sqrt(sum(r * r for r in downside) / len(downside))
    if dd_std == 0:
        return None
    return (mean / dd_std) * math.sqrt(ANNUALIZATION)


def _compute_cagr(initial: float, final: float, bars: int) -> float | None:
    if bars <= 0 or initial <= 0 or final <= 0:
        return None
    years = bars / ANNUALIZATION
    if years <= 0:
        return None
    return (final / initial) ** (1.0 / years) - 1.0


def _trade_stats(
    trade: dict,
) -> tuple[int, float | None, float | None, float | None]:
    total_closed = _safe_get(trade, "total", "closed", default=0) or 0
    won = _safe_get(trade, "won", "total", default=0) or 0
    won_pnl = _safe_get(trade, "won", "pnl", "total", default=0.0) or 0.0
    lost_pnl = _safe_get(trade, "lost", "pnl", "total", default=0.0) or 0.0
    avg_len = _safe_get(trade, "len", "average", default=None)

    if total_closed == 0:
        return 0, None, None, None

    win_rate = won / total_closed
    profit_factor: float | None
    profit_factor = float(won_pnl) / abs(float(lost_pnl)) if lost_pnl else None
    avg_len_f = float(avg_len) if avg_len is not None else None
    return int(total_closed), float(win_rate), profit_factor, avg_len_f


def _lookahead_audit_passed(cerebro: bt.Cerebro) -> bool:
    coc = getattr(cerebro.broker.p, "coc", False)
    coo = getattr(cerebro.broker.p, "coo", False)
    return not coc and not coo


def run_backtest(
    prices: pd.DataFrame,
    *,
    strategy_cls: type[bt.Strategy],
    initial_cash: float,
    transaction_cost_bps: int = 10,
    slippage_bps: int = 0,
) -> BacktestResult:
    cerebro = bt.Cerebro(stdstats=False)
    cerebro.broker.setcash(initial_cash)
    cerebro.broker.setcommission(commission=transaction_cost_bps / 10_000)
    if slippage_bps > 0:
        cerebro.broker.set_slippage_perc(perc=slippage_bps / 10_000)

    feed = bt.feeds.PandasData(dataname=prices)
    cerebro.adddata(feed)
    cerebro.addstrategy(strategy_cls)
    cerebro.addanalyzer(bt.analyzers.TimeReturn, _name="timereturn")
    cerebro.addanalyzer(
        bt.analyzers.TimeReturn,
        _name="monthly",
        timeframe=bt.TimeFrame.Months,
    )
    cerebro.addanalyzer(
        bt.analyzers.SharpeRatio,
        _name="sharpe",
        timeframe=bt.TimeFrame.Days,
        annualize=True,
        riskfreerate=0.0,
    )
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="dd")
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")

    strategy = cerebro.run()[0]

    final_value = float(cerebro.broker.getvalue())
    total_return_pct = ((final_value / initial_cash) - 1.0) * 100.0

    time_returns_raw = strategy.analyzers.timereturn.get_analysis()
    daily_pairs = sorted(time_returns_raw.items(), key=lambda x: x[0])
    daily_returns = [float(v) for _, v in daily_pairs]
    equity_curve = _build_equity_curve(initial_cash, daily_pairs)

    monthly_raw = strategy.analyzers.monthly.get_analysis()
    monthly_returns = [float(v) for _, v in sorted(monthly_raw.items(), key=lambda x: x[0])]

    sharpe_raw = strategy.analyzers.sharpe.get_analysis().get("sharperatio")
    sharpe = float(sharpe_raw) if sharpe_raw is not None else None

    dd_analysis = strategy.analyzers.dd.get_analysis()
    max_dd_pct_raw = _safe_get(dd_analysis, "max", "drawdown")
    max_dd_len_raw = _safe_get(dd_analysis, "max", "len")
    max_dd_pct = float(max_dd_pct_raw) if max_dd_pct_raw is not None else None
    max_dd_len = int(max_dd_len_raw) if max_dd_len_raw is not None else None

    bars = len(prices)
    cagr = _compute_cagr(initial_cash, final_value, bars)

    calmar: float | None = None
    if cagr is not None and max_dd_pct is not None and max_dd_pct > 0:
        calmar = cagr / (max_dd_pct / 100.0)

    sortino = _compute_sortino(daily_returns)

    trades_analysis = strategy.analyzers.trades.get_analysis()
    total_trades, win_rate, profit_factor, avg_len = _trade_stats(trades_analysis)

    backtest_start = prices.index[0].isoformat() if bars else None
    backtest_end = prices.index[-1].isoformat() if bars else None

    look_ahead_passed = _lookahead_audit_passed(cerebro)

    return BacktestResult(
        final_value=final_value,
        total_return_pct=total_return_pct,
        equity_curve=equity_curve,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        calmar_ratio=calmar,
        max_drawdown_pct=max_dd_pct,
        max_drawdown_duration_bars=max_dd_len,
        cagr=cagr,
        total_trades=total_trades,
        win_rate=win_rate,
        profit_factor=profit_factor,
        avg_holding_period_days=avg_len,
        monthly_returns=monthly_returns,
        daily_returns=daily_returns,
        transaction_cost_bps=transaction_cost_bps,
        slippage_bps=slippage_bps,
        look_ahead_audit_passed=look_ahead_passed,
        bars=bars,
        backtest_start=backtest_start,
        backtest_end=backtest_end,
    )


def run_buy_and_hold(
    prices: pd.DataFrame,
    *,
    initial_cash: float,
    transaction_cost_bps: int = 10,
    slippage_bps: int = 0,
) -> BacktestResult:
    return run_backtest(
        prices,
        strategy_cls=BuyAndHoldStrategy,
        initial_cash=initial_cash,
        transaction_cost_bps=transaction_cost_bps,
        slippage_bps=slippage_bps,
    )
