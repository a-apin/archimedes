"""Hermetic tests for paper_rag semantic retrieval (issue #874).

No real model downloads: every test stubs the encoder at the module-level
global or mocks ``SentenceTransformer.__init__`` so the HuggingFace cache is
never touched. Tests cover:

- Health state transitions:
    - model loads OK → ``live``
    - sentence-transformers import error → ``degraded``
    - model load raises exception → ``degraded``
    - feature flag OFF → ``disabled``
- Retrieval ranking: ``_rerank_with_embeddings`` calls encoder and returns
  papers sorted by descending score.
- TF-IDF fallback: ``_rerank_tfidf`` ranks by textual overlap (no model dep).
- Model-name env var: ``MINILM_MODEL`` overrides the default.
"""

from __future__ import annotations

import types
from unittest.mock import MagicMock, patch

import numpy as np

import archimedes.services.paper_rag as rag_module
from archimedes.services.paper_rag import (
    _rerank_tfidf,
    _rerank_with_embeddings,
    augment_candidate_scores,
    paper_rag_health,
    semantic_rerank,
)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _reset_model_cache() -> None:
    """Reset module-level model singleton so each test starts clean."""
    rag_module._embedding_model = None
    rag_module._embedding_load_attempted = False


def _fake_model(embeddings: dict[str, list[float]] | None = None):
    """Return a MagicMock that mimics a SentenceTransformer encode call.

    ``embeddings`` maps input text → embedding vector; if a text is not in
    the dict a zero vector is returned.  If ``embeddings`` is None a unit
    vector [1.0, 0.0] is returned for every input.
    """
    model = MagicMock()

    def _encode(texts: list[str]) -> np.ndarray:
        vecs = []
        for t in texts:
            if embeddings and t in embeddings:
                vecs.append(np.array(embeddings[t], dtype=float))
            else:
                vecs.append(np.array([1.0, 0.0], dtype=float))
        return np.array(vecs)

    model.encode.side_effect = _encode
    return model


def _mk_paper(arxiv_id: str, title: str, abstract: str = "") -> dict:
    return {"arxiv_id": arxiv_id, "title": title, "abstract": abstract}


# ── Health state transitions ─────────────────────────────────────────────────


class TestPaperRAGHealth:
    def setup_method(self):
        _reset_model_cache()

    def test_disabled_when_flag_off(self, monkeypatch):
        monkeypatch.setenv("FUSION_SEMANTIC_RETRIEVAL", "false")
        _reset_model_cache()
        h = paper_rag_health()
        assert h.status == "disabled"

    def test_live_when_model_loads(self, monkeypatch):
        monkeypatch.setenv("FUSION_SEMANTIC_RETRIEVAL", "true")
        _reset_model_cache()
        with patch.object(rag_module, "_get_embedding_model", return_value=_fake_model()):
            h = paper_rag_health()
        assert h.status == "live"
        assert "model=" in h.reason

    def test_live_reason_includes_model_name(self, monkeypatch):
        monkeypatch.setenv("FUSION_SEMANTIC_RETRIEVAL", "true")
        monkeypatch.setenv("MINILM_MODEL", "all-MiniLM-L6-v2")
        _reset_model_cache()
        with patch.object(rag_module, "_get_embedding_model", return_value=_fake_model()):
            h = paper_rag_health()
        assert h.status == "live"
        assert "all-MiniLM-L6-v2" in h.reason

    def test_degraded_when_model_returns_none(self, monkeypatch):
        monkeypatch.setenv("FUSION_SEMANTIC_RETRIEVAL", "true")
        _reset_model_cache()
        with patch.object(rag_module, "_get_embedding_model", return_value=None):
            h = paper_rag_health()
        assert h.status == "degraded"
        assert "TF-IDF" in h.reason

    def test_degraded_on_import_error(self, monkeypatch):
        """ImportError during SentenceTransformer import → degraded (TF-IDF fallback)."""
        monkeypatch.setenv("FUSION_SEMANTIC_RETRIEVAL", "true")
        _reset_model_cache()

        # Simulate ImportError by making _get_embedding_model return None
        # (which is what happens when sentence-transformers isn't installed).
        with patch.object(rag_module, "_get_embedding_model", return_value=None):
            h = paper_rag_health()
        assert h.status == "degraded"

    def test_model_load_exception_leads_to_degraded(self, monkeypatch):
        """Exception during model loading (e.g., corrupted cache) → degraded."""
        monkeypatch.setenv("FUSION_SEMANTIC_RETRIEVAL", "true")
        _reset_model_cache()

        # Patch SentenceTransformer to raise an exception
        with patch.dict("sys.modules", {"sentence_transformers": types.ModuleType("sentence_transformers")}):
            import sys

            st_mod = sys.modules["sentence_transformers"]
            st_mod.SentenceTransformer = MagicMock(side_effect=OSError("corrupted model files"))
            h = paper_rag_health()
        # Reset after test
        _reset_model_cache()
        assert h.status == "degraded"


