"""Tests for the flagged multi-paper strategy-fusion module.

No network: the Anthropic client is never constructed — every test injects
either a mock `LLMBackend` or the deterministic `CannedBackend`. The corpus
is a self-contained in-test fixture written to a tmp manifest (no dependency
on Stream-A's data/corpus/manifest.jsonl).

Covers:
- feature-flag gating (default OFF → inert; ON → real path)
- steering filters candidates (asset_classes + strategic_direction + budget)
- the synthesis prompt contains >=2 candidate papers
- provenance lists the source arxiv_ids; recorded model == response.model
- the offline fallback is explicitly labelled (not model reasoning)
- defensive manifest loader skips bad lines, no hard file dependency
- the >=2-paper floor: insufficient corpus / single-paper output declined
"""

from __future__ import annotations

import json

import pytest

from archimedes.models.portfolio import RiskProfile
from archimedes.services.strategy_fusion import (
    MIN_PAPERS,
    CannedBackend,
    FusionBrief,
    StrategyFusion,
    fusion_enabled,
    load_corpus,
    select_candidates,
)

# ── Self-contained fixture corpus (frozen manifest schema) ──────

_FIXTURE_ROWS = [
    {
        "arxiv_id": "2401.00001",
        "title": "Cross-Sectional Equity Momentum with Regime Conditioning",
        "authors": ["A. One"],
        "primary_category": "q-fin.PM",
        "categories": ["q-fin.PM"],
        "published": "2024-01-05",
        "updated": "2024-01-06",
        "abstract": "We study a regime-switching overlay on cross-sectional "
        "equity momentum across the stock universe.",
        "pdf_url": "http://x/1",
        "pdf_sha256": "a" * 64,
        "pdf_path": "data/corpus/pdfs/2401.00001.pdf",
        "text_path": "data/corpus/text/2401.00001.txt",
        "fetched_at": "2026-05-16T00:00:00Z",
    },
    {
        "arxiv_id": "2402.00002",
        "title": "Treasury Yield Curve Carry and Macro Regimes",
        "authors": ["B. Two"],
        "primary_category": "q-fin.PM",
        "categories": ["q-fin.PM", "econ.EM"],
        "published": "2024-02-10",
        "updated": "2024-02-11",
        "abstract": "A carry strategy on the treasury yield curve conditioned "
        "on macro regime states.",
        "pdf_url": "http://x/2",
        "pdf_sha256": "b" * 64,
        "pdf_path": "data/corpus/pdfs/2402.00002.pdf",
        "text_path": "data/corpus/text/2402.00002.txt",
        "fetched_at": "2026-05-16T00:00:00Z",
    },
    {
        "arxiv_id": "2403.00003",
        "title": "Implied Volatility Surface Dynamics for Index Options",
        "authors": ["C. Three"],
        "primary_category": "q-fin.PR",
        "categories": ["q-fin.PR"],
        "published": "2024-03-15",
        "updated": "2024-03-16",
        "abstract": "Modelling implied volatility surface dynamics and variance "
        "risk premia for index options.",
        "pdf_url": "http://x/3",
        "pdf_sha256": "c" * 64,
        "pdf_path": "data/corpus/pdfs/2403.00003.pdf",
        "text_path": "data/corpus/text/2403.00003.txt",
        "fetched_at": "2026-05-16T00:00:00Z",
    },
    {
        "arxiv_id": "2404.00004",
        "title": "Deep Learning for Protein Folding",
        "authors": ["D. Four"],
        "primary_category": "q-bio.BM",
        "categories": ["q-bio.BM"],
        "published": "2024-04-20",
        "updated": "2024-04-21",
        "abstract": "An unrelated biology paper that must never match a "
        "finance asset-class steer.",
        "pdf_url": "http://x/4",
        "pdf_sha256": "d" * 64,
        "pdf_path": "data/corpus/pdfs/2404.00004.pdf",
        "text_path": "data/corpus/text/2404.00004.txt",
        "fetched_at": "2026-05-16T00:00:00Z",
    },
]


@pytest.fixture
def manifest(tmp_path):
    """Write the fixture corpus + a deliberately corrupt + blank line."""
    p = tmp_path / "manifest.jsonl"
    lines = [json.dumps(r) for r in _FIXTURE_ROWS]
    lines.insert(2, "")  # blank line — must be skipped
    lines.insert(3, "{not valid json")  # corrupt line — must be skipped
    lines.append('{"title": "no arxiv id"}')  # missing core field — skipped
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


@pytest.fixture
def corpus(manifest):
    return load_corpus(manifest)


@pytest.fixture(autouse=True)
def _flag_off_by_default(monkeypatch):
    """Every test starts with the flag explicitly cleared (default OFF)."""
    monkeypatch.delenv("ARCHIMEDES_FUSION_ENABLED", raising=False)
    monkeypatch.delenv("ARCHIMEDES_CORPUS_MANIFEST", raising=False)


# ── A mock backend that records what it was asked ───────────────


