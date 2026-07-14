from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any

import backtrader as bt
import pandas as pd

from archimedes_analytics_engine.costs import CostModel, TurnoverAnalyzer

ANNUALIZATION = 252
RF_ANNUAL = 0.05  # 5% annual risk-free rate
RF_DAILY = RF_ANNUAL / ANNUALIZATION
BACKTEST_ENGINE_TAG = "backtrader"


class FeedArityError(ValueError):
    """A strategy that hard-requires N aligned data feeds was given fewer.

    Raised fail-closed at the runner boundary (issue #887) instead of letting
    a pairs strategy crash mid-run with a bare ``IndexError`` on
    ``self.datas[1]``. The message is passport-grade: it says what the
    strategy needs ("pairs needs >= 2 instruments") rather than where a list
    index went out of range.
    """


def required_feeds(strategy_cls: type[bt.Strategy]) -> int:
    """How many aligned data feeds ``strategy_cls`` declares it hard-requires.

    Strategies that index ``self.datas[1]`` (the pairs family) declare
    ``REQUIRED_FEEDS = 2`` as a class attribute; anything undeclared or
    malformed is treated as the single-feed default of 1.
    """
    try:
        return max(1, int(getattr(strategy_cls, "REQUIRED_FEEDS", 1) or 1))
    except (TypeError, ValueError):
        return 1


@dataclass
class BacktestResult:
    """Container for every metric a single backtest run produces.

    Aggregates performance, risk, trade-level, cost-realism, and provenance
    fields into one passport-ready record. Optional metrics are ``None`` when
    they are undefined for the run (e.g. ``sortino_ratio`` when the strategy
    never had a losing day). All ratios follow the engine's annualized
    conventions (252 trading days, 5% annual risk-free rate).

    Attributes
    ----------
    final_value : float
        Portfolio value at the end of the backtest, in account currency.
    total_return_pct : float
        Total return over the whole period, in percent.
    equity_curve : list of float
        Per-bar portfolio value, seeded with the initial cash at index 0, so
        ``len(equity_curve) == len(daily_returns) + 1``.
    sharpe_ratio : float or None
        Annualized net Sharpe (commissions and slippage included).
    daily_returns : list of float
        Per-bar net returns.
    daily_return_dates : list of str
        ISO ``"YYYY-MM-DD"`` dates aligned 1:1 with ``daily_returns``.
    gross_sharpe_ratio : float or None
        Sharpe with commissions added back (slippage is not recoverable).
    look_ahead_audit_passed : bool
        ``True`` when the backtrader broker used neither cheat-on-close nor
        cheat-on-open execution timing. **This is a broker-execution-timing
        check only** — it is derived solely from ``cerebro.broker.p.coc`` /
        ``cerebro.broker.p.coo``, which this engine never sets to ``True``,
        so the field is unconditionally ``True`` for every run regardless of
        whether the strategy's own signal logic has a look-ahead bug. It does
        NOT perform any source-level audit of the strategy code. For a real
        source-level look-ahead audit, see ``look_ahead_audit()`` in
        ``backend/archimedes/services/rigor_evaluator.py`` (AST-based, checks
        the strategy's own code for future-data access), which is a separate
        system from this analytics engine.

    See Also
    --------
    run_backtest : Produces this result for a single-asset strategy.
    """

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
    # ISO dates ("YYYY-MM-DD") aligned 1:1 with daily_returns — lets callers join
    # return series across strategies whose feeds trade different calendars
    # (e.g. ^N225 vs SPY) for library-level metrics such as CSCV PBO.
    daily_return_dates: list[str] = field(default_factory=list)
    transaction_cost_bps: int = 10
    slippage_bps: int = 0
    # Turnover / cost-realism metrics (see costs.py for conventions).
    turnover_annualized: float | None = None  # one-way, x/year
    traded_notional: float = 0.0  # two-way, total over the backtest
    total_commission_paid: float = 0.0
    cost_drag_annual_pct: float | None = None  # annualized commission as % of avg equity
    break_even_cost_bps: float | None = None  # per-side bps at which gross CAGR is consumed
    gross_sharpe_ratio: float | None = None  # Sharpe with commissions added back (not slippage)
    walk_forward_split: float | None = None
    out_of_sample_sharpe: float | None = None
    look_ahead_audit_passed: bool = False
    backtest_engine: str = BACKTEST_ENGINE_TAG
    bars: int = 0
    backtest_start: str | None = None
    backtest_end: str | None = None