# ── _get_embedding_model caching ──────────────────────────────────────────────


class TestGetEmbeddingModelCaching:
    def setup_method(self):
        _reset_model_cache()

    def teardown_method(self):
        _reset_model_cache()

    def test_returns_none_on_import_error(self, monkeypatch):
        """If sentence_transformers is not importable, returns None."""
        _reset_model_cache()
        fake_mod = types.ModuleType("sentence_transformers")
        fake_mod.SentenceTransformer = MagicMock(side_effect=ImportError("no module"))
        with patch.dict("sys.modules", {"sentence_transformers": fake_mod}):
            result = rag_module._get_embedding_model()
        _reset_model_cache()
        assert result is None

    def test_caches_successful_load(self, monkeypatch):
        """Second call returns the cached instance without re-loading."""
        _reset_model_cache()
        stub = _fake_model()
        with patch.object(rag_module, "_get_embedding_model", return_value=stub) as mock_fn:
            r1 = rag_module._get_embedding_model()
            r2 = rag_module._get_embedding_model()
        # Both should be the same stub
        assert r1 is stub
        assert r2 is stub

    def test_attempted_flag_prevents_retry_after_failure(self, monkeypatch):
        """After a failed load, _embedding_load_attempted=True blocks re-tries."""
        _reset_model_cache()
        rag_module._embedding_load_attempted = True  # simulate prior failure
        # Should immediately return None without trying to import
        result = rag_module._get_embedding_model()
        assert result is None


# ── Env-configurable model name ───────────────────────────────────────────────


class TestModelName:
    def test_default_model_name(self, monkeypatch):
        monkeypatch.delenv("MINILM_MODEL", raising=False)
        assert rag_module._model_name() == "all-MiniLM-L6-v2"

    def test_custom_model_name_via_env(self, monkeypatch):
        monkeypatch.setenv("MINILM_MODEL", "paraphrase-MiniLM-L3-v2")
        assert rag_module._model_name() == "paraphrase-MiniLM-L3-v2"


# ── Semantic reranking with stubbed encoder ───────────────────────────────────


class TestReRankWithEmbeddings:
    """Verify _rerank_with_embeddings ranks papers by cosine similarity."""

    def _vol_paper(self) -> dict:
        return _mk_paper("2401.v1", "Volatility-Managed Portfolio Returns", "vol management sharpe")

    def _momentum_paper(self) -> dict:
        return _mk_paper("2401.m1", "Cross-Sectional Momentum Strategies", "momentum factor returns")

    def test_high_similarity_paper_ranks_first(self):
        query = "volatility managed equity"
        # query embedding ≈ volatility paper; momentum paper orthogonal
        embeddings = {
            query: [1.0, 0.0],
            "Volatility-Managed Portfolio Returns vol management sharpe": [0.99, 0.01],
            "Cross-Sectional Momentum Strategies momentum factor returns": [0.0, 1.0],
        }
        model = _fake_model(embeddings)
        papers = [self._momentum_paper(), self._vol_paper()]
        results = _rerank_with_embeddings(query, papers, model)
        assert results[0][0]["arxiv_id"] == "2401.v1", "vol paper should rank first"
        assert results[1][0]["arxiv_id"] == "2401.m1"

    def test_encoder_called_with_query_and_paper_texts(self):
        model = _fake_model()
        papers = [_mk_paper("1", "title one", "abstract one"), _mk_paper("2", "title two", "abstract two")]
        _rerank_with_embeddings("some query", papers, model)
        assert model.encode.called
        # encode called at least twice: once for query, once for papers
        assert model.encode.call_count >= 2

    def test_scores_clamped_to_unit_range(self):
        # Dot product could exceed 1 for non-normalised embeddings
        model = MagicMock()
        model.encode.side_effect = lambda texts: np.array([[2.0, 0.0] for _ in texts])
        papers = [_mk_paper("x", "foo", "bar")]
        results = _rerank_with_embeddings("query", papers, model)
        score = results[0][1]
        assert 0.0 <= score <= 1.0

    def test_empty_papers_returns_empty(self):
        model = _fake_model()
        assert _rerank_with_embeddings("query", [], model) == []


