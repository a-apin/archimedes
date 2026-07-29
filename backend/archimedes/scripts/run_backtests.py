"""Run analytics-engine backtests and persist latest metrics.

Usage:
  cd backend
  python -m archimedes.scripts.run_backtests
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from archimedes.db import get_session, init_db
from archimedes.services.backtest_mapper import (
    AnalyticsArtifactModel,
    canonical_artifact_hash,
    map_artifact_to_backtest_result,
)
from archimedes.services.backtest_repository import get_daily_returns, insert_backtest_if_missing
from archimedes.services.strategy_provider import default_provider
from archimedes.services.vol_plausibility import (
    VolPlausibilityError,
    assess_strategy,
    compute_vol_stats,
    daily_returns_from_equity_curve,
)

logger = logging.getLogger(__name__)

# The confirmed-correct reference strategy for the Rule-B equity-baseline-match
# check (see services/vol_plausibility.py module docstring) — a stem match
# against analytics-engine/strategies/pipeline_buy_hold.py.
_BASELINE_STRATEGY_STEM = "pipeline_buy_hold"


@dataclass(frozen=True)
class RunConfig:
    operations: list[str]
    start: str
    end: str
    initial_cash: float
    tx_cost_bps: int
    slippage_bps: int


def _repo_root() -> Path:
    """Nearest ancestor that contains ``analytics-engine`` — layout-robust.

    Host layout: .../backend/archimedes/scripts/... with analytics-engine at
    the repo root (parents[3]). Container layout: /app/archimedes/scripts/...
    with /app/analytics-engine mounted (parents[2]). A fixed parents[3]
    resolved to "/" inside the container, so the prod backfill raised
    ModuleNotFoundError (same class of bug as #846's portfolio_backtester fix).
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "analytics-engine").is_dir():
            return parent
    return here.parents[3]  # historical fallback; loudly wrong paths beat a crash here


def _analytics_strategy_dir(repo_root: Path) -> Path:
    return repo_root / "analytics-engine" / "strategies"


def _artifact_dir(repo_root: Path) -> Path:
    return repo_root / "analytics-engine" / "artifacts"


def _ensure_analytics_import(repo_root: Path) -> None:
    analytics_src = repo_root / "analytics-engine" / "src"
    src_text = str(analytics_src)
    if src_text not in sys.path:
        sys.path.insert(0, src_text)


def _load_run_command(repo_root: Path):
    _ensure_analytics_import(repo_root)
    from archimedes_analytics_engine.cli import run_command

    return run_command


def _typed_error_types(repo_root: Path) -> tuple[type[BaseException], ...]:
    """Typed, explanatory strategy-run errors — log the message, not a stack trace.

    ``FeedArityError`` (issue #887) is the analytics engine failing closed with
    a passport-grade explanation (e.g. "pairs needs >= 2 instruments"); a
    traceback adds nothing. Returns an empty tuple when the engine is not
    importable so the caller degrades to generic exception handling.
    """
    try:
        _ensure_analytics_import(repo_root)
        from archimedes_analytics_engine.engine import FeedArityError

        return (FeedArityError,)
    except Exception:
        return ()


def _read_config() -> RunConfig:
    operations = [op.strip().upper() for op in os.getenv("BACKTEST_OPERATIONS", "SPY").split(",") if op.strip()]
    # Full curated evidence depth by default (~5,400 daily bars). The old
    # 2018 default silently produced 8-year runs with materially weaker DSR
    # statistics than the seed-time store — the scheduler and the CLI should
    # both grade on the same evidence window the passports were built on.
    start = os.getenv("BACKTEST_START", "2004-01-02")
    end = os.getenv("BACKTEST_END", datetime.now(UTC).date().isoformat())
    initial_cash = float(os.getenv("BACKTEST_INITIAL_CASH", "100000"))
    tx_cost_bps = int(os.getenv("BACKTEST_TX_COST_BPS", "10"))
    slippage_bps = int(os.getenv("BACKTEST_SLIPPAGE_BPS", "5"))
    return RunConfig(
        operations=operations,
        start=start,
        end=end,
        initial_cash=initial_cash,
        tx_cost_bps=tx_cost_bps,
        slippage_bps=slippage_bps,
    )


