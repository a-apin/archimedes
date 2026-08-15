"""Map analytics-engine artifacts into backend BacktestResult models."""

from __future__ import annotations

import json
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from archimedes.models.backtest import BacktestResult


def _safe_iso_to_date(value: str | None) -> date | None:
    if not value:
        return None
    text = value.strip()
    try:
        if "T" in text:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        return date.fromisoformat(text)
    except ValueError:
        return None


def _f(value: float | int | None, default: float = 0.0) -> float:
    if value is None:
        return default
    return float(value)


class EngineMetricsModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sharpe_ratio: float | None = None
    sortino_ratio: float | None = None
    calmar_ratio: float | None = None
    max_drawdown_pct: float | None = None
    cagr: float | None = None

    win_rate: float | None = None
    profit_factor: float | None = None
    total_trades: int = 0
    avg_holding_period_days: float | None = None

    correlation_to_spy: float | None = None
    correlation_to_btc: float | None = None

    equity_curve: list[float] = Field(default_factory=list)
    monthly_returns: list[float] = Field(default_factory=list)

    backtest_start: str | None = None
    backtest_end: str | None = None

    out_of_sample_sharpe: float | None = None
    look_ahead_audit_passed: bool = False

    backtest_engine: str | None = None
    transaction_cost_bps: int | None = None
    # Declared so `extra="ignore"` stops silently eating it at the boundary.
    # The engine has been emitting a cost fingerprint since the cost SSOT
    # landed; without a field here it never reached a BacktestResult.
    cost_model_id: str | None = None


class OperationResultModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    operation: str
    symbol: str
    metrics: EngineMetricsModel
    # Present only on the "UNIVERSE" composite row (analytics-engine
    # cli.run_command's declared-universe path) — the per-asset operations it
    # was averaged from. Absent/empty on every ordinary per-asset row.
    constituent_operations: list[str] = Field(default_factory=list)


class StrategyBlockModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    backtest_code_hash: str | None = None
    paper_claimed_sharpe: float | None = None
    paper_claimed_cagr: float | None = None
    paper_claimed_max_dd: float | None = None


class AssumptionsBlockModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    transaction_cost_bps: int = 10
    walk_forward_split: float | None = None
    backtest_engine: str | None = None
    # cli.run_command writes the fingerprint into `assumptions`; the metrics
    # block may also carry one. Metrics win when both are present, since they
    # describe the individual run rather than the invocation's defaults.
    cost_model_id: str | None = None


class IntegrityFlagsModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    lookahead_audit_passed: bool = False


class AnalyticsArtifactModel(BaseModel):
    """Pydantic schema for analytics-engine JSON artifact."""

    model_config = ConfigDict(extra="ignore")

    run_id: str
    strategy: StrategyBlockModel
    assumptions: AssumptionsBlockModel
    integrity_flags: IntegrityFlagsModel
    results: list[OperationResultModel]


def canonical_artifact_hash(payload: dict[str, Any]) -> str:
    """Deterministic SHA-256 for artifact payload."""
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def select_operation_result(
    artifact: AnalyticsArtifactModel,
    *,
    operation: str | None = None,
) -> OperationResultModel:
    if not artifact.results:
        raise ValueError("artifact has no results")

    if operation:
        wanted = operation.upper()
        for row in artifact.results:
            if row.operation.upper() == wanted:
                return row

    # The "UNIVERSE" composite (cli.run_command's declared-universe path —
    # equal-weighted, date-aligned across every asset the strategy declares)
    # represents the strategy applied across its own declared universe, not
    # just one leg of it. Grade that ahead of "SPY", which is just one of
    # potentially several declared assets and is no longer special once a
    # genuine composite exists (backtest-vol audit; see cli.py).
    for row in artifact.results:
        if row.operation.upper() == "UNIVERSE":
            return row

    for row in artifact.results:
        if row.operation.upper() == "SPY":
            return row

    return artifact.results[0]


