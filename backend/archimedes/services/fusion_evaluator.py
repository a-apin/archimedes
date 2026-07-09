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
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from archimedes.services._fusion_helpers import (
    FusionQualityScorer,  # noqa: F401 - re-exported for test_fusion_quality_scorer
    _annualized_sharpe,
    _annualized_sortino,
    _compute_monthly_returns,
    _csv_data_feed,
    _EquityCurveAnalyzer,
    _max_drawdown,
    _synthetic_data,
    _TradeStatsAnalyzer,
    equity_curve_to_daily_returns,
    generate_fusion_report,  # noqa: F401 - re-exported for test_fusion_quality_scorer
    score_correlation_stability,  # noqa: F401 - re-exported for test_fusion_quality_scorer
    score_diversification_benefit,  # noqa: F401 - re-exported for test_fusion_quality_scorer
    score_economic_sense,  # noqa: F401 - re-exported for test_fusion_quality_scorer
    score_parameter_stability,  # noqa: F401 - re-exported for test_fusion_quality_scorer
    score_tail_hedge,  # noqa: F401 - re-exported for test_fusion_quality_scorer
    score_turnover_interaction,  # noqa: F401 - re-exported for test_fusion_quality_scorer
)
from archimedes.services.dsl_to_backtrader import interpret_spec, interpret_variant
from archimedes.services.rigor_evaluator import (
    compute_average_pairwise_correlation,
    compute_dsr,
    compute_in_sample_sharpe,
    compute_oos_sharpe,
    compute_pbo,
)
from archimedes.services.rigor_profiles import STRICTEST_LEVEL, get_profile
from archimedes.services.strategy_dsl import (
    DSLError,
    StrategySpec,
    validate_strategy_spec,
)

logger = logging.getLogger(__name__)

# CSCV partition count used when calling compute_pbo without an explicit
# s_partitions (its default). Mirrored here so the fusion path can detect a
# too-short window (T // S < 1) before compute_pbo returns its all-0.0 sentinel
# (#918). Keep in sync with _rigor_helpers.compute_pbo's default.
_PBO_S_PARTITIONS = 16

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
    # Provenance of the price series the metrics were computed on. "synthetic"
    # means random.gauss noise (dev/test only); "csv:<name>" / "provided" mean
    # real OHLCV. Rigor metrics from a "synthetic" run are NOT admissible.
    data_source: str = "synthetic"


@dataclass(frozen=True)
class RigorVerdict:
    """Result of applying the rigor gate to fusion output."""

    passing: bool
    dsr: float | None
    dsr_p_value: float | None
    pbo_score: float | None
    oos_sharpe: float | None
    # True for every spec that reaches this evaluator: validate_strategy_spec
    # rejects look_ahead_safe=False before a backtest ever runs, so this is
    # always True in practice. It is NOT the result of an independent
    # AST-based audit (cf. rigor_evaluator.look_ahead_audit, which runs only
    # against cited curated source) — it is the LLM's own self-declared
    # look_ahead_safe flag, enforced as a closed-DSL admission gate. Kept as
    # a bool because it participates in the `passing` computation; see
    # `look_ahead_label` for the honest user-facing string (audit 06-14, Q6).
    look_ahead_clean: bool
    num_trials: int
    # In-sample (training-slice) Sharpe, surfaced so the OOS/IS cliff that the
    # gate enforces is visible on the passport, not just used internally.
    in_sample_sharpe: float | None = None
    # Provenance carried through from the backtest. ``admissible`` is the
    # honest gate: a strategy can only be certified Tier-1 if it both passes
    # the statistics AND those statistics were computed on real market data.
    data_source: str = "synthetic"
    admissible: bool = False
    # Honest user-facing label for the look-ahead check (audit 06-14, Q6).
    # Distinct from `look_ahead_clean` (the gating bool, always True here):
    # this string makes clear that "clean" means "the closed DSL's
    # self-attested look_ahead_safe flag was True", NOT "an independent
    # AST audit of the generated code ran and found no look-ahead bias" —
    # the latter is what `rigor_evaluator.look_ahead_audit` does for cited
    # curated source, and it is NOT run against fusion/DSL output. Mirrors
    # the "MISSING"-style honest labels in
    # `selection_bias_routes.RigorGateDetail.look_ahead`.
    look_ahead_label: str = "N/A (closed-DSL, self-attested, not source-audited)"


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

    @property
    def admissible(self) -> bool:
        """True only if rigor passed AND on real (non-synthetic) market data."""
        return self.rigor is not None and self.rigor.admissible