def _load_baseline_vol(strategy_by_path: dict) -> float | None:
    """Best-effort fetch of the pipeline_buy_hold realized vol for Rule B.

    Deliverable-2 permanent guard (audit 2026-07-27, services/vol_plausibility.py):
    every strategy's freshly-computed backtest is checked against BOTH the
    self-contained asset-class-band rule (Rule A) AND, when available, this
    pre-existing equity baseline (Rule B) — the check that specifically catches a
    diversified/non-equity strategy whose realized vol is suspiciously close to
    pure S&P equity (the exact signature of the confirmed single-feed-default
    fallback). Reads whatever ``pipeline_buy_hold`` last persisted BEFORE this
    run started — one query, done once, not per strategy.

    Returns ``None`` (never raises) when the reference strategy file is missing,
    has no persisted backtest yet (e.g. a brand new clone before the first ever
    run), or its own persisted series is degenerate/too short — every case logs
    a clear warning so the "Rule B skipped this run" gap is visible, not silent.
    """
    baseline = next(
        (s for s in strategy_by_path.values() if Path(s.strategy_code_path or "").stem == _BASELINE_STRATEGY_STEM),
        None,
    )
    if baseline is None:
        logger.warning(
            "vol-plausibility guard: no strategy file matching stem %r found — "
            "Rule B (equity-baseline match) skipped this run; only Rule A runs",
            _BASELINE_STRATEGY_STEM,
        )
        return None

    with get_session() as session:
        baseline_returns = get_daily_returns(session, baseline.id)

    if not baseline_returns:
        logger.warning(
            "vol-plausibility guard: %s has no persisted backtest yet — "
            "Rule B (equity-baseline match) skipped this run; only Rule A runs",
            _BASELINE_STRATEGY_STEM,
        )
        return None

    stats = compute_vol_stats(baseline_returns)
    if stats.annualized_vol is None:
        logger.warning(
            "vol-plausibility guard: %s's persisted series is degenerate/too short "
            "(n_obs=%d) — Rule B (equity-baseline match) skipped this run; only Rule A runs",
            _BASELINE_STRATEGY_STEM,
            stats.n_obs,
        )
        return None

    return stats.annualized_vol


def _daily_returns_for_operation(artifact_payload: dict, operation: str | None) -> list[float] | None:
    """Raw ``daily_returns`` for one named operation, read directly off the
    parsed artifact payload — ``None`` if not found/empty.

    ``AnalyticsArtifactModel``/``EngineMetricsModel`` (``backtest_mapper.py``)
    declare ``extra="ignore"`` and never define a ``daily_returns`` field, so
    the raw per-bar returns array the analytics engine actually wrote
    (``archimedes_analytics_engine.cli.run_command``'s ``asdict(bt_result)``,
    which includes ``daily_returns``) only survives in the plain-dict
    ``artifact_payload`` from ``json.loads`` — not in the pydantic-validated
    ``artifact`` object. Matches by operation name (case-insensitive), the same
    identity ``select_operation_result`` (``backtest_mapper.py``) used to pick
    the row ``map_artifact_to_backtest_result`` mapped from — so this reads the
    CHOSEN operation's own series, never whichever entry happens to sit first
    in ``results`` (see ``get_daily_returns`` in ``backtest_repository.py``,
    which — unlike ``select_operation_result`` — does exactly that and is the
    reason this guard cannot just reuse it).
    """
    if not operation:
        return None
    wanted = operation.upper()
    for row in artifact_payload.get("results", []):
        if str(row.get("operation", "")).upper() != wanted:
            continue
        raw = row.get("metrics", {}).get("daily_returns")
        return [float(v) for v in raw] if raw else None
    return None


