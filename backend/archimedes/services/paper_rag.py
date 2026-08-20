"""Paper RAG — defense-in-depth semantic reranker for fusion candidate selection.

Wraps paper-qa (Apache 2.0, sentence-transformer embeddings) behind the
existing ``select_candidates()`` seam in ``strategy_fusion.py``. Runs as a
SECOND pass after the keyword filter — defense-in-depth beats either alone.

Architecture:
  1. Keyword filter (existing): selects candidates via asset-class overlap +
     strategic-direction keyword hits.
  2. Semantic rerank (this module): re-scores the keyword-selected candidates
     using embedding similarity between the user's strategic_direction and the
     paper title + abstract. Optionally uses paper-qa's QA engine for deeper
     relevance verification.

Scoring:
  - Without paper-qa: pure embedding cosine similarity (TF-IDF fallback when
    sentence-transformers unavailable).
  - With paper-qa: ``0.6 * embedding_sim + 0.4 * qa_relevance``.

Feature flag:
  ``FUSION_SEMANTIC_RETRIEVAL=true`` (default ON in production). When OFF or
  when dependencies are missing, silently falls back to keyword-only ranking.

Health surface:
  ``/health`` reports ``paper_rag: live | degraded | disabled`` so silent
  failure is impossible.

References:
  - ``submodules/Linus/src/linus/knowledge/`` — reference pattern
  - Issue #158 — spec + acceptance criteria
  - ``strategy_fusion.py::select_candidates()`` — the integration seam
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}

# Weights for dual scoring: embedding similarity + QA relevance.
_EMBEDDING_WEIGHT = 0.6
_QA_WEIGHT = 0.4

# Default model — override via MINILM_MODEL env var if needed.
_DEFAULT_MODEL = "all-MiniLM-L6-v2"


def _model_name() -> str:
    """Return the sentence-transformers model identifier (env-configurable)."""
    return os.getenv("MINILM_MODEL", _DEFAULT_MODEL)


# ── Health states ────────────────────────────────────────────────


@dataclass(frozen=True)
class PaperRAGHealth:
    """Health diagnostic for the paper RAG subsystem."""

    status: str  # live | ready | degraded | disabled
    reason: str = ""


def paper_rag_health(probe: bool = False) -> PaperRAGHealth:
    """Report the current health of the paper RAG subsystem.

    - ``live``: semantic retrieval enabled and the model is loaded in THIS
      process right now.
    - ``ready``: enabled and the weights are present on disk, but nothing has
      needed the model yet so it has not been loaded. Distinct from ``live``
      on purpose — this reports what is true, not what is likely.
    - ``degraded``: enabled but a load was attempted and failed (TF-IDF fallback).
    - ``disabled``: ``FUSION_SEMANTIC_RETRIEVAL`` is off.

    ``probe`` controls whether this is allowed to LOAD the model to answer.
    Default ``False``, because this function is on the ``/health`` path that the
    ALB polls every 30s: loading ``all-MiniLM-L6-v2`` costs a measured **521 MB
    of RSS** (torch 207 MB + sentence_transformers 109 MB + the model itself
    ~205 MB), which more than doubled the API container and was a direct
    contributor to the 2026-08-19 OOM crash loop. A liveness probe must not
    allocate half a gigabyte to answer a question about itself.

    Pass ``probe=True`` (the dedicated ``/health/paper-rag`` endpoint does) to
    force the load and get a proven ``live``/``degraded`` verdict.
    """
    if not _semantic_enabled():
        return PaperRAGHealth(status="disabled", reason="FUSION_SEMANTIC_RETRIEVAL not set")

    # Already loaded (something used retrieval) — free to report, no allocation.
    if _embedding_model is not None:
        return PaperRAGHealth(status="live", reason=f"model={_model_name()}")

    # A load was tried and failed — that verdict is cached and also free.
    if _embedding_load_attempted:
        return PaperRAGHealth(status="degraded", reason="embedding model unavailable, TF-IDF fallback")

    if not probe:
        if _weights_present():
            return PaperRAGHealth(
                status="ready",
                reason=f"model={_model_name()} present, not loaded (no retrieval yet)",
            )
        return PaperRAGHealth(
            status="degraded",
            reason=f"model={_model_name()} weights not found in {os.getenv('HF_HOME', '<unset HF_HOME>')}",
        )

    model = _get_embedding_model()
    if model is not None:
        return PaperRAGHealth(status="live", reason=f"model={_model_name()}")
    return PaperRAGHealth(status="degraded", reason="embedding model unavailable, TF-IDF fallback")


def _weights_present() -> bool:
    """Cheap on-disk check that the baked model cache holds our model.

    The Docker image bakes the weights into ``HF_HOME`` at build time
    (backend/Dockerfile:70) and runs with ``HF_HUB_OFFLINE=1``, so "the
    directory for this model exists and is non-empty" is the strongest claim
    available without importing torch. It is deliberately NOT reported as
    ``live`` — presence on disk is not proof the model loads.
    """
    hf_home = os.getenv("HF_HOME")
    if not hf_home:
        return False
    slug = _model_name().replace("/", "--")
    root = Path(hf_home)
    try:
        for candidate in root.iterdir():
            if not candidate.is_dir():
                continue
            name = candidate.name
            if slug in name or name in (slug, f"models--sentence-transformers--{slug}"):
                return any(candidate.rglob("*.safetensors")) or any(candidate.rglob("*.bin"))
    except OSError:
        return False
    return False


def _semantic_enabled() -> bool:
    """Check the feature flag. Default ON for production."""
    val = os.getenv("FUSION_SEMANTIC_RETRIEVAL", "true").strip().lower()
    return val in _TRUTHY


# ── Embedding engine ─────────────────────────────────────────────

# Lazy-loaded embedding model (sentence-transformers).
_embedding_model: Any = None
# Sentinel: True once a load attempt has been made and failed, so repeated
# health checks don't retry indefinitely (the result is cached after first try).
_embedding_load_attempted: bool = False


def _get_embedding_model():
    """Lazy-load the sentence-transformers model (cached after first attempt).

    Returns the loaded model on success, or ``None`` if the import is missing
    or the model files cannot be found (preserves TF-IDF fallback path).
    ``HF_HOME`` controls the HuggingFace cache directory; the Docker image
    bakes the model into ``/app/model_cache`` at build time so no network
    access is needed at runtime.
    """
    global _embedding_model, _embedding_load_attempted
    if _embedding_model is not None:
        return _embedding_model
    if _embedding_load_attempted:
        return None
    _embedding_load_attempted = True
    try:
        from sentence_transformers import SentenceTransformer

        # CPU guardrail: torch defaults to ALL cores — on the 2-vCPU prod box
        # that pegs the machine during encodes and starves the uvicorn event
        # loop (2026-07-04 incident: streams dropped, reads timed out, jobs
        # starved). One torch thread keeps the app responsive; encodes are
        # slightly slower but bounded by the cache below.
        try:
            import torch

            torch.set_num_threads(1)
        except Exception as exc:
            # Log at WARNING so prod observability catches when the guardrail
            # is not applied — without it the 2026-07-04 starvation can recur.
            logger.warning("paper_rag: torch.set_num_threads(1) failed — CPU guardrail not applied: %s", exc)
        _embedding_model = SentenceTransformer(_model_name())
        logger.info("paper_rag: loaded embedding model %s", _model_name())
        return _embedding_model
    except ImportError:
        logger.debug("paper_rag: sentence-transformers not installed, using TF-IDF")
        return None
    except Exception as exc:
        logger.warning("paper_rag: embedding model load failed (%s), using TF-IDF: %s", _model_name(), exc)
        return None


# ── Paper-qa QA engine ───────────────────────────────────────────


def _paperqa_available() -> bool:
    """True if paper-qa is importable."""
    try:
        from paperqa import Docs  # noqa: F401

        return True
    except ImportError:
        return False


async def _paperqa_relevance(query: str, paper_text: str) -> float:
    """Score a paper's relevance to a query using paper-qa's QA engine.

    Returns a float in [0, 1] representing answer confidence. Falls back
    to 0.5 (neutral) if paper-qa is unavailable.
    """
    try:
        from paperqa import Docs

        docs = Docs()
        docs.add(paper_text, docname="candidate")
        result = await docs.aquery(query)
        if result and hasattr(result, "answer"):
            # paper-qa returns a confidence; extract or default to 0.5
            confidence = getattr(result, "score", None)
            if confidence is not None:
                return float(confidence)
            # If no score, use the length of a non-trivial answer as a proxy
            if result.answer and len(result.answer.strip()) > 20:
                return 0.7
        return 0.3
    except Exception as exc:
        logger.debug("paper_rag: paper-qa QA failed, neutral fallback: %s", exc)
        return 0.5


# ── TF-IDF fallback ─────────────────────────────────────────────


def _tokenize(text: str) -> list[str]:
    """Simple tokenizer: lowercase, extract alpha tokens of length >= 2."""
    return re.findall(r"[a-z]{2,}", text.lower())


def _tfidf_vector(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    """Compute TF-IDF vector from tokens and precomputed IDF."""
    tf = Counter(tokens)
    total = len(tokens) if tokens else 1
    return {term: (count / total) * idf.get(term, 1.0) for term, count in tf.items()}


def _cosine_sim(v1: dict[str, float], v2: dict[str, float]) -> float:
    """Cosine similarity between two sparse vectors (dicts)."""
    common = set(v1) & set(v2)
    if not common:
        return 0.0
    dot = sum(v1[t] * v2[t] for t in common)
    mag1 = math.sqrt(sum(v**2 for v in v1.values()))
    mag2 = math.sqrt(sum(v**2 for v in v2.values()))
    if mag1 == 0 or mag2 == 0:
        return 0.0
    return dot / (mag1 * mag2)


def _compute_idf(documents: list[list[str]]) -> dict[str, float]:
    """Compute IDF over a set of documents."""
    n = len(documents)
    if n == 0:
        return {}
    df: dict[str, int] = {}
    for doc in documents:
        seen = set(doc)
        for term in seen:
            df[term] = df.get(term, 0) + 1
    return {
        term: math.log((n + 1) / (count + 1)) + 1  # smoothed IDF
        for term, count in df.items()
    }


# ── Core: semantic rerank ───────────────────────────────────────


def semantic_rerank(
    query: str,
    papers: list[dict[str, Any]],
    *,
    use_paperqa: bool = False,  # noqa: ARG001 — opt-in toggle for the PaperQA backend; wiring is in flight, kept declared so callers can pass it
) -> list[tuple[dict[str, Any], float]]:
    """Rerank papers by semantic similarity to ``query``.

    Parameters
    ----------
    query : str
        The user's strategic direction / intent text.
    papers : list[dict]
        Papers to rerank. Each must have ``title`` and ``abstract`` keys.
    use_paperqa : bool
        If True, attempt paper-qa QA scoring as a second signal.

    Returns
    -------
    list[tuple[dict, float]]
        Papers sorted by descending semantic score (0.0–1.0).
    """
    if not papers:
        return []

    model = _get_embedding_model()

    if model is not None:
        return _rerank_with_embeddings(query, papers, model)
    return _rerank_tfidf(query, papers)


# Per-process paper-embedding cache. The debate pool makes ~10 rerank calls
# per generation over heavily-overlapping candidate sets; without this, every
# call re-encoded EVERY paper text on CPU (the 2026-07-04 incident). Keyed by
# the exact text; bounded FIFO so a long-lived process can't grow unbounded.
# select_candidates() is invoked via asyncio.to_thread(), i.e. real OS threads;
# protect all cache mutations with a threading.Lock.
_paper_emb_cache: dict[str, Any] = {}
_paper_emb_cache_lock = threading.Lock()
_PAPER_EMB_CACHE_MAX = 20_000

# Encode at most this many candidates per rerank; the keyword pre-filter's
# tail is noise anyway, and an unbounded steer must not melt the box.
_RERANK_MAX_TEXTS = 150


def _rerank_with_embeddings(
    query: str,
    papers: list[dict[str, Any]],
    model: Any,
) -> list[tuple[dict[str, Any], float]]:
    """Rerank using sentence-transformer embeddings (cached, capped)."""
    if len(papers) > _RERANK_MAX_TEXTS:
        papers = papers[:_RERANK_MAX_TEXTS]
    query_emb = model.encode([query])
    texts = [f"{p.get('title', '')} {p.get('abstract', '')}" for p in papers]

    # Score from a batch-local map: the shared cache is a bonus, never a
    # dependency — eviction must not be able to drop an entry the CURRENT
    # rerank still needs (caught by test_cache_is_bounded).
    # Hold the lock only for the dict reads/writes; model.encode() runs outside
    # the lock so other threads aren't blocked during the CPU-intensive encode.
    with _paper_emb_cache_lock:
        embs = {t: _paper_emb_cache[t] for t in texts if t in _paper_emb_cache}
    to_encode = [t for t in texts if t not in embs]
    if to_encode:
        fresh = model.encode(to_encode)
        with _paper_emb_cache_lock:
            for t, emb in zip(to_encode, fresh, strict=False):
                embs[t] = emb
                if len(_paper_emb_cache) >= _PAPER_EMB_CACHE_MAX:
                    _paper_emb_cache.pop(next(iter(_paper_emb_cache)))
                _paper_emb_cache[t] = emb

    results: list[tuple[dict[str, Any], float]] = []
    for i, paper in enumerate(papers):
        # Cosine similarity (sentence-transformers outputs are normalized)
        sim = float(query_emb[0] @ embs[texts[i]].T)
        # Clamp to [0, 1]
        score = max(0.0, min(1.0, sim))
        results.append((paper, score))

    results.sort(key=lambda x: x[1], reverse=True)
    return results


def _rerank_tfidf(
    query: str,
    papers: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], float]]:
    """Rerank using TF-IDF cosine similarity (fallback, no external deps)."""
    query_tokens = _tokenize(query)
    doc_tokens = [_tokenize(f"{p.get('title', '')} {p.get('abstract', '')}") for p in papers]

    # Build IDF from the paper corpus + query
    all_docs = [*doc_tokens, query_tokens]
    idf = _compute_idf(all_docs)

    query_vec = _tfidf_vector(query_tokens, idf)
    results: list[tuple[dict[str, Any], float]] = []
    for paper, tokens in zip(papers, doc_tokens, strict=False):
        doc_vec = _tfidf_vector(tokens, idf)
        sim = _cosine_sim(query_vec, doc_vec)
        # Normalize to [0, 1] range
        score = max(0.0, min(1.0, (sim + 1.0) / 2.0))
        results.append((paper, score))

    results.sort(key=lambda x: x[1], reverse=True)
    return results


# ── Integration seam: augment select_candidates ─────────────────


def augment_candidate_scores(
    brief_direction: str,
    candidates: list[Any],
) -> list[tuple[Any, float]]:
    """Compute semantic scores for fusion candidates.

    Called from ``select_candidates()`` after the keyword filter has
    produced the initial ranked list. Returns (candidate, score) tuples
    sorted by descending semantic relevance.

    When semantic retrieval is disabled or fails, returns uniform scores
    so the keyword ranking is preserved unchanged.
    """
    if not _semantic_enabled() or not candidates:
        return [(c, 1.0) for c in candidates]

    # Cap before the rerank call so that tail candidates beyond _RERANK_MAX_TEXTS
    # are never given the 0.5 default score (which could incorrectly promote them
    # above semantically-scored head candidates). Tail is appended at 0.0 so it
    # sorts below all scored head candidates while preserving keyword order.
    head = candidates[:_RERANK_MAX_TEXTS]
    tail = candidates[_RERANK_MAX_TEXTS:]

    # Convert CorpusPaper-like objects to dicts for the reranker
    paper_dicts = []
    for c in head:
        paper_dicts.append(
            {
                "arxiv_id": getattr(c, "arxiv_id", ""),
                "title": getattr(c, "title", ""),
                "abstract": getattr(c, "abstract", ""),
            }
        )

    try:
        scored = semantic_rerank(brief_direction, paper_dicts)
    except Exception as exc:
        logger.warning("paper_rag: semantic rerank failed, keyword-only fallback: %s", exc)
        return [(c, 1.0) for c in candidates]

    # Map scores back to the original objects by paper-dict OBJECT IDENTITY, not
    # by arxiv_id. Keying on arxiv_id let empty or duplicate ids collapse in the
    # dict — a real candidate then fell through to the 0.5 default, which can
    # outrank a genuinely-scored paper and reorder fusion pair selection (#938).
    # semantic_rerank returns the same dict objects it was handed, one per input,
    # so identity is a stable, collision-free key.
    score_by_dict_id = {id(s[0]): s[1] for s in scored}
    result = []
    for c, paper_dict in zip(head, paper_dicts, strict=True):
        aid = str(getattr(c, "arxiv_id", "") or "").strip()
        if not aid:
            # A candidate with no arxiv_id can't be provenance-tracked into a
            # fusion pair — score it 0.0 so it can't outrank a real paper (#938).
            result.append((c, 0.0))
            continue
        result.append((c, score_by_dict_id.get(id(paper_dict), 0.0)))
    # Tail candidates were not semantically scored; rank them below the head.
    for c in tail:
        result.append((c, 0.0))

    # Sort by descending semantic score
    result.sort(key=lambda x: x[1], reverse=True)
    return result
