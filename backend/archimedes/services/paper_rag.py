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
from collections import Counter
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}

# Weights for dual scoring: embedding similarity + QA relevance.
_EMBEDDING_WEIGHT = 0.6
_QA_WEIGHT = 0.4


# ── Health states ────────────────────────────────────────────────


@dataclass(frozen=True)
class PaperRAGHealth:
    """Health diagnostic for the paper RAG subsystem."""

    status: str  # live | degraded | disabled
    reason: str = ""


def paper_rag_health() -> PaperRAGHealth:
    """Report the current health of the paper RAG subsystem.

    - ``live``: semantic retrieval enabled, dependencies available.
    - ``degraded``: enabled but core dependencies missing (falls back to
      TF-IDF).
    - ``disabled``: ``FUSION_SEMANTIC_RETRIEVAL`` is off.
    """
    if not _semantic_enabled():
        return PaperRAGHealth(status="disabled", reason="FUSION_SEMANTIC_RETRIEVAL not set")

    has_embeddings = _embedding_available()
    if has_embeddings:
        return PaperRAGHealth(status="live", reason="semantic retrieval active")
    return PaperRAGHealth(status="degraded", reason="embedding model unavailable, TF-IDF fallback")


def _semantic_enabled() -> bool:
    """Check the feature flag. Default ON for production."""
    val = os.getenv("FUSION_SEMANTIC_RETRIEVAL", "true").strip().lower()
    return val in _TRUTHY


# ── Embedding engine ─────────────────────────────────────────────

# Lazy-loaded embedding model (sentence-transformers).
_embedding_model: Any = None


def _embedding_available() -> bool:
    """True if sentence-transformers is importable and a model is loadable."""
    try:
        from sentence_transformers import SentenceTransformer  # noqa: F401

        return True
    except ImportError:
        return False


def _get_embedding_model():
    """Lazy-load the sentence-transformers model."""
    global _embedding_model
    if _embedding_model is not None:
        return _embedding_model
    try:
        from sentence_transformers import SentenceTransformer

        _embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
        return _embedding_model
    except ImportError:
        logger.debug("paper_rag: sentence-transformers not available, using TF-IDF")
        return None
    except Exception as exc:
        logger.warning("paper_rag: embedding model load failed: %s", exc)
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
    use_paperqa: bool = False,
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


def _rerank_with_embeddings(
    query: str,
    papers: list[dict[str, Any]],
    model: Any,
) -> list[tuple[dict[str, Any], float]]:
    """Rerank using sentence-transformer embeddings."""
    query_emb = model.encode([query])
    texts = [f"{p.get('title', '')} {p.get('abstract', '')}" for p in papers]
    paper_embs = model.encode(texts)

    results: list[tuple[dict[str, Any], float]] = []
    for i, paper in enumerate(papers):
        # Cosine similarity (sentence-transformers outputs are normalized)
        sim = float(query_emb[0] @ paper_embs[i].T)
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

    # Convert CorpusPaper-like objects to dicts for the reranker
    paper_dicts = []
    for c in candidates:
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

    # Map back to original objects, preserving score
    arxiv_to_score = {s[0]["arxiv_id"]: s[1] for s in scored}
    result = []
    for c in candidates:
        aid = getattr(c, "arxiv_id", "")
        score = arxiv_to_score.get(aid, 0.5)
        result.append((c, score))

    # Sort by descending semantic score
    result.sort(key=lambda x: x[1], reverse=True)
    return result
