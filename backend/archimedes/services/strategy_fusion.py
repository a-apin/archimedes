"""Strategy fusion — multi-paper, user-steered, novelty-seeking synthesis.

A NEW, feature-flagged primitive that sits *beside* `strategy_architect.py`,
not inside it. The architect selects + weights pre-curated single-paper
library strategies (the verified-library path that feeds the strategy-passport
/ reasoning-trace data flow). Fusion does the opposite-direction thing:
synthesizes a *new* strategy hypothesis by fusing >=2 raw arXiv q-fin papers,
steered by the user, optimizing for novelty (McLean & Pontiff 2016: published
alpha decays — the un-decayed edge is combinations not yet in the literature).

Why a separate, flagged module (owner-decided HARD constraint):
- `strategy_architect.py` and the construction-trace path are
  contract-review-grade (the live `ReasoningTraceRegistry`). Fusion is
  additive, behind `ARCHIMEDES_FUSION_ENABLED` (default OFF), and revertible
  by deleting this file + its spec. Nothing in the audited flow is touched.
- The LLM-backend seam, lazy `anthropic` import, `extract_json`, frozen
  artifact and honest-fallback labelling deliberately mirror the architect so
  a later route-wiring is a small, familiar diff.

True-model honesty: our backend is routed through a GLM-backed,
Anthropic-compatible endpoint. `messages.create(model=...)` gets the
*configured* string, but `response.model` is the model that actually served
the request (e.g. `glm-4.7`). The proposal records `response.model` as the
provenance field of record and keeps the configured/requested string
separately. See `docs/specs/strategy-fusion-spec.md`.

References:
- `docs/specs/strategy-fusion-spec.md` — the design this implements
- `backend/archimedes/services/strategy_architect.py` — the seam mirrored here
- `backend/archimedes/services/strategy_provider.py` — env-override precedent
- `backend/archimedes/models/portfolio.py` — RiskProfile, RISK_PROFILE_PARAMS
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from archimedes.models.portfolio import RISK_PROFILE_PARAMS, RiskProfile
from archimedes.services.strategy_architect import extract_json

logger = logging.getLogger(__name__)

# Mirror the architect's configured default; `response.model` is what is
# actually recorded for provenance (see module docstring / spec).
DEFAULT_MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 4096

# Hard floor: a fusion of one paper is just extraction (the architect's job).
MIN_PAPERS = 2
# Hard cap: token + cross-paper-coherence budget. max_papers clamps into here.
FUSION_MAX_PAPERS = 6

_TRUTHY = {"1", "true", "yes", "on"}


# ── Feature flag (mirrors ARCHIMEDES_STRATEGIES_DIR: plain getenv) ──


def fusion_enabled() -> bool:
    """True iff ARCHIMEDES_FUSION_ENABLED is truthy. Default OFF.

    Truthy = {1,true,yes,on} case-insensitive — the env convention shared
    across the codebase. No central settings module exists; env override is
    the established pattern (`strategy_provider.default_provider`).
    """
    return os.getenv("ARCHIMEDES_FUSION_ENABLED", "").strip().lower() in _TRUTHY


# ── Asset-class synonym map (deterministic candidate filtering) ──
#
# Lowercased substring match against primary_category + categories + title +
# abstract. Intentionally simple and reviewable rather than embedding-based;
# a SPECTER2 ranker is a clean post-hackathon swap behind this same seam.
_ASSET_SYNONYMS: dict[str, tuple[str, ...]] = {
    "equities": ("equit", "stock", "share", "q-fin.pm", "cross-section"),
    "rates": ("rate", "bond", "treasury", "yield curve", "fixed income", "duration"),
    "credit": ("credit", "default", "cds", "spread", "bankruptcy"),
    "fx": ("fx", "currency", "exchange rate", "carry trade"),
    "commodities": ("commodit", "oil", "energy", "metal", "gold", "futures"),
    "crypto": ("crypto", "bitcoin", "blockchain", "defi", "token"),
    "vol": ("volatil", "variance", "option", "vix", "implied vol"),
    "macro": ("macro", "regime", "business cycle", "monetary", "inflation"),
}


# ── LLM backend seam (mirrors the architect's Protocol) ─────────


class LLMBackend(Protocol):
    """Minimal text-completion seam. Identical shape to the architect's.

    `served_model` is the fusion-specific addition: the model that actually
    answered (`response.model`), distinct from the configured `model_id`.
    """

    @property
    def model_id(self) -> str:
        """The configured/requested model string (reproducibility)."""
        ...

    @property
    def served_model(self) -> str:
        """The model that actually served the last call (provenance)."""
        ...

    def complete(self, system: str, user: str) -> str:
        """Return the model's raw text response to a single user turn."""
        ...


