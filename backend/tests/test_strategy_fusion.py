"""Tests for the flagged multi-paper strategy-fusion module.

No network: the Anthropic client is never constructed — every test injects
either a mock `LLMBackend` or the deterministic `FusionCannedBackend`. The corpus
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
from archimedes.agents.strategy_fusion import (
    MIN_PAPERS,
    SUPPORTED_UNIVERSE,
    CorpusPaper,
    FusionBrief,
    FusionCannedBackend,
    StrategyFusion,
    _gap_fill_tickers,
    derive_asset_universe,
    fusion_enabled,
    load_corpus,
    select_candidates,
)
from archimedes.models.portfolio import RiskProfile

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
        "abstract": "We study a regime-switching overlay on cross-sectional equity momentum across the stock universe.",
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
        "abstract": "A carry strategy on the treasury yield curve conditioned on macro regime states.",
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
        "abstract": "Modelling implied volatility surface dynamics and variance risk premia for index options.",
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
        "abstract": "An unrelated biology paper that must never match a finance asset-class steer.",
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
        # .system/.user hold the FIRST (proposal) call — the spec-repair retry
        # appends later calls to .calls without clobbering what tests assert on.
        self.system: str | None = None
        self.user: str | None = None
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if self.system is None:
            self.system = system
            self.user = user
        payload = json.loads(self.user)
        ids = [p["arxiv_id"] for p in payload["candidate_papers"][:2]]
        return json.dumps(
            {
                "strategy_name": "Regime-conditioned carry/momentum fusion",
                "thesis": "Fuse the two mechanisms. Pre-backtest hypothesis.",
                "source_arxiv_ids": [*ids, "9999.99999"],  # hallucinated id — must be dropped
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

        def complete(self, system, user):
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
    brief = FusionBrief(asset_classes=[], strategic_direction="implied volatility variance")
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
    assert all(sid in {p.arxiv_id for p in corpus} for sid in proposal.source_arxiv_ids)

    # True-model honesty: recorded model == response.model (served), and the
    # configured/requested model is kept separately.
    assert proposal.model == "glm-4.7"
    assert proposal.requested_model == "claude-sonnet-4-20250514"


def test_propose_survives_array_wrapped_object(monkeypatch, corpus):
    """Regression for #911: a model that wraps its object in a JSON array
    (``[{...}]``) must not crash the ``parsed.get(...)`` calls in propose.
    extract_json recovers the embedded object instead of returning a list."""
    monkeypatch.setenv("ARCHIMEDES_FUSION_ENABLED", "1")

    class _ArrayWrappingBackend(_MockBackend):
        def complete(self, system: str, user: str) -> str:
            return "[" + super().complete(system, user) + "]"

    backend = _ArrayWrappingBackend()
    svc = StrategyFusion(backend=backend, corpus=corpus)
    brief = FusionBrief(
        asset_classes=["equities", "rates"],
        risk_appetite=RiskProfile.MODERATE,
        strategic_direction="regime conditioning",
        max_papers=4,
    )
    # Before the fix this raised AttributeError inside propose.
    proposal = svc.propose(brief)
    assert proposal.status == "ok"
    assert proposal.is_actionable is True
    assert len(proposal.source_arxiv_ids) >= MIN_PAPERS


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

        def complete(self, system, user):
            ids = [json.loads(user)["candidate_papers"][0]["arxiv_id"]]
            return json.dumps(
                {
                    "strategy_name": "x",
                    "thesis": "x",
                    "source_arxiv_ids": [*ids, "hallucinated"],
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
    svc = StrategyFusion(backend=FusionCannedBackend(), corpus=corpus)
    proposal = svc.propose(FusionBrief(asset_classes=["equities", "rates"], max_papers=3))
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

        def complete(self, system, user):
            return "this is not json at all"

    svc = StrategyFusion(backend=_Garbage(), corpus=corpus)
    proposal = svc.propose(FusionBrief(asset_classes=["equities", "rates"]))
    assert proposal.status == "unparseable"
    assert proposal.model == "glm-4.7"  # still records the served model
    assert proposal.requested_model == "claude-sonnet-4-20250514"


# ── Asset-universe derivation (T1.6 — no hardcoded SPY) ──────────


class _SpecBackend:
    """A mock that emits a strategy_spec whose universe defaults to ["SPY"].

    This is the worst case the derivation must defend against: a model that
    parrots the old single-proxy default. The service must override it with
    the user's selected assets (or the SSOT fallback) so the universe is the
    user's lever, never a hardcoded literal.
    """

    model_id = "claude-sonnet-4-20250514"
    served_model = "glm-4.7"

    def complete(self, system: str, user: str) -> str:
        payload = json.loads(user)
        ids = [p["arxiv_id"] for p in payload["candidate_papers"][:2]]
        return json.dumps(
            {
                "strategy_name": "Universe-steer test fusion",
                "thesis": "Pre-backtest hypothesis.",
                "source_arxiv_ids": ids,
                "fusion_reasoning": "Paper A + paper B.",
                "novelty_rationale": "Joint combination unpublished.",
                "risk_notes": "Pre-backtest; rigor gate pending.",
                "strategy_spec": {
                    "name": "Universe-steer test fusion",
                    "asset_universe": ["SPY"],  # the model's stale default
                    "rebalance_frequency": "monthly",
                    "entry": {"gt": ["close", "sma_200"]},
                    "exit": {"lt": ["close", "sma_200"]},
                    "position_sizing": {"type": "full_invested_when_in_market"},
                    "source_arxiv_ids": ids,
                    "look_ahead_safe": True,
                    "indicators": ["sma_200"],
                },
            }
        )


def test_universe_derived_from_user_selected_assets(monkeypatch, corpus):
    """The user's selected instruments steer the spec universe, overriding the model.

    Realistic steer: a broad class ("equities") drives paper retrieval, while the
    specific instruments the user picked drive the concrete universe.
    """
    monkeypatch.setenv("ARCHIMEDES_FUSION_ENABLED", "1")
    svc = StrategyFusion(backend=_SpecBackend(), corpus=corpus)
    proposal = svc.propose(FusionBrief(asset_classes=["equities", "QQQ", "IWM"]))
    assert proposal.status == "ok"
    assert proposal.strategy_spec is not None
    # The model emitted ["SPY"]; the service replaced it with the user's picks.
    assert proposal.strategy_spec["asset_universe"] == ["QQQ", "IWM"]
    assert proposal.strategy_spec["asset_universe"] != ["SPY"]


def test_universe_falls_back_to_ssot_when_no_instrument_steer(monkeypatch, corpus):
    """No concrete instrument in the steer → the full SSOT universe, never ["SPY"]."""
    monkeypatch.setenv("ARCHIMEDES_FUSION_ENABLED", "1")
    svc = StrategyFusion(backend=_SpecBackend(), corpus=corpus)
    # Broad-class steer ("equities") drives the paper filter but resolves to no
    # single instrument, so the universe falls back to the SSOT.
    proposal = svc.propose(FusionBrief(asset_classes=["equities", "rates"]))
    assert proposal.status == "ok"
    assert proposal.strategy_spec is not None
    universe = proposal.strategy_spec["asset_universe"]
    assert universe != ["SPY"]  # never the bare hardcoded literal
    assert universe == list(SUPPORTED_UNIVERSE)
    assert len(universe) > 1  # a real multi-instrument universe


def test_derive_asset_universe_resolves_synth_and_yfinance_aliases():
    """The derivation accepts display symbols, synth keys, and yfinance tickers."""
    assert derive_asset_universe(["SPY"]) == ["SPY"]
    assert derive_asset_universe(["sSPY"]) == ["SPY"]  # synth key
    assert derive_asset_universe(["spy"]) == ["SPY"]  # case-insensitive
    # De-dupe + order-preserve across alias forms.
    assert derive_asset_universe(["QQQ", "sQQQ", "QQQ"]) == ["QQQ"]


def test_derive_asset_universe_empty_is_full_ssot_not_spy():
    """Empty steer → the full supported universe, never a bare ["SPY"]."""
    universe = derive_asset_universe([])
    assert universe == list(SUPPORTED_UNIVERSE)
    assert universe != ["SPY"]
    assert "SPY" in universe  # SPY is *in* the universe, just not the whole of it


def test_supported_universe_is_ssot_derived_and_nontrivial():
    """The supported universe is derived from the GLOBAL_ASSETS SSOT, not a literal."""
    assert "SPY" in SUPPORTED_UNIVERSE
    assert "QQQ" in SUPPORTED_UNIVERSE
    assert len(SUPPORTED_UNIVERSE) > 50  # the expanded multi-asset universe
    assert tuple(sorted(SUPPORTED_UNIVERSE)) == SUPPORTED_UNIVERSE  # stable order


# ── universe_source provenance (#857, follow-up to #847) ─────────


class _MultiAssetModelUniverseBackend:
    """A mock whose strategy_spec emits a deliberate MULTI-instrument universe.

    Distinct from ``_SpecBackend`` (which parrots a bare ``["SPY"]"`` — the
    weak-model default that's treated as a non-choice and falls to "full").
    A genuine multi-instrument pick is trusted as the thesis's own universe,
    so with no user steer this should resolve to universe_source == "model".
    """

    model_id = "claude-sonnet-4-20250514"
    served_model = "glm-4.7"

    def complete(self, system: str, user: str) -> str:
        payload = json.loads(user)
        ids = [p["arxiv_id"] for p in payload["candidate_papers"][:2]]
        return json.dumps(
            {
                "strategy_name": "Model-picked universe fusion",
                "thesis": "Pre-backtest hypothesis.",
                "source_arxiv_ids": ids,
                "fusion_reasoning": "Paper A + paper B.",
                "novelty_rationale": "Joint combination unpublished.",
                "risk_notes": "Pre-backtest; rigor gate pending.",
                "strategy_spec": {
                    "name": "Model-picked universe fusion",
                    "asset_universe": ["QQQ", "TLT", "GLD"],  # deliberate multi-instrument pick
                    "rebalance_frequency": "monthly",
                    "entry": {"gt": ["close", "sma_200"]},
                    "exit": {"lt": ["close", "sma_200"]},
                    "position_sizing": {"type": "full_invested_when_in_market"},
                    "source_arxiv_ids": ids,
                    "look_ahead_safe": True,
                    "indicators": ["sma_200"],
                },
            }
        )


def test_universe_source_is_user_when_steer_given(monkeypatch, corpus):
    """A user asset-instrument steer wins over the model's spec universe;
    universe_source records "user" (#857)."""
    monkeypatch.setenv("ARCHIMEDES_FUSION_ENABLED", "1")
    svc = StrategyFusion(backend=_SpecBackend(), corpus=corpus)
    proposal = svc.propose(FusionBrief(asset_classes=["equities", "QQQ", "IWM"]))
    assert proposal.status == "ok"
    assert proposal.strategy_spec["asset_universe"] == ["QQQ", "IWM"]
    assert proposal.universe_source == "user"


def test_universe_source_is_model_when_spec_emits_valid_universe(monkeypatch, corpus):
    """No user steer + a genuine multi-instrument model spec → "model" (#857)."""
    monkeypatch.setenv("ARCHIMEDES_FUSION_ENABLED", "1")
    svc = StrategyFusion(backend=_MultiAssetModelUniverseBackend(), corpus=corpus)
    proposal = svc.propose(FusionBrief(asset_classes=["equities", "rates"]))
    assert proposal.status == "ok"
    assert proposal.strategy_spec["asset_universe"] == ["QQQ", "TLT", "GLD"]
    assert proposal.universe_source == "model"


def test_universe_source_is_full_on_fallback(monkeypatch, corpus):
    """No user steer + the model's spec collapses to the parrot default →
    the full SSOT universe with universe_source == "full" (#857)."""
    monkeypatch.setenv("ARCHIMEDES_FUSION_ENABLED", "1")
    svc = StrategyFusion(backend=_SpecBackend(), corpus=corpus)
    proposal = svc.propose(FusionBrief(asset_classes=["equities", "rates"]))
    assert proposal.status == "ok"
    assert proposal.strategy_spec["asset_universe"] == list(SUPPORTED_UNIVERSE)
    assert proposal.universe_source == "full"


# ── Defensive-leg universe gap-fill + honest surfacing (#892) ─────────────
#
# Issue #892: a brief classified with multiple asset_classes (e.g.
# ["crypto", "treasuries"]) had its "treasuries" leg silently vanish from
# every candidate's traded universe — the model named concrete crypto tickers
# but "treasuries" is a class, not a ticker, so `_resolve_user_assets` /
# `_spec_universe` dropped it with no signal. Fixed by (a) wiring the
# already-registered treasury tickers (IEF/SHY/TLT etc.) into the universe
# via a class-name -> proxy-ticker gap-fill, and (b) honestly surfacing any
# classified class that STILL has zero representation (universe_gaps /
# risk_notes) instead of silently proceeding.


@pytest.fixture
def crypto_treasury_corpus():
    """A tiny dedicated corpus whose abstracts literally contain "crypto" and
    "treasuries" so the deterministic pre-LLM paper filter (select_candidates,
    keyed on _ASSET_SYNONYMS + the raw class term) yields >=MIN_PAPERS for
    those exact asset_classes — independent of the shared fixture corpus
    above, which has no crypto paper and only a singular "treasury" (not
    "treasuries") mention.
    """
    return [
        CorpusPaper(
            arxiv_id="2501.00001",
            title="Momentum and Trend-Following in Crypto Markets",
            abstract="A trend-following strategy on major crypto assets with regime conditioning.",
            primary_category="q-fin.PM",
            categories=("q-fin.PM",),
            published="2025-01-05",
        ),
        CorpusPaper(
            arxiv_id="2502.00002",
            title="Defensive Rotation into Treasuries During Volatility Spikes",
            abstract="A volatility-managed rotation into treasuries as a defensive overlay.",
            primary_category="q-fin.PM",
            categories=("q-fin.PM",),
            published="2025-02-10",
        ),
    ]


class _CryptoOnlyUniverseBackend:
    """Mirrors the issue's exact reproduction: the model names concrete crypto
    tickers (BTC, ETH) but never mentions the classified "treasuries" leg —
    a class name, not a ticker, so it can't appear in a ticker-only spec."""

    model_id = "claude-sonnet-4-20250514"
    served_model = "glm-4.7"

    def complete(self, system: str, user: str) -> str:
        payload = json.loads(user)
        ids = [p["arxiv_id"] for p in payload["candidate_papers"][:2]]
        return json.dumps(
            {
                "strategy_name": "TrendFusion Momentum Strategy",
                "thesis": "Trend-following on BTC/ETH with a defensive rotation.",
                "source_arxiv_ids": ids,
                "fusion_reasoning": "Paper A momentum + paper B vol-managed rotation.",
                "novelty_rationale": "Joint combination unpublished.",
                "risk_notes": "Pre-backtest; rigor gate pending.",
                "strategy_spec": {
                    "name": "TrendFusion Momentum Strategy",
                    "asset_universe": ["BTC", "ETH"],  # the model only named the crypto leg
                    "rebalance_frequency": "monthly",
                    "entry": {"gt": ["close", "sma_200"]},
                    "exit": {"lt": ["close", "sma_200"]},
                    "position_sizing": {"type": "full_invested_when_in_market"},
                    "source_arxiv_ids": ids,
                    "look_ahead_safe": True,
                    "indicators": ["sma_200"],
                },
            }
        )


def test_defensive_leg_is_gap_filled_not_silently_dropped(monkeypatch, crypto_treasury_corpus):
    """Reproduces #892: brief classified ["crypto", "treasuries"]; the model's
    spec only names BTC/ETH. The treasury leg must be injected into the final
    universe (real registered tickers, e.g. IEF/SHY/TLT), not silently lost —
    and universe_gaps must be empty since both classified legs end up
    represented (crypto directly by the model, treasuries via gap-fill)."""
    monkeypatch.setenv("ARCHIMEDES_FUSION_ENABLED", "1")
    svc = StrategyFusion(backend=_CryptoOnlyUniverseBackend(), corpus=crypto_treasury_corpus)
    proposal = svc.propose(FusionBrief(asset_classes=["crypto", "treasuries"]))

    assert proposal.status == "ok"
    universe = proposal.strategy_spec["asset_universe"]
    assert "BTC" in universe and "ETH" in universe  # the model's crypto leg survives
    assert any(t in universe for t in ("IEF", "SHY", "TLT")), (
        f"treasury leg was dropped from the universe: {universe!r}"
    )
    assert proposal.universe_gaps == []  # both classified legs are represented
    assert "gap" not in proposal.risk_notes.lower()


def test_genuinely_unavailable_asset_class_is_surfaced_not_fabricated(monkeypatch, crypto_treasury_corpus):
    """Option (b) of the issue: a classified class with NO real data source at
    all must be surfaced honestly (universe_gaps + risk_notes), never silently
    dropped AND never covered by a fabricated data feed."""
    monkeypatch.setenv("ARCHIMEDES_FUSION_ENABLED", "1")
    svc = StrategyFusion(backend=_CryptoOnlyUniverseBackend(), corpus=crypto_treasury_corpus)
    # "lunar_futures" has no proxy map entry and no SSOT asset_class tag match —
    # there is genuinely no data source for it. "treasuries" is included purely
    # so the paper filter matches both fixture papers (>=MIN_PAPERS) — the
    # model's spec below never names a treasury ticker either, so this brief
    # exercises "two classified classes, one gets a real proxy, one doesn't".
    proposal = svc.propose(FusionBrief(asset_classes=["crypto", "lunar_futures", "treasuries"]))

    assert proposal.status == "ok"
    universe = proposal.strategy_spec["asset_universe"]
    assert "lunar_futures" not in universe  # never fabricated as if it were a real ticker
    assert proposal.universe_gaps == ["lunar_futures"]
    assert "lunar_futures" in proposal.risk_notes
    assert "not available" in proposal.risk_notes.lower()


def test_gap_fill_never_overrides_explicit_user_ticker_picks(monkeypatch, crypto_treasury_corpus):
    """The user's own concrete ticker picks stay the authoritative universe;
    gap-fill only ADDS a classified class's proxy, never removes a user pick.

    "crypto" is included purely so the paper filter matches both fixture
    papers (>=MIN_PAPERS); this particular brief's model backend never names
    a crypto ticker, so "crypto" is honestly (and separately, correctly)
    reported as a gap — the assertion below is about QQQ/treasuries, which
    are NOT gaps.
    """
    monkeypatch.setenv("ARCHIMEDES_FUSION_ENABLED", "1")
    svc = StrategyFusion(backend=_SpecBackend(), corpus=crypto_treasury_corpus)  # model parrots ["SPY"], ignored
    proposal = svc.propose(FusionBrief(asset_classes=["QQQ", "treasuries", "crypto"]))

    assert proposal.status == "ok"
    universe = proposal.strategy_spec["asset_universe"]
    assert "QQQ" in universe
    assert any(t in universe for t in ("IEF", "SHY", "TLT"))
    assert proposal.universe_source == "user"
    assert proposal.universe_gaps == ["crypto"]  # honest: no crypto ticker was ever named


def test_gap_fill_tickers_does_not_top_up_a_partially_represented_class():
    """Copilot review comment on PR #1033: `_gap_fill_tickers` must treat a
    class as represented once ANY of its proxy tickers is already in the
    universe — it should not keep injecting the remaining proxies (e.g. add
    SHY/TLT on top of an already-present IEF). Gap-fill patches in a *missing*
    leg; it doesn't pad out a partially-present one.
    """
    fill = _gap_fill_tickers(["treasuries"], ["IEF"])
    assert fill == []

    # Still fills when genuinely absent.
    fill = _gap_fill_tickers(["treasuries"], ["BTC", "ETH"])
    assert set(fill) == {"IEF", "SHY", "TLT"}


def test_broad_paper_filter_classes_still_report_as_gaps_on_full_fallback(monkeypatch, corpus):
    """ "equities"/"rates" remain paper-RETRIEVAL-only class filters (established
    behavior, #847/#682) — they resolve no proxy ticker of their own. On the
    full-SSOT fallback the SSOT-tag check still recognizes them as represented
    (equities/bonds ARE abundantly present in the full universe), so this must
    NOT be a false-positive gap.
    """
    monkeypatch.setenv("ARCHIMEDES_FUSION_ENABLED", "1")
    svc = StrategyFusion(backend=_SpecBackend(), corpus=corpus)
    proposal = svc.propose(FusionBrief(asset_classes=["equities", "rates"]))
    assert proposal.status == "ok"
    assert proposal.universe_source == "full"
    assert proposal.universe_gaps == []
