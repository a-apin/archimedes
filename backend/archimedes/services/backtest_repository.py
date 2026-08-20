"""Persistence helpers for backtest_results table.

Provides read/write access to persisted backtest results, including
daily returns for the selection-bias rigor gate.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from archimedes.models.backtest import BacktestResult
from archimedes.models.backtest_store import BacktestResultRecord

logger = logging.getLogger(__name__)

# Canonical source_pipeline values — every writer through
# insert_backtest_if_missing must pass one of these (the function has no
# default; a caller that forgets fails loudly instead of writing an
# unattributed row). The migration that added the column backfills historical
# rows using the same vocabulary — see
# backend/migrations/versions/363d1c6ff0c0_add_backtest_results_provenance.py.
SOURCE_PIPELINE_RUN_BACKTESTS = "run_backtests"
SOURCE_PIPELINE_SEED_FROM_ARTIFACTS = "seed_backtests_from_artifacts"
SOURCE_PIPELINE_DSL_FUSION = "generation_pipeline.dsl_fusion"
SOURCE_PIPELINE_PORTFOLIO_BACKTESTER = "generation_pipeline.portfolio_backtester"
# Backfill-only sentinel — never stamped by a live writer. Existing rows whose
# backtest_engine ('backtrader') is shared by two indistinguishable historical
# writers land here rather than guessing which one wrote them.
SOURCE_PIPELINE_UNKNOWN_LEGACY = "unknown_pre_provenance"


class _Unset:
    """Sentinel distinguishing "argument omitted" from an explicit ``None``.

    A plain ``None`` default cannot express both "use the running build's SHA"
    and "this row's producing commit is unknowable" — and conflating them makes
    replayed rows claim a commit that did not produce them.
    """

    __slots__ = ()


_UNSET = _Unset()


def insert_backtest_if_missing(
    session: Session,
    *,
    strategy_id: str,
    content_hash: str,
    result: BacktestResult,
    source_pipeline: str,
    run_id: str | None = None,
    operation: str | None = None,
    artifact_json: str | None = None,
    computed_at: datetime | None = None,
    source_git_sha: str | None | _Unset = _UNSET,
) -> tuple[BacktestResultRecord, bool]:
    """Insert row if strategy_id+content_hash missing. Returns (row, inserted).

    ``source_pipeline`` is required (no default) — it is the field the
    2026-08-03 audit found missing that let two writers (the analytics-engine
    /backtrader path and the DSL-fusion path) silently share one table with no
    way to tell a row's origin apart. ``computed_at`` defaults to "now" (the
    common case: compute and persist in the same call); pass the artifact's
    own timestamp when replaying an older run (e.g.
    ``seed_backtests_from_artifacts.py``). ``source_git_sha`` OMITTED means "stamp the
    running build" and resolves from the same ``ARCHIMEDES_GIT_SHA`` env var
    ``/health`` reads (issue #1039). Passing an EXPLICIT ``None`` means "the
    producing commit is genuinely not knowable" and persists NULL.

    Those two must stay distinguishable. ``seed_backtests_from_artifacts.py``
    replays artifacts produced by some earlier, unrecorded commit; if omission
    and explicit-unknown collapsed together, every replayed row would be
    stamped with the CURRENT deploy SHA — a confident, wrong provenance claim
    in the very column added to make provenance trustworthy. An honest NULL is
    the whole point; a plausible wrong SHA is worse than no SHA.
    """
    # Fail closed on an unattributed row. `backtest_engine` has been a column
    # since before the 2026-08-03 provenance audit, but nothing enforced it, so
    # a writer could land a row that no reader could attribute to an engine.
    # That matters now that three engines feed this table and one gate ranks
    # them together: an unattributed row is one whose cost basis cannot be
    # established, and it would be ranked beside rows whose cost basis can.
    # Refusing the write is the only version of this that stays true — a
    # default tag would just be a guess wearing a provenance column's name.
    if not result.backtest_engine:
        raise ValueError(
            f"refusing to persist an unattributed backtest for strategy_id={strategy_id!r}: "
            "backtest_engine is required so every row can be traced to the engine that "
            "produced it and compared on a known cost basis"
        )

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
        source_pipeline=source_pipeline,
        computed_at=computed_at or datetime.now(UTC),
        source_git_sha=(os.getenv("ARCHIMEDES_GIT_SHA") or None)
        if isinstance(source_git_sha, _Unset)
        else source_git_sha,
    )
    session.add(row)
    session.flush()

    # Invalidate the live rigor-gate cache (#library-load-latency): a genuinely
    # new backtest row means this strategy's persisted daily returns just
    # changed, so any cached rigor-gate computation over the old cohort is now
    # stale-in-waiting. rigor_cache.cohort_key already changes the moment the
    # underlying returns change, which is what makes the cache safe even
    # WITHOUT this call — this just tightens the window between "new backtest
    # written" and "next Library read recomputes live" from up to the cache's
    # TTL down to ~0. This is every writer path funneling through the single
    # insert point (generation_pipeline.py, run_backtests.py,
    # seed_backtests_from_artifacts.py all call insert_backtest_if_missing), so
    # one hook here covers all of them. Best-effort: never let a cache-clear
    # failure block a real backtest write from committing.
    try:
        from archimedes.services import rigor_cache

        rigor_cache.clear()
    except Exception as exc:
        logger.warning("rigor_cache invalidation failed after new backtest row (non-fatal): %s", exc)

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
            logger.debug("cached backtest parse failed", exc_info=True)

    # Fallback: derive from equity_curve
    result = row.to_backtest_result()
    if result.equity_curve and len(result.equity_curve) > 1:
        import numpy as np

        ec = np.array(result.equity_curve)
        return ((ec[1:] - ec[:-1]) / ec[:-1]).tolist()

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
    ids = list(strategy_ids)
    if not ids:
        return {}

    # Resolve "latest per strategy" IN THE DATABASE, then hydrate only those
    # rows. The previous implementation `.all()`-ed every row for every id and
    # reduced in Python, which meant fetching N_refreshes x N_strategies rows to
    # return N_strategies. Each row carries a ~489 KB artifact_json plus a
    # ~108 KB equity_curve_json, and nothing dedupes them (canonical_artifact_hash
    # includes run_id, a timestamp, so content_hash is unique per run by
    # construction), so the table grows ~30 rows per refresh cycle forever.
    # Measured cost of the old path: 0.58 MB of peak RSS per persisted row,
    # perfectly linear — 21 MB at 34 rows, 395 MB at 680 rows. That staircase is
    # what pushed the 2026-08-19 backend past its 3072 MB task budget.
    #
    # The inner query deliberately selects ONLY the id column: the window
    # function must not drag the JSON payloads through the client buffer. Window
    # functions are available on Postgres and on SQLite >= 3.25 (the test path).
    ranked = (
        select(
            BacktestResultRecord.id.label("id"),
            func.row_number()
            .over(
                partition_by=BacktestResultRecord.strategy_id,
                order_by=(
                    BacktestResultRecord.created_at.desc(),
                    BacktestResultRecord.id.desc(),
                ),
            )
            .label("rank"),
        )
        .where(BacktestResultRecord.strategy_id.in_(ids))
        .subquery()
    )
    latest_ids = select(ranked.c.id).where(ranked.c.rank == 1)

    rows = session.query(BacktestResultRecord).filter(BacktestResultRecord.id.in_(latest_ids)).all()

    return {row.strategy_id: row for row in rows}