class BuyAndHoldStrategy(bt.Strategy):
    """Baseline strategy: buy the asset on the first flat bar and hold to the end.

    Allocates all available cash to a single long position the first time no
    position is open, then never trades again — the passive benchmark every
    active strategy is measured against.
    """

    PAPER_ARXIV_ID: str | None = None
    PAPER_TITLE = "Buy-and-Hold Baseline"
    METHODOLOGY_TEXT = (
        "Allocate the full available cash to the single asset on the first bar "
        "where no position is open; hold the position to the end of the period."
    )
    PAPER_CLAIMED_SHARPE: float | None = None

    def next(self) -> None:
        if not self.position:
            if self.data.close[0] <= 0:
                return
            # Target ~100% of equity rather than int(cash / price): the latter
            # floors share count and strands up to (price - ε) of capital per
            # trade, biasing this passive benchmark's return downward (and so
            # flattering every active strategy measured against it).
            self.order_target_percent(data=self.data, target=1.0)


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
    # Denominator is the TOTAL sample size (not just the count of negative
    # days) per the standard Sortino/target-semideviation convention (e.g.
    # empyrical.downside_risk) — non-negative days contribute 0 to the sum but
    # still count in the denominator. Dividing by len(downside) alone inflates
    # the ratio for strategies with a skewed win/loss-day ratio.
    dd_rms = math.sqrt(sum(r * r for r in downside) / len(daily_returns))
    if dd_rms == 0:
        return None
    return ((mean - RF_DAILY) / dd_rms) * math.sqrt(ANNUALIZATION)


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


def _sharpe_bt_convention(daily_returns: list[float]) -> float | None:
    """Sharpe under the same convention as the bt.analyzers.SharpeRatio config in
    _add_analyzers (annualized, geometric daily risk-free, population stddev), so
    gross-vs-net Sharpe comparisons are apples-to-apples (zero-cost => equal)."""
    if len(daily_returns) < 2:
        return None
    rf_daily_geo = (1.0 + RF_ANNUAL) ** (1.0 / ANNUALIZATION) - 1.0
    excess = [r - rf_daily_geo for r in daily_returns]
    dev = statistics.pstdev(excess)
    if dev == 0:
        return None
    return (statistics.fmean(excess) / dev) * math.sqrt(ANNUALIZATION)


def _cost_metrics(
    *,
    turnover_analysis: dict,
    daily_returns: list[float],
    equity_curve: list[float],
    bars: int,
) -> dict[str, float | None]:
    """Derive turnover / cost-drag / break-even / gross-Sharpe from the
    TurnoverAnalyzer output. Conventions documented in costs.py."""
    traded_notional = float(turnover_analysis.get("traded_notional", 0.0))
    commission_paid = float(turnover_analysis.get("commission_paid", 0.0))
    bar_commissions = turnover_analysis.get("bar_commissions", [])

    metrics: dict[str, float | None] = {
        "turnover_annualized": None,
        "traded_notional": traded_notional,
        "total_commission_paid": commission_paid,
        "cost_drag_annual_pct": None,
        "break_even_cost_bps": None,
        "gross_sharpe_ratio": None,
    }

    avg_equity = statistics.fmean(equity_curve) if equity_curve else 0.0
    years = bars / ANNUALIZATION
    if avg_equity <= 0 or years <= 0:
        return metrics

    turnover_annualized = (traded_notional / 2.0) / avg_equity / years
    metrics["turnover_annualized"] = turnover_annualized
    metrics["cost_drag_annual_pct"] = (commission_paid / avg_equity / years) * 100.0

    # Gross (commissions-added-back) return series. Positional alignment with
    # daily_returns holds because both analyzers fire once per bar; if it ever
    # doesn't, report None rather than misreport.
    if len(bar_commissions) != len(daily_returns) or not daily_returns:
        return metrics

    gross_returns: list[float] = []
    for i, net_r in enumerate(daily_returns):
        prev_equity = equity_curve[i]  # equity_curve[0] is initial cash
        if prev_equity <= 0:
            return metrics
        gross_returns.append(net_r + float(bar_commissions[i]) / prev_equity)

    metrics["gross_sharpe_ratio"] = _sharpe_bt_convention(gross_returns)

    gross_growth = 1.0
    for g in gross_returns:
        gross_growth *= 1.0 + g
    if gross_growth > 0:
        gross_cagr = gross_growth ** (1.0 / years) - 1.0
        if turnover_annualized > 0:
            # Annual cost at per-side cost c = 2 * one-way turnover * c.
            metrics["break_even_cost_bps"] = max(gross_cagr / (2.0 * turnover_annualized) * 10_000.0, 0.0)

    return metrics


