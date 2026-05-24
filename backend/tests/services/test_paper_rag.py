"""Tests for paper_rag — semantic reranker for fusion candidate selection.

Covers:
- Feature flag gating (FUSION_SEMANTIC_RETRIEVAL)
- TF-IDF fallback when sentence-transformers unavailable
- Health endpoint (live / degraded / disabled)
- Integration with select_candidates() via augment_candidate_scores
- Anti-hallucination: scores don't introduce phantom candidates
- Graceful fallback when semantic retrieval fails
"""

from __future__ import annotations

from archimedes.services.paper_rag import (
    PaperRAGHealth,
    _compute_idf,
    _cosine_sim,
    _semantic_enabled,
    _tfidf_vector,
    _tokenize,
    augment_candidate_scores,
    paper_rag_health,
    semantic_rerank,
)

# ── Fixtures ─────────────────────────────────────────────────────


class FakePaper:
    """Minimal CorpusPaper-like object for testing."""

    def __init__(self, arxiv_id: str, title: str, abstract: str):
        self.arxiv_id = arxiv_id
        self.title = title
        self.abstract = abstract


PAPERS = [
    FakePaper(
        "2004.00001",
        "Momentum Strategies in Global Equity Markets",
        "We study cross-sectional momentum strategies across 40 global equity markets.",
    ),
    FakePaper(
        "2004.00002",
        "Volatility-Managed Portfolios",
        "We show that managing portfolio exposure to volatility improves risk-adjusted returns.",
    ),
    FakePaper(
        "2004.00003",
        "A Deep Reinforcement Learning Framework for Portfolio Optimization",
        "We propose a deep RL agent for dynamic asset allocation in cryptocurrency markets.",
    ),
    FakePaper(
        "2004.00004",
        "Trend-Following with Moving Averages",
        "Simple moving average crossover strategies deliver positive returns in trending markets.",
    ),
    FakePaper(
        "2004.00005",
        "Credit Risk Modeling with Machine Learning",
        "We apply gradient boosted trees to corporate credit default prediction.",
    ),
]


# ── Tokenizer + TF-IDF tests ────────────────────────────────────


class TestTokenizer:
    def test_basic(self):
        assert _tokenize("Hello World") == ["hello", "world"]

    def test_filters_short(self):
        assert _tokenize("A B CD") == ["cd"]

    def test_extracts_alpha_only(self):
        tokens = _tokenize("moving-average 200-day SMA")
        assert "moving" in tokens
        assert "average" in tokens
        assert "sma" in tokens
        assert "200" not in tokens  # digits excluded


class TestTFIDF:
    def test_idf_single_doc(self):
        idf = _compute_idf([["alpha", "beta", "gamma"]])
        assert "alpha" in idf
        assert idf["alpha"] > 0

    def test_idf_rare_terms_higher(self):
        docs = [["alpha", "beta"], ["alpha", "gamma"]]
        idf = _compute_idf(docs)
        # "beta" and "gamma" are rarer → higher IDF
        assert idf["beta"] > idf["alpha"]

    def test_cosine_sim_identical(self):
        v = {"a": 1.0, "b": 2.0}
        assert abs(_cosine_sim(v, v) - 1.0) < 0.01

    def test_cosine_sim_orthogonal(self):
        v1 = {"a": 1.0}
        v2 = {"b": 1.0}
        assert _cosine_sim(v1, v2) == 0.0

    def test_tfidf_vector(self):
        idf = {"alpha": 2.0, "beta": 1.0}
        vec = _tfidf_vector(["alpha", "alpha", "beta"], idf)
        assert vec["alpha"] > vec["beta"]  # higher TF * IDF


# ── Feature flag tests ──────────────────────────────────────────


class TestFeatureFlag:
    def test_default_on(self, monkeypatch):
        monkeypatch.delenv("FUSION_SEMANTIC_RETRIEVAL", raising=False)
        assert _semantic_enabled() is True

    def test_explicit_on(self, monkeypatch):
        monkeypatch.setenv("FUSION_SEMANTIC_RETRIEVAL", "true")
        assert _semantic_enabled() is True

    def test_explicit_off(self, monkeypatch):
        monkeypatch.setenv("FUSION_SEMANTIC_RETRIEVAL", "false")
        assert _semantic_enabled() is False

    def test_various_truthy(self, monkeypatch, val="1"):
        for v in ["1", "true", "yes", "on", "TRUE", "Yes"]:
            monkeypatch.setenv("FUSION_SEMANTIC_RETRIEVAL", v)
            assert _semantic_enabled() is True

    def test_various_falsy(self, monkeypatch):
        for v in ["0", "false", "no", "off", "", "random"]:
            monkeypatch.setenv("FUSION_SEMANTIC_RETRIEVAL", v)
            assert _semantic_enabled() is False


# ── Health tests ─────────────────────────────────────────────────


