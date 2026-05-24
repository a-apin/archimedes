"""KB artifact loader — reads S3-backed (or local volume) pipeline outputs.

Loads pre-computed artifacts from the KB pipeline (SPECTER2 embeddings,
HDBSCAN clusters, REBEL/SciSpacy KG graph) produced by
``scripts/run_kb_pipeline.py``.

Strategy:
  1. Try S3 first (bucket + prefix from env vars).
  2. Fall back to local artifact dir (docker volume).
  3. In-memory cache with configurable TTL (default 1 hour).
  4. No fake fallbacks — if no artifact exists, callers get a clear 503.

Environment variables:
  KB_S3_BUCKET        — S3 bucket name (e.g. ``archimedes-kb``)
  KB_S3_PREFIX        — Key prefix (default ``artifacts/``)
  KB_ARTIFACT_DIR     — Local fallback dir (default ``/srv/corpus-artifact``)
  KB_CACHE_TTL        — Cache TTL in seconds (default ``3600``)
  AWS_REGION          — AWS region (default ``us-east-1``)
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_S3_BUCKET = os.getenv("KB_S3_BUCKET", "")
_S3_PREFIX = os.getenv("KB_S3_PREFIX", "artifacts/")
_ARTIFACT_DIR = Path(os.getenv("KB_ARTIFACT_DIR", "/srv/corpus-artifact"))
_CACHE_TTL = int(os.getenv("KB_CACHE_TTL", "3600"))
_AWS_REGION = os.getenv("AWS_REGION", "us-east-1")


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_cache: dict[str, tuple[float, Any]] = {}


def _cache_get(key: str) -> Any | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.monotonic() - ts > _CACHE_TTL:
        del _cache[key]
        return None
    return value


def _cache_set(key: str, value: Any) -> None:
    _cache[key] = (time.monotonic(), value)


def invalidate_cache() -> int:
    """Clear the entire artifact cache. Returns number of entries cleared."""
    n = len(_cache)
    _cache.clear()
    return n


# ---------------------------------------------------------------------------
# S3 client (lazy)
# ---------------------------------------------------------------------------

_s3_client = None


def _get_s3_client():
    """Return a boto3 S3 client, or None if boto3 / credentials unavailable."""
    global _s3_client
    if _s3_client is not None:
        return _s3_client
    if not _S3_BUCKET:
        return None
    try:
        import boto3

        _s3_client = boto3.client("s3", region_name=_AWS_REGION)
        # Quick check: can we reach the bucket?
        _s3_client.head_bucket(Bucket=_S3_BUCKET)
        return _s3_client
    except Exception as exc:
        logger.info("kb_artifacts: S3 not available (%s), using local fallback", exc)
        _s3_client = None
        return None


# ---------------------------------------------------------------------------
# Artifact readers
# ---------------------------------------------------------------------------


def _read_bytes(key: str) -> bytes | None:
    """Read artifact bytes from S3 or local dir. Returns None if not found."""
    # Try S3 first
    s3 = _get_s3_client()
    if s3 is not None:
        try:
            resp = s3.get_object(Bucket=_S3_BUCKET, Key=_S3_PREFIX + key)
            return resp["Body"].read()
        except Exception as exc:
            logger.debug("kb_artifacts: S3 key %s not found (%s)", key, exc)

    # Local fallback
    local_path = _ARTIFACT_DIR / key
    if local_path.exists():
        try:
            return local_path.read_bytes()
        except OSError as exc:
            logger.warning("kb_artifacts: local read failed for %s: %s", key, exc)

    return None


def _read_json(key: str) -> dict | list | None:
    """Read and parse a JSON artifact."""
    raw = _read_bytes(key)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("kb_artifacts: JSON parse failed for %s: %s", key, exc)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class ArtifactNotFound(Exception):
    """Raised when a required KB artifact is not available."""


def load_manifest() -> dict:
    """Load the pipeline manifest."""
    cached = _cache_get("manifest")
    if cached is not None:
        return cached
    data = _read_json("manifest.json")
    if data is None:
        raise ArtifactNotFound("manifest.json not found in S3 or local artifact dir")
    _cache_set("manifest", data)
    return data


def load_embeddings() -> tuple[list[str], Any]:
    """Load embeddings.npy + ids.json.

    Returns (ids, embeddings_array). Uses numpy if available.
    """
    cached = _cache_get("embeddings")
    if cached is not None:
        return cached

    ids_raw = _read_bytes("ids.json")
    emb_raw = _read_bytes("embeddings.npy")

    if ids_raw is None or emb_raw is None:
        raise ArtifactNotFound("embeddings.npy / ids.json not found")

    ids = json.loads(ids_raw)

    # Try numpy for efficient loading
    try:
        import io

        import numpy as np

        embeddings = np.load(io.BytesIO(emb_raw), allow_pickle=False)
    except ImportError:
        # Without numpy, embeddings are opaque — caller must handle
        embeddings = emb_raw

    result = (ids, embeddings)
    _cache_set("embeddings", result)
    return result


def load_clusters() -> dict:
    """Load clusters.json — {arxiv_id: cluster_id}."""
    cached = _cache_get("clusters")
    if cached is not None:
        return cached
    data = _read_json("clusters.json")
    if data is None:
        raise ArtifactNotFound("clusters.json not found")
    _cache_set("clusters", data)
    return data


def load_topics() -> dict:
    """Load topics.json — {cluster_id: {label, top_terms: [...]}}."""
    cached = _cache_get("topics")
    if cached is not None:
        return cached
    data = _read_json("topics.json")
    if data is None:
        raise ArtifactNotFound("topics.json not found")
    _cache_set("topics", data)
    return data


def load_kg_graph() -> dict:
    """Load kg_graph.json — {nodes: [...], edges: [...]}."""
    cached = _cache_get("kg_graph")
    if cached is not None:
        return cached
    data = _read_json("kg_graph.json")
    if data is None:
        raise ArtifactNotFound("kg_graph.json not found")
    _cache_set("kg_graph", data)
    return data


def load_umap_projection() -> list[dict]:
    """Load pre-computed UMAP projection if available.

    If ``umap_projection.json`` exists (pre-computed by the pipeline),
    load it. Otherwise return None so the caller knows to compute it
    on the fly.

    Returns list of {arxiv_id, x, y, cluster_id} dicts.
    """
    cached = _cache_get("umap_projection")
    if cached is not None:
        return cached
    data = _read_json("umap_projection.json")
    if data is None:
        return None
    _cache_set("umap_projection", data)
    return data


def compute_and_cache_umap_projection(
    ids: list[str],
    embeddings,
    clusters: dict[str, str] | None = None,
) -> list[dict]:
    """Compute 2D projection from embeddings and cache it.

    Tries in order: UMAP → PCA (sklearn) → randomized projection (numpy only).
    The last fallback requires *only* numpy and always works.

    Parameters
    ----------
    ids : list[str]
        Paper arxiv_ids parallel to embeddings rows.
    embeddings : np.ndarray
        N×768 embedding matrix.
    clusters : dict, optional
        {arxiv_id: cluster_id} mapping.

    Returns
    -------
    list[dict]
        [{arxiv_id, x, y, cluster_id}, ...]
    """
    import numpy as np

    n, d = embeddings.shape

    # Try UMAP (best quality, requires umap-learn)
    try:
        from umap import UMAP

        reducer = UMAP(n_components=2, n_neighbors=min(15, n - 1), min_dist=0.1, random_state=42)
        coords = reducer.fit_transform(embeddings)
    except ImportError:
        pass
    else:
        coords = _finalize_projection(ids, coords, clusters)
        _cache_set("umap_projection", coords)
        return coords

    # Try PCA (good quality, requires scikit-learn)
    try:
        from sklearn.decomposition import PCA

        reducer = PCA(n_components=2, random_state=42)
        coords = reducer.fit_transform(embeddings)
    except ImportError:
        pass
    else:
        logger.info("kb_artifacts: using PCA (scikit-learn) for 2D projection")
        coords = _finalize_projection(ids, coords, clusters)
        _cache_set("umap_projection", coords)
        return coords

    # Pure-numpy fallback: randomized projection (Gaussian)
    logger.info("kb_artifacts: using random projection (numpy-only) for 2D projection")
    rng = np.random.RandomState(42)
    proj = rng.randn(d, 2).astype(embeddings.dtype)
    proj /= np.linalg.norm(proj, axis=0, keepdims=True)  # normalize columns
    coords = embeddings @ proj
    coords = _finalize_projection(ids, coords, clusters)
    _cache_set("umap_projection", coords)
    return coords


def _finalize_projection(
    ids: list[str],
    coords,
    clusters: dict[str, str] | None,
) -> list[dict]:
    """Convert raw 2D coordinates to list of point dicts."""
    points = []
    for i, arxiv_id in enumerate(ids):
        points.append(
            {
                "arxiv_id": arxiv_id,
                "x": float(coords[i, 0]),
                "y": float(coords[i, 1]),
                "cluster_id": clusters.get(arxiv_id) if clusters else None,
            }
        )
    return points