# ── Backtest runner ───────────────────────────────────────────────────

_DEFAULT_CASH = 100_000.0
_DEFAULT_TX_BPS = 10

# The only price-data provenance that is NOT admissible for Tier-1 rigor
# certification. Everything else (real CSV, an explicitly provided feed) is
# trusted to be real market data — the caller owns that contract.
_SYNTHETIC_SOURCE = "synthetic"


def _data_source_label(
    data_feed: Any,
    data_csv_path: str | Path | None,
    data_feed_factory: Any = None,
    label_override: str | None = None,
) -> str:
    """Honest provenance label for the price series a backtest ran on."""
    if label_override is not None:
        return label_override
    if data_feed is not None or data_feed_factory is not None:
        return "provided"
    if data_csv_path is not None:
        return f"csv:{Path(data_csv_path).name}"
    return _SYNTHETIC_SOURCE


def is_admissible_source(data_source: str) -> bool:
    """True unless the metrics were computed on synthetic (random) prices.

    Rigor numbers from synthetic data describe noise, not a strategy — they
    must never be the basis for Tier-1 admission.
    """
    return data_source != _SYNTHETIC_SOURCE


def run_dsl_backtest(
    spec: StrategySpec,
    *,
    data_feed: Any = None,
    data_feed_factory: Any = None,
    data_source_label: str | None = None,
    data_csv_path: str | Path | None = None,
    initial_cash: float = _DEFAULT_CASH,
    tx_cost_bps: int = _DEFAULT_TX_BPS,
) -> BacktestMetrics:
    """Run a backtest for a DSL-interpreted strategy.

    ``data_feed_factory`` (preferred for real data) is a zero-arg callable
    returning a FRESH feed — a concrete backtrader feed is consumed by a single
    ``cerebro.run()``, so anything that re-runs (the variant grid) must build a
    new feed per run. If ``data_feed`` is None and ``data_csv_path`` is set,
    builds a ``GenericCSVData`` feed from the CSV. If all are None, generates a
    deterministic synthetic price series. The equity curve is captured
    **bar-by-bar** via a backtrader analyzer.
    """
    import backtrader as bt

    strategy_cls = interpret_spec(spec)
    cerebro = bt.Cerebro(stdstats=False)
    cerebro.addstrategy(strategy_cls)

    data_source = _data_source_label(data_feed, data_csv_path, data_feed_factory, data_source_label)
    if data_source == _SYNTHETIC_SOURCE:
        logger.warning(
            "run_dsl_backtest[%s] is using SYNTHETIC price data — rigor metrics "
            "from this run are NOT admissible for Tier-1 certification. Pass "
            "data_csv_path or data_feed with real OHLCV for an admissible result.",
            spec.name,
        )

    if data_feed is None and data_feed_factory is not None:
        data_feed = data_feed_factory()
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

    daily_returns = equity_curve_to_daily_returns(equity_curve)
    # rf-convention (deliberate split — do NOT "reconcile" to one rate):
    #   DISPLAY (here) = raw Sharpe/Sortino with rf=0. This is the passport
    #   headline and matches how backtrader's analyzer / practitioners quote it.
    #   GATE = rigor_evaluator._RF_ANNUAL = 0.05: the DSR deflates an *excess*-
    #   return Sharpe because Bailey-LdP (2014) is defined on excess returns.
    # The two answer different questions (headline performance vs. selection-bias-
    # corrected significance); forcing a single rf would corrupt one of them.
    # See rigor_evaluator.py for the gate side. (audit 2026-06-13, MEDIUM #8)
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
        data_source=data_source,
    )


