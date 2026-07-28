# `docs/` — Documentation Index

Navigation aid for everything under `docs/`. Grouped by purpose so you can find the right doc without grep'ing. Last updated 2026-05-25 (Day-14, submission day).

Every doc carries a `> **Status:** …` line in its header so judges/readers can tell at a glance what's shipped, what's spec, what's archived, and what's been filed as a live GitHub issue. The tables below mirror that.

For repo-level setup + operations, start at the **repo root**:

- [`../README.md`](../README.md) — project overview + status + documentation map
- [`../SETUP.md`](../SETUP.md) — prerequisites + 5-step install + platform notes + test suite
- [`../OPERATIONS.md`](runbooks/operations.md) — run the stack + RPC deep-dive + LLM backends + traction + security
- [`../ARC.md`](arc-integration.md) — Arc testnet reference + Circle sponsor alignment
- [`../ARC-OSS-SHOWCASE.md`](archive/agora-2026-05/ARC-OSS-SHOWCASE.md) — Arc OSS Showcase positioning + forkable primitives
- [`../CLAUDE.md`](../CLAUDE.md) — project context for Claude Code sessions

## Product spine (canonical — read these first)

| Doc | What it is |
|---|---|
| [`user-stories.md`](user-stories.md) | The locked product spine. Primary archetype = capable non-expert. Per-page stories. Honesty rules. **Canonical reference for what the product *is*.** |

## Architecture (current shipped state)

| Doc | Status | What it is |
|---|---|---|
| [`design.md`](archive/agora-2026-05/design.md) | superseded for product framing | Original single-vault design. Architecture lineage; the canonical product framing is `user-stories.md`. Component-level shipped state in `chuan-architecture-survey.md`. |
| [`architectural-principles.md`](architectural-principles.md) | shipped | The four primitives (paper-claim binding, reasoning trace, tool-call provenance, selection-bias correction). All four live. |
| [`chuan-architecture-survey.md`](archive/agora-2026-05/chuan-architecture-survey.md) | snapshot — Day-11 | File-by-file survey of `backend/archimedes/` (~89 files). Aggregate gap clusters with t2o2-issue cross-refs. |
| [`corpus-architecture.md`](corpus-architecture.md) | partial | The q-fin corpus end-to-end: 3-layer substrate (seed → DB → artifact), fusion path, wired-vs-not-yet table. |

## Specs — architectural contracts

Durable implementation contracts. Spec-only items are tracked under their respective phase plan.

