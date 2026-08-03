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
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from archimedes.db import get_session, init_db
from archimedes.services.backtest_mapper import (
    AnalyticsArtifactModel,
    canonical_artifact_hash,
    map_artifact_to_backtest_result,
)
from archimedes.services.backtest_repository import (
    SOURCE_PIPELINE_SEED_FROM_ARTIFACTS,
    insert_backtest_if_missing,
)
from archimedes.services.strategy_provider import default_provider

logger = logging.getLogger(__name__)


def _computed_at_from_payload(payload: dict) -> datetime | None:
    """Parse the artifact's own ``timestamp_utc`` — this is the field that
    matters most for THIS script: it seeds artifacts that were computed by an
    earlier ``run_backtests.py`` invocation, possibly long before this seed
    run inserts the row, so ``created_at`` alone would misrepresent when the
    backtest actually ran. Returns ``None`` (never raises) on a missing/
    malformed field; the caller then falls back to "now".
    """
    raw = payload.get("timestamp_utc")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


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

    # Group every artifact file by the strategy (paper title) it describes, then
    # seed only the *current-state* run per strategy: the newest artifact that
    # parses + maps cleanly. The artifact directory accumulates one JSON per
    # backtest re-run, so a single strategy can have several timestamped files;
    # globbing and seeding all of them persisted stale runs alongside the current
    # one. run_id is a sortable ISO-8601 UTC timestamp (e.g. "20260518T223800Z"),
    # so a reverse string sort gives newest-first, and if the newest run is broken
    # we fall back to the next-newest — a single corrupt artifact never silently
    # drops a strategy's backtest entirely.
    artifacts_by_title: dict[str, list[tuple[Path, str, dict]]] = defaultdict(list)
    for artifact_path in artifact_dir.glob("*.json"):
        try:
            raw = artifact_path.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except Exception as exc:
            logger.warning("skip unreadable artifact %s: %s", artifact_path.name, exc)
            continue
        title = payload.get("strategy", {}).get("paper_title", "")
        artifacts_by_title[title].append((artifact_path, raw, payload))

    # Newest run first, keyed on run_id (falling back to the filename stem).
    for candidates in artifacts_by_title.values():
        candidates.sort(key=lambda c: c[2].get("run_id") or c[0].stem, reverse=True)

    inserted = 0
    skipped = 0
    failed = 0

    for title, candidates in artifacts_by_title.items():
        strategy = strategy_by_title.get(title)
        if strategy is None:
            logger.warning("skip %d artifact(s): no matching strategy for '%s'", len(candidates), title)
            failed += 1
            continue

        # Try newest-first; stop at the first run that seeds successfully and
        # ignore that strategy's older runs.
        seeded = False
        for artifact_path, raw, payload in candidates:
            try:
                artifact = AnalyticsArtifactModel.model_validate(payload)
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
                        source_pipeline=SOURCE_PIPELINE_SEED_FROM_ARTIFACTS,
                        computed_at=_computed_at_from_payload(payload),
                    )
                    session.commit()

                if was_inserted:
                    inserted += 1
                    logger.info("seeded: %s (%s) from %s", strategy.paper_title, strategy.id[:12], artifact_path.name)
                else:
                    skipped += 1
                seeded = True
                break
            except Exception as exc:
                logger.warning("artifact %s failed to seed (%s); falling back to older run", artifact_path.name, exc)
                continue

        if not seeded:
            failed += 1
            logger.error("all artifacts failed for strategy '%s'", title)

    summary = {"inserted": inserted, "skipped": skipped, "failed": failed}
    logger.info("seed summary: %s", summary)
    return summary


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    summary = seed_from_artifacts()
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