class ClaudeBackend:
    """Hosted Claude via the Anthropic SDK, GLM-routed in this deployment.

    `anthropic` is imported lazily inside `complete` so this module stays
    importable in dependency-light environments (tests, AST tooling) — the
    architect's pattern. `served_model` is populated from `response.model`
    on each call so the proposal records the model that actually answered.
    """

    def __init__(self, model: str = DEFAULT_MODEL, api_key: str | None = None) -> None:
        import anthropic

        self._model = model
        self._served = model  # configured until a real response overrides it
        self._api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self._client = anthropic.Anthropic(api_key=self._api_key) if self._api_key else None

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def served_model(self) -> str:
        return self._served

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def complete(self, system: str, user: str) -> str:
        import anthropic

        client = self._client or anthropic.Anthropic(api_key=self._api_key)
        response = client.messages.create(
            model=self._model,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        # The honesty rule: record what actually served the request, not what
        # we asked for. GLM-routed endpoint returns e.g. "glm-4.7" here.
        served = getattr(response, "model", None)
        if served:
            self._served = str(served)
        return response.content[0].text.strip() if response.content else ""


class CannedBackend:
    """Deterministic offline fallback. Explicitly NOT model reasoning.

    Mirrors the architect's `CannedBackend`: keeps the path demoable with no
    API key and gives the parser a stable fixture in tests. The text is
    emphatic that this is a non-novel placeholder so it can never masquerade
    as a real cross-paper synthesis in a provenance record.
    """

    model_id = "canned-fusion-fallback"
    served_model = "canned-fusion-fallback"

    def complete(self, system: str, user: str) -> str:  # noqa: ARG002
        ids = re.findall(r'"arxiv_id"\s*:\s*"([^"]+)"', user)
        ids = ids[:FUSION_MAX_PAPERS] or ["__none__"]
        return json.dumps(
            {
                "strategy_name": "Offline fusion placeholder",
                "thesis": (
                    "Offline fallback: no LLM backend available, so no genuine "
                    "cross-paper synthesis was performed. Connect "
                    "ANTHROPIC_API_KEY for a real, novelty-seeking fusion."
                ),
                "source_arxiv_ids": ids,
                "fusion_reasoning": (
                    "Not model reasoning. Papers are echoed back unfused; this "
                    "is a labelled placeholder, not a novel combination."
                ),
                "novelty_rationale": (
                    "None claimed — a fallback is by definition not novel."
                ),
                "risk_notes": (
                    "Fallback output. Pre-backtest hypothesis only; the "
                    "selection-bias gate (DSR/PBO/OOS/look-ahead) still applies."
                ),
            }
        )


# ── User-steering input ─────────────────────────────────────────


@dataclass
class FusionBrief:
    """The user's steer. Fusion never free-runs the whole corpus.

    `asset_classes` is a required-overlap filter (empty = no asset filter).
    `risk_appetite` shapes the synthesis envelope (RISK_PROFILE_PARAMS), it
    does not hard-filter papers. `strategic_direction` biases ranking and is
    passed verbatim to the prompt. `max_papers` is clamped to
    [MIN_PAPERS, FUSION_MAX_PAPERS]; the >=2 floor is non-negotiable.
    """

    asset_classes: list[str] = field(default_factory=list)
    risk_appetite: RiskProfile | str = RiskProfile.MODERATE
    strategic_direction: str = ""
    max_papers: int = 4

    @property
    def risk_profile(self) -> RiskProfile:
        rp = self.risk_appetite
        return RiskProfile(rp) if isinstance(rp, str) else rp

    @property
    def paper_budget(self) -> int:
        """max_papers clamped into the enforced [MIN_PAPERS, cap] range."""
        return max(MIN_PAPERS, min(FUSION_MAX_PAPERS, int(self.max_papers)))


# ── Corpus manifest (read-only, defensive, not a hard dependency) ──


@dataclass(frozen=True)
class CorpusPaper:
    """One manifest line, reduced to the fields fusion needs."""

    arxiv_id: str
    title: str
    abstract: str
    primary_category: str
    categories: tuple[str, ...]
    published: str

    @property
    def haystack(self) -> str:
        """Lowercased text used for asset-class + direction matching."""
        cats = " ".join(self.categories)
        return f"{self.primary_category} {cats} {self.title} {self.abstract}".lower()


def _manifest_path() -> Path | None:
    """Resolve the corpus manifest. Mirrors default_provider() precedence.

    1. ARCHIMEDES_CORPUS_MANIFEST env override (deployment / tests).
    2. First existing candidate among host + container-plausible layouts.
    Returns None if nothing resolvable — caller degrades, never raises.
    """
    env = os.getenv("ARCHIMEDES_CORPUS_MANIFEST")
    if env:
        p = Path(env)
        return p if p.exists() else None

    here = Path(__file__).resolve()
    candidates = [
        here.parents[3] / "data" / "corpus" / "manifest.jsonl",  # host repo
        Path("/app/data/corpus/manifest.jsonl"),  # repo-root build context
        Path("/data/corpus/manifest.jsonl"),  # bind-mount at root
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def load_corpus(path: Path | None = None) -> list[CorpusPaper]:
    """Defensive line-by-line manifest load. No hard dependency on the file.

    A blank / non-JSON line, or one missing arxiv_id or all of
    title+abstract, is logged at debug and skipped — one bad line never
    aborts the load (arxiv_pipeline's per-page tolerance posture). A
    missing/empty manifest yields an empty list (caller declines honestly).
    """
    path = path or _manifest_path()
    if path is None or not path.exists():
        logger.info("fusion: no corpus manifest resolvable; empty corpus")
        return []

    papers: list[CorpusPaper] = []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("fusion: cannot read manifest %s: %s", path, exc)
        return []

    for lineno, line in enumerate(raw.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj: dict[str, Any] = json.loads(line)
        except json.JSONDecodeError as exc:
            logger.debug("fusion: skip manifest line %d (bad JSON): %s", lineno, exc)
            continue
        arxiv_id = str(obj.get("arxiv_id", "")).strip()
        title = str(obj.get("title", "")).strip()
        abstract = str(obj.get("abstract", "")).strip()
        if not arxiv_id or not (title or abstract):
            logger.debug("fusion: skip manifest line %d (missing core fields)", lineno)
            continue
        cats = obj.get("categories") or []
        papers.append(
            CorpusPaper(
                arxiv_id=arxiv_id,
                title=title,
                abstract=abstract,
                primary_category=str(obj.get("primary_category", "")).strip(),
                categories=tuple(str(c) for c in cats if isinstance(c, str)),
                published=str(obj.get("published", "")).strip(),
            )
        )
    logger.info("fusion: loaded %d corpus papers from %s", len(papers), path)
    return papers


# ── Deterministic pre-LLM candidate selection ───────────────────


def _asset_terms(asset_classes: list[str]) -> list[str]:
    """Expand requested asset classes through the synonym map (+ raw term)."""
    terms: list[str] = []
    for ac in asset_classes:
        key = ac.strip().lower()
        if not key:
            continue
        terms.append(key)
        terms.extend(_ASSET_SYNONYMS.get(key, ()))
    return terms


def select_candidates(brief: FusionBrief, corpus: list[CorpusPaper]) -> list[CorpusPaper]:
    """Deterministic, explainable, pre-LLM. The model never widens this set.

    1. Asset-class overlap filter (skipped if no asset_classes given).
    2. Rank by strategic_direction keyword hits, then recency (newer first
       — alpha decay favours fresher results), then arxiv_id for total order.
    3. Take top `paper_budget`.
    """
    terms = _asset_terms(brief.asset_classes)
    if terms:
        filtered = [p for p in corpus if any(t in p.haystack for t in terms)]
    else:
        filtered = list(corpus)

    direction_kws = [
        w for w in re.findall(r"[a-z]{3,}", brief.strategic_direction.lower())
    ]

    def score(p: CorpusPaper) -> tuple[int, str, str]:
        hits = sum(1 for kw in direction_kws if kw in p.haystack)
        # Negative hits → higher hits sort first; published desc → newer
        # first; arxiv_id asc as the final deterministic tiebreak.
        return (-hits, _recency_key(p.published), p.arxiv_id)

    ranked = sorted(filtered, key=score)
    return ranked[: brief.paper_budget]


def _recency_key(published: str) -> str:
    """Sort key making newer `published` sort first (we sort ascending).

    ISO dates sort lexicographically; invert by complementing digits so a
    later date yields a smaller key. Non-dates sort last (treated as oldest).
    """
    digits = re.sub(r"\D", "", published)[:8]
    if len(digits) != 8:
        return "99999999"
    return "".join(str(9 - int(c)) for c in digits)


# ── Output artifact ─────────────────────────────────────────────


@dataclass(frozen=True)
class FusionProposal:
    """A novel cross-paper strategy hypothesis. Pre-backtest, pre-curation.

    `status` is explicit so callers never infer failure from emptiness
    (architect parity). `model` is the TRUE served model (`response.model`)
    — the provenance field of record; `requested_model` is what we asked
    for, kept separately.
    """

    status: str  # ok | disabled | insufficient_corpus | unparseable
    brief: FusionBrief
    strategy_name: str
    thesis: str
    source_arxiv_ids: list[str]
    fusion_reasoning: str
    novelty_rationale: str
    risk_notes: str
    model: str
    requested_model: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_actionable(self) -> bool:
        """True only for a real, parseable, >=2-paper fusion."""
        return self.status == "ok" and len(self.source_arxiv_ids) >= MIN_PAPERS


# ── Prompt construction ─────────────────────────────────────────


_SYSTEM_PROMPT = """You are Archimedes Fusion, an AI quant-research synthesizer. \
You design a NOVEL trading-strategy hypothesis by FUSING the mechanisms of \
MULTIPLE peer-reviewed quantitative-finance papers into one combined approach.

Hard rules:
- You MUST fuse AT LEAST TWO of the provided papers. A single-paper answer is \
invalid — that is a different tool's job. You may use a subset of the papers, \
but never fewer than two and never a paper not in the provided list.
- Reference papers ONLY by an arxiv_id from the provided candidates. Never \
invent a paper or an arxiv_id.
- OPTIMIZE FOR NOVELTY. The edge is the combination the literature has NOT \
published. Published single-paper alpha decays post-publication (McLean & \
Pontiff 2016) — your value is the non-obvious synthesis, not re-stating one \
paper. Explain why the COMBINATION is non-obvious relative to each paper alone.
- This is a HYPOTHESIS, not validated alpha. Do NOT invent Sharpe ratios, \
returns, or backtest numbers. Do NOT promise or forecast returns. State \
plainly that empirical validation (backtest / DSR / PBO) is pending.
- Respect the user's risk envelope (USYC floor/ceiling, target vol, max DD) \
as a synthesis constraint, not as a paper filter.

Output STRICT JSON ONLY (no prose, no markdown fences), exactly this schema:
{
  "strategy_name": "<short working name for the fused strategy>",
  "thesis": "<the fused strategy in plain language, honest it is pre-backtest>",
  "source_arxiv_ids": ["<arxiv_id from candidates>", "<another>", ...],
  "fusion_reasoning": "<what mechanism EACH cited paper contributes and how \
they combine>",
  "novelty_rationale": "<why this specific combination is not already in the \
literature>",
  "risk_notes": "<key risks + the pre-backtest / selection-bias caveat>"
}"""


def _build_user_prompt(brief: FusionBrief, candidates: list[CorpusPaper]) -> str:
    rp = brief.risk_profile
    payload = {
        "user_steer": {
            "asset_classes": brief.asset_classes,
            "risk_appetite": rp.value,
            "risk_envelope": RISK_PROFILE_PARAMS[rp],
            "strategic_direction": brief.strategic_direction
            or "(none given — optimize for novelty within the asset steer)",
            "min_papers_to_fuse": MIN_PAPERS,
            "max_papers_to_fuse": brief.paper_budget,
        },
        "candidate_papers": [
            {
                "arxiv_id": p.arxiv_id,
                "title": p.title,
                "primary_category": p.primary_category,
                "categories": list(p.categories),
                "published": p.published,
                "abstract": p.abstract,
            }
            for p in candidates
        ],
    }
    return json.dumps(payload, indent=2)


# ── The fusion service ──────────────────────────────────────────


def _inert_proposal(brief: FusionBrief, status: str, thesis: str) -> FusionProposal:
    """A well-formed, self-describing non-fusion (disabled / declined)."""
    return FusionProposal(
        status=status,
        brief=brief,
        strategy_name="",
        thesis=thesis,
        source_arxiv_ids=[],
        fusion_reasoning="",
        novelty_rationale="",
        risk_notes="No fusion performed.",
        model="",
        requested_model="",
    )


class StrategyFusion:
    """User-steered, novelty-seeking, multi-paper strategy synthesizer.

    Pure service — no FastAPI, no on-chain, not wired into the architect or
    the construction-trace flow. Flag-gated: flag-off is a hard inert path
    (no anthropic import, no manifest read, sentinel proposal).
    """

    def __init__(
        self,
        backend: LLMBackend | None = None,
        corpus: list[CorpusPaper] | None = None,
    ) -> None:
        # Backend/corpus are injectable for offline tests. They are resolved
        # lazily in `propose` so constructing the service never triggers an
        # anthropic import or a manifest read (matters for the flag-off path
        # and dependency-light import sites).
        self._backend = backend
        self._corpus = corpus

    def _resolve_backend(self) -> LLMBackend:
        if self._backend is not None:
            return self._backend
        self._backend = default_backend()
        return self._backend

    def _resolve_corpus(self) -> list[CorpusPaper]:
        if self._corpus is not None:
            return self._corpus
        self._corpus = load_corpus()
        return self._corpus

    def propose(self, brief: FusionBrief) -> FusionProposal:
        if not fusion_enabled():
            # Hard inert path: no LLM, no manifest read, sentinel out.
            return _inert_proposal(
                brief,
                "disabled",
                "Strategy fusion is disabled. Set ARCHIMEDES_FUSION_ENABLED=1 "
                "to enable multi-paper, novelty-seeking synthesis.",
            )

        corpus = self._resolve_corpus()
        candidates = select_candidates(brief, corpus)
        if len(candidates) < MIN_PAPERS:
            return _inert_proposal(
                brief,
                "insufficient_corpus",
                f"Need at least {MIN_PAPERS} papers matching the steer to "
                f"fuse; the corpus yielded {len(candidates)}. Broaden "
                "asset_classes / strategic_direction or grow the corpus. "
                "(Single-paper output is intentionally not produced — that "
                "is the strategy architect's job, not fusion's.)",
            )

        backend = self._resolve_backend()
        valid_ids = {p.arxiv_id for p in candidates}
        raw = backend.complete(_SYSTEM_PROMPT, _build_user_prompt(brief, candidates))

        try:
            parsed = extract_json(raw)
        except ValueError:
            logger.warning("fusion: unparseable LLM output; declined proposal")
            return FusionProposal(
                status="unparseable",
                brief=brief,
                strategy_name="",
                thesis=(
                    "Could not parse a valid fusion from the model. No "
                    "hypothesis proposed — safer than shipping a guess."
                ),
                source_arxiv_ids=[],
                fusion_reasoning="",
                novelty_rationale="",
                risk_notes="Model output was not valid JSON.",
                model=backend.served_model,
                requested_model=backend.model_id,
            )

        # Anti-hallucination: drop any arxiv_id not in the deterministically
        # selected candidate set (architect parity — it drops unknown ids).
        raw_ids = parsed.get("source_arxiv_ids", [])
        source_ids = [
            str(i) for i in raw_ids if isinstance(i, str) and i in valid_ids
        ]
        # De-dupe, preserve order.
        seen: set[str] = set()
        source_ids = [i for i in source_ids if not (i in seen or seen.add(i))]

        if len(source_ids) < MIN_PAPERS:
            logger.warning(
                "fusion: model fused %d valid papers (<%d); declined",
                len(source_ids),
                MIN_PAPERS,
            )
            return _inert_proposal(
                brief,
                "insufficient_corpus",
                f"Model did not fuse at least {MIN_PAPERS} of the provided "
                "papers (after dropping any hallucinated ids). No "
                "single-paper hypothesis is produced.",
            )

        return FusionProposal(
            status="ok",
            brief=brief,
            strategy_name=str(parsed.get("strategy_name", "")).strip(),
            thesis=str(parsed.get("thesis", "")).strip(),
            source_arxiv_ids=source_ids,
            fusion_reasoning=str(parsed.get("fusion_reasoning", "")).strip(),
            novelty_rationale=str(parsed.get("novelty_rationale", "")).strip(),
            risk_notes=str(parsed.get("risk_notes", "")).strip(),
            model=backend.served_model,  # TRUE served model — field of record
            requested_model=backend.model_id,  # what we asked for
        )


def default_backend() -> LLMBackend:
    """Hosted Claude when a key is present; canned fallback otherwise.

    Mirrors `strategy_architect.default_backend()`.
    """
    claude = ClaudeBackend()
    if claude.available:
        return claude
    logger.warning(
        "ANTHROPIC_API_KEY not set — strategy fusion using canned fallback"
    )
    return CannedBackend()


def default_fusion() -> StrategyFusion:
    """Factory mirroring `default_architect()`. Not yet route-wired."""
    return StrategyFusion()