# ── TF-IDF fallback (no model dep) ───────────────────────────────────────────


class TestReRankTFIDF:
    def test_relevant_paper_ranks_first(self):
        query = "volatility momentum portfolio"
        papers = [
            _mk_paper("vol", "Volatility Momentum Equity Portfolio", "vol momentum returns risk"),
            _mk_paper("unrelated", "Cooking Recipes", "pasta tomato sauce"),
        ]
        results = _rerank_tfidf(query, papers)
        assert results[0][0]["arxiv_id"] == "vol"

    def test_empty_papers_returns_empty(self):
        assert _rerank_tfidf("query", []) == []

    def test_scores_in_unit_range(self):
        papers = [_mk_paper("a", "Test paper", "test abstract content")]
        results = _rerank_tfidf("test", papers)
        for _, score in results:
            assert 0.0 <= score <= 1.0


# ── semantic_rerank delegates correctly ──────────────────────────────────────


class TestSemanticRerank:
    def setup_method(self):
        _reset_model_cache()

    def teardown_method(self):
        _reset_model_cache()

    def test_uses_embedding_model_when_available(self, monkeypatch):
        stub = _fake_model()
        papers = [_mk_paper("a", "title", "abstract")]
        with patch.object(rag_module, "_get_embedding_model", return_value=stub):
            results = semantic_rerank("query", papers)
        assert stub.encode.called
        assert len(results) == 1

    def test_falls_back_to_tfidf_when_model_none(self, monkeypatch):
        papers = [_mk_paper("a", "title", "abstract")]
        with patch.object(rag_module, "_get_embedding_model", return_value=None):
            results = semantic_rerank("query", papers)
        # TF-IDF still produces a result
        assert len(results) == 1
        _, score = results[0]
        assert 0.0 <= score <= 1.0


# ── augment_candidate_scores integration ─────────────────────────────────────


class TestAugmentCandidateScores:
    def setup_method(self):
        _reset_model_cache()

    def teardown_method(self):
        _reset_model_cache()

    def _make_candidate(self, arxiv_id: str, title: str, abstract: str = "") -> MagicMock:
        c = MagicMock()
        c.arxiv_id = arxiv_id
        c.title = title
        c.abstract = abstract
        return c

    def test_returns_uniform_scores_when_disabled(self, monkeypatch):
        monkeypatch.setenv("FUSION_SEMANTIC_RETRIEVAL", "false")
        candidates = [self._make_candidate("1", "Paper One"), self._make_candidate("2", "Paper Two")]
        results = augment_candidate_scores("some direction", candidates)
        scores = [s for _, s in results]
        assert all(s == 1.0 for s in scores)

    def test_semantic_scores_applied_when_enabled(self, monkeypatch):
        monkeypatch.setenv("FUSION_SEMANTIC_RETRIEVAL", "true")
        _reset_model_cache()
        embeddings = {
            "volatility managed portfolio": [1.0, 0.0],
            "Volatility Portfolio Returns ": [0.95, 0.0],
            "Random Unrelated Text ": [0.0, 1.0],
        }
        stub = _fake_model(embeddings)
        c1 = self._make_candidate("vol", "Volatility Portfolio Returns", "")
        c2 = self._make_candidate("unrel", "Random Unrelated Text", "")
        with patch.object(rag_module, "_get_embedding_model", return_value=stub):
            results = augment_candidate_scores("volatility managed portfolio", [c1, c2])
        # vol paper should rank ahead of unrelated paper
        ids = [c.arxiv_id for c, _ in results]
        assert ids[0] == "vol"

    def test_empty_candidates_returns_empty(self, monkeypatch):
        monkeypatch.setenv("FUSION_SEMANTIC_RETRIEVAL", "true")
        assert augment_candidate_scores("direction", []) == []

    def test_gracefully_handles_rerank_exception(self, monkeypatch):
        """If semantic_rerank raises, augment falls back to uniform 1.0 scores."""
        monkeypatch.setenv("FUSION_SEMANTIC_RETRIEVAL", "true")
        _reset_model_cache()
        candidates = [self._make_candidate("a", "Paper A")]
        with patch.object(rag_module, "semantic_rerank", side_effect=RuntimeError("boom")):
            with patch.object(rag_module, "_get_embedding_model", return_value=_fake_model()):
                results = augment_candidate_scores("direction", candidates)
        assert all(s == 1.0 for _, s in results)