def map_artifact_to_backtest_result(
    artifact: AnalyticsArtifactModel,
    *,
    strategy_id: str,
    operation: str | None = None,
) -> tuple[BacktestResult, str | None]:
    """Map artifact to backend BacktestResult + chosen operation."""
    chosen = select_operation_result(artifact, operation=operation)
    m = chosen.metrics

    max_dd_fraction = _f(m.max_drawdown_pct) / 100.0 if m.max_drawdown_pct is not None else 0.0

    # Both sides of this used to be OR'd together as if they were independent
    # signals. They are not: cli.run_command derives
    # integrity_flags.lookahead_audit_passed as
    # `all(r["metrics"]["look_ahead_audit_passed"] for r in results)` — an AND
    # over per-result copies of the very same field. So the OR compared a value
    # against a reduction of itself and could never add information.
    #
    # Worse, the value it is reducing is not a look-ahead audit.
    # engine._lookahead_audit_passed reads only cerebro.broker.p.coc/coo, which
    # that engine never sets, so it is True for every run regardless of whether
    # the strategy's signal logic peeks at future data. Its own docstring says
    # so. The real AST audit is rigor_evaluator.look_ahead_audit, and it does
    # not run on this path at all.
    #
    # Deliberately NOT flipping the value here. Setting it False would trip the
    # always-on look-ahead floor at every strictness level and fail every
    # curated strategy — a verdict-moving change that belongs with the library
    # re-run and its before/after table, not a quiet mapper edit. What changes
    # is that the row now says where the boolean came from, so nobody reads it
    # as a passed audit. Tracked on the claims ledger.
    lookahead = m.look_ahead_audit_passed or artifact.integrity_flags.lookahead_audit_passed
    lookahead_source = "broker_config_only"

    result = BacktestResult(
        strategy_id=strategy_id,
        sharpe_ratio=_f(m.sharpe_ratio),
        sortino_ratio=_f(m.sortino_ratio),
        max_drawdown=max_dd_fraction,
        cagr=_f(m.cagr),
        calmar_ratio=_f(m.calmar_ratio),
        win_rate=_f(m.win_rate),
        profit_factor=_f(m.profit_factor),
        total_trades=int(m.total_trades or 0),
        avg_holding_period_days=_f(m.avg_holding_period_days),
        # Not _f(...): coercing a missing correlation to 0.0 asserts "this
        # strategy is uncorrelated to SPY/BTC", which is a real claim and one
        # nothing measured. Populating them needs a benchmark feed in every run,
        # which is not this sprint's work, so serve the absence instead of
        # inventing a number.
        correlation_to_spy=m.correlation_to_spy,
        correlation_to_btc=m.correlation_to_btc,
        equity_curve=list(m.equity_curve),
        monthly_returns=list(m.monthly_returns),
        backtest_start=_safe_iso_to_date(m.backtest_start),
        backtest_end=_safe_iso_to_date(m.backtest_end),
        paper_claimed_sharpe=artifact.strategy.paper_claimed_sharpe,
        paper_claimed_cagr=artifact.strategy.paper_claimed_cagr,
        paper_claimed_max_dd=artifact.strategy.paper_claimed_max_dd,
        out_of_sample_sharpe=m.out_of_sample_sharpe,
        walk_forward_train_fraction=artifact.assumptions.walk_forward_split or 0.70,
        look_ahead_audit_passed=lookahead,
        backtest_engine=m.backtest_engine or artifact.assumptions.backtest_engine,
        backtest_code_hash=artifact.strategy.backtest_code_hash,
        transaction_cost_bps=m.transaction_cost_bps
        if m.transaction_cost_bps is not None
        else artifact.assumptions.transaction_cost_bps,
        cost_model_id=m.cost_model_id or artifact.assumptions.cost_model_id,
        look_ahead_audit_source=lookahead_source,
    )
    return result, chosen.operation


def load_artifact(path: Path) -> AnalyticsArtifactModel:
    return AnalyticsArtifactModel.model_validate_json(path.read_text(encoding="utf-8"))