| Doc | Status | What it is |
|---|---|---|
| [`specs/strategy-passport-spec.md`](specs/strategy-passport-spec.md) | shipped | The strategy passport schema + provenance contract. Live in the UI. |
| [`specs/selection-bias-corrections-spec.md`](specs/selection-bias-corrections-spec.md) | shipped | DSR + PBO + walk-forward OOS + look-ahead audit math + thresholds. 2 Tier-1 strategies pass today. |
| [`specs/strategy-fusion-spec.md`](specs/strategy-fusion-spec.md) | shipped | Multi-paper fusion engine. SPECTER2 + RAG upgrade is the unblocked `#96` follow-on. |
| [`specs/strategy-lifecycle-spec.md`](specs/strategy-lifecycle-spec.md) | shipped (Phase 0) | Generated → Validated → Deployed → Active → Completed/Expired/Rejected. The state machine fusion-evaluator output enters. |
| [`specs/portfolio-constructor-decision-tree.md`](adr/portfolio-constructor-decision-tree.md) | shipped (Phase 0) | Names `portfolio_agent.py` (top-level) + `portfolio_optimizer.py` (math leaf) as canonical; retirement of the other two filed as [#131](https://github.com/a-apin/archimedes-arcadia/issues/131). |
| [`specs/page-roles-spec.md`](specs/page-roles-spec.md) | shipped (Phase 0) | Per-page ownership in the spine — what each page is for + isn't for. Backs the Reasoning restructure + Library deep-link work. |
| [`specs/vault-semantics-spec.md`](specs/vault-semantics-spec.md) | spec-only — Phase 4 | Vault lifecycle + trade-window semantics. Waits on Marten/Chuan alignment. |
| [`specs/generation-streaming-spec.md`](specs/generation-streaming-spec.md) | shipped (Phase 2) | SSE streaming protocol for `/api/generate/*`. Backs the streaming Generate UI. |
| [`specs/kb-integration-spec.md`](specs/kb-integration-spec.md) | partial (Phase 3c) | KB pipeline integration. Skeleton landed; production body waits on Dan's Linus-side iteration. |
| [`specs/ecosystem-design-spec.md`](specs/ecosystem-design-spec.md) | substantially shipped | Day-3 marketplace pivot — 4-layer architecture (Synthetic Protocol + AMM + Vault Factory + Agent-as-a-Service). |
| [`specs/component-interfaces-spec.md`](specs/component-interfaces-spec.md) | shipped (interfaces); ownership softened | Original frozen `I*` Protocol contracts. Interfaces are architecturally correct; ownership has evolved to lead+coverage per CLAUDE.md. |
| [`specs/ipfs-reasoning-traces-design-note.md`](specs/ipfs-reasoning-traces-design-note.md) | design note — not wired | Hash → Pinata CID → on-chain anchor (Rosetta-Alpha pattern). |
| [`specs/commit-reveal-trace-spec.md`](specs/commit-reveal-trace-spec.md) | spec-only — v1.5 | Promotes "trace existed at T" to "trace existed *before* the trade". |

## Specs — phase plans

| Doc | Status | What it is |
|---|---|---|
| [`specs/spine-plus-v2-plan.md`](plans/spine-plus-v2-plan.md) | active — Phases 0–3 shipped + Phase 6 (onboarding tour) merged; Phase 7 all shipped via t2o2; Phases 4 & 5 in-flight | The master plan for the spine-plus-v2 effort. |

## Specs — t2o2 issue specs (all closed and archived)

The five spec files below shipped on `main` between 2026-05-23 and 2026-05-24 and were moved to [`archive/`](archive/) as historical artifacts. They are kept for traceability of intent + acceptance shape that the bot executed against, but are not load-bearing for current architecture.

| Spec file | Status | Issue |
|---|---|---|
| (spec files removed 2026-07-27 — recoverable from git history) | ✓ all closed — shipped `bd6935b`, `e030ee4`, `dc91b43`, `a4a09fb`, `be9260b` | [#128](https://github.com/a-apin/archimedes-arcadia/issues/128), [#129](https://github.com/a-apin/archimedes-arcadia/issues/129), [#130](https://github.com/a-apin/archimedes-arcadia/issues/130), [#131](https://github.com/a-apin/archimedes-arcadia/issues/131), [#132](https://github.com/a-apin/archimedes-arcadia/issues/132) |
| (no file — drafted inline as fast-follow to #128) | ✓ closed — shipped `2f7f871` | [#133](https://github.com/a-apin/archimedes-arcadia/issues/133) |

## Strategy + launch + marketing

| Doc | What it is |
|---|---|
| [`demo-script-pitch-deck-outline.md`](archive/agora-2026-05/demo-script-pitch-deck-outline.md) | 3-min pitch + 2-min demo + Q&A structure; 9-slide deck; honesty rules baked in. |
| [`pitch-talking-points-rigor-track.md`](archive/agora-2026-05/pitch-talking-points-rigor-track.md) | One-page handout for the rigor / provenance / agent track. Supplements the demo script. |
| [`portfolio-advisor-demo-cues.md`](archive/agora-2026-05/portfolio-advisor-demo-cues.md) | 60-second verbatim cue card for the Portfolio Advisor moment inside the live demo. |
| [`claude-design-prompts.md`](archive/agora-2026-05/claude-design-prompts.md) | Paste-ready prompts for [Claude Design](https://claude.ai/design) — logo, slide deck, UI screens, plus explainer diagrams. |
| [`arc-alignment.md`](archive/agora-2026-05/arc-alignment.md) | Arc testnet posture as a strategic strength + Circle Agent Stack opportunity framing. |
| [`traction-logging.md`](archive/agora-2026-05/traction-logging.md) | Operational cheat sheet for `arc-canteen update-product` + `update-traction` (the 30% rubric weight). |

## Benchmarks + diagrams + runbooks

| Path | What it is |
|---|---|
| [`benchmarks/stockbench-results.md`](benchmarks/stockbench-results.md) | Archimedes #15/15 on Chen et al. 2026 StockBench (honest, surfaced not hidden). |
| [`diagrams/strategy-passport-architecture.md`](diagrams/strategy-passport-architecture.md) | Mermaid + reference doc for the passport flow + on-chain anchoring. |
| [`runbooks/arc-testnet-e2e.md`](runbooks/arc-testnet-e2e.md) | End-to-end smoke test runbook (`verify_arc_e2e.py`). Evidence file is generated by `--execute`. |
| [`runbooks/github-security-toggles.md`](runbooks/github-security-toggles.md) | One-time GitHub Settings toggles for Dependabot + secret scanning + push protection. |

## Reference / process

| Doc | What it is |
|---|---|
| [`judging-rubric-assessment.md`](archive/agora-2026-05/judging-rubric-assessment.md) | Day-13 self-assessment against the rubric (Agentic Sophistication + Traction + Circle Tool Usage + Innovation + **Arc OSS Showcase**). |
| [`rigor-methods.md`](rigor-methods.md) | Plain-English summary of the rigor methods (DSR / PBO / Kelly / MVO) that the selection-bias spec implements. Reader-friendly companion. |
| [`anti-features.md`](anti-features.md) | What Archimedes is *not* building, with rationale. Back-pressure document for scope creep. |
| [`infra-setup.md`](archive/agora-2026-05/infra-setup.md) | Deploy + CI/CD + Terraform reference (prod is ECS Fargate). Owner: Dan. |

## Architecture Decision Records ([`adr/`](adr/))

Durable technical decisions captured once, with alternatives + reasoning, so future contributors understand the choice without relitigating.

| ADR | Decision |
|---|---|
| [`adr/backtrader-vs-vectorbt-decision-memo.md`](adr/backtrader-vs-vectorbt-decision-memo.md) | Why backtrader over vectorbt for v1 backtest engine |

[`adr/README.md`](adr/README.md) covers the ADR convention + when to add a new one.

## Historical ([`archive/`](archive/))

Docs that were authoritative at an earlier phase and have since been superseded. Kept for traceability but **not the current shape of the product**. Always prefer the current doc that supersedes it (each archive entry names its replacement in [`archive/README.md`](archive/README.md)).

| Archived doc | Now superseded by |
|---|---|
| [`archive/mvp-scope-memo.md`](archive/mvp-scope-memo.md) | [`user-stories.md`](user-stories.md) (spine) + [`AUDIT_2026-06-14.md`](audits/2026-06-14-full-tree-audit.md) (current scope) |
| [`archive/launch-plan-2026-05-19.md`](archive/launch-plan-2026-05-19.md) | [`AUDIT_2026-06-14.md`](audits/2026-06-14-full-tree-audit.md) |
| [`archive/ui-simplification-proposal-2026-05-20.md`](archive/ui-simplification-proposal-2026-05-20.md) | [`specs/page-roles-spec.md`](specs/page-roles-spec.md) + spine Phases 0–7 shipped per [`specs/spine-plus-v2-plan.md`](plans/spine-plus-v2-plan.md) |
| [`archive/evening-execution-plan-2026-05-24.md`](archive/evening-execution-plan-2026-05-24.md) | Shipped via PRs #220–#241; reality captured in [`archive/sunday-night-handoff-2026-05-24.md`](archive/sunday-night-handoff-2026-05-24.md) and [`AUDIT_2026-06-14.md`](audits/2026-06-14-full-tree-audit.md) |
| [`archive/sunday-night-handoff-2026-05-24.md`](archive/sunday-night-handoff-2026-05-24.md) | [`AUDIT_2026-06-14.md`](audits/2026-06-14-full-tree-audit.md) |
| [`archive/rfb-alignment.md`](archive/rfb-alignment.md) | [`arc-alignment.md`](archive/agora-2026-05/arc-alignment.md) + [`demo-script-pitch-deck-outline.md`](archive/agora-2026-05/demo-script-pitch-deck-outline.md) |
| [`archive/qfin-paper-corpus-seed.md`](archive/qfin-paper-corpus-seed.md) | [`corpus-architecture.md`](corpus-architecture.md) |
| [`archive/agora_project_analysis.md`](archive/agora_project_analysis.md) | [`architectural-principles.md`](architectural-principles.md) + [`specs/selection-bias-corrections-spec.md`](specs/selection-bias-corrections-spec.md) |

### Operational artifacts (archived 2026-05-24)

Same-day execution plans, phase-specific runbooks, and the launch-night operational runbook. Useful for traceability of how the build was sequenced; not load-bearing for product or architecture.

| Archived doc | What it was |
|---|---|
| [`archive/morning-execution-plan-2026-05-24.md`](archive/morning-execution-plan-2026-05-24.md) | Sunday-morning workstream sequencing artifact |
| [`archive/afternoon-execution-plan-2026-05-24.md`](archive/afternoon-execution-plan-2026-05-24.md) | Sunday-afternoon merge-train + subagent research artifact |
| [`archive/launch-execution-plan-2026-05-23.md`](archive/launch-execution-plan-2026-05-23.md) | Day-12 launch sequencing plan (210KB) |
| [`archive/launch-night-operational-runbook.md`](archive/launch-night-operational-runbook.md) | Launch-night ops + rollback procedure |
| [`archive/phase5-execution-runbook.md`](archive/phase5-execution-runbook.md) | Phase-5 execution runbook |

## Research ([`research/`](research/))

Research artifacts that don't fit the spine + architecture + specs hierarchy but are referenced by other docs. Currently the Linus↔Archimedes lineage comparison + the Archimedes→Linus port-backs that came out of the Day-9 cross-repo work.

## Conventions for this folder

- **Every doc has a `> **Status:**` line** in its second-block header. The Day-11 cleanup pass standardized vocabulary: `shipped` · `partial` · `spec-only` · `snapshot — <date>` · `archived` · `filed as #NNN` (for t2o2 issues) · `superseded by …`. Add the most specific status that fits.
- **Cross-references use relative paths** within `docs/` (e.g. `[corpus-architecture.md](corpus-architecture.md)`); root-level docs use the `../` prefix.
- **t2o2 issue specs** carry a status line linking to their GitHub issue. The file is the source-of-truth for the spec body; the issue is the source-of-truth for PR and review activity.
- **Archived docs stay archived** — don't move them back. If something in an archived doc is still load-bearing, fold it into the current canonical doc + leave the archive in place.
- **ADRs are immutable** — capture a decision once, supersede with a new ADR if it changes; don't edit the original.

## How to add a new doc

1. Decide which group it belongs to (Product spine? Architecture? Architectural spec? Phase plan? t2o2 issue? Strategy? Reference? ADR?).
2. Pick a filename (kebab-case, descriptive, no dates). For t2o2 issue specs, suffix `-t2o2-issue.md`.
3. Add a `> **Status:** <vocabulary> · <one-liner>` header.
4. Link from this index (`docs/README.md`) under the right group.
5. Cross-link from related docs in the same group.
6. If it's a t2o2 issue spec: file it as a GitHub issue with `gh issue create … --assignee t2o2` and back-link the issue # in the spec's status block.

If you're unsure where it goes, default to `docs/<your-doc>.md` (top-level docs/) and ask in #standups if a subdirectory is warranted.