class TestHealth:
    def test_disabled_when_off(self, monkeypatch):
        monkeypatch.setenv("FUSION_SEMANTIC_RETRIEVAL", "false")
        h = paper_rag_health()
        assert h.status == "disabled"

    def test_live_or_degraded_when_on(self, monkeypatch):
        monkeypatch.setenv("FUSION_SEMANTIC_RETRIEVAL", "true")
        h = paper_rag_health()
        assert h.status in ("live", "degraded")

    def test_health_dataclass(self):
        h = PaperRAGHealth(status="live", reason="ok")
        assert h.status == "live"
        assert h.reason == "ok"


# ── Semantic rerank tests ────────────────────────────────────────


class TestSemanticRerank:
    def test_empty_input(self):
        assert semantic_rerank("momentum", []) == []

    def test_tfidf_rerank_orders_by_relevance(self):
        """Papers with 'momentum' in their text should rank higher for a
        'momentum' query."""
        papers = [
            {"arxiv_id": "a1", "title": "Credit Risk", "abstract": "default prediction"},
            {"arxiv_id": "a2", "title": "Momentum Strategies", "abstract": "cross-sectional momentum"},
        ]
        results = semantic_rerank("momentum strategies equity", papers)
        # Momentum paper should score higher than credit risk paper
        a1_score = next(s for p, s in results if p["arxiv_id"] == "a1")
        a2_score = next(s for p, s in results if p["arxiv_id"] == "a2")
        assert a2_score > a1_score

    def test_tfidf_rerank_returns_all_papers(self):
        papers = [{"arxiv_id": f"p{i}", "title": f"Paper {i}", "abstract": f"Abstract {i}"} for i in range(5)]
        results = semantic_rerank("test query", papers)
        assert len(results) == 5

    def test_scores_bounded(self):
        papers = [
            {"arxiv_id": "x", "title": "Alpha", "abstract": "Beta"},
        ]
        results = semantic_rerank("gamma delta", papers)
        for _p, score in results:
            assert 0.0 <= score <= 1.0


# ── Integration with select_candidates ──────────────────────────


class TestAugmentCandidateScores:
    def test_disabled_returns_uniform(self, monkeypatch):
        """When FUSION_SEMANTIC_RETRIEVAL=false, all scores are 1.0."""
        monkeypatch.setenv("FUSION_SEMANTIC_RETRIEVAL", "false")
        papers = PAPERS[:3]
        result = augment_candidate_scores("momentum", papers)
        assert len(result) == 3
        for _c, score in result:
            assert score == 1.0

    def test_enabled_returns_scored(self, monkeypatch):
        """When enabled, papers get differentiated scores."""
        monkeypatch.setenv("FUSION_SEMANTIC_RETRIEVAL", "true")
        papers = PAPERS[:3]
        result = augment_candidate_scores("momentum equity strategies", papers)
        assert len(result) == 3
        # At least one score should differ from another
        scores = [s for _c, s in result]
        assert len(set(f"{s:.4f}" for s in scores)) > 1 or len(papers) <= 1

    def test_preserves_all_candidates(self, monkeypatch):
        """No candidates are dropped — only reordered."""
        monkeypatch.setenv("FUSION_SEMANTIC_RETRIEVAL", "true")
        papers = PAPERS
        result = augment_candidate_scores("trend following", papers)
        result_ids = {c.arxiv_id for c, _s in result}
        original_ids = {p.arxiv_id for p in papers}
        assert result_ids == original_ids

    def test_empty_candidates(self, monkeypatch):
        monkeypatch.setenv("FUSION_SEMANTIC_RETRIEVAL", "true")
        result = augment_candidate_scores("test", [])
        assert result == []

    def test_momentum_query_ranks_momentum_first(self, monkeypatch):
        """A 'momentum' query should rank the momentum paper highest."""
        monkeypatch.setenv("FUSION_SEMANTIC_RETRIEVAL", "true")
        papers = PAPERS[:5]
        result = augment_candidate_scores("momentum strategies in equity markets", papers)
        # The momentum paper (2004.00001) should rank highest
        top_paper = result[0][0]
        assert "momentum" in top_paper.title.lower() or top_paper.arxiv_id == "2004.00001"


