# Archimedes — User Stories & The One Spine

> **Status:** Draft, 2026-05-19 (testnet-reality reframe). Spine locked by product-lead decision (Dan). Visual
> items marked 🔍 await Marten/Daniel's UX walkthrough (per issue #39). Read with
> [`design.md`](design.md) and [`demo-script-pitch-deck-outline.md`](demo-script-pitch-deck-outline.md).

## One-line definition

**Archimedes is Linus, specialized to quantitative finance for user profit.**
Linus turns a research corpus into a curated, provenance-anchored knowledge
substrate that compounds over time. Archimedes turns the q-fin literature into a
curated, provenance-anchored **strategy library** that compounds a user's
portfolio over time.

> **Testnet reality (read this first).** Arc has **no mainnet** — it is
> testnet-only (Circle's Arc docs list mainnet as "upcoming"; the public testnet
> "mirrors mainnet behavior, no real assets"). The honest user story is *"try the
> full flow on the Arc public testnet with faucet USDC"* (<https://faucet.circle.com/>,
> 20 USDC / 2h, USDC-is-gas) — **no real funds at risk, by design.** This is a
> strength, not a hedge: it is the correct posture for an Arc-stage project, and
> it means the "Deposit" step is a *testnet deposit*. Real-funds custody, mainnet,
> and the regulatory architecture (off-chain redemptions, preset-strategy / RIA
> posture, exploit alerting) are the **mainnet / business-plan roadmap** — see
> [`competitor-landscape.md`](competitor-landscape.md) § Regulatory.

## Lineage — what we port from Linus / KnowledgeBase, and what we don't

The deep primitive is not "paper notes." It is a **layered-memory + knowledge-graph
architecture with provenance and a quality/curation gate**.

| Linus / KnowledgeBase primitive | Archimedes port |
| --- | --- |
| KnowledgeBase: research corpus → SPECTER2 embeddings → clustering → **similarity graph + knowledge graph** (entity-relation triplets) over curated metadata | The **q-fin research graph**: papers ↔ methods ↔ assets ↔ regimes ↔ strategies. Strategy *fusion* = traversal/synthesis across related papers, not a single-paper port |
| Memory pillar — 5 layers (DEC-0028): session scratchpad (B), **cross-session episodic** (C), **investigation memory** (D), **semantic knowledge** (E) | The user's **strategy library + portfolio history** = episodic memory (C); a single generate→backtest run = investigation memory (D); the q-fin research graph = semantic memory (E) |
| Per-paper **quality scorecard** as a retrieval-time signal (peer-review, preprint, data/code, retraction, citation/age) | The **selection-bias rigor gate (DSR/PBO/walk-forward) + strategy passport** — the q-fin quality scorecard. *This is the curation protocol that makes the library trustworthy.* |
| Provenance/integrity: claim typing, content hashing, append-only audit | **On-chain reasoning traces + content-hashed methodology** — already our differentiator; same primitive |
| Garrison's four obligations: addressability, disambiguation, temporal order, integrity | The properties the library/portfolio memory must satisfy: find a strategy, distinguish near-duplicates, ordered decision history, tamper-evident traces (on-chain) |
| Maestro/Worker discipline | Hosted Claude/GLM plans; the agent executes; humans review |

**Deliberately NOT ported** (explicit product-lead decision): Apple-Silicon /
local-first execution; fine-tuning, LoRA/QLoRA, pmetal; offline data sovereignty
(Kiwix/PMTiles); the electricity-budget framing. **Inference is the GLM API key**
(z.ai Anthropic-compatible). Archimedes is a hosted, single-user web product.

## Additional architectural primitives (beyond the memory pillar)

Linus's orchestration layer (`src/linus/`, Phase 2a largely landed) carries
primitives Archimedes should lift as **patterns** (not local-first machinery):

| Linus primitive (status) | Archimedes port | Value |
| --- | --- | --- |
| **RAG gateway** — fuses SPECTER2 + TF-IDF + KG into one context object; cached, query-logged (Phase 2, thin adapter) | The research-grounding engine: hybrid retrieval over the q-fin corpus is what makes a strategy *grounded*, not merely prompted | ★ highest |
| **Tool registry, MCP-shape** — `@tool` decorator, domain-grouped (`linus.tools`, landed) | `archimedes.research.* / strategy.* / rigor.* / vault.*` as inspectable MCP tools — composable and judge-legible | ★ high |
| **Anthropic-compatible server** + tool-call routing + model-preference fallback (`linus.server`; `/v1/messages` = DEC-0056, outstanding N3) | Direct reference for Archimedes's GLM gateway (already Anthropic-compat); fallback = resilience if GLM drops | ★ high |
| **Agent spawner** — N parallel Workers, scoped prompt + scoped tool allowlist + shared SQLite results, merged to parent (Phase 3, deferred) | **The strategy-fusion engine**: one Worker per paper/method → merge into a novel fused strategy. This *is* the novelty differentiator | ★ high |
| **Sandbox** — allowlist/blocklist per tool call; confirmation-required ops returned for user approval; every call audited (landed) | Safe autonomous USDC moves; "confirm before rebalance" = the human-in-the-loop trust gate | ★ high |
| **Audit log** — append-only JSONL of every model/tool/policy decision; basis for "autonomy graduation" (landed) | Off-chain provenance richer than trace hashes; *"the agent earns autonomy as its audited record grows"* = a strong pitch arc | ★ high |
| **Maestro-side output synthesis** (DEC-0023) — Worker outputs → balanced bullets + prose with citation drill-down | How a generated strategy is presented: synthesized rationale that drills down to source papers = the passport | medium-high |
| **Trust-tier tagging** on every context item | Each strategy input (peer-reviewed vs preprint vs scraped) tagged → feeds the rigor scorecard | medium-high |
| Router metadata (`memory_mode`/`cot_budget`), Skills (`SKILL.md`) | Cost/quality discipline; templated strategy archetypes | defer |

