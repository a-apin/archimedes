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
from archimedes.services.backtest_repository import insert_backtest_if_missing
from archimedes.services.strategy_provider import default_provider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunConfig:
    operations: list[str]
    start: str
    end: str
    initial_cash: float
    tx_cost_bps: int
    slippage_bps: int


def _repo_root() -> Path:
    # .../backend/archimedes/scripts/run_backtests.py -> repo root
    return Path(__file__).resolve().parents[3]


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


def _read_config() -> RunConfig:
    operations = [op.strip().upper() for op in os.getenv("BACKTEST_OPERATIONS", "SPY").split(",") if op.strip()]
    start = os.getenv("BACKTEST_START", "2018-01-01")
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


def run_backtests() -> dict[str, int]:
    repo_root = _repo_root()
    strategy_dir = _analytics_strategy_dir(repo_root)
    artifact_dir = _artifact_dir(repo_root)
    cfg = _read_config()

    run_command = _load_run_command(repo_root)

    provider = default_provider(repo_root=repo_root)
    strategy_by_path = {
        Path(s.strategy_code_path).resolve(): s for s in provider.list_strategies() if s.strategy_code_path
    }

    init_db()

    inserted = 0
    skipped = 0
    failed = 0

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

        except Exception as exc:
            failed += 1
            logger.exception("backtest failed for %s: %s", strategy_file, exc)

    summary = {
        "inserted": inserted,
        "skipped": skipped,
        "failed": failed,
    }
    logger.info("backtest run summary: %s", summary)
    return summary


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    summary = run_backtests()
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