def run_backtests() -> dict:
    repo_root = _repo_root()
    strategy_dir = _analytics_strategy_dir(repo_root)
    artifact_dir = _artifact_dir(repo_root)
    cfg = _read_config()

    run_command = _load_run_command(repo_root)
    typed_errors = _typed_error_types(repo_root)

    provider = default_provider(repo_root=repo_root)
    strategy_by_path = {
        Path(s.strategy_code_path).resolve(): s for s in provider.list_strategies() if s.strategy_code_path
    }

    init_db()

    # Deliverable-2 permanent guard: fetch the pre-existing equity baseline ONCE
    # (not per strategy) so every freshly-computed artifact below can be checked
    # against both plausibility rules before it is persisted. See
    # services/vol_plausibility.py's module docstring for Rule A / Rule B.
    baseline_vol = _load_baseline_vol(strategy_by_path)

    inserted = 0
    skipped = 0
    failed = 0
    errors: dict[str, str] = {}

    for strategy_file in sorted(strategy_dir.glob("*.py")):
        if strategy_file.name.startswith("_"):
            continue

        strategy = strategy_by_path.get(strategy_file.resolve())
        if strategy is None:
            logger.warning("skip %s: no strategy_id from provider", strategy_file)
            failed += 1
            continue

        try:
            out = run_command(
                operations=cfg.operations,
                start=cfg.start,
                end=cfg.end,
                initial_cash=cfg.initial_cash,
                tx_cost_bps=cfg.tx_cost_bps,
                slippage_bps=cfg.slippage_bps,
                artifact_dir=artifact_dir,
                strategy_path=strategy_file,
            )

            artifact_path = Path(str(out["artifact_path"]))
            artifact_json = artifact_path.read_text(encoding="utf-8")
            artifact_payload = json.loads(artifact_json)
            artifact = AnalyticsArtifactModel.model_validate(artifact_payload)
            mapped, selected_operation = map_artifact_to_backtest_result(
                artifact,
                strategy_id=strategy.id,
            )
            content_hash = canonical_artifact_hash(artifact_payload)

            # Deliverable-2 permanent guard (audit 2026-07-27): refuse to persist
            # a backtest whose realized vol is wildly inconsistent with the
            # strategy's own declared ASSET_UNIVERSE — this is exactly the
            # confirmed "backtesting the wrong asset" defect class (e.g.
            # capital_preservation_tbill declaring BIL but reading pure equity
            # vol). Checked BEFORE insert_backtest_if_missing so a flagged
            # artifact is never written, loudly, fail-closed.
            #
            # Pull the CHOSEN operation's raw daily_returns straight off the
            # artifact payload rather than re-deriving them from
            # mapped.equity_curve via pct-change: select_operation_result()
            # (backtest_mapper.py) picks the row by NAME (searches for "SPY"
            # regardless of position), but this file's own _load_baseline_vol()
            # — and every other downstream reader, via
            # backtest_repository.get_daily_returns() — reads artifact
            # results[0], which is not guaranteed to be the same row once
            # BACKTEST_OPERATIONS lists more than one op (e.g.
            # "NIKKEI,SPY" puts SPY, the chosen op, at index 1). Deriving from
            # mapped.equity_curve happened to coincide with the chosen op
            # today, but named lookup is what actually keeps this guard
            # checking the row that gets persisted and later re-read — falling
            # back to the equity-curve derivation only if the raw field is
            # absent (e.g. an older artifact schema).
            candidate_returns = _daily_returns_for_operation(artifact_payload, selected_operation)
            if not candidate_returns:
                candidate_returns = daily_returns_from_equity_curve(mapped.equity_curve)
            verdict = assess_strategy(strategy.asset_universe, candidate_returns, baseline_vol=baseline_vol)
            if verdict.status in ("flagged", "degenerate"):
                raise VolPlausibilityError(
                    f"strategy {strategy.id} ({strategy.paper_title}) declares ASSET_UNIVERSE="
                    f"{strategy.asset_universe!r}; realized vol/return check status={verdict.status!r}: "
                    f"{'; '.join(verdict.reasons)}"
                )

            with get_session() as session:
                _, was_inserted = insert_backtest_if_missing(
                    session,
                    strategy_id=strategy.id,
                    content_hash=content_hash,
                    result=mapped,
                    run_id=artifact.run_id,
                    operation=selected_operation,
                    artifact_json=artifact_json,
                )
                session.commit()

            if was_inserted:
                inserted += 1
                logger.info("inserted backtest row: %s (%s)", strategy.paper_title, strategy.id)
            else:
                skipped += 1
                logger.info("skip duplicate content hash: %s (%s)", strategy.paper_title, strategy.id)

        except VolPlausibilityError as exc:
            # Loud + fail-closed by construction: this exception is only ever
            # raised AFTER the artifact was computed but BEFORE
            # insert_backtest_if_missing — so nothing was written. ERROR (not
            # WARNING) because this is the exact defect class the whole guard
            # exists to catch, not a routine engine limitation.
            failed += 1
            errors[strategy.id] = f"VolPlausibilityError: {exc}"
            logger.error("REFUSING to persist backtest for %s: %s", strategy_file.name, exc)
        except Exception as exc:
            failed += 1
            if typed_errors and isinstance(exc, typed_errors):
                # Typed fail-closed error from the engine (e.g. FeedArityError,
                # issue #887): the message is the explanation — log it clean
                # and carry it in the summary instead of dumping a stack trace.
                errors[strategy.id] = f"{type(exc).__name__}: {exc}"
                logger.warning("backtest failed for %s: %s: %s", strategy_file.name, type(exc).__name__, exc)
            else:
                logger.exception("backtest failed for %s: %s", strategy_file, exc)

    summary: dict = {
        "inserted": inserted,
        "skipped": skipped,
        "failed": failed,
    }
    if errors:
        summary["errors"] = errors
    logger.info("backtest run summary: %s", summary)
    return summary


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    summary = run_backtests()
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
