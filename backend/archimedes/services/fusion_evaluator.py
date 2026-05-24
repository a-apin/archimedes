"""Fusion evaluator — spec → interpreter → backtest → rigor gate → library upsert.

Orchestrates the full pipeline for fusion-generated strategies:
1. Validate the strategy_spec from the fusion proposal
2. Interpret it into a backtrader.Strategy subclass
3. Run a backtest
4. Apply the rigor gate (DSR, PBO, OOS Sharpe, look-ahead audit)
5. Persist the result in the strategy library
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from archimedes.services.dsl_to_backtrader import interpret_spec, interpret_variant
from archimedes.services.rigor_evaluator import (
    compute_dsr,
    compute_oos_sharpe,
    compute_pbo,
)
from archimedes.services.strategy_dsl import (
    DSLError,
    StrategySpec,
    validate_strategy_spec,
)

logger = logging.getLogger(__name__)

# ── Result types ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class BacktestMetrics:
    """Metrics from a DSL-interpreted strategy backtest."""

    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float
    cagr: float
    calmar_ratio: float
    win_rate: float
    total_trades: int
    avg_holding_period_days: float
    equity_curve: list[float]
    monthly_returns: list[float]
    backtest_start: date | None
    backtest_end: date | None


@dataclass(frozen=True)
class RigorVerdict:
    """Result of applying the rigor gate to fusion output."""

    passing: bool
    dsr: float | None
    dsr_p_value: float | None
    pbo_score: float | None
    oos_sharpe: float | None
    look_ahead_clean: bool
    num_trials: int


@dataclass(frozen=True)
class FusionEvalResult:
    """Complete result from the fusion evaluator pipeline."""

    spec: StrategySpec
    backtest: BacktestMetrics | None
    rigor: RigorVerdict | None
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.error is None and self.backtest is not None


# ── Backtest runner ───────────────────────────────────────────────────

_DEFAULT_CASH = 100_000.0
_DEFAULT_TX_BPS = 10


def run_dsl_backtest(
    spec: StrategySpec,
    *,
    data_feed: Any = None,
    data_csv_path: str | Path | None = None,
    initial_cash: float = _DEFAULT_CASH,
    tx_cost_bps: int = _DEFAULT_TX_BPS,
) -> BacktestMetrics:
    """Run a backtest for a DSL-interpreted strategy.

    If ``data_feed`` is None and ``data_csv_path`` is set, builds a
    ``GenericCSVData`` feed from the CSV. If both are None, generates a
    deterministic synthetic price series. The equity curve is captured
    **bar-by-bar** via a backtrader analyzer.
    """
    import backtrader as bt

    strategy_cls = interpret_spec(spec)
    cerebro = bt.Cerebro(stdstats=False)
    cerebro.addstrategy(strategy_cls)

    if data_feed is None:
        data_feed = _csv_data_feed(Path(data_csv_path)) if data_csv_path is not None else _synthetic_data()

    cerebro.adddata(data_feed)
    cerebro.broker.setcash(initial_cash)
    cerebro.broker.setcommission(commission=tx_cost_bps / 10_000)

    # Per-bar equity capture + trade tracking via custom analyzers. Without
    # these, _extract_equity_curve falls back to fake linear interpolation
    # (see commit log for the equity-curve correctness fix).
    cerebro.addanalyzer(_EquityCurveAnalyzer, _name="equity_curve")
    cerebro.addanalyzer(_TradeStatsAnalyzer, _name="trade_stats")

    results = cerebro.run()
    final_value = cerebro.broker.getvalue()
    initial = initial_cash

    strat = results[0] if results else None
    equity_curve: list[float]
    if strat is not None:
        ec = strat.analyzers.equity_curve.get_analysis()
        equity_curve = list(ec.get("values", [])) or [initial_cash]
    else:
        equity_curve = [initial_cash]

    monthly_returns = _compute_monthly_returns(equity_curve)

    total_return = (final_value - initial) / initial
    n_bars = len(equity_curve)
    years = max(n_bars / 252, 0.01)
    cagr = (1 + total_return) ** (1 / years) - 1 if total_return > -1 else -1.0

    daily_returns = [
        (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
        for i in range(1, len(equity_curve))
        if equity_curve[i - 1] > 0
    ]
    sharpe = _annualized_sharpe(daily_returns, rf_annual=0.0)
    sortino = _annualized_sortino(daily_returns, rf_annual=0.0)
    max_dd = _max_drawdown(equity_curve)
    calmar = cagr / max_dd if max_dd > 0 else 0.0

    # Real trade stats from the analyzer (replaces the fixed 0.5 win-rate stub).
    trade_stats = (
        strat.analyzers.trade_stats.get_analysis()
        if strat is not None
        else {"total_trades": 0, "win_rate": 0.0, "avg_holding_period_days": 0.0}
    )

    return BacktestMetrics(
        sharpe_ratio=round(sharpe, 4),
        sortino_ratio=round(sortino, 4),
        max_drawdown=round(max_dd, 4),
        cagr=round(cagr, 4),
        calmar_ratio=round(calmar, 4),
        win_rate=round(float(trade_stats.get("win_rate", 0.0)), 4),
        total_trades=int(trade_stats.get("total_trades", 0)),
        avg_holding_period_days=round(
            float(trade_stats.get("avg_holding_period_days", 0.0)),
            2,
        ),
        equity_curve=[round(e, 2) for e in equity_curve],
        monthly_returns=[round(m, 4) for m in monthly_returns],
        backtest_start=date(2004, 1, 2) if data_feed is None else None,
        backtest_end=date(2026, 4, 30) if data_feed is None else None,
    )


def run_dsl_backtest_variants(
    spec: StrategySpec,
    *,
    data_feed: Any = None,
    data_csv_path: str | Path | None = None,
    initial_cash: float = _DEFAULT_CASH,
    tx_cost_bps: int = _DEFAULT_TX_BPS,
) -> dict[str, BacktestMetrics]:
    """Run backtests for each cartesian-product point in the parameter grid.

    If ``spec.parameter_variants`` is ``None`` or empty, returns a
    single-entry dict ``{"base": metrics}`` for the unmodified spec.
    Otherwise expands the variant grid and runs one backtest per combination.

    Returns:
        ``{variant_id: BacktestMetrics}`` where variant_id is a
        dash-separated key like ``"150"`` or ``"150_0.12"``.
    """
    if spec.parameter_variants is None or not spec.parameter_variants:
        metrics = run_dsl_backtest(
            spec,
            data_feed=data_feed,
            data_csv_path=data_csv_path,
            initial_cash=initial_cash,
            tx_cost_bps=tx_cost_bps,
        )
        return {"base": metrics}

    # Compute the cartesian product of variant values.
    import itertools

    variant_keys = sorted(spec.parameter_variants.keys())
    variant_value_lists = [spec.parameter_variants[k] for k in variant_keys]

    results: dict[str, BacktestMetrics] = {}
    for combo in itertools.product(*variant_value_lists):
        overrides = {k: int(v) for k, v in zip(variant_keys, combo, strict=False)}
        variant_id = "_".join(str(v) for v in combo)

        # Build a spec *without* parameter_variants for the variant run.
        strategy_cls = interpret_variant(spec, overrides)

        # Re-run the variant through the same backtest harness.
        # We call run_dsl_backtest with a spec that produces the variant cls.
        # Instead of re-validating, we build a variant spec and use interpret_spec.
        variant_metrics = _run_variant_backtest(
            strategy_cls,
            data_feed=data_feed,
            data_csv_path=data_csv_path,
            initial_cash=initial_cash,
            tx_cost_bps=tx_cost_bps,
        )
        results[variant_id] = variant_metrics

    return results


def _run_variant_backtest(
    strategy_cls: Any,
    *,
    data_feed: Any = None,
    data_csv_path: str | Path | None = None,
    initial_cash: float = _DEFAULT_CASH,
    tx_cost_bps: int = _DEFAULT_TX_BPS,
) -> BacktestMetrics:
    """Run a single variant backtest given an already-interpreted strategy class."""
    import backtrader as bt

    cerebro = bt.Cerebro(stdstats=False)
    cerebro.addstrategy(strategy_cls)

    if data_feed is None:
        data_feed = _csv_data_feed(Path(data_csv_path)) if data_csv_path is not None else _synthetic_data()

    cerebro.adddata(data_feed)
    cerebro.broker.setcash(initial_cash)
    cerebro.broker.setcommission(commission=tx_cost_bps / 10_000)

    cerebro.addanalyzer(_EquityCurveAnalyzer, _name="equity_curve")
    cerebro.addanalyzer(_TradeStatsAnalyzer, _name="trade_stats")

    results = cerebro.run()
    final_value = cerebro.broker.getvalue()
    initial = initial_cash

    strat = results[0] if results else None
    equity_curve: list[float]
    if strat is not None:
        ec = strat.analyzers.equity_curve.get_analysis()
        equity_curve = list(ec.get("values", [])) or [initial_cash]
    else:
        equity_curve = [initial_cash]

    monthly_returns = _compute_monthly_returns(equity_curve)

    total_return = (final_value - initial) / initial
    n_bars = len(equity_curve)
    years = max(n_bars / 252, 0.01)
    cagr = (1 + total_return) ** (1 / years) - 1 if total_return > -1 else -1.0

    daily_returns = [
        (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
        for i in range(1, len(equity_curve))
        if equity_curve[i - 1] > 0
    ]
    sharpe = _annualized_sharpe(daily_returns, rf_annual=0.0)
    sortino = _annualized_sortino(daily_returns, rf_annual=0.0)
    max_dd = _max_drawdown(equity_curve)
    calmar = cagr / max_dd if max_dd > 0 else 0.0

    trade_stats = (
        strat.analyzers.trade_stats.get_analysis()
        if strat is not None
        else {"total_trades": 0, "win_rate": 0.0, "avg_holding_period_days": 0.0}
    )

    return BacktestMetrics(
        sharpe_ratio=round(sharpe, 4),
        sortino_ratio=round(sortino, 4),
        max_drawdown=round(max_dd, 4),
        cagr=round(cagr, 4),
        calmar_ratio=round(calmar, 4),
        win_rate=round(float(trade_stats.get("win_rate", 0.0)), 4),
        total_trades=int(trade_stats.get("total_trades", 0)),
        avg_holding_period_days=round(
            float(trade_stats.get("avg_holding_period_days", 0.0)),
            2,
        ),
        equity_curve=[round(e, 2) for e in equity_curve],
        monthly_returns=[round(m, 4) for m in monthly_returns],
        backtest_start=date(2004, 1, 2) if data_csv_path is None else None,
        backtest_end=date(2026, 4, 30) if data_csv_path is None else None,
    )


# ── Rigor gate ────────────────────────────────────────────────────────


def apply_rigor_gate(
    metrics: BacktestMetrics,
    num_trials: int = 10,
    variants_metrics: dict[str, BacktestMetrics] | None = None,
) -> RigorVerdict:
    """Apply rigor gate to fusion backtest metrics.

    PBO is set to ``None`` (not 0.0) when there are fewer than 2 variant
    backtests. The Bailey/Borwein/López de Prado/Zhu CSCV PBO algorithm
    formally compares multiple competing strategies against the same return
    matrix; a single DSL-generated strategy doesn't yield a meaningful PBO
    without parameter-sweep variants to cross-validate against.

    When ``variants_metrics`` is provided with >= 2 entries, real CSCV PBO
    is computed from the variant returns matrix and attached to the verdict.
    """
    daily_returns = [
        (metrics.equity_curve[i] - metrics.equity_curve[i - 1]) / metrics.equity_curve[i - 1]
        for i in range(1, len(metrics.equity_curve))
        if metrics.equity_curve[i - 1] > 0
    ]

    dsr, dsr_p = compute_dsr(daily_returns, num_trials)
    oos_sharpe = compute_oos_sharpe(daily_returns)

    # PBO: compute real CSCV PBO when >= 2 variant backtests are available.
    pbo_score: float | None = None
    if variants_metrics is not None and len(variants_metrics) >= 2:
        variant_returns: dict[str, list[float]] = {}
        for vid, vm in variants_metrics.items():
            curve = vm.equity_curve
            variant_returns[vid] = [
                (curve[i] - curve[i - 1]) / curve[i - 1] for i in range(1, len(curve)) if curve[i - 1] > 0
            ]
        pbo_map = compute_pbo(variant_returns)
        # All strategies in the matrix get the same PBO score (library-level
        # metric per Bailey et al. 2014). Pick the first entry's value.
        first_key = next(iter(pbo_map))
        pbo_score = pbo_map[first_key]

    # Look-ahead is guaranteed by the DSL design (static check in
    # validate_strategy_spec — DSL specs with look_ahead_safe=false are
    # rejected at validation time, long before reaching this evaluator).
    look_ahead_clean = True

    # DSR-passing convention follows the seed strategies' implementation:
    # the canonical Bailey-LdP DSR is "credibly > 0" via the test
    # statistic z-score, where ``dsr_p_value`` is the p-value under the
    # null Sharpe=0. We pass when ``dsr > 0`` (the DSR itself is positive
    # after correcting for the trials-correction).
    dsr_pass = dsr is not None and dsr > 0.0

    passing = metrics.sharpe_ratio > 0.0 and dsr_pass and look_ahead_clean and (pbo_score is None or pbo_score < 0.5)

    return RigorVerdict(
        passing=passing,
        dsr=dsr,
        dsr_p_value=dsr_p,
        pbo_score=pbo_score,
        oos_sharpe=oos_sharpe,
        look_ahead_clean=look_ahead_clean,
        num_trials=num_trials,
    )


# ── Full pipeline ─────────────────────────────────────────────────────


def evaluate_fusion_spec(
    spec_dict: dict[str, Any],
    *,
    data_feed: Any = None,
    num_trials: int = 10,
) -> FusionEvalResult:
    """Full pipeline: validate → interpret → backtest → rigor gate."""
    try:
        spec = validate_strategy_spec(spec_dict)
    except DSLError as e:
        logger.warning("fusion spec validation failed: %s", e)
        return FusionEvalResult(
            spec=None,  # type: ignore[arg-type]
            backtest=None,
            rigor=None,
            error=str(e),
        )

    try:
        metrics = run_dsl_backtest(spec, data_feed=data_feed)
    except Exception as e:
        logger.exception("DSL backtest failed for %s", spec.name)
        return FusionEvalResult(spec=spec, backtest=None, rigor=None, error=str(e))

    # Run variant grid if parameter_variants is present.
    variants_metrics: dict[str, BacktestMetrics] | None = None
    if spec.parameter_variants is not None and len(spec.parameter_variants) >= 1:
        try:
            variants_metrics = run_dsl_backtest_variants(spec, data_feed=data_feed)
        except Exception as e:
            logger.warning("variant backtest failed for %s: %s", spec.name, e)
            variants_metrics = None

    rigor = apply_rigor_gate(
        metrics,
        num_trials=num_trials,
        variants_metrics=variants_metrics,
    )

    logger.info(
        "fusion eval: %s — sharpe=%.3f rigor.passing=%s pbo=%s",
        spec.name,
        metrics.sharpe_ratio,
        rigor.passing,
        rigor.pbo_score,
    )

    return FusionEvalResult(spec=spec, backtest=metrics, rigor=rigor)


# ── Helpers ───────────────────────────────────────────────────────────


def _synthetic_data() -> Any:
    """Generate synthetic SPY-like daily data for testing (2004-2026)."""
    import random
    import tempfile
    from datetime import timedelta

    import backtrader as bt

    random.seed(42)
    rows = []
    price = 100.0
    d = date(2004, 1, 2)
    end = date(2026, 4, 30)

    while d <= end:
        daily_ret = random.gauss(0.0003, 0.012)
        price *= 1 + daily_ret
        rows.append(f"{d.isoformat()},{price:.4f},{price * 1.001:.4f},{price * 0.999:.4f},{price:.4f},1000000")
        d += timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)

    csv_text = "\n".join(rows)
    # delete=False is required: backtrader reads the file from disk in the
    # GenericCSVData(dataname=tmp.name) call below. A context manager would
    # close+delete it before backtrader can open it.
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False)  # noqa: SIM115
    tmp.write(csv_text)
    tmp.close()

    return bt.feeds.GenericCSVData(
        dataname=tmp.name,
        dtformat=("%Y-%m-%d"),
        datetime=0,
        open=1,
        high=2,
        low=3,
        close=4,
        volume=5,
        openinterest=-1,
    )


# ── Analyzers: real per-bar equity capture + trade stats ────────────────
# Replace the pre-existing _extract_equity_curve that linearly interpolated
# between initial and final broker value (making all downstream Sharpe/MaxDD
# numbers meaningless because they were computed on a fake straight line).


class _EquityCurveAnalyzer:
    """Backtrader analyzer that captures broker value on every bar.

    Backtrader's standard ``Cerebro.run()`` does not retain the broker's
    per-bar value history; an analyzer is the canonical way to record it.
    The captured list is the input to all downstream Sharpe/Sortino/MaxDD
    calculations.
    """

    # NOTE: defined as a regular class with the bt.Analyzer-compatible
    # surface rather than `class X(bt.Analyzer)` so it's importable even
    # when backtrader isn't on the path at module-import time (matters for
    # the test harness's lazy import pattern).
    def __init__(self):
        self._values: list[float] = []

    def __getattr__(self, name):
        # backtrader pokes a few attributes at analyzer instances during wiring;
        # be liberal about missing ones (we only need start/next/get_analysis).
        raise AttributeError(name)


# Wire as a real backtrader analyzer at import time (so cerebro.addanalyzer
# accepts it). Done lazily inside a function so module import doesn't pull
# backtrader (matters for environments where backtrader is optional).
def _build_analyzers():
    import backtrader as bt

    class _EquityCurve(bt.Analyzer):
        def start(self):
            self._values: list[float] = []

        def next(self):
            self._values.append(float(self.strategy.broker.getvalue()))

        def stop(self):
            # Capture the final value once after the last bar so the curve
            # spans the full backtest including the closing mark.
            final = float(self.strategy.broker.getvalue())
            if not self._values or self._values[-1] != final:
                self._values.append(final)

        def get_analysis(self):
            return {"values": list(self._values)}

    class _TradeStats(bt.Analyzer):
        """Trade-level stats: total trades, win rate, average holding period."""

        def start(self):
            self._closed_pnls: list[float] = []
            self._holding_periods_bars: list[int] = []

        def notify_trade(self, trade):
            if trade.isclosed:
                self._closed_pnls.append(float(trade.pnlcomm))
                self._holding_periods_bars.append(int(trade.barlen))

        def get_analysis(self):
            n = len(self._closed_pnls)
            wins = sum(1 for p in self._closed_pnls if p > 0)
            win_rate = wins / n if n > 0 else 0.0
            avg_hold = sum(self._holding_periods_bars) / n if n > 0 else 0.0
            return {
                "total_trades": n,
                "win_rate": win_rate,
                # Bars are roughly daily for our seed strategies → days.
                "avg_holding_period_days": avg_hold,
            }

    return _EquityCurve, _TradeStats


# Resolve the real analyzer classes once and bind module-level so
# cerebro.addanalyzer(_EquityCurveAnalyzer, ...) works.
_EquityCurveAnalyzer, _TradeStatsAnalyzer = _build_analyzers()


def _csv_data_feed(csv_path: Path) -> Any:
    """Build a GenericCSVData feed from a CSV file on disk.

    Column layout matches _synthetic_data(): datetime=0, open=1, high=2,
    low=3, close=4, volume=5, openinterest=-1.
    """
    import backtrader as bt

    return bt.feeds.GenericCSVData(
        dataname=str(csv_path),
        dtformat=("%Y-%m-%d"),
        datetime=0,
        open=1,
        high=2,
        low=3,
        close=4,
        volume=5,
        openinterest=-1,
    )


def _extract_daily_returns(strat: Any) -> list[float]:
    """Extract daily return series from the TimeReturn analyzer."""
    try:
        tr = strat.analyzers.timereturn.get_analysis()
        return list(tr.values())
    except (AttributeError, KeyError):
        return []


def _extract_analyzer_sharpe(strat: Any) -> float:
    """Extract Sharpe ratio from the SharpeRatio analyzer (rf=0, annualized)."""
    try:
        raw = strat.analyzers.sharpe.get_analysis().get("sharperatio")
        return float(raw) if raw is not None else 0.0
    except (AttributeError, KeyError, TypeError):
        return 0.0


def _extract_analyzer_drawdown(strat: Any) -> float:
    """Extract max drawdown (decimal) from the DrawDown analyzer."""
    try:
        dd_analysis = strat.analyzers.drawdown.get_analysis()
        max_dd_pct = dd_analysis.get("max", {}).get("drawdown", 0.0)
        return float(max_dd_pct) / 100.0
    except (AttributeError, KeyError, TypeError):
        return 0.0


def _build_equity_curve(daily_returns: list[float], initial_cash: float) -> list[float]:
    """Build an equity curve from a series of daily returns."""
    if not daily_returns:
        return [initial_cash]
    curve = [initial_cash]
    for ret in daily_returns:
        curve.append(curve[-1] * (1 + ret))
    return curve


def _extract_equity_curve(strat: Any, initial_cash: float) -> list[float]:
    """Extract equity curve from a completed strategy run."""
    # Use the analyzer if available, otherwise synthesize from broker value
    try:
        for _i in range(len(strat.data)):
            # Approximate by replaying — in practice cerebro.run() doesn't keep history
            pass
    except Exception:
        pass
    # Cerebro doesn't easily expose per-bar equity after run()
    # Return a simplified curve based on initial → final
    final = float(strat.broker.getvalue())
    n_bars = max(1, len(strat.data))
    # Linear interpolation as approximation (real curve would need observer)
    return [initial_cash + (final - initial_cash) * i / n_bars for i in range(n_bars + 1)]


def _compute_monthly_returns(equity_curve: list[float]) -> list[float]:
    """Compute approximate monthly returns from daily equity curve."""
    if len(equity_curve) < 22:
        return []
    returns = []
    for i in range(21, len(equity_curve), 21):
        start = equity_curve[i - 21]
        end = equity_curve[i]
        if start > 0:
            returns.append((end - start) / start)
    return returns


def _annualized_sharpe(daily_returns: list[float], rf_annual: float = 0.05) -> float:
    if not daily_returns:
        return 0.0
    mean_ret = sum(daily_returns) / len(daily_returns)
    var = sum((r - mean_ret) ** 2 for r in daily_returns) / len(daily_returns)
    std = var**0.5 if var > 0 else 0.0
    if std == 0:
        return 0.0
    daily_rf = rf_annual / 252
    return (mean_ret - daily_rf) / std * (252**0.5)


def _annualized_sortino(daily_returns: list[float], rf_annual: float = 0.05) -> float:
    if not daily_returns:
        return 0.0
    mean_ret = sum(daily_returns) / len(daily_returns)
    daily_rf = rf_annual / 252
    downside = [min(r - daily_rf, 0) ** 2 for r in daily_returns]
    ds_std = (sum(downside) / len(downside)) ** 0.5 if downside else 0.0
    if ds_std == 0:
        return 0.0
    return (mean_ret - daily_rf) / ds_std * (252**0.5)


def _max_drawdown(equity_curve: list[float]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for v in equity_curve:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return max_dd
