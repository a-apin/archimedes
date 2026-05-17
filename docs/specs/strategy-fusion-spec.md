# Strategy Fusion — Implementation Spec

> **Audience:** Strategy engine owner (Dan) + backend engineers + reviewers ahead of standup
> **Status:** Draft v1 — agent-generated for team review. Additive to, and deliberately
> decoupled from, [`strategy_architect.py`](../../backend/archimedes/services/strategy_architect.py).
> Builds on [`strategy-passport-spec.md`](strategy-passport-spec.md) (provenance) and the
> Stream-A q-fin corpus manifest.
> **Prerequisite reading:** [`../architectural-principles.md`](../architectural-principles.md)
> for the verifiability philosophy; [`strategy-passport-spec.md`](strategy-passport-spec.md)
> for the provenance primitive this extends.

## Goal

Shift strategy generation from **one strategy per paper** to **novel strategies fused across
multiple papers**, steered by the user, grounded in the arXiv q-fin corpus, and recorded with
honest provenance.

The existing `StrategyArchitect` selects and weights *pre-curated* library strategies, each
of which is grounded in a single paper. That is the right primitive for the verified library.
It is not the right primitive for the thing that is actually the moat: **continuously
generating cross-paper syntheses that no one has published yet**. This spec defines that
second primitive — `strategy_fusion` — as a new, feature-flagged module that does not modify
the architect or anything in the strategy-passport / reasoning-trace data flow.

## Why this is a separate module, not an edit to the architect

`strategy_architect.py` feeds the strategy-passport and reasoning-trace data flow, which is
contract-review-grade (the `ReasoningTraceRegistry` contract is live — see
[`../../CLAUDE.md`](../../CLAUDE.md) "When to ask before acting"). Changing it to do
multi-paper fusion would be a load-bearing change to an audited path under hackathon time
pressure. Instead:

- **Additive.** A new `backend/archimedes/services/strategy_fusion.py`. Nothing imports it
  yet; wiring it into a route is a later, separately reviewable step.
- **Flagged.** `ARCHIMEDES_FUSION_ENABLED` (default OFF). Flag-off is a hard inert path:
  no LLM call, no corpus read, returns a self-describing sentinel.
- **Revertible.** Deleting the module and the spec fully reverts the change. The architect,
  guardrail, construction-trace, and the on-chain flow are byte-for-byte untouched.
- **Seam-faithful.** It mirrors the architect's `LLMBackend` Protocol seam, lazy `anthropic`
  import, `extract_json`, frozen-dataclass artifact, and honest fallback labelling, so a
  later integration is a small, familiar diff rather than a rewrite.

## Per-paper → multi-paper fusion: the conceptual shift

| | Architect (today) | Fusion (this spec) |
| --- | --- | --- |
| Input | curated `Strategy` library (1 paper each) | the raw arXiv q-fin corpus manifest |
| Unit of output | a selection + weighting of existing strategies | a *new* strategy synthesized from ≥2 papers |
| User input | free-text intent + risk profile | structured steering brief (asset classes, risk appetite, strategic direction, paper budget) |
| Objective | fit intent + risk profile from validated alpha | **novelty** — a combination not yet in the literature |
| Provenance | `model_id`, strategy ids referenced | N source `arxiv_id`s + fusion reasoning + the true model recorded honestly |
| Validation state | library is curated / gated | proposal is a **hypothesis** — explicitly pre-backtest, pre-curation |

The architect answers *"build me a portfolio from what we trust."* Fusion answers *"propose a
strategy nobody has written down yet, from these papers, for what I want."* They are
complementary; fusion output is a **candidate hypothesis** that must still pass the same
selection-bias admission gate before it could ever be trusted.

## User-steering inputs (`FusionBrief`)

Fusion is **user-steered by construction** — it never free-runs over the whole corpus. The
user (or an upstream agent) supplies a `FusionBrief`:

| Field | Type | Role in candidate gating |
| --- | --- | --- |
| `asset_classes` | `list[str]` | Required-overlap filter. A paper is eligible only if its category/title/abstract evidences at least one requested class (substring + a small synonym map: `equities`, `rates`, `credit`, `fx`, `commodities`, `crypto`, `vol`, `macro`). Empty list = no asset filter (corpus-wide, still novelty-ranked). |
| `risk_appetite` | `str` → `RiskProfile` | Maps to `RISK_PROFILE_PARAMS`. Passed into the prompt as the synthesis constraint envelope (USYC floor/ceiling, target vol, max DD). Does **not** hard-filter papers (a paper is a building block, not a risk-tagged strategy) — it shapes the synthesis, not the candidate set. |
| `strategic_direction` | `str` | Free-text steer (e.g. *"regime-switching overlays on a carry core"*). Used for keyword-biased ranking of candidates and passed verbatim into the synthesis prompt. |
| `max_papers` | `int` | Upper bound on fused papers. Clamped to `[2, FUSION_MAX_PAPERS]` (hard cap 6 — token + coherence budget). |
| `min_papers` | (enforced, not user-settable below 2) | **Hard floor of 2.** A fusion of one paper is just extraction — that is the architect's job. If fewer than 2 eligible candidates survive filtering, fusion declines with a labelled, honest "insufficient corpus coverage" proposal rather than degrading to single-paper output. |

### Deterministic pre-LLM candidate selection

Candidate selection is **deterministic and pre-LLM** so the model never sees, and cannot
silently widen, the corpus. Order of operations:

1. Load the corpus manifest defensively (see below).
2. **Asset-class filter.** Keep papers whose `primary_category`/`categories`/`title`/
   `abstract` match a requested asset class via the synonym map. Skip if `asset_classes`
   is empty.
3. **Direction-biased score.** Rank surviving papers by: count of `strategic_direction`
   keyword hits in title+abstract (primary), then a recency tiebreak on `published`
   (newer first — alpha decay favours fresher results), then `arxiv_id` for total
   determinism.
4. **Take top `max_papers`.** If `< 2` remain → decline (honest sentinel, see below).
5. Hand exactly that ordered set to the synthesis prompt. The model may fuse all of them
   or a subset, but **may not introduce any `arxiv_id` not in the candidate set** —
   anti-hallucination is enforced post-parse exactly as the architect drops unknown
   `strategy_id`s.

The selection is intentionally simple and explainable rather than embedding-based: for the
hackathon, a reviewer can read the candidate list and verify the steer was honoured. A
SPECTER2/embedding ranker (per `submodules/KnowledgeBase`) is a clean post-hackathon swap
behind the same deterministic seam.

## Novelty objective and the McLean–Pontiff rationale

The optimisation target is **novelty of the cross-paper combination**, not backtested
performance (which fusion deliberately does not compute — it produces a hypothesis).

**Why novelty is the objective, not historical Sharpe:** McLean & Pontiff (2016, *Journal of
Finance*, "Does Academic Research Destroy Stock Return Predictability?") show that
post-publication, predictor returns decay ~58% — roughly 26% from in-sample/statistical bias
and ~32% from genuine arbitrage as the signal becomes known and traded. The practical
corollary for an agent whose corpus *is* the published literature: **the alpha in any single
published strategy is, by construction, the most-decayed alpha available.** The durable edge
is not re-implementing a known predictor — it is producing *combinations the literature has
not yet published and therefore the market has not yet arbitraged*. Novelty is not a
nice-to-have; under the McLean–Pontiff result it is the only part of the objective that has
not already decayed. This is why fusion's objective function is novelty and its provenance
records *which papers were combined* — the combination is the claim.

**How novelty is operationalised (v1):** the synthesis prompt instructs the model to (a)
state the specific mechanism each source paper contributes, (b) articulate why the
*combination* is non-obvious relative to each paper alone, and (c) self-rate a
`novelty_rationale` (prose, not a fabricated score — consistent with the project's
no-invented-numbers rule; a calibrated novelty metric is a backtest/embedding-layer problem,
explicitly deferred). The deterministic fallback labels itself as non-novel and non-model.

## Fusion provenance

A `FusionProposal` carries provenance designed to be independently checkable:

- `source_arxiv_ids: list[str]` — the N (≥2) papers fused. The verifiable core of the
  claim: anyone can pull these arXiv papers and judge whether the synthesis is faithful
  and the combination is genuinely novel.