def run_dsl_backtest_variants(
    spec: StrategySpec,
    *,
    data_feed: Any = None,
    data_feed_factory: Any = None,
    data_source_label: str | None = None,
    data_csv_path: str | Path | None = None,
    initial_cash: float = _DEFAULT_CASH,
    tx_cost_bps: int = _DEFAULT_TX_BPS,
) -> dict[str, BacktestMetrics]:
    """Run backtests for each cartesian-product point in the parameter grid.

    If ``spec.parameter_variants`` is ``None`` or empty, returns a
    single-entry dict ``{"base": metrics}`` for the unmodified spec.
    Otherwise expands the variant grid and runs one backtest per combination.
    Prefer ``data_feed_factory`` over ``data_feed`` when running a real grid:
    a concrete feed object is consumed by the first run.

    Returns:
        ``{variant_id: BacktestMetrics}`` where variant_id is a
        dash-separated key like ``"150"`` or ``"150_0.12"``.
    """
    if spec.parameter_variants is None or not spec.parameter_variants:
        metrics = run_dsl_backtest(
            spec,
            data_feed=data_feed,
            data_feed_factory=data_feed_factory,
            data_source_label=data_source_label,
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
            data_feed_factory=data_feed_factory,
            data_source_label=data_source_label,
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
    data_feed_factory: Any = None,
    data_source_label: str | None = None,
    data_csv_path: str | Path | None = None,
    initial_cash: float = _DEFAULT_CASH,
    tx_cost_bps: int = _DEFAULT_TX_BPS,
) -> BacktestMetrics:
    """Run a single variant backtest given an already-interpreted strategy class."""
    import backtrader as bt

    data_source = _data_source_label(data_feed, data_csv_path, data_feed_factory, data_source_label)
    cerebro = bt.Cerebro(stdstats=False)
    cerebro.addstrategy(strategy_cls)

    if data_feed is None and data_feed_factory is not None:
        data_feed = data_feed_factory()
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

    daily_returns = equity_curve_to_daily_returns(equity_curve)
    # rf-convention (deliberate split — do NOT "reconcile" to one rate):
    #   DISPLAY (here) = raw Sharpe/Sortino with rf=0. This is the passport
    #   headline and matches how backtrader's analyzer / practitioners quote it.
    #   GATE = rigor_evaluator._RF_ANNUAL = 0.05: the DSR deflates an *excess*-
    #   return Sharpe because Bailey-LdP (2014) is defined on excess returns.
    # The two answer different questions (headline performance vs. selection-bias-
    # corrected significance); forcing a single rf would corrupt one of them.
    # See rigor_evaluator.py for the gate side. (audit 2026-06-13, MEDIUM #8)
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
        data_source=data_source,
    )


# ── Multi-asset portfolio runner (real data, #788/#818) ──────────────
#
# The DSL engine is single-feed by construction (the interpreted strategy only
# reads ``self.data``), so a multi-asset spec is evaluated honestly by applying
# the SAME rules to EACH asset over an inner-joined real panel (identical dates
# per sleeve) with equal cash per sleeve, then judging the SUMMED sleeve equity
# as the portfolio. No cross-sleeve rebalancing is simulated — the aggregate is
# a buy-the-sleeves portfolio, which matches the DSL's per-asset semantics.


