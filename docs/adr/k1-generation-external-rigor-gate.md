# ADR: K=1 generation + externalized rigor gate

> **Audience:** Archimedes team (decision owner: Dan, architecture; implementation: Önder + Daniel R.)
> **Status:** **Adopted.** Codified 2026-05-23 after a Linus-Maestro architecture audit; live in the generation + passport flow.
> **Question being decided:** How many candidate strategies should the generation agent emit per Generate call — K=1 (one winner + considered alternatives), or K=many (parallel candidates all gated together)?
> **Related:** [`docs/architectural-principles.md` § 5](../architectural-principles.md), [`docs/user-stories.md`](../user-stories.md), `backend/archimedes/agents/generation_pipeline.py`, `backend/archimedes/agents/strategy_fusion.py`.

## TL;DR

**The generation agent emits ONE winner per Generate call (plus a short list of considered-rejects with rationale); the rigor gate runs EXTERNALLY and is what the user reviews on the strategy passport before deploy.** Two reasons: **(1) hosted-LLM budget economics** — K=many multiplies LLM + backtest cost per Generate; K=1 keeps the demo affordable and responsive for the single-user MVP. **(2) Externally-verifiable provenance** — shifting rigor enforcement from runtime types to externally-verifiable hashes (`methodology_hash` + `consulted_paper_hashes` anchored on-chain via `ReasoningTraceRegistry` / `StrategyRegistry`) is a strict upgrade: anyone can recompute and verify; the agent cannot lie about what it consulted.

## Context

The generation pipeline runs inside a hosted LLM with a per-token cost. An earlier design considered **K=many internal generation**: emit N candidates, run the rigor gate (DSR + PBO + walk-forward OOS + look-ahead audit) over all N, rank, and surface the top K. The appeal is *more diversity*, but the economics are punishing — N synthesis rounds + N backtest runs + an N-way PBO matrix per Generate, multiplying both cost and latency. For a demo whose Traction score is computed from `arc-canteen update-product` telemetry, K=many makes every user session several times more expensive and slower.

The provenance argument is the stronger one. K=1 + an external gate means the user sees exactly what the agent generated, then reviews the rigor verdict before deploying. The methodology + paper-corpus hashes anchor on-chain only when rigor passes — they become verifiable facts, not labels claimed before earned.

## Decision

**Emit K=1 (one winner per Generate) plus a considered-alternatives panel.**

1. **One primary winner** — the highest-fitness strategy for the user brief + current regime (papers list, methodology, thesis, asset universe, regime tag).
2. **Considered-rejects panel** — 2–5 alternatives the agent evaluated but rejected, each with a one-sentence rationale, persisted in the reasoning trace (not asserted on the passport).
3. **The rigor gate is external** — the user reviews the winner on the strategy passport (methodology, papers, backtest, rigor verdict) before the Deploy button enables.

The user-facing flow: **Generate → winner + alternatives panel → review passport (rigor verdict) → Deploy** (or reject + regenerate). On-chain anchoring is per-winning-strategy only (the `StrategyRegistry` stores only passed-rigor passports); failed-rigor candidates persist in `strategy_proposals` (episodic memory) but are never anchored — keeping the registry meaningful and gas bounded.

## Consequences

### Positive
- **Predictable cost + latency.** One synthesis round per Generate, not K. The considered-rejects panel preserves the "what else was on the table" signal without paying for parallel deep generation.
- **Verifiable provenance out of the box.** K=1 + external gate + on-chain anchoring of passed-rigor strategies makes the full decision trail auditable without trusting the platform's internal process.
- **Budget aligns with the single-user MVP** and scales naturally; K=many batching is a v2 decision that needs no core change.

### Negative / costs we accept
- **Users can't see rigor verdicts on the alternatives** (rationales only). Mitigation: a post-MVP "run rigor on this alternative" button can evaluate a selected reject on demand.
- **A single bad winner can look bad** if the user doesn't notice until the passport review. Mitigation: the considered-alternatives panel + cheap regeneration (one synthesis round).

## Alternatives considered
- **K=many internal gating (rank by Sharpe/PBO) — rejected** for cost (N× LLM + backtest + N-way PBO) and for burying the "we evaluated K, this won" decision inside the agent rather than in verifiable on-chain anchors.
- **K=1 with asynchronous gating (poll for verdict) — rejected** for UX: the linear "review verdict before Deploy" story is more credible than "wait, then review."

## Ratification

Adopted and live (generation pipeline → rigor evaluator → passport review). This ADR formalizes the rationale after the fact. Episodic compounding builds on top: every fusion proposal + rigor verdict + user-reject is content-hashed into `strategy_proposals` so the library demonstrably compounds rather than restarting per session.