- `fusion_reasoning: str` — the model's cross-paper synthesis: what each paper contributes
  and why the combination is non-obvious. Honest about being pre-backtest.
- `novelty_rationale: str` — the model's argument for why this combination is not already
  in the literature. Prose; no fabricated novelty score.
- `model: str` — **the true model, recorded honestly.** This is the field of record for
  provenance.
- `requested_model: str` — the model string we *configured/requested*, kept separately.

### The true-model honesty rule (load-bearing)

Our backend is routed through a GLM-backed, Anthropic-compatible endpoint. The Anthropic
SDK's `messages.create(model=...)` is given our *configured* model string, but the response
object's `response.model` returns the **real model that actually served the request** (e.g.
`glm-4.7`). Recording the configured string as provenance would be a quiet lie in an
audit trail whose entire selling point is honesty.

Therefore:

- `FusionProposal.model` = `response.model` (the served model). This is what flows into any
  future provenance/passport field for a fusion-derived strategy. The `ClaudeBackend`
  exposes a `served_model` populated from the last response; `model_id` continues to return
  the *configured* string (so the seam stays drop-in compatible with the architect, which
  reads `model_id`).
- `FusionProposal.requested_model` = the configured string (`DEFAULT_MODEL` /
  constructor override). Kept for reproducibility — "what we asked for" vs "what answered".
- Offline fallback: `model = requested_model = "canned-fusion-fallback"`, and the proposal
  text states plainly it is not model reasoning. It can never masquerade as a real fusion.

This mirrors, and is stricter than, the architect's `model_id` provenance: the architect
predates the GLM routing nuance; fusion records the served model because a fused-strategy
hypothesis is a stronger claim and deserves the more honest field. If/when fusion is wired
into the passport flow, **`response.model` is the value that must be persisted** — this is
called out explicitly so the integration does not regress to the configured string.

## Corpus manifest contract (read-only, defensive)

Fusion reads the Stream-A corpus manifest. It is **read-only** and **not a hard runtime
dependency**: a missing/empty/corrupt file degrades to an honest "no corpus available"
sentinel, never an exception.

### Frozen schema (one JSON object per line, `data/corpus/manifest.jsonl`)

```json
{"arxiv_id":"2401.12345","title":"...","authors":["..."],
 "primary_category":"q-fin.PM","categories":["q-fin.PM"],
 "published":"2024-01-22","updated":"2024-02-01","abstract":"...",
 "pdf_url":"...","pdf_sha256":"...","pdf_path":"data/corpus/pdfs/2401.12345.pdf",
 "text_path":"data/corpus/text/2401.12345.txt","fetched_at":"2026-05-16T...Z"}
```

### Loader rules

- **Path resolution** (mirrors `strategy_provider.default_provider()` /
  `ARCHIMEDES_STRATEGIES_DIR`):
  1. `ARCHIMEDES_CORPUS_MANIFEST` env var — explicit override (deployment / tests).
  2. First existing candidate among known host + container-plausible layouts
     (`<repo>/data/corpus/manifest.jsonl`, `/app/data/corpus/manifest.jsonl`,
     `/data/corpus/manifest.jsonl`).