class _MockBackend:
    """Records the prompt; returns a canned fusion of the first 2 candidates.

    `served_model` differs from `model_id` to assert the true-model honesty
    rule (the deployment is GLM-routed: response.model != requested model).
    """

    model_id = "claude-sonnet-4-20250514"  # what we'd configure/request
    served_model = "glm-4.7"  # what actually answers (response.model)

    def __init__(self) -> None:
        self.system: str | None = None
        self.user: str | None = None

    def complete(self, system: str, user: str) -> str:
        self.system = system
        self.user = user
        payload = json.loads(user)
        ids = [p["arxiv_id"] for p in payload["candidate_papers"][:2]]
        return json.dumps(
            {
                "strategy_name": "Regime-conditioned carry/momentum fusion",
                "thesis": "Fuse the two mechanisms. Pre-backtest hypothesis.",
                "source_arxiv_ids": ids
                + ["9999.99999"],  # hallucinated id — must be dropped
                "fusion_reasoning": "Paper A gives momentum; paper B gives carry.",
                "novelty_rationale": "The joint regime conditioning is unpublished.",
                "risk_notes": "Pre-backtest; selection-bias gate still applies.",
            }
        )


# ── Feature-flag gating ─────────────────────────────────────────


def test_flag_default_off():
    assert fusion_enabled() is False


@pytest.mark.parametrize("val", ["1", "true", "TRUE", "Yes", "on"])
def test_flag_truthy_values(monkeypatch, val):
    monkeypatch.setenv("ARCHIMEDES_FUSION_ENABLED", val)
    assert fusion_enabled() is True


@pytest.mark.parametrize("val", ["", "0", "false", "no", "off", "maybe"])
def test_flag_falsy_values(monkeypatch, val):
    monkeypatch.setenv("ARCHIMEDES_FUSION_ENABLED", val)
    assert fusion_enabled() is False


def test_flag_off_is_inert_no_backend_no_corpus():
    """Flag OFF: sentinel out, backend never resolved, corpus never read."""

    class _Boom:
        model_id = "x"
        served_model = "x"

        def complete(self, system, user):  # noqa: ARG002
            raise AssertionError("backend must not be called when flag is OFF")

    def _boom_corpus():
        raise AssertionError("corpus must not be read when flag is OFF")

    svc = StrategyFusion(backend=_Boom(), corpus=None)
    svc._resolve_corpus = _boom_corpus  # would raise if touched
    proposal = svc.propose(FusionBrief(asset_classes=["equities"]))

    assert proposal.status == "disabled"
    assert proposal.source_arxiv_ids == []
    assert "ARCHIMEDES_FUSION_ENABLED" in proposal.thesis
    assert proposal.is_actionable is False


# ── Defensive manifest loader ───────────────────────────────────


def test_loader_skips_blank_corrupt_and_incomplete_lines(corpus):
    # 4 good rows; blank + corrupt + missing-core lines all skipped.
    assert {p.arxiv_id for p in corpus} == {
        "2401.00001",
        "2402.00002",
        "2403.00003",
        "2404.00004",
    }


def test_loader_no_file_returns_empty(tmp_path):
    assert load_corpus(tmp_path / "does_not_exist.jsonl") == []


def test_loader_env_override(monkeypatch, manifest):
    monkeypatch.setenv("ARCHIMEDES_CORPUS_MANIFEST", str(manifest))
    # path=None → resolves via ARCHIMEDES_CORPUS_MANIFEST.
    assert len(load_corpus()) == 4


# ── Deterministic steering / candidate selection ────────────────


def test_asset_class_filter_excludes_unrelated(corpus):
    brief = FusionBrief(asset_classes=["equities"])
    picked = select_candidates(brief, corpus)
    ids = {p.arxiv_id for p in picked}
    assert "2401.00001" in ids  # equity momentum matches
    assert "2404.00004" not in ids  # biology paper excluded


def test_direction_biases_ranking(corpus):
    """A 'volatility' steer should surface the IV-surface paper first."""
    brief = FusionBrief(
        asset_classes=[], strategic_direction="implied volatility variance"
    )
    picked = select_candidates(brief, corpus)
    assert picked[0].arxiv_id == "2403.00003"


def test_paper_budget_clamped_and_enforced_floor(corpus):
    # max_papers below the floor is clamped UP to MIN_PAPERS.
    brief = FusionBrief(asset_classes=[], max_papers=1)
    assert brief.paper_budget == MIN_PAPERS
    picked = select_candidates(brief, corpus)
    assert len(picked) == MIN_PAPERS


def test_selection_is_deterministic(corpus):
    brief = FusionBrief(asset_classes=["rates", "equities"], max_papers=3)
    a = [p.arxiv_id for p in select_candidates(brief, corpus)]
    b = [p.arxiv_id for p in select_candidates(brief, corpus)]
    assert a == b


# ── Fusion happy path (flag ON, mocked backend) ─────────────────