def _aggregate_portfolio_metrics(
    per_asset: dict[str, BacktestMetrics],
    *,
    label: str,
    backtest_start: date | None,
    backtest_end: date | None,
) -> BacktestMetrics:
    """Combine per-asset sleeve backtests into portfolio-level metrics."""
    curves = [m.equity_curve for m in per_asset.values()]
    n_bars = min(len(c) for c in curves)
    if any(len(c) != n_bars for c in curves):
        # Sleeves run on an inner-joined panel, so equal lengths are expected;
        # a mismatch means a feed dropped bars — truncate and say so.
        logger.warning(
            "portfolio sleeves have unequal bar counts %s — truncating to %d",
            [len(c) for c in curves],
            n_bars,
        )
    portfolio_curve = [sum(c[i] for c in curves) for i in range(n_bars)]

    daily_returns = [
        (portfolio_curve[i] - portfolio_curve[i - 1]) / portfolio_curve[i - 1]
        for i in range(1, len(portfolio_curve))
        if portfolio_curve[i - 1] > 0
    ]
    initial = portfolio_curve[0] if portfolio_curve else 0.0
    final = portfolio_curve[-1] if portfolio_curve else 0.0
    total_return = (final - initial) / initial if initial > 0 else 0.0
    years = max(n_bars / 252, 0.01)
    cagr = (1 + total_return) ** (1 / years) - 1 if total_return > -1 else -1.0

    # Same rf-convention split as the single-feed runner (display rf=0).
    sharpe = _annualized_sharpe(daily_returns, rf_annual=0.0)
    sortino = _annualized_sortino(daily_returns, rf_annual=0.0)
    max_dd = _max_drawdown(portfolio_curve)
    calmar = cagr / max_dd if max_dd > 0 else 0.0

    total_trades = sum(m.total_trades for m in per_asset.values())
    if total_trades > 0:
        win_rate = sum(m.win_rate * m.total_trades for m in per_asset.values()) / total_trades
        avg_holding = sum(m.avg_holding_period_days * m.total_trades for m in per_asset.values()) / total_trades
    else:
        win_rate = 0.0
        avg_holding = 0.0

    return BacktestMetrics(
        sharpe_ratio=round(sharpe, 4),
        sortino_ratio=round(sortino, 4),
        max_drawdown=round(max_dd, 4),
        cagr=round(cagr, 4),
        calmar_ratio=round(calmar, 4),
        win_rate=round(win_rate, 4),
        total_trades=total_trades,
        avg_holding_period_days=round(avg_holding, 2),
        equity_curve=[round(e, 2) for e in portfolio_curve],
        monthly_returns=[round(m, 4) for m in _compute_monthly_returns(portfolio_curve)],
        backtest_start=backtest_start,
        backtest_end=backtest_end,
        data_source=label,
    )


def run_dsl_backtest_portfolio(
    spec: StrategySpec,
    feed_factories: dict[str, Any],
    *,
    label: str,
    backtest_start: date | None = None,
    backtest_end: date | None = None,
    initial_cash: float = _DEFAULT_CASH,
    tx_cost_bps: int = _DEFAULT_TX_BPS,
) -> BacktestMetrics:
    """Backtest a DSL spec per asset over real feeds and aggregate the sleeves."""
    per_asset: dict[str, BacktestMetrics] = {}
    sleeve_cash = initial_cash / max(1, len(feed_factories))
    for sym, factory in feed_factories.items():
        per_asset[sym] = run_dsl_backtest(
            spec,
            data_feed_factory=factory,
            data_source_label=label,
            initial_cash=sleeve_cash,
            tx_cost_bps=tx_cost_bps,
        )
    return _aggregate_portfolio_metrics(
        per_asset, label=label, backtest_start=backtest_start, backtest_end=backtest_end
    )


def run_dsl_backtest_portfolio_variants(
    spec: StrategySpec,
    feed_factories: dict[str, Any],
    *,
    label: str,
    backtest_start: date | None = None,
    backtest_end: date | None = None,
    initial_cash: float = _DEFAULT_CASH,
    tx_cost_bps: int = _DEFAULT_TX_BPS,
) -> dict[str, BacktestMetrics]:
    """Variant grid over real multi-asset feeds — one aggregated portfolio per combo."""
    if spec.parameter_variants is None or not spec.parameter_variants:
        return {
            "base": run_dsl_backtest_portfolio(
                spec,
                feed_factories,
                label=label,
                backtest_start=backtest_start,
                backtest_end=backtest_end,
                initial_cash=initial_cash,
                tx_cost_bps=tx_cost_bps,
            )
        }

    import itertools

    variant_keys = sorted(spec.parameter_variants.keys())
    variant_value_lists = [spec.parameter_variants[k] for k in variant_keys]
    sleeve_cash = initial_cash / max(1, len(feed_factories))

    results: dict[str, BacktestMetrics] = {}
    for combo in itertools.product(*variant_value_lists):
        overrides = {k: int(v) for k, v in zip(variant_keys, combo, strict=False)}
        variant_id = "_".join(str(v) for v in combo)
        strategy_cls = interpret_variant(spec, overrides)
        per_asset = {
            sym: _run_variant_backtest(
                strategy_cls,
                data_feed_factory=factory,
                data_source_label=label,
                initial_cash=sleeve_cash,
                tx_cost_bps=tx_cost_bps,
            )
            for sym, factory in feed_factories.items()
        }
        results[variant_id] = _aggregate_portfolio_metrics(
            per_asset, label=label, backtest_start=backtest_start, backtest_end=backtest_end
        )
    return results


