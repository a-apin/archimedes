"""Periodic KnowledgeBase pipeline runner.

Mirrors ``chain/oracle_runner.py`` — a standalone ``python -m`` loop that
polls corpus state and triggers ``scripts.run_kb_pipeline`` when conditions
are met. Runs in its own docker-compose service (``kb-runner``) so the API
container stays small.

Re-run trigger:
  (new papers since last run ≥ KB_NEW_PAPER_THRESHOLD)
  OR (days elapsed since last run ≥ KB_MAX_DAYS_SINCE_LAST)
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = int(os.getenv("KB_RUNNER_INTERVAL_SECONDS", "21600"))  # 6 h
NEW_PAPER_THRESHOLD = int(os.getenv("KB_NEW_PAPER_THRESHOLD", "100"))
MAX_DAYS_SINCE_LAST = int(os.getenv("KB_MAX_DAYS_SINCE_LAST", "7"))
ARTIFACT_DIR = Path(os.getenv("KB_ARTIFACT_DIR", "/srv/corpus-artifact"))


def _load_manifest() -> dict | None:
    """Read the last-run manifest from the artifact volume."""
    manifest_path = ARTIFACT_DIR / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        return json.loads(manifest_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("kb_runner: manifest unreadable: %s", exc)
        return None


def _count_new_papers_since(iso_ts: str | None) -> int:
    """Count papers ingested after a given ISO timestamp.

    Soft import — if the DB isn't reachable yet (container boot ordering),
    return 0 so the runner just waits another tick.
    """
    if not iso_ts:
        return 0
    try:
        from sqlalchemy import func

        from archimedes.db import get_session
        from archimedes.models.corpus_store import PaperRecord

        with get_session() as session:
            return session.query(func.count(PaperRecord.arxiv_id)).filter(PaperRecord.created_at > iso_ts).scalar() or 0
    except Exception as exc:
        logger.debug("kb_runner: paper count failed: %s", exc)
        return 0


def needs_rerun() -> bool:
    manifest = _load_manifest()
    if manifest is None:
        logger.info("kb_runner: no prior run — eligible to run")
        return True

    last_run = manifest.get("run_ts")
    new_papers = _count_new_papers_since(last_run)
    if new_papers >= NEW_PAPER_THRESHOLD:
        logger.info("kb_runner: %d new papers ≥ %d threshold", new_papers, NEW_PAPER_THRESHOLD)
        return True

    if last_run:
        try:
            last_dt = datetime.fromisoformat(last_run)
            days = (datetime.now(UTC) - last_dt).days
            if days >= MAX_DAYS_SINCE_LAST:
                logger.info("kb_runner: %d days since last run ≥ %d threshold", days, MAX_DAYS_SINCE_LAST)
                return True
        except ValueError:
            return True

    return False


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s kb_runner %(message)s")
    logger.info(
        "kb_runner: starting (interval=%ds, new_paper=%d, max_days=%d, artifact=%s)",
        INTERVAL_SECONDS,
        NEW_PAPER_THRESHOLD,
        MAX_DAYS_SINCE_LAST,
        ARTIFACT_DIR,
    )

    while True:
        try:
            if needs_rerun():
                logger.info("kb_runner: triggering pipeline")
                from archimedes.scripts.run_kb_pipeline import run_pipeline

                run_pipeline()
            else:
                logger.info("kb_runner: needs_rerun=False, sleeping")
        except NotImplementedError as exc:
            logger.warning("kb_runner: pipeline not yet wired: %s", exc)
        except Exception as exc:
            logger.exception("kb_runner: tick failed: %s", exc)
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