def test_propose_fuses_and_records_provenance(monkeypatch, corpus):
    monkeypatch.setenv("ARCHIMEDES_FUSION_ENABLED", "1")
    backend = _MockBackend()
    svc = StrategyFusion(backend=backend, corpus=corpus)
    brief = FusionBrief(
        asset_classes=["equities", "rates"],
        risk_appetite=RiskProfile.MODERATE,
        strategic_direction="regime conditioning",
        max_papers=4,
    )
    proposal = svc.propose(brief)

    assert proposal.status == "ok"
    assert proposal.is_actionable is True

    # Prompt was handed >=2 candidate papers.
    sent = json.loads(backend.user)
    assert len(sent["candidate_papers"]) >= MIN_PAPERS

    # Provenance lists the fused source arxiv_ids (>=2) ...
    assert len(proposal.source_arxiv_ids) >= MIN_PAPERS
    # ... and the hallucinated id was dropped (anti-hallucination).
    assert "9999.99999" not in proposal.source_arxiv_ids
    assert all(
        sid in {p.arxiv_id for p in corpus} for sid in proposal.source_arxiv_ids
    )

    # True-model honesty: recorded model == response.model (served), and the
    # configured/requested model is kept separately.
    assert proposal.model == "glm-4.7"
    assert proposal.requested_model == "claude-sonnet-4-20250514"


def test_prompt_demands_at_least_two_papers(monkeypatch, corpus):
    monkeypatch.setenv("ARCHIMEDES_FUSION_ENABLED", "1")
    backend = _MockBackend()
    svc = StrategyFusion(backend=backend, corpus=corpus)
    svc.propose(FusionBrief(asset_classes=["equities", "rates", "vol"]))
    assert "AT LEAST TWO" in backend.system


# ── Insufficient corpus / >=2 floor declines ────────────────────


def test_insufficient_corpus_declines(monkeypatch, corpus):
    monkeypatch.setenv("ARCHIMEDES_FUSION_ENABLED", "1")
    backend = _MockBackend()
    # Asset class that matches at most one fixture paper → < MIN_PAPERS.
    svc = StrategyFusion(backend=backend, corpus=corpus)
    proposal = svc.propose(FusionBrief(asset_classes=["crypto"]))
    assert proposal.status == "insufficient_corpus"
    assert proposal.is_actionable is False
    assert backend.user is None  # LLM never called when corpus too thin


def test_empty_corpus_declines_without_llm(monkeypatch):
    monkeypatch.setenv("ARCHIMEDES_FUSION_ENABLED", "1")
    backend = _MockBackend()
    svc = StrategyFusion(backend=backend, corpus=[])
    proposal = svc.propose(FusionBrief(asset_classes=["equities"]))
    assert proposal.status == "insufficient_corpus"
    assert backend.user is None


def test_model_fusing_under_two_valid_papers_declines(monkeypatch, corpus):
    monkeypatch.setenv("ARCHIMEDES_FUSION_ENABLED", "1")

    class _SinglePaperBackend:
        model_id = "claude-sonnet-4-20250514"
        served_model = "glm-4.7"

        def complete(self, system, user):  # noqa: ARG002
            ids = [json.loads(user)["candidate_papers"][0]["arxiv_id"]]
            return json.dumps(
                {
                    "strategy_name": "x",
                    "thesis": "x",
                    "source_arxiv_ids": ids + ["hallucinated"],
                    "fusion_reasoning": "x",
                    "novelty_rationale": "x",
                    "risk_notes": "x",
                }
            )

    svc = StrategyFusion(backend=_SinglePaperBackend(), corpus=corpus)
    proposal = svc.propose(FusionBrief(asset_classes=["equities", "rates"]))
    assert proposal.status == "insufficient_corpus"
    assert proposal.is_actionable is False


# ── Offline fallback labelling ──────────────────────────────────


def test_canned_fallback_is_labelled_not_model_reasoning(monkeypatch, corpus):
    monkeypatch.setenv("ARCHIMEDES_FUSION_ENABLED", "1")
    svc = StrategyFusion(backend=CannedBackend(), corpus=corpus)
    proposal = svc.propose(
        FusionBrief(asset_classes=["equities", "rates"], max_papers=3)
    )
    assert proposal.status == "ok"
    # The model field must out itself as the fallback, never a real model.
    assert proposal.model == "canned-fusion-fallback"
    assert proposal.requested_model == "canned-fusion-fallback"
    blob = (proposal.thesis + proposal.fusion_reasoning).lower()
    assert "not model reasoning" in blob or "fallback" in blob


def test_unparseable_model_output_declines(monkeypatch, corpus):
    monkeypatch.setenv("ARCHIMEDES_FUSION_ENABLED", "1")

    class _Garbage:
        model_id = "claude-sonnet-4-20250514"
        served_model = "glm-4.7"

        def complete(self, system, user):  # noqa: ARG002
            return "this is not json at all"

    svc = StrategyFusion(backend=_Garbage(), corpus=corpus)
    proposal = svc.propose(FusionBrief(asset_classes=["equities", "rates"]))
    assert proposal.status == "unparseable"
    assert proposal.model == "glm-4.7"  # still records the served model
    assert proposal.requested_model == "claude-sonnet-4-20250514"