## Linus-MVP prioritization for Archimedes (reverse signal)

Ranked by Archimedes value, to steer what the Linus MVP should include:

- **Tier 1 — make these clean & copyable in the Linus MVP:**
  1. **RAG gateway interface.** Thin in Linus, but the #1 Archimedes primitive —
     the entire "research-grounded" claim rests on hybrid retrieval. A clean
     gateway contract is the single highest-leverage thing to nail.
  2. **Tool registry (MCP-shape).** Already landed (`linus.tools`); the
     architectural backbone Archimedes copies wholesale.
  3. **Anthropic `/v1/messages` + model fallback (N3, outstanding).**
     De-risks Archimedes's GLM gateway directly — worth finishing in the MVP.
- **Tier 2 — pull forward if the Archimedes pitch needs it:**
  4. **Agent spawner.** Deferred to Phase 3 in Linus, but *Phase 1* for
     Archimedes — it is the fusion/novelty engine.
  5. **Sandbox + audit log.** Landed; lift directly for autonomous-USDC safety
     and the provenance / "autonomy graduation" narrative.
- **Tier 3 — defer for the hackathon:** Skills, router intelligence,
  training/fine-tuning, data sovereignty.

**The two divergences that matter for Linus-MVP scoping:** (1) the **RAG
gateway** is "thin / Phase 2" in Linus but Tier-1 for Archimedes — make its
interface a first-class, copyable contract; (2) the **agent spawner** is
"Phase 3 / deferred" in Linus but the engine of Archimedes's novelty pitch — a
minimal version in the MVP pays outsized Archimedes dividends.

## Personas

- **Primary — the single user / allocator.** Has idle USDC, wants
  research-grounded exposure without hand-picking strategies. The product is
  built for exactly one of them at a time (MVP).
- **Evaluation lens — judge-as-operator.** A Stellar/Coinbase/Arc-Circle/Protocol
  Labs judge who reads the repo and clicks the live link like an operator. The
  judge is the primary persona, on rails. Whatever serves the judge serves the
  real user.

## The one spine (this is the whole product story)

```
   describe intent
        │
        ▼
   ① GENERATE      research-grounded strategy from your interests,
                   fused across the q-fin research graph
        │
        ▼
   ② RIGOR-GATE    DSR / PBO / walk-forward — the curation protocol;
                   only what clears it is admitted to your LIVE library
        │
        ▼
   ③ EXECUTE       allocate it into a non-custodial vault (USDC on Arc)
        │
        ▼
   ④ MONITOR       portfolio, results, and the agent's on-chain reasoning
        │
        ▼
   ⑤ EXPLORE       your compounding library of strategies + their passports
```

Mapped to the existing UI surfaces:

| Step | Story | Surface |
| --- | --- | --- |
| ① Generate | "As a user, I describe what I want (e.g. *steady growth, low drawdown, trend-following*) and Archimedes generates a strategy grounded in named q-fin papers." | Strategies → *Strategy Architect* |
| ② Rigor-gate | "I see *why* I should trust it: paper provenance, methodology hash, and the selection-bias scorecard — not a placeholder number." | Strategies passport · Risk Analysis |
| ③ Execute | "I allocate USDC into a vault running it. This is the only step that needs a wallet." | Vault Detail → Deposit |
| ④ Monitor | "I watch holdings, results, regime, and the agent's reasoning trace — verifiable on-chain." | Dashboard · Reasoning · Vault Detail |
| ⑤ Explore | "I browse my growing library of strategies and revisit any passport." | Strategies / library view |

DeFi primitives (Mint/Burn, Liquidity, raw Trade) are **plumbing, not the
product** → group under "Advanced"; they are not on the spine or the demo path.

## Judge happy-path (the ~3-min demo, read-only until deposit)

1. Landing → **Explore** (no wallet).
2. **Strategies** — real paper-grounded strategy; open a passport (provenance + hash + rigor scorecard, **no "est." placeholder**).
3. **Reasoning** — the agent's decisions, traced and on-chain-verifiable.
4. **A Vault** — strategies → allocations → reasoning, tied together.
5. **Risk Analysis** — DSR/PBO/Kelly: the curation protocol made visible.
6. Wallet wall appears **only at Deposit** — the single gated action.

## Scope

**In (the MVP we ship & demo):** the single-user spine end-to-end, one user at a
time, GLM-backed, hosted, **on the Arc public testnet with faucet USDC (no real
funds)**.

**Out (stated vision / roadmap — narrate, do not build):** multi-user accounts;
**a social network of shared strategies & vaults** — users publishing strategies
others can discover, allocate to, and fork. This is the scale story for the
README and demo video: the same curated-library substrate, made social. A clear,
enticing expansion path strengthens the pitch; building it does not fit the
hackathon window.

## Open items to verify (🔍 — owners: Marten / Daniel, per #39)

- 🔍 Is the entire hero path (Strategies→Reasoning→Vault→Risk) traversable
  **read-only with no wallet**, gating only at Deposit?
- 🔍 Do builder affordances (Strategies' *Architect* box, Reasoning's manual
  *publish-trace* form) render to an anonymous visitor? Gate/label for the demo.
- 🔍 No router: do refresh / browser-back / shared deep-links survive mid-journey?
- 🔍 Two strategy surfaces (Strategies vs Marketplace) — confirm which is canonical
  for the spine; the other is removed from the demo path.

## Definition of done

- This spine is the single narrative in README, demo script, and the live app.
- No placeholder ("est.") metrics on the judge path (ties to the rigor-wedge P0).
- 🔍 items resolved by the walkthrough; the canonical strategy surface chosen.