# ── Rigor gate ────────────────────────────────────────────────────────


def _default_num_trials() -> int:
    """Self-contained default selection-set size = 1 (decouple #2, Dan's principle).

    A strategy's rigor depends ONLY on itself, never on the curated library's count.
    With no explicit caller-supplied selection count, a single fusion-generated
    strategy is one trial — NOT the library size (the prior behavior). Its own
    parameter-variant grid, when present, is layered on separately in
    ``apply_rigor_gate`` (the ``max(num_trials, len(variants))`` below), which is
    genuinely part of the strategy's own selection process.
    """
    return 1


def apply_rigor_gate(
    metrics: BacktestMetrics,
    num_trials: int | None = None,
    variants_metrics: dict[str, BacktestMetrics] | None = None,
    data_source: str | None = None,
) -> RigorVerdict:
    """Apply rigor gate to fusion backtest metrics.

    PBO is set to ``None`` (not 0.0) when there are fewer than 2 variant
    backtests. The Bailey/Borwein/López de Prado/Zhu CSCV PBO algorithm
    formally compares multiple competing strategies against the same return
    matrix; a single DSL-generated strategy doesn't yield a meaningful PBO
    without parameter-sweep variants to cross-validate against.

    When ``variants_metrics`` is provided with >= 2 entries, real CSCV PBO
    is computed from the variant returns matrix and attached to the verdict.

    ``num_trials``, when ``None`` (the default), falls back to a self-contained
    ``1`` via ``_default_num_trials()`` — a strategy is graded on its own selection
    set, never deflated by the curated library's count (decouple #2). The caller
    passes an explicit count when the strategy came from a real N-candidate pool.
    """
    if num_trials is None:
        num_trials = _default_num_trials()
    daily_returns = [
        (metrics.equity_curve[i] - metrics.equity_curve[i - 1]) / metrics.equity_curve[i - 1]
        for i in range(1, len(metrics.equity_curve))
        if metrics.equity_curve[i - 1] > 0
    ]

    # Build the parameter-variant returns matrix once — it is the multiple-
    # testing selection set, and feeds both the DSR effective-N correction
    # (average pairwise correlation of the trials) and the CSCV PBO.
    variant_returns: dict[str, list[float]] = {}
    if variants_metrics is not None and len(variants_metrics) >= 2:
        for vid, vm in variants_metrics.items():
            curve = vm.equity_curve
            variant_returns[vid] = [
                (curve[i] - curve[i - 1]) / curve[i - 1] for i in range(1, len(curve)) if curve[i - 1] > 0
            ]

    # num_trials = actual size of the multiple-testing selection set. A variant
    # grid is a second, independent selection layer — a parameter sweep within
    # this one candidate, on top of whatever selection set the caller already
    # accounted for (society N + library, #820, or the plain library-size
    # fallback when the caller passed nothing more precise). Take whichever
    # count is larger rather than letting a small grid (e.g. 3 variants)
    # silently override a bigger, correct caller-supplied count — this can
    # only make the gate at least as strict as either layer alone, never
    # looser than either (#820).
    effective_trials = num_trials
    if variants_metrics is not None and len(variants_metrics) >= 2:
        effective_trials = max(num_trials, len(variants_metrics))

    # Correlated variants carry fewer independent trials than their nominal
    # count, so the multiple-testing penalty in the DSR is relaxed accordingly.
    avg_correlation = compute_average_pairwise_correlation(variant_returns) if len(variant_returns) >= 2 else 0.0
    dsr, dsr_p = compute_dsr(daily_returns, effective_trials, avg_correlation)
    oos_sharpe = compute_oos_sharpe(daily_returns)
    in_sample_sharpe = compute_in_sample_sharpe(daily_returns)

    # PBO: compute real CSCV PBO when >= 2 variant backtests are available.
    pbo_score: float | None = None
    if len(variant_returns) >= 2:
        # Fail-closed on a too-short variant window (#918). compute_pbo returns
        # an all-0.0 sentinel (a spurious PASS, since 0.0 < pbo_max) when
        # rows_per_block = T // s_partitions < 1 — i.e. fewer aligned bars than
        # partitions (e.g. a 5-day fusion window against the default S=16). That
        # 0.0 is meaningless, so guard it here exactly as the curated path does
        # in rigor_evaluator.compute_library_pbo: an under-length window is
        # non-computable and must FAIL the PBO criterion, not pass it. Leaving
        # pbo_score = None routes into the pbo_pass fail-closed branch below.
        shortest = min((len(r) for r in variant_returns.values()), default=0)
        if shortest // _PBO_S_PARTITIONS >= 1:
            pbo_map = compute_pbo(variant_returns)
            # All strategies in the matrix get the same PBO score (library-level
            # metric per Bailey et al. 2014). Pick the first entry's value.
            first_key = next(iter(pbo_map))
            pbo_score = pbo_map[first_key]

    # Look-ahead admission: validate_strategy_spec rejects any DSL spec with a
    # self-declared look_ahead_safe=False before this evaluator ever runs, so
    # `look_ahead_clean` is always True for specs reaching this point. This is
    # the LLM's OWN self-attestation enforced as a closed-DSL gate — NOT the
    # independent AST audit (rigor_evaluator.look_ahead_audit) that runs
    # against cited curated source. `look_ahead_label` carries the honest
    # framing of that distinction for the passport/UI (audit 06-14, Q6).
    look_ahead_clean = True
    look_ahead_label = "N/A (closed-DSL, self-attested, not source-audited)"

    # DSR gate: Tier-1 fusion certification is a BADGE decision, so it uses the
    # strictest (Archimedes Verified) profile's DSR bar — one source of truth with
    # the curated path in run_rigor_gate (rigor_profiles.get_profile(STRICTEST_LEVEL)).
    # The per-user strictness slider never loosens this: the badge is a global claim,
    # not a personal risk knob. Using dsr > 0.0 was too permissive — a z-score of
    # 0.5 (p ≈ 0.69) would pass here while the same strategy fails the API gate,
    # creating an inconsistency that could admit under-credentialed Tier-1 strategies
    # through the fusion path.
    # NaN-harden every numeric comparison: a NaN metric makes `>=`/`<` False,
    # which would silently skip a fail branch. Treat non-finite as fail.
    _badge = get_profile(STRICTEST_LEVEL)
    dsr_pass = dsr_p is not None and math.isfinite(dsr_p) and dsr_p >= _badge.dsr_p_min

    # Walk-forward OOS Sharpe is the fourth admission primitive (DSL look-ahead
    # safety, DSR, PBO, walk-forward OOS). Mirror the curated path's
    # RigorGateResult.passes_all in full: an absolute floor (OOS > 0) AND the
    # in-/out-of-sample cliff (OOS/IS >= 0.5). Before this, the fusion path
    # enforced only the floor, so a strategy grossly overfit in-sample (e.g. IS
    # Sharpe 5.0, OOS Sharpe +0.05) passed the fusion gate and was certified
    # Tier-1 while the identical strategy failed the curated API gate.
    oos_pass = oos_sharpe is not None and math.isfinite(oos_sharpe) and oos_sharpe > 0.0
    if (
        oos_pass
        and in_sample_sharpe is not None
        and math.isfinite(in_sample_sharpe)
        and in_sample_sharpe > 0
        and oos_sharpe / in_sample_sharpe < _badge.oos_is_ratio_min
    ):
        oos_pass = False

    # Fail-closed when PBO wasn't computed (audit 06-14, Q4): a missing PBO
    # means CSCV never ran (fewer than 2 variant backtests), NOT that the
    # strategy passed the overfitting check. Mirrors RigorGateResult.passes_all
    # (rigor_evaluator.py), where pbo_score is None fails the overall gate
    # rather than vacuously passing it.
    pbo_pass = pbo_score is not None and math.isfinite(pbo_score) and pbo_score < _badge.pbo_max
    passing = dsr_pass and oos_pass and look_ahead_clean and pbo_pass

    # Provenance gate: a strategy is only admissible for Tier-1 if it passes
    # the statistics AND those statistics were computed on real market data.
    # Default to the metrics' own provenance unless the caller overrides it.
    source = data_source if data_source is not None else metrics.data_source
    admissible = passing and is_admissible_source(source)
    if passing and not admissible:
        logger.warning(
            "rigor verdict is PASSING but NOT admissible — metrics came from "
            "non-real data source %r. Refusing Tier-1 certification.",
            source,
        )

    return RigorVerdict(
        passing=passing,
        dsr=dsr,
        dsr_p_value=dsr_p,
        pbo_score=pbo_score,
        oos_sharpe=oos_sharpe,
        in_sample_sharpe=in_sample_sharpe,
        look_ahead_clean=look_ahead_clean,
        look_ahead_label=look_ahead_label,
        num_trials=effective_trials,
        data_source=source,
        admissible=admissible,
    )