class TestSelectCandidatesIntegration:
    """Integration tests verifying select_candidates() uses paper_rag."""

    def test_keyword_ranking_preserved_when_disabled(self, monkeypatch):
        """When FUSION_SEMANTIC_RETRIEVAL=false, select_candidates is pure keyword."""
        monkeypatch.setenv("FUSION_SEMANTIC_RETRIEVAL", "false")
        monkeypatch.setenv("ARCHIMEDES_FUSION_ENABLED", "true")
        from archimedes.agents.strategy_fusion import (
            FusionBrief,
            select_candidates,
        )

        brief = FusionBrief(
            strategic_direction="momentum strategies",
            asset_classes=["equities"],
            max_papers=4,
        )

        # Build a corpus from FakePapers
        corpus = []
        for p in PAPERS:
            cp = type(
                "CP",
                (),
                {
                    "arxiv_id": p.arxiv_id,
                    "title": p.title,
                    "abstract": p.abstract,
                    "primary_category": "q-fin.pm",
                    "categories": ("q-fin.pm",),
                    "published": "2020-04-01",
                    "haystack": f"q-fin.pm {p.title} {p.abstract}".lower(),
                },
            )()
            corpus.append(cp)

        result = select_candidates(brief, corpus)
        # Should return papers (keyword filtered by "equit" asset term)
        assert len(result) >= 2  # at least momentum + trend-following

    def test_semantic_rerank_used_when_enabled(self, monkeypatch):
        """When FUSION_SEMANTIC_RETRIEVAL=true, select_candidates applies
        semantic rerank after keyword filter."""
        monkeypatch.setenv("FUSION_SEMANTIC_RETRIEVAL", "true")
        monkeypatch.setenv("ARCHIMEDES_FUSION_ENABLED", "true")
        from archimedes.agents.strategy_fusion import (
            FusionBrief,
            select_candidates,
        )

        brief = FusionBrief(
            strategic_direction="volatility managed portfolios with risk control",
            asset_classes=[],
            max_papers=3,
        )

        corpus = []
        for p in PAPERS:
            cp = type(
                "CP",
                (),
                {
                    "arxiv_id": p.arxiv_id,
                    "title": p.title,
                    "abstract": p.abstract,
                    "primary_category": "q-fin.pm",
                    "categories": ("q-fin.pm",),
                    "published": "2020-04-01",
                    "haystack": f"q-fin.pm {p.title} {p.abstract}".lower(),
                },
            )()
            corpus.append(cp)

        result = select_candidates(brief, corpus)
        assert len(result) >= 2
        # The volatility paper should be present in results
        ids = [p.arxiv_id for p in result]
        assert "2004.00002" in ids  # volatility-managed


# ── Anti-hallucination ──────────────────────────────────────────


class TestAntiHallucination:
    def test_no_phantom_candidates(self, monkeypatch):
        """augment_candidate_scores never introduces papers not in the input."""
        monkeypatch.setenv("FUSION_SEMANTIC_RETRIEVAL", "true")
        input_ids = {p.arxiv_id for p in PAPERS[:2]}
        result = augment_candidate_scores("test", PAPERS[:2])
        result_ids = {c.arxiv_id for c, _s in result}
        assert result_ids == input_ids

    def test_paper_count_preserved(self, monkeypatch):
        """Output count equals input count (no additions, no drops)."""
        monkeypatch.setenv("FUSION_SEMANTIC_RETRIEVAL", "true")
        for n in [1, 2, 3, 5]:
            result = augment_candidate_scores("test", PAPERS[:n])
            assert len(result) == n


# ── Graceful fallback ───────────────────────────────────────────


class TestGracefulFallback:
    def test_fallback_on_exception_in_rerank(self, monkeypatch):
        """If semantic_rerank raises, keyword ranking is preserved."""
        monkeypatch.setenv("FUSION_SEMANTIC_RETRIEVAL", "true")

        # Patch semantic_rerank to raise
        import archimedes.services.paper_rag as prg

        def boom(*args, **kwargs):
            raise RuntimeError("simulated failure")

        monkeypatch.setattr(prg, "semantic_rerank", boom)

        papers = PAPERS[:3]
        result = augment_candidate_scores("momentum", papers)
        # Falls back to uniform scores — keyword ranking unchanged
        assert len(result) == 3
        for _c, score in result:
            assert score == 1.0

    def test_paper_budget_floor_respected(self, monkeypatch):
        """Even with semantic rerank, paper_budget floor/ceiling holds."""
        monkeypatch.setenv("FUSION_SEMANTIC_RETRIEVAL", "true")
        monkeypatch.setenv("ARCHIMEDES_FUSION_ENABLED", "true")
        from archimedes.agents.strategy_fusion import (
            FUSION_MAX_PAPERS,
            MIN_PAPERS,
            FusionBrief,
            select_candidates,
        )

        brief = FusionBrief(
            strategic_direction="test",
            max_papers=1,  # below floor
        )
        corpus = []
        for p in PAPERS:
            cp = type(
                "CP",
                (),
                {
                    "arxiv_id": p.arxiv_id,
                    "title": p.title,
                    "abstract": p.abstract,
                    "primary_category": "q-fin.pm",
                    "categories": ("q-fin.pm",),
                    "published": "2020-04-01",
                    "haystack": f"q-fin.pm {p.title} {p.abstract}".lower(),
                },
            )()
            corpus.append(cp)

        result = select_candidates(brief, corpus)
        assert len(result) >= MIN_PAPERS
        assert len(result) <= FUSION_MAX_PAPERS
