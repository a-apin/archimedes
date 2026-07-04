"""CPU guardrails for MiniLM reranking (2026-07-04 prod incident).

Uncached per-call encoding of every candidate paper + torch grabbing both
vCPUs starved the event loop: streams dropped, reads timed out, generation
jobs died. These pin the guardrails: paper embeddings are cached per process,
rerank input is capped, and a repeat rerank encodes ONLY the query.
"""

from __future__ import annotations

import archimedes.services.paper_rag as pr
import pytest


class _CountingModel:
    def __init__(self):
        self.encode_calls: list[int] = []

    def encode(self, texts):
        import numpy as np

        self.encode_calls.append(len(texts))
        rng = np.random.default_rng(len(texts))
        out = rng.normal(size=(len(texts), 8))
        return out / np.linalg.norm(out, axis=1, keepdims=True)


@pytest.fixture(autouse=True)
def _clean_cache():
    pr._paper_emb_cache.clear()
    yield
    pr._paper_emb_cache.clear()


def _papers(n):
    return [{"title": f"Paper {i}", "abstract": f"abstract {i}", "arxiv_id": f"24{i:02d}.0"} for i in range(n)]


def test_repeat_rerank_encodes_only_the_query():
    model = _CountingModel()
    papers = _papers(10)
    pr._rerank_with_embeddings("momentum", papers, model)
    assert model.encode_calls == [1, 10]  # query + 10 cold papers
    pr._rerank_with_embeddings("volatility hedge", papers, model)
    # Second call: query only — every paper embedding served from cache.
    assert model.encode_calls == [1, 10, 1]


def test_rerank_input_is_capped():
    model = _CountingModel()
    papers = _papers(pr._RERANK_MAX_TEXTS + 50)
    out = pr._rerank_with_embeddings("q", papers, model)
    assert len(out) == pr._RERANK_MAX_TEXTS
    assert max(model.encode_calls) <= pr._RERANK_MAX_TEXTS


def test_cache_is_bounded():
    model = _CountingModel()
    pr._paper_emb_cache.clear()
    old_max = pr._PAPER_EMB_CACHE_MAX
    try:
        pr._PAPER_EMB_CACHE_MAX = 5
        pr._rerank_with_embeddings("q", _papers(10), model)
        assert len(pr._paper_emb_cache) <= pr._PAPER_EMB_CACHE_MAX  # eviction kept cache at configured max
    finally:
        pr._PAPER_EMB_CACHE_MAX = old_max