# ── Full pipeline ─────────────────────────────────────────────────────


def evaluate_fusion_spec(
    spec_dict: dict[str, Any],
    *,
    data_feed: Any = None,
    num_trials: int | None = None,
    use_real_data: bool = False,
) -> FusionEvalResult:
    """Full pipeline: validate → interpret → backtest → rigor gate.

    ``num_trials=None`` (the default) defers to ``apply_rigor_gate``'s own
    self-contained fallback of ``1`` (decouple #2) — see ``_default_num_trials``.

    ``use_real_data=True`` (the live generate/debate paths) fetches real daily
    OHLCV for the spec's asset universe via ``fusion_market_data`` and runs the
    multi-asset portfolio backtest — the only route to an ``admissible``
    verdict. It fails CLOSED: if nothing in the universe maps to the SSOT or
    the fetch/join comes up short, the run falls back to the synthetic path,
    which stays honestly inadmissible ("pending" at the live gate) — never a
    fake pass. Default False keeps unit tests hermetic (no network).
    """
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

    real_factories: dict[str, Any] | None = None
    real_label = ""
    real_start: date | None = None
    real_end: date | None = None
    if use_real_data and data_feed is None:
        from archimedes.services import fusion_market_data

        panel = fusion_market_data.fetch_real_panel(spec.asset_universe)
        if panel is not None:
            real_factories = {sym: fusion_market_data.feed_factory(df) for sym, df in panel.frames.items()}
            real_label, real_start, real_end = panel.label, panel.start, panel.end
        else:
            logger.warning(
                "fusion eval[%s]: real data unavailable for universe %s — "
                "falling back to SYNTHETIC (verdict will be inadmissible)",
                spec.name,
                spec.asset_universe,
            )

    try:
        if real_factories:
            metrics = run_dsl_backtest_portfolio(
                spec,
                real_factories,
                label=real_label,
                backtest_start=real_start,
                backtest_end=real_end,
            )
        else:
            metrics = run_dsl_backtest(spec, data_feed=data_feed)
    except Exception as e:
        logger.exception("DSL backtest failed for %s", spec.name)
        return FusionEvalResult(spec=spec, backtest=None, rigor=None, error=str(e))

    # Run variant grid if parameter_variants is present.
    variants_metrics: dict[str, BacktestMetrics] | None = None
    if spec.parameter_variants is not None and len(spec.parameter_variants) >= 1:
        try:
            if real_factories:
                variants_metrics = run_dsl_backtest_portfolio_variants(
                    spec,
                    real_factories,
                    label=real_label,
                    backtest_start=real_start,
                    backtest_end=real_end,
                )
            else:
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