def _lookahead_audit_passed(cerebro: bt.Cerebro) -> bool:
    """Check the broker's execution-timing config only.

    NOT a source-level look-ahead audit of the strategy's signal logic — this
    engine never sets ``coc``/``coo`` to ``True`` anywhere, so this always
    returns ``True``. It only guards against a future misconfiguration that
    would let the broker cheat-execute on the same bar's close/open. The real
    AST-based check of strategy code lives in
    ``backend/archimedes/services/rigor_evaluator.look_ahead_audit`` — a
    separate system, not invoked from here.
    """
    coc = getattr(cerebro.broker.p, "coc", False)
    coo = getattr(cerebro.broker.p, "coo", False)
    return not coc and not coo


def _configure_broker(
    cerebro: bt.Cerebro,
    *,
    initial_cash: float,
    transaction_cost_bps: int,
    slippage_bps: int,
    cost_model: CostModel | None,
    feed_names: list[str],
) -> tuple[int, int]:
    """Install cash + costs on the broker; shared by all runners.

    When ``cost_model`` is given it supersedes the flat ``transaction_cost_bps``
    / ``slippage_bps`` arguments (per-feed overrides included). Returns the
    (transaction_cost_bps, slippage_bps) actually recorded on the result —
    the model's defaults when a model is used; per-symbol detail stays in the
    model, not the summary fields.
    """
    cerebro.broker.setcash(initial_cash)
    if cost_model is not None:
        cost_model.apply_to_broker(cerebro, feed_names)
        return int(round(cost_model.default_bps)), int(round(cost_model.slippage_bps))
    cerebro.broker.setcommission(commission=transaction_cost_bps / 10_000)
    if slippage_bps > 0:
        cerebro.broker.set_slippage_perc(perc=slippage_bps / 10_000)
    return transaction_cost_bps, slippage_bps


def run_backtest(
    prices: pd.DataFrame,
    *,
    strategy_cls: type[bt.Strategy],
    initial_cash: float,
    transaction_cost_bps: int = 10,
    slippage_bps: int = 0,
    cost_model: CostModel | None = None,
    strategy_params: dict | None = None,
) -> BacktestResult:
    """Run a single-asset strategy over one OHLCV frame and extract its metrics.

    Parameters
    ----------
    prices : pandas.DataFrame
        OHLCV data with a ``DatetimeIndex``; columns ``Open``, ``High``,
        ``Low``, ``Close``, ``Volume`` as consumed by ``bt.feeds.PandasData``.
    strategy_cls : type[backtrader.Strategy]
        The strategy class to run.
    initial_cash : float
        Starting account cash.
    transaction_cost_bps : int, default 10
        Flat per-side commission in basis points. Ignored when ``cost_model``
        is supplied.
    slippage_bps : int, default 0
        Flat percent slippage in basis points. Ignored when ``cost_model``
        is supplied.
    cost_model : CostModel or None, default None
        Per-feed cost model; supersedes the flat ``transaction_cost_bps`` /
        ``slippage_bps`` arguments when given.
    strategy_params : dict or None, default None
        Keyword parameters forwarded to ``strategy_cls``.

    Returns
    -------
    BacktestResult
        Full metric record for the run.

    Raises
    ------
    FeedArityError
        If ``strategy_cls`` declares ``REQUIRED_FEEDS > 1`` — a pairs /
        multi-feed strategy cannot run on a single feed and must go through
        :func:`run_pairs_backtest` or :func:`run_multi_backtest`.
    """
    required = required_feeds(strategy_cls)
    if required > 1:
        raise FeedArityError(
            f"strategy {strategy_cls.__name__} requires {required} aligned data feeds "
            "but the single-feed runner provides 1 — a pairs strategy needs >= 2 "
            "instruments; run it via run_pairs_backtest/run_multi_backtest with its "
            "declared asset universe"
        )

    cerebro = bt.Cerebro(stdstats=False)
    transaction_cost_bps, slippage_bps = _configure_broker(
        cerebro,
        initial_cash=initial_cash,
        transaction_cost_bps=transaction_cost_bps,
        slippage_bps=slippage_bps,
        cost_model=cost_model,
        feed_names=[],
    )

    feed = bt.feeds.PandasData(dataname=prices)
    cerebro.adddata(feed)
    cerebro.addstrategy(strategy_cls, **(strategy_params or {}))
    _add_analyzers(cerebro)

    strategy = cerebro.run()[0]

    bars = len(prices)
    return _extract_result(
        cerebro=cerebro,
        strategy=strategy,
        initial_cash=initial_cash,
        bars=bars,
        backtest_start=prices.index[0].isoformat() if bars else None,
        backtest_end=prices.index[-1].isoformat() if bars else None,
        transaction_cost_bps=transaction_cost_bps,
        slippage_bps=slippage_bps,
    )


