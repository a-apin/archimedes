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


def run_pipeline(*, corpus_dir: Path | None = None, artifact_dir: Path | None = None) -> dict:
    """Run the full KB pipeline. Returns a manifest dict.

    The actual model-call code is gated behind ``KB_PIPELINE_ENABLED`` until
    the dedicated container with SPECTER2/REBEL/SciSpacy weights is built
    out. Without the flag we still write an empty manifest so the runner's
    "have we ever run?" check has a stable target.
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
        (artifact_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
        logger.info("kb_pipeline: skipped (KB_PIPELINE_ENABLED not set)")
        return manifest

    # Real pipeline path — gated by feature flag. Invocation pattern lives in
    # kb-integration-spec.md § "Pipeline invocation". Adding to sys.path so
    # the submodule's `papers_analysis` package is importable.
    kb_root = _kb_submodule_path()
    if str(kb_root) not in sys.path:
        sys.path.insert(0, str(kb_root))

    # The actual functions live in:
    #   papers_analysis.vectorize.embed_corpus(text_dir) → (np.ndarray, ids)
    #   papers_analysis.cluster.cluster_embeddings(...) → dict[id, cluster]
    #   papers_analysis.cluster.bertopic_labels(...)     → dict[cluster, label]
    #   papers_analysis.knowledge_graph.extract_triples(text_dir) → list
    #   papers_analysis.graph.aggregate(triples) → {nodes, edges}
    #
    # See spec for the canonical wiring. Left as TODO under flag because the
    # full implementation pulls in ~6 GB of model weights and only runs
    # meaningfully inside the dedicated docker service.
    raise NotImplementedError(
        "KB_PIPELINE_ENABLED set, but the full pipeline invocation is not yet wired. "
        "See docs/specs/kb-integration-spec.md § Pipeline invocation for the canonical shape. "
        "Until the dedicated docker container with SPECTER2/REBEL/SciSpacy weights ships, "
        "leave KB_PIPELINE_ENABLED unset and rely on the skip path."
    )


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
