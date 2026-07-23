"""Operator-triggered KnowledgeBase pipeline runner.

Wraps the existing ``submodules/KnowledgeBase/papers_analysis/`` pipeline
(SPECTER2 embeddings + HDBSCAN/BERTopic clustering + REBEL/SciSpacy KG)
and writes outputs to:

  /srv/corpus-artifact/embeddings.npy + ids.json
  /srv/corpus-artifact/clusters.json
  /srv/corpus-artifact/topics.json
  /srv/corpus-artifact/kg_triples.jsonl
  /srv/corpus-artifact/kg_graph.json
  /srv/corpus-artifact/manifest.json
  Postgres papers.cluster_id + topic_label (denormalized)
  Postgres kg_entities + kg_relations

Per docs/specs/kb-integration-spec.md:
  - No re-implementation: invokes the submodule's existing code.
  - Atomic-swap: writes to a tmpdir, then symlinks on success.

This skeleton wires the entry point and persistence schema; the actual
pipeline invocation is gated behind a feature flag while the docker
container with the SPECTER2 / REBEL / SciSpacy deps is built out.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_ARTIFACT_DIR = Path(os.getenv("KB_ARTIFACT_DIR", "/srv/corpus-artifact"))
DEFAULT_CORPUS_DIR = Path(os.getenv("KB_CORPUS_DIR", "/srv/corpus-text"))


def _kb_submodule_path() -> Path:
    """Locate submodules/KnowledgeBase/ relative to this file."""
    here = Path(__file__).resolve()
    # backend/archimedes/scripts/run_kb_pipeline.py → repo root → submodules/KB/
    return here.parents[3] / "submodules" / "KnowledgeBase"


def run_pipeline(
    *,
    corpus_dir: Path | None = None,
    artifact_dir: Path | None = None,
    lease_lost: threading.Event | None = None,
) -> dict:
    """Run the full KB pipeline. Returns a manifest dict.

    The actual model-call code is gated behind ``KB_PIPELINE_ENABLED`` until
    the dedicated container with SPECTER2/REBEL/SciSpacy weights is built
    out. Without the flag we still write an empty manifest so the runner's
    "have we ever run?" check has a stable target.

    ``lease_lost`` (#1046 review, exactly-once #1043): a ``threading.Event``
    the caller's independent renewal thread sets the instant it can no
    longer prove this process still holds the runner lease (CAS loss, or an
    error that leaves renewal unconfirmed). When set, this function still
    computes and returns a manifest dict (so the caller's return value stays
    informative) but REFUSES TO WRITE manifest.json — a run whose lease
    already lapsed must not clobber the authoritative instance's manifest
    with a lost-update race, since another kb_runner copy may already be
    running its own pipeline under a fresh lease.

    This is a guard-the-write strategy rather than a fully cooperative
    mid-pipeline abort because today's real pipeline invocation below is
    still a skeleton (raises ``NotImplementedError``) with exactly one
    finalization step — the manifest write. It IS checked as an early
    cooperative-abort checkpoint before that (currently unimplemented, ~6GB
    of model weights) stage begins, so once the real SPECTER2/HDBSCAN/REBEL
    stages are wired in, prefer adding further ``lease_lost`` checks at their
    natural checkpoints rather than relying solely on this final guard.
    """
    corpus_dir = corpus_dir or DEFAULT_CORPUS_DIR
    artifact_dir = artifact_dir or DEFAULT_ARTIFACT_DIR
    artifact_dir.mkdir(parents=True, exist_ok=True)

    time.time()
    started_iso = datetime.now(UTC).isoformat()
    logger.info("kb_pipeline: starting (corpus=%s, artifact=%s)", corpus_dir, artifact_dir)

    if not os.getenv("KB_PIPELINE_ENABLED"):
        manifest = {
            "run_ts": started_iso,
            "duration_s": 0.0,
            "paper_count": 0,
            "cluster_count": 0,
            "kg_node_count": 0,
            "kg_edge_count": 0,
            "status": "skipped — set KB_PIPELINE_ENABLED=1 to invoke the full pipeline",
        }
        if lease_lost is not None and lease_lost.is_set():
            logger.error(
                "kb_pipeline: lease lost during this run — refusing to write manifest.json "
                "(another kb_runner copy may already hold a fresh lease and be running its "
                "own pipeline; writing now would risk a lost-update race on the authoritative "
                "manifest)"
            )
            return manifest
        (artifact_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
        logger.info("kb_pipeline: skipped (KB_PIPELINE_ENABLED not set)")
        return manifest

    if lease_lost is not None and lease_lost.is_set():
        logger.error(
            "kb_pipeline: lease already lost before the real pipeline stage started — "
            "aborting without running the multi-GB model pipeline or writing a manifest"
        )
        return {
            "run_ts": started_iso,
            "duration_s": 0.0,
            "paper_count": 0,
            "cluster_count": 0,
            "kg_node_count": 0,
            "kg_edge_count": 0,
            "status": "aborted — lease lost before pipeline start",
        }

    # Real pipeline path — gated by feature flag (#1090)
    kb_root = _kb_submodule_path()
    if str(kb_root) not in sys.path and kb_root.exists():
        sys.path.insert(0, str(kb_root))

    t0 = time.time()
    try:
        from papers_analysis import cluster, graph, knowledge_graph, vectorize  # type: ignore

        logger.info("kb_pipeline: running SPECTER2 vectorization on %s", corpus_dir)
        embeddings, ids = vectorize.embed_corpus(str(corpus_dir))
        cluster_map = cluster.cluster_embeddings(embeddings, ids)
        topic_map = cluster.bertopic_labels(embeddings, ids, cluster_map)
        triples = knowledge_graph.extract_triples(str(corpus_dir))
        kg_data = graph.aggregate(triples)

        # Write to staging directory before atomic swap
        staging_dir = artifact_dir.parent / f".tmp_{artifact_dir.name}_{int(t0)}"
        staging_dir.mkdir(parents=True, exist_ok=True)

        (staging_dir / "ids.json").write_text(json.dumps(ids))
        (staging_dir / "clusters.json").write_text(json.dumps(cluster_map))
        (staging_dir / "topics.json").write_text(json.dumps(topic_map))
        (staging_dir / "kg_graph.json").write_text(json.dumps(kg_data))

        import numpy as np
        np.save(str(staging_dir / "embeddings.npy"), embeddings)

        manifest = {
            "run_ts": started_iso,
            "duration_s": round(time.time() - t0, 2),
            "paper_count": len(ids),
            "cluster_count": len(set(cluster_map.values())),
            "kg_node_count": len(kg_data.get("nodes", [])),
            "kg_edge_count": len(kg_data.get("edges", [])),
            "status": "complete",
        }
        (staging_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

        # Check lease before atomic swap (#1043)
        if lease_lost is not None and lease_lost.is_set():
            logger.error("kb_pipeline: lease lost before atomic swap — cleaning staging dir and aborting swap")
            import shutil
            shutil.rmtree(staging_dir, ignore_errors=True)
            return manifest

        # Atomic directory swap
        import shutil
        if artifact_dir.exists():
            backup_dir = artifact_dir.with_name(artifact_dir.name + "_backup")
            shutil.rmtree(backup_dir, ignore_errors=True)
            artifact_dir.rename(backup_dir)
            try:
                staging_dir.rename(artifact_dir)
            except Exception:
                backup_dir.rename(artifact_dir)
                raise
            shutil.rmtree(backup_dir, ignore_errors=True)
        else:
            staging_dir.rename(artifact_dir)
        logger.info("kb_pipeline: successfully completed pipeline run (%d papers)", len(ids))
        return manifest

    except Exception as exc:
        logger.warning("kb_pipeline submodule execution degraded: %s — falling back to metadata manifest", exc)
        duration_s = round(time.time() - t0, 2)
        manifest = {
            "run_ts": started_iso,
            "duration_s": duration_s,
            "paper_count": 0,
            "cluster_count": 0,
            "kg_node_count": 0,
            "kg_edge_count": 0,
            "status": f"degraded — {exc}",
        }
        if lease_lost is not None and lease_lost.is_set():
            return manifest
        (artifact_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
        return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_DIR)
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT_DIR)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    manifest = run_pipeline(corpus_dir=args.corpus, artifact_dir=args.artifact)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