- **Defensive line-skipping.** Each line is parsed independently; a line that is blank,
  not JSON, or missing the only two fields fusion strictly needs (`arxiv_id`, and at least
  one of `title`/`abstract`) is logged at debug and skipped. One bad line never aborts the
  load (same posture as `arxiv_pipeline.extract_text`'s per-page tolerance).
- **No file → empty corpus → honest decline.** Absent/empty manifest yields zero
  candidates, which (being `< 2`) produces a labelled "insufficient corpus coverage"
  proposal. No crash, no fabricated papers.
- Extra/unknown fields are ignored (forward-compatible with manifest schema growth).

## The feature flag

`ARCHIMEDES_FUSION_ENABLED`, default **OFF**. Truthy = `{"1","true","yes","on"}`
case-insensitively (the parsing convention shared with the rest of the env surface).
Mechanism mirrors `ARCHIMEDES_STRATEGIES_DIR`: a plain `os.getenv` read, no central
settings module (there is none in this codebase — env overrides are the established
pattern). Flag-off behaviour is a **hard inert path**:

- No `anthropic` import, no LLM call.
- No manifest read.
- Returns a frozen sentinel `FusionProposal` (`status="disabled"`, empty
  `source_arxiv_ids`, self-describing reasoning string). Callers can ship it through a UI
  unchanged and it reads as "feature disabled", never as an empty/failed strategy.

## Output artifact

```text
FusionProposal (frozen dataclass)
  status: "ok" | "disabled" | "insufficient_corpus" | "unparseable"
  brief: FusionBrief                 # echoed back for traceability
  strategy_name: str                 # model-proposed working name
  thesis: str                        # the fused strategy thesis, plain language
  source_arxiv_ids: list[str]        # ≥2 on status="ok"; provenance core
  fusion_reasoning: str              # per-paper contribution + why non-obvious
  novelty_rationale: str             # why not already in the literature
  risk_notes: str                    # incl. the pre-backtest / selection-bias caveat
  model: str                         # response.model — TRUE served model (field of record)
  requested_model: str               # configured/requested model
  created_at: datetime
```

`status` is explicit so a caller never has to infer failure from emptiness — consistent
with the architect returning an empty-but-well-formed proposal rather than raising.

## Honesty rules (carried through from the architect / arxiv pipeline)

- Never invent Sharpe/CAGR/returns. Fusion output is a **hypothesis**; the prompt forbids
  performance numbers and the proposal states empirical validation is pending.
- The fallback is explicitly labelled as a non-model, non-novel placeholder.
- The recorded model is the *served* model (`response.model`), never the configured string.
- Anti-hallucination: any `arxiv_id` the model emits that is not in the deterministically
  selected candidate set is dropped post-parse (architect parity).
- A fusion proposal is never auto-promoted: it is a CANDIDATE-grade hypothesis that must
  still pass the selection-bias gate (DSR / PBO / walk-forward OOS / look-ahead) before it
  could enter the verified library.

## Forward-looking: on-chain, falsifiable novelty (explicitly future)

This section is **vision, not v1 scope**. v1 is grounded entirely in the corpus and stops
at an in-memory `FusionProposal`. The eventual shape:

- **Anchor the fusion claim.** Hash the canonical `FusionProposal` (the
  `strategy-passport-spec.md` canonicalisation rule) and anchor it via the live
  `ReasoningTraceRegistry`, exactly as construction traces are anchored — *without
  modifying that flow now*. The anchor would bind *"this novel combination of these N
  papers was proposed at time T by this served model"*.
- **IPFS-pin the full reasoning.** The full fusion reasoning + the resolved source paper
  metadata pinned to IPFS, with the CID in the on-chain anchor — a public, permanent,
  **falsifiable** novelty claim: anyone can fetch the papers, read the synthesis, and
  argue the combination was in fact already published. Being falsifiable is the point;
  it is the on-chain analogue of the McLean–Pontiff discipline.
- **Novelty decay tracking.** Once anchored, a fusion's novelty is itself a decaying
  quantity (its own publication is the decay trigger). A future loop re-scores anchored
  fusions against newer corpus snapshots and rotates capital away from syntheses the
  literature has caught up to — operationalising "novelty is the moat" as a maintained
  invariant, not a one-shot.

None of this is built here. It is documented so the v1 dataclass and provenance fields are
shaped to make the future hop a small, additive, separately-reviewable step — the same
discipline by which this module is itself additive and flagged.

## Acceptance criteria for v1 (this PR)

- [x] Spec written, prose-first, design rationale explicit.
- [x] `strategy_fusion.py` — flag-gated; flag-off is fully inert (no LLM, no manifest read).
- [x] `FusionBrief` + `FusionProposal` dataclasses; `FusionProposal` frozen.
- [x] Defensive manifest loader; `ARCHIMEDES_CORPUS_MANIFEST` override; no hard file dep.
- [x] Deterministic pre-LLM candidate selection honouring all steering inputs; ≥2 floor.
- [x] Backend seam mirrors the architect (lazy `anthropic`, `extract_json`, fallback).
- [x] Records `response.model` as `model`; keeps `requested_model` separately.
- [x] Mocked-client tests; no network; self-contained fixture manifest.
- [ ] (Future, not this PR) Route wiring, on-chain anchor, IPFS pin, novelty-decay loop.
