"""Periodic KnowledgeBase pipeline runner.

Mirrors ``chain/oracle_runner.py`` — a standalone ``python -m`` loop that
polls corpus state and triggers ``scripts.run_kb_pipeline`` when conditions
are met. Runs in its own docker-compose service (``kb-runner``) so the API
container stays small.

Re-run trigger:
  (new papers since last run ≥ KB_NEW_PAPER_THRESHOLD)
  OR (days elapsed since last run ≥ KB_MAX_DAYS_SINCE_LAST)

Exactly-once (#1043): the manifest read-decide-write above is UNLOCKED — two
live copies could both decide ``needs_rerun() == True`` and race the same
(heavy, expensive) pipeline against the same manifest file. kb_runner is a
plain synchronous ``time.sleep`` loop (not asyncio), so it does not use
``services.runner_lease.RunnerLeaseGuard`` (that class's independent
``asyncio.create_task`` renewal loop needs a running event loop). Instead
``_SyncLeaseClient`` below talks to the exact same Redis keys via the exact
same compare-and-set/delete Lua scripts imported from ``redis_state.py`` —
a lease taken out here is indistinguishable on the wire from one taken out
by the async runners; only the transport (sync ``redis`` client vs.
``redis.asyncio``) differs. kb is NOT funds-adjacent, so unlike
oracle_runner/agent_runner this uses a simpler non-blocking acquire-hold-
release around the pipeline: if another copy already holds the lease, this
cycle is just skipped (not retried-with-backoff) — the next scheduled tick
tries again.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = int(os.getenv("KB_RUNNER_INTERVAL_SECONDS", "21600"))  # 6 h
NEW_PAPER_THRESHOLD = int(os.getenv("KB_NEW_PAPER_THRESHOLD", "100"))
MAX_DAYS_SINCE_LAST = int(os.getenv("KB_MAX_DAYS_SINCE_LAST", "7"))
ARTIFACT_DIR = Path(os.getenv("KB_ARTIFACT_DIR", "/srv/corpus-artifact"))

# Exactly-once lease (#1043)
RUNNER_NAME = "kb_runner"
# Long TTL — the corpus pipeline (SPECTER2 embed + HDBSCAN/BERTopic cluster +
# REBEL/SciSpacy KG) can run for a while; the background renewal thread keeps
# it alive for the duration, this just bounds an unclean-death orphan.
LEASE_TTL_MS = int(os.getenv("KB_LEASE_TTL_MS", "1_800_000"))  # 30 min
LEASE_RENEW_INTERVAL_S = int(os.getenv("KB_LEASE_RENEW_INTERVAL_S", "600"))  # 10 min, ~ttl/3


class _SyncLeaseClient:
    """Minimal SYNC counterpart to ``AgentStateStore``'s lease API (#1043).

    kb_runner is a plain ``time.sleep`` loop, not asyncio — round-tripping
    through ``asyncio.run(...)`` for every acquire/renew call would spin up a
    fresh event loop each time for no benefit. This talks directly to the
    SAME Redis keys (``archimedes:leader:<runner_name>`` +
    ``:fencing``) using the SAME compare-and-set/delete Lua scripts imported
    from ``redis_state.py``, so a lease taken out here is indistinguishable
    on the wire from one taken out by the async runners.
    """

    def __init__(self, url: str | None = None) -> None:
        # Imported lazily so importing kb_runner in tests never requires the
        # sync `redis` package to actually reach a server, and so the async
        # AgentStateStore module (redis.asyncio) stays the only import site
        # for callers that don't need the sync path.
        import redis as sync_redis

        from archimedes.services.redis_state import _LEASE_RELEASE_LUA, _LEASE_RENEW_LUA, REDIS_URL

        self._client = sync_redis.Redis.from_url(url or REDIS_URL, decode_responses=True)
        self._renew_script = self._client.register_script(_LEASE_RENEW_LUA)
        self._release_script = self._client.register_script(_LEASE_RELEASE_LUA)

    def acquire(self, runner_name: str, ttl_ms: int) -> str | None:
        """Win-then-fence, mirroring ``AgentStateStore.acquire_lease`` exactly
        (#1046 review): only a winner of the ``SET NX`` consumes a fence
        number, so a non-holder retrying this in a loop does not burn fence
        values on every failed attempt. See the async version's docstring in
        ``redis_state.py`` for the full step-by-step rationale, including the
        ``XX``-guarded overwrite for the rare expire-in-between race.
        """
        from archimedes.services.redis_state import KEY_LEASE_FENCING_SUFFIX, KEY_LEASE_PREFIX

        key = f"{KEY_LEASE_PREFIX}{runner_name}"
        fencing_key = f"{KEY_LEASE_PREFIX}{runner_name}{KEY_LEASE_FENCING_SUFFIX}"

        holder_uuid = str(uuid.uuid4())
        acquired = self._client.set(key, holder_uuid, nx=True, px=ttl_ms)
        if not acquired:
            return None

        fence = self._client.incr(fencing_key)
        token = f"{holder_uuid}:{fence}"
        overwritten = self._client.set(key, token, px=ttl_ms, xx=True)
        return token if overwritten else None

    def renew(self, runner_name: str, token: str, ttl_ms: int) -> bool:
        from archimedes.services.redis_state import KEY_LEASE_PREFIX

        key = f"{KEY_LEASE_PREFIX}{runner_name}"
        result = self._renew_script(keys=[key], args=[token, str(ttl_ms)])
        return bool(result)

    def release(self, runner_name: str, token: str) -> None:
        from archimedes.services.redis_state import KEY_LEASE_PREFIX

        key = f"{KEY_LEASE_PREFIX}{runner_name}"
        self._release_script(keys=[key], args=[token])


def _run_pipeline_with_lease() -> None:
    """Acquire the exactly-once lease, run the pipeline with a background
    renewal thread, then release. Fails closed: if the lease cannot be
    acquired — Redis unreachable, or another kb_runner copy already holds it
    — this cycle is skipped rather than racing the unlocked manifest
    read-decide-write against a concurrent pipeline run.
    """
    try:
        client = _SyncLeaseClient()
    except Exception:
        logger.exception("kb_runner: lease client unavailable (Redis down?) — skipping this cycle (fail-closed)")
        return

    try:
        token = client.acquire(RUNNER_NAME, LEASE_TTL_MS)
    except Exception:
        logger.exception("kb_runner: lease acquire errored — skipping this cycle (fail-closed)")
        return

    if token is None:
        logger.info("kb_runner: lease held by another copy — skipping this cycle")
        return

    logger.info("kb_runner: lease acquired — triggering pipeline")
    stop_renew = threading.Event()
    # Set by the renewal thread the instant it can no longer PROVE this
    # process still holds the lease — either a definitive CAS loss (someone
    # else's fence/token is now in the key) or an error that leaves renewal
    # unconfirmed (e.g. a Redis blip). Both are treated the same, fail-closed
    # way: run_pipeline() below checks this and refuses to write manifest.json
    # if it is set, so a run whose lease expired mid-flight cannot clobber the
    # authoritative instance's manifest with a lost-update race (#1046 review).
    lease_lost = threading.Event()

    def _renew_loop() -> None:
        while not stop_renew.wait(LEASE_RENEW_INTERVAL_S):
            try:
                if not client.renew(RUNNER_NAME, token, LEASE_TTL_MS):
                    logger.error("kb_runner: lease renew compare-and-set FAILED — lease lost mid-run")
                    lease_lost.set()
            except Exception:
                logger.exception("kb_runner: lease renew errored — cannot confirm the lease is still held")
                lease_lost.set()

    renew_thread = threading.Thread(target=_renew_loop, name="kb-lease-renew", daemon=True)
    renew_thread.start()
    try:
        from archimedes.scripts.run_kb_pipeline import run_pipeline

        run_pipeline(lease_lost=lease_lost)
    finally:
        stop_renew.set()
        renew_thread.join(timeout=5)
        if lease_lost.is_set():
            # Not provably ours anymore — still safe to call (release_lease is
            # a compare-and-delete no-op against a token we no longer hold),
            # but log distinctly so this case is visible in the runner logs.
            logger.warning("kb_runner: lease was lost during this run — releasing (likely a no-op)")
        try:
            client.release(RUNNER_NAME, token)
        except Exception:
            logger.warning("kb_runner: lease release failed (TTL expiry will reclaim it)", exc_info=True)


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
                _run_pipeline_with_lease()
            else:
                logger.info("kb_runner: needs_rerun=False, sleeping")
        except NotImplementedError as exc:
            logger.warning("kb_runner: pipeline not yet wired: %s", exc)
        except Exception as exc:
            logger.exception("kb_runner: tick failed: %s", exc)
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
