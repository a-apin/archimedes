"""Seed backtest results from existing analytics-engine artifacts.

Unlike run_backtests.py (which re-runs the analytics-engine), this script
only loads pre-existing artifact JSON files into the backtest_results table.
Useful for deployment where the analytics-engine isn't installed in the
backend container but artifacts are mounted as a volume.

Usage:
  cd backend
  python -m archimedes.scripts.seed_backtests_from_artifacts
"""

from __future__ import annotations

import json
import logging
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


def seed_from_artifacts(artifact_dir: Path | None = None) -> dict[str, int]:
    """Load all artifact JSON files and persist them to the DB.

    Returns {"inserted": N, "skipped": N, "failed": N}.
    """
    if artifact_dir is None:
        # Try common locations
        candidates = [
            Path("/app/analytics-engine/artifacts"),  # Docker mount
            Path(__file__).resolve().parents[3] / "analytics-engine" / "artifacts",  # Host
        ]
        for c in candidates:
            if c.exists():
                artifact_dir = c
                break
        else:
            logger.warning("No artifact directory found")
            return {"inserted": 0, "skipped": 0, "failed": 0}

    init_db()
    provider = default_provider()
    strategies = provider.list_strategies()

    # Index strategies by paper title for matching
    strategy_by_title: dict[str, object] = {}
    for s in strategies:
        strategy_by_title[s.paper_title] = s

    inserted = 0
    skipped = 0
    failed = 0

    for artifact_path in sorted(artifact_dir.glob("*.json")):
        try:
            raw = artifact_path.read_text(encoding="utf-8")
            payload = json.loads(raw)
            artifact = AnalyticsArtifactModel.model_validate(payload)

            # Match artifact to strategy by paper title
            title = payload.get("strategy", {}).get("paper_title", "")
            strategy = strategy_by_title.get(title)

            if strategy is None:
                logger.warning("skip %s: no matching strategy for '%s'", artifact_path.name, title)
                failed += 1
                continue

            mapped, selected_operation = map_artifact_to_backtest_result(
                artifact,
                strategy_id=strategy.id,
            )
            content_hash = canonical_artifact_hash(payload)

            with get_session() as session:
                _, was_inserted = insert_backtest_if_missing(
                    session,
                    strategy_id=strategy.id,
                    content_hash=content_hash,
                    result=mapped,
                    run_id=artifact.run_id,
                    operation=selected_operation,
                    artifact_json=raw,
                )
                session.commit()

            if was_inserted:
                inserted += 1
                logger.info("seeded: %s (%s)", strategy.paper_title, strategy.id[:12])
            else:
                skipped += 1

        except Exception as exc:
            failed += 1
            logger.exception("failed to seed %s: %s", artifact_path, exc)

    summary = {"inserted": inserted, "skipped": skipped, "failed": failed}
    logger.info("seed summary: %s", summary)
    return summary


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    summary = seed_from_artifacts()
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