def _extract_result(
    *,
    cerebro: bt.Cerebro,
    strategy: bt.Strategy,
    initial_cash: float,
    bars: int,
    backtest_start: str | None,
    backtest_end: str | None,
    transaction_cost_bps: int,
    slippage_bps: int,
) -> BacktestResult:
    """Pull metrics off a finished cerebro run. Shared by single- and multi-asset runners."""
    final_value = float(cerebro.broker.getvalue())
    total_return_pct = ((final_value / initial_cash) - 1.0) * 100.0

    time_returns_raw = strategy.analyzers.timereturn.get_analysis()
    daily_pairs = sorted(time_returns_raw.items(), key=lambda x: x[0])
    daily_returns = [float(v) for _, v in daily_pairs]
    # TimeReturn keys are datetimes; isoformat()[:10] gives the bare ISO date.
    daily_return_dates = [k.isoformat()[:10] for k, _ in daily_pairs]
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

    cagr = _compute_cagr(initial_cash, final_value, bars)

    calmar: float | None = None
    if cagr is not None and max_dd_pct is not None and max_dd_pct > 0:
        calmar = cagr / (max_dd_pct / 100.0)

    sortino = _compute_sortino(daily_returns)

    trades_analysis = strategy.analyzers.trades.get_analysis()
    total_trades, win_rate, profit_factor, avg_len = _trade_stats(trades_analysis)

    look_ahead_passed = _lookahead_audit_passed(cerebro)

    cost_metrics = _cost_metrics(
        turnover_analysis=strategy.analyzers.turnover.get_analysis(),
        daily_returns=daily_returns,
        equity_curve=equity_curve,
        bars=bars,
    )

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
        daily_return_dates=daily_return_dates,
        transaction_cost_bps=transaction_cost_bps,
        slippage_bps=slippage_bps,
        turnover_annualized=cost_metrics["turnover_annualized"],
        traded_notional=cost_metrics["traded_notional"],
        total_commission_paid=cost_metrics["total_commission_paid"],
        cost_drag_annual_pct=cost_metrics["cost_drag_annual_pct"],
        break_even_cost_bps=cost_metrics["break_even_cost_bps"],
        gross_sharpe_ratio=cost_metrics["gross_sharpe_ratio"],
        look_ahead_audit_passed=look_ahead_passed,
        bars=bars,
        backtest_start=backtest_start,
        backtest_end=backtest_end,
    )


def _add_analyzers(cerebro: bt.Cerebro) -> None:
    cerebro.addanalyzer(bt.analyzers.TimeReturn, _name="timereturn")
    cerebro.addanalyzer(bt.analyzers.TimeReturn, _name="monthly", timeframe=bt.TimeFrame.Months)
    cerebro.addanalyzer(
        bt.analyzers.SharpeRatio,
        _name="sharpe",
        timeframe=bt.TimeFrame.Days,
        annualize=True,
        riskfreerate=RF_ANNUAL,
    )
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="dd")
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
    cerebro.addanalyzer(TurnoverAnalyzer, _name="turnover")


def run_pairs_backtest(
    prices_a: pd.DataFrame,
    prices_b: pd.DataFrame,
    *,
    strategy_cls: type[bt.Strategy],
    initial_cash: float,
    name_a: str = "leg_a",
    name_b: str = "leg_b",
    transaction_cost_bps: int = 10,
    slippage_bps: int = 0,
    cost_model: CostModel | None = None,
    strategy_params: dict | None = None,
) -> BacktestResult:
    """Run a two-asset (pairs / relative-value) strategy in a single cerebro.

    Both price frames are inner-joined on their datetime index first so the two
    feeds are bar-aligned — the strategy can rely on ``self.datas[0]`` and
    ``self.datas[1]`` advancing together. Metrics are computed on portfolio value,
    so the existing analyzer + extraction path is reused unchanged.
    """
    common_index = prices_a.index.intersection(prices_b.index)
    if len(common_index) == 0:
        raise ValueError("prices_a and prices_b share no common dates; cannot align pair feeds")
    aligned_a = prices_a.loc[common_index].sort_index()
    aligned_b = prices_b.loc[common_index].sort_index()

    cerebro = bt.Cerebro(stdstats=False)
    transaction_cost_bps, slippage_bps = _configure_broker(
        cerebro,
        initial_cash=initial_cash,
        transaction_cost_bps=transaction_cost_bps,
        slippage_bps=slippage_bps,
        cost_model=cost_model,
        feed_names=[name_a, name_b],
    )

    cerebro.adddata(bt.feeds.PandasData(dataname=aligned_a), name=name_a)
    cerebro.adddata(bt.feeds.PandasData(dataname=aligned_b), name=name_b)
    cerebro.addstrategy(strategy_cls, **(strategy_params or {}))
    _add_analyzers(cerebro)

    strategy = cerebro.run()[0]

    bars = len(common_index)
    return _extract_result(
        cerebro=cerebro,
        strategy=strategy,
        initial_cash=initial_cash,
        bars=bars,
        backtest_start=common_index[0].isoformat() if bars else None,
        backtest_end=common_index[-1].isoformat() if bars else None,
        transaction_cost_bps=transaction_cost_bps,
        slippage_bps=slippage_bps,
    )


