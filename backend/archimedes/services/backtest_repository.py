"""Persistence helpers for backtest_results table.

Provides read/write access to persisted backtest results, including
daily returns for the selection-bias rigor gate.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy.orm import Session

from archimedes.models.backtest import BacktestResult
from archimedes.models.backtest_store import BacktestResultRecord


def insert_backtest_if_missing(
    session: Session,
    *,
    strategy_id: str,
    content_hash: str,
    result: BacktestResult,
    run_id: str | None = None,
    operation: str | None = None,
    artifact_json: str | None = None,
) -> tuple[BacktestResultRecord, bool]:
    """Insert row if strategy_id+content_hash missing. Returns (row, inserted)."""
    existing = (
        session.query(BacktestResultRecord)
        .filter(
            BacktestResultRecord.strategy_id == strategy_id,
            BacktestResultRecord.content_hash == content_hash,
        )
        .one_or_none()
    )
    if existing is not None:
        return existing, False

    row = BacktestResultRecord.from_backtest_result(
        strategy_id=strategy_id,
        content_hash=content_hash,
        result=result,
        run_id=run_id,
        operation=operation,
        artifact_json=artifact_json,
    )
    session.add(row)
    session.flush()
    return row, True


def get_daily_returns(session: Session, strategy_id: str) -> list[float]:
    """Fetch daily returns from the latest backtest for a strategy.

    Daily returns are stored in the artifact_json blob (not as a separate column).
    Falls back to deriving from equity_curve if artifact is unavailable.
    Returns an empty list if no persisted result exists.
    """
    row = (
        session.query(BacktestResultRecord)
        .filter(BacktestResultRecord.strategy_id == strategy_id)
        .order_by(BacktestResultRecord.created_at.desc(), BacktestResultRecord.id.desc())
        .first()
    )
    if row is None:
        return []

    # Try artifact_json first (has raw daily_returns from analytics-engine)
    import json as _json

    if row.artifact_json:
        try:
            artifact = _json.loads(row.artifact_json)
            for r in artifact.get("results", []):
                daily = r.get("metrics", {}).get("daily_returns", [])
                if daily:
                    return daily
        except (_json.JSONDecodeError, KeyError):
            pass

    # Fallback: derive from equity_curve
    result = row.to_backtest_result()
    if result.equity_curve and len(result.equity_curve) > 1:
        import numpy as np

        ec = np.array(result.equity_curve)
        returns = ((ec[1:] - ec[:-1]) / ec[:-1]).tolist()
        return returns

    return []


def get_all_daily_returns(
    session: Session,
    strategy_ids: list[str],
) -> dict[str, list[float]]:
    """Fetch daily returns for multiple strategies.

    Returns {strategy_id: [daily_returns]} for strategies with persisted data.
    """
    out: dict[str, list[float]] = {}
    for sid in strategy_ids:
        returns = get_daily_returns(session, sid)
        if returns:
            out[sid] = returns
    return out


def update_rigor_gate_fields(
    session: Session,
    strategy_id: str,
    *,
    deflated_sharpe_ratio: float | None = None,
    dsr_p_value: float | None = None,
    num_trials_in_selection: int | None = None,
    pbo_score: float | None = None,
    out_of_sample_sharpe: float | None = None,
    look_ahead_audit_passed: bool | None = None,
) -> BacktestResultRecord | None:
    """Update rigor-gate fields on the latest backtest row for a strategy.

    Returns the updated row, or None if no persisted result exists.
    """
    row = (
        session.query(BacktestResultRecord)
        .filter(BacktestResultRecord.strategy_id == strategy_id)
        .order_by(BacktestResultRecord.created_at.desc(), BacktestResultRecord.id.desc())
        .first()
    )
    if row is None:
        return None

    if deflated_sharpe_ratio is not None:
        row.deflated_sharpe_ratio = deflated_sharpe_ratio
    if dsr_p_value is not None:
        row.dsr_p_value = dsr_p_value
    if num_trials_in_selection is not None:
        row.num_trials_in_selection = num_trials_in_selection
    if pbo_score is not None:
        row.pbo_score = pbo_score
    if out_of_sample_sharpe is not None:
        row.out_of_sample_sharpe = out_of_sample_sharpe
    if look_ahead_audit_passed is not None:
        row.look_ahead_audit_passed = look_ahead_audit_passed

    session.flush()
    return row


def latest_backtests_by_strategy(
    session: Session,
    strategy_ids: Iterable[str],
) -> dict[str, BacktestResultRecord]:
    """Fetch latest row per strategy_id."""
    ids = [sid for sid in strategy_ids]
    if not ids:
        return {}

    rows = (
        session.query(BacktestResultRecord)
        .filter(BacktestResultRecord.strategy_id.in_(ids))
        .order_by(
            BacktestResultRecord.strategy_id.asc(),
            BacktestResultRecord.created_at.desc(),
            BacktestResultRecord.id.desc(),
        )
        .all()
    )

    latest: dict[str, BacktestResultRecord] = {}
    for row in rows:
        if row.strategy_id not in latest:
            latest[row.strategy_id] = row
    return latest
