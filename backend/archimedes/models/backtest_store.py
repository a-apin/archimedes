"""Persistent storage for engine-produced backtest results."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from archimedes.models.backtest import BacktestResult
from archimedes.models.chat import Base


class BacktestResultRecord(Base):
    """DB row for a strategy backtest snapshot."""

    __tablename__ = "backtest_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    operation: Mapped[str | None] = mapped_column(String(32), nullable=True)

    sharpe_ratio: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    sortino_ratio: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    max_drawdown: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    cagr: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    calmar_ratio: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    win_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    profit_factor: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_trades: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_holding_period_days: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Nullable: a missing correlation used to be stored as 0.0, which asserts
    # "uncorrelated to SPY/BTC" — a real claim nothing measured. Populating
    # these needs a benchmark feed in every run; until then NULL is the honest
    # value and the UI renders it as unavailable rather than as zero.
    correlation_to_spy: Mapped[float | None] = mapped_column(Float, nullable=True)
    correlation_to_btc: Mapped[float | None] = mapped_column(Float, nullable=True)

    equity_curve_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    monthly_returns_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    backtest_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    backtest_end: Mapped[date | None] = mapped_column(Date, nullable=True)

    paper_claimed_sharpe: Mapped[float | None] = mapped_column(Float, nullable=True)
    paper_claimed_cagr: Mapped[float | None] = mapped_column(Float, nullable=True)
    paper_claimed_max_dd: Mapped[float | None] = mapped_column(Float, nullable=True)

    deflated_sharpe_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    dsr_p_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    num_trials_in_selection: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pbo_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    out_of_sample_sharpe: Mapped[float | None] = mapped_column(Float, nullable=True)
    walk_forward_train_fraction: Mapped[float] = mapped_column(Float, nullable=False, default=0.70)
    look_ahead_audit_passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    backtest_engine: Mapped[str | None] = mapped_column(String(32), nullable=True)
    backtest_code_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    transaction_cost_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    # Cost-model fingerprint, e.g. "cm1:d10:s5". Three engines write this table
    # and one gate ranks them together; two rows are only comparable when this
    # matches. NULL on rows written before the cost SSOT landed.
    cost_model_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Provenance for look_ahead_audit_passed above — see BacktestResult for the
    # vocabulary. Without it, a boolean produced by an execution-timing check
    # that never fails is indistinguishable from a real AST audit pass.
    look_ahead_audit_source: Mapped[str | None] = mapped_column(String(32), nullable=True)

    artifact_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    # ── Row-level provenance (audit 2026-08-03) ─────────────────────────
    # PR #1203's routing defect (single-feed strategies graded against a
    # hardcoded SPY default; cross-sectional strategies run on one feed
    # instead of their declared ASSET_UNIVERSE) went undetected in part
    # because two independent pipelines write this table and a row gave no
    # way to tell which one produced it. `backtest_engine` (above) already
    # names the COMPUTE engine ('backtrader' | 'dsl-fusion' |
    # 'portfolio-simulator-v1') and `backtest_code_hash` (above) already
    # identifies per-row STRATEGY code — neither says which SCRIPT called
    # `insert_backtest_if_missing`, which two different scripts can share
    # the same engine tag (run_backtests.py and seed_backtests_from_artifacts.py
    # both produce 'backtrader'-tagged artifacts through the same mapper).
    # `source_pipeline` is that missing identity. Required (no default) —
    # every writer must pass it explicitly; see backtest_repository.py's
    # SOURCE_PIPELINE_* constants.
    source_pipeline: Mapped[str] = mapped_column(String(64), nullable=False)

    # When the backtest was actually COMPUTED — may predate `created_at`:
    # seed_backtests_from_artifacts.py loads a pre-existing artifact file a
    # much earlier run_backtests.py invocation produced and inserts it later.
    # Defaults to "now" (mirrors created_at) for writers that compute and
    # persist in the same call; run_backtests.py / seed_backtests_from_artifacts.py
    # pass the artifact's own `timestamp_utc` when present for an accurate value.
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    # Git SHA of the backend code that computed this row — the SAME
    # ARCHIMEDES_GIT_SHA build-provenance env var `/health`'s "version" field
    # already reads (issue #1039), reused rather than inventing a second
    # mechanism. NULL when unset (local/dev, or a historical row predating
    # this column) — an honest unknown, never a fabricated value.
    source_git_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # True only for rows whose source_pipeline/computed_at/source_git_sha were
    # RECONSTRUCTED after the fact by the 2026-08-03 backfill migration rather
    # than stamped live by the writer at insert time (see that migration for
    # exactly how each value was inferred). False for every row a
    # provenance-aware writer stamps live from here on.
    provenance_inferred: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("strategy_id", "content_hash", name="uq_backtest_strategy_content"),
        Index("ix_backtest_strategy_created", "strategy_id", "created_at"),
        Index("ix_backtest_source_pipeline", "source_pipeline"),
    )

    def to_backtest_result(self) -> BacktestResult:
        return BacktestResult(
            strategy_id=self.strategy_id,
            sharpe_ratio=self.sharpe_ratio,
            sortino_ratio=self.sortino_ratio,
            max_drawdown=self.max_drawdown,
            cagr=self.cagr,
            calmar_ratio=self.calmar_ratio,
            win_rate=self.win_rate,
            profit_factor=self.profit_factor,
            total_trades=self.total_trades,
            avg_holding_period_days=self.avg_holding_period_days,
            correlation_to_spy=self.correlation_to_spy,
            correlation_to_btc=self.correlation_to_btc,
            equity_curve=json.loads(self.equity_curve_json or "[]"),
            monthly_returns=json.loads(self.monthly_returns_json or "[]"),
            backtest_start=self.backtest_start,
            backtest_end=self.backtest_end,
            paper_claimed_sharpe=self.paper_claimed_sharpe,
            paper_claimed_cagr=self.paper_claimed_cagr,
            paper_claimed_max_dd=self.paper_claimed_max_dd,
            deflated_sharpe_ratio=self.deflated_sharpe_ratio,
            dsr_p_value=self.dsr_p_value,
            num_trials_in_selection=self.num_trials_in_selection,
            pbo_score=self.pbo_score,
            out_of_sample_sharpe=self.out_of_sample_sharpe,
            walk_forward_train_fraction=self.walk_forward_train_fraction,
            look_ahead_audit_passed=self.look_ahead_audit_passed,
            backtest_engine=self.backtest_engine,
            backtest_code_hash=self.backtest_code_hash,
            transaction_cost_bps=self.transaction_cost_bps,
            cost_model_id=self.cost_model_id,
            look_ahead_audit_source=self.look_ahead_audit_source,
        )

    @classmethod
    def from_backtest_result(
        cls,
        *,
        strategy_id: str,
        content_hash: str,
        result: BacktestResult,
        source_pipeline: str,
        run_id: str | None = None,
        operation: str | None = None,
        artifact_json: str | None = None,
        computed_at: datetime | None = None,
        source_git_sha: str | None = None,
    ) -> BacktestResultRecord:
        kwargs: dict[str, object] = {}
        if computed_at is not None:
            kwargs["computed_at"] = computed_at
        return cls(
            strategy_id=strategy_id,
            content_hash=content_hash,
            run_id=run_id,
            operation=operation,
            source_pipeline=source_pipeline,
            source_git_sha=source_git_sha,
            **kwargs,
            sharpe_ratio=result.sharpe_ratio,
            sortino_ratio=result.sortino_ratio,
            max_drawdown=result.max_drawdown,
            cagr=result.cagr,
            calmar_ratio=result.calmar_ratio,
            win_rate=result.win_rate,
            profit_factor=result.profit_factor,
            total_trades=result.total_trades,
            avg_holding_period_days=result.avg_holding_period_days,
            correlation_to_spy=result.correlation_to_spy,
            correlation_to_btc=result.correlation_to_btc,
            equity_curve_json=json.dumps(result.equity_curve),
            monthly_returns_json=json.dumps(result.monthly_returns),
            backtest_start=result.backtest_start,
            backtest_end=result.backtest_end,
            paper_claimed_sharpe=result.paper_claimed_sharpe,
            paper_claimed_cagr=result.paper_claimed_cagr,
            paper_claimed_max_dd=result.paper_claimed_max_dd,
            deflated_sharpe_ratio=result.deflated_sharpe_ratio,
            dsr_p_value=result.dsr_p_value,
            num_trials_in_selection=result.num_trials_in_selection,
            pbo_score=result.pbo_score,
            out_of_sample_sharpe=result.out_of_sample_sharpe,
            walk_forward_train_fraction=result.walk_forward_train_fraction,
            look_ahead_audit_passed=result.look_ahead_audit_passed,
            backtest_engine=result.backtest_engine,
            backtest_code_hash=result.backtest_code_hash,
            transaction_cost_bps=result.transaction_cost_bps,
            cost_model_id=result.cost_model_id,
            look_ahead_audit_source=result.look_ahead_audit_source,
            artifact_json=artifact_json,
        )