def run_multi_backtest(
    prices_list: list[pd.DataFrame],
    *,
    strategy_cls: type[bt.Strategy],
    initial_cash: float,
    names: list[str] | None = None,
    transaction_cost_bps: int = 10,
    slippage_bps: int = 0,
    cost_model: CostModel | None = None,
    strategy_params: dict | None = None,
) -> BacktestResult:
    """Run an N-asset (cross-sectional / portfolio) strategy in a single cerebro.

    The N-feed generalization of :func:`run_pairs_backtest`. All price frames are
    inner-joined on their common datetime index first, so every feed advances
    together bar-for-bar — the strategy can rely on ``self.datas[i]`` for
    ``i in range(N)`` being aligned and rank/weight across the universe each bar.
    Metrics are computed on portfolio value, so the existing analyzer +
    extraction path (:func:`_add_analyzers` / :func:`_extract_result`) is reused
    unchanged, keeping metric shapes identical across single / pair / N-asset runs.
    """
    if not prices_list:
        raise ValueError("prices_list is empty; need at least one price frame")
    if names is not None and len(names) != len(prices_list):
        raise ValueError(f"names has {len(names)} entries but prices_list has {len(prices_list)}")

    feed_names = names if names is not None else [f"leg_{i}" for i in range(len(prices_list))]

    # N-way index intersection: only bars present in every feed are backtested.
    common_index = prices_list[0].index
    for prices in prices_list[1:]:
        common_index = common_index.intersection(prices.index)
    if len(common_index) == 0:
        raise ValueError("price frames share no common dates; cannot align feeds")

    cerebro = bt.Cerebro(stdstats=False)
    transaction_cost_bps, slippage_bps = _configure_broker(
        cerebro,
        initial_cash=initial_cash,
        transaction_cost_bps=transaction_cost_bps,
        slippage_bps=slippage_bps,
        cost_model=cost_model,
        feed_names=feed_names,
    )

    for prices, name in zip(prices_list, feed_names, strict=True):
        aligned = prices.loc[common_index].sort_index()
        cerebro.adddata(bt.feeds.PandasData(dataname=aligned), name=name)
    cerebro.addstrategy(strategy_cls, **(strategy_params or {}))
    _add_analyzers(cerebro)

    strategy = cerebro.run()[0]

    bars = len(common_index)
    return _extract_result(
        cerebro=cerebro,
        strategy=strategy,
        initial_cash=initial_cash,
        bars=bars,
        backtest_start=common_index[0].isoformat() if bars else None,
        backtest_end=common_index[-1].isoformat() if bars else None,
        transaction_cost_bps=transaction_cost_bps,
        slippage_bps=slippage_bps,
    )


def run_buy_and_hold(
    prices: pd.DataFrame,
    *,
    initial_cash: float,
    transaction_cost_bps: int = 10,
    slippage_bps: int = 0,
) -> BacktestResult:
    """Run the passive :class:`BuyAndHoldStrategy` benchmark over ``prices``.

    Convenience wrapper around :func:`run_backtest` that fixes the strategy to
    the buy-and-hold baseline. Parameters and return value mirror
    :func:`run_backtest`.

    Returns
    -------
    BacktestResult
        Metric record for the buy-and-hold run.
    """
    return run_backtest(
        prices,
        strategy_cls=BuyAndHoldStrategy,
        initial_cash=initial_cash,
        transaction_cost_bps=transaction_cost_bps,
        slippage_bps=slippage_bps,
    )
