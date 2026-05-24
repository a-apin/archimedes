# Arc OSS Showcase — Archimedes Submission

> **Status:** Day-10 (2026-05-22). Submission target: [Arc OSS Showcase](https://arc-oss.thecanteenapp.com/) — Canteen's parallel competition for open-source codebases that other Arc builders can fork.
> **License:** [Unlicense](LICENSE) — full public-domain dedication. Use, modify, distribute freely, no warranty, no attribution required.
> **Repo:** <https://github.com/a-apin/archimedes-arcadia>
> **Live testnet deploy:** <http://13.40.112.220>

## Why Archimedes belongs in the Arc OSS Showcase

The showcase rewards codebases that **expose useful primitives other Arc builders can adopt** with **clear documentation explaining functionality and usage**. Archimedes ships **seven distinct primitives**, each with a dedicated spec or walkthrough doc, each forkable as a unit. Together they form the substrate any Arc app that wants research-grounded + selection-bias-corrected + provenance-anchored AI-decision-making would need.

We hit the showcase criteria explicitly:

- ✅ **Fully open** under the Unlicense (more permissive than MIT — no attribution required)
- ✅ **Stays open during and after the event** — this is the only license we've ever shipped
- ✅ **Exposes useful primitives** — seven distinct ones, listed below
- ✅ **Documentation explains functionality and usage** — every primitive has a per-primitive spec doc; project-level docs in [`README.md`](README.md), [`SETUP.md`](SETUP.md), [`OPERATIONS.md`](OPERATIONS.md), [`ARC.md`](ARC.md), and [`docs/`](docs/README.md)
- ✅ **Standalone, fork-friendly modules** — each primitive lives in its own file(s) with clearly named imports and no hidden coupling

## Agent ↔ user interaction model

**The custody boundary.** Funds sit in the user's ERC-4626 vault contract; the agent's on-chain capability is bounded to `rebalance(tokens, weights_bps)` — never withdraw, never change allocations, never change owner. The user signs all 4 binding deployment transactions; the agent operates within those rails autonomously after deployment. The sequence:

```mermaid
sequenceDiagram
  participant U as User (wallet)
  participant SG as Strategy Generation Agent
  participant PC as Portfolio Construction Agent
  participant V as Vault contract (Arc)
  participant LE as Live Execution Agent (agent_runner)
  participant AMM as AMM + PriceOracle (Arc)
  participant R as ReasoningTraceRegistry (Arc)

  U->>SG: Describe brief on /generate (intent, risk, asset classes)
  SG->>SG: Paper retrieval + market context + synthesis + rigor gate
  SG-->>U: StrategyPassport (paper anchors, DSR/PBO, OOS) on /strategy/:id
  Note over U,SG: No funds moved. Agent has zero trade authority at this point.

  U->>PC: Click 'Deploy as Vault' (CreateVaultModal)
  PC->>PC: Asset selection + Kelly sizing + stress test → vault proposal
  PC-->>U: Vault proposal (target weights, projected behavior, fee model)
  U->>V: Sign #1 vault.create(strategy_id) via wallet (CreateVaultModal)
  U->>V: Sign #2 USDC.approve(vault, amount) (DepositFlow stepper)
  U->>V: Sign #3 vault.deposit(amount, receiver) (DepositFlow stepper)
  U->>V: Sign #4 vault.setTargetAllocations(tokens, weights_bps) (DepositFlow stepper)
  Note over U,V: User has now FUNDED + CONFIGURED the vault. Agent gains *rebalance authority only*.

  loop Every agent tick (default 60s; configurable per strategy)
    LE->>V: Read current vault state (NAV, current weights, USDC balance)
    LE->>AMM: Read oracle prices + AMM pool liquidity
    LE->>LE: Signal evaluation (strategy DSL) → drift calc → cost-benefit
    alt rebalance triggered
      LE->>V: Submit rebalance tx (Circle signer; agent has approved-router role)
      V->>AMM: Execute swap(s) per target weights
      LE->>R: Publish decision trace (canonical hash + paper anchors + reasoning)
    else hold (no profitable rebalance)
      LE->>R: Publish 'hold' trace (auditable inactivity)
    end
  end

  U->>R: Click 'Verify on-chain' on /reasoning?trace_id=X
  R-->>U: VERIFIED ✓ (hash matches anchor) + arcscan tx link
  U->>V: Withdraw (vault.withdraw at any time; agent cannot block)
```

This is the technical claim behind "non-custodial in the strong sense" — the worst the agent can do is rebalance within the user's stated risk envelope. The user retains exit authority at all times.

## The seven forkable primitives

### 1. Strategy Passport schema + validation

A passport-aware data model + provenance binding for AI-generated strategies. Every strategy carries its source paper (arXiv ID), methodology hash, curator signature, paper-claim deltas, and on-chain registration tx. The opposite of "trust me bro" AI claims.

| Where it lives | What it is |
|---|---|
| [`backend/archimedes/models/strategy.py`](backend/archimedes/models/strategy.py) | `Strategy` dataclass (173 lines) — passport fields, status enum, signal definition |
| [`backend/archimedes/services/strategy_provider.py`](backend/archimedes/services/strategy_provider.py) | `LocalStrategyProvider` (494 lines) — AST-parses `analytics-engine/strategies/*.py` to extract passport metadata, computes methodology hash + strategy id |
| [`docs/specs/strategy-passport-spec.md`](docs/specs/strategy-passport-spec.md) | The full spec: required fields, semantics, integration with on-chain anchoring |

**How to fork:** copy the `Strategy` dataclass + the AST-parse pattern + the methodology hash convention; point at your own strategies directory. The Protocol contract is in [`backend/archimedes/interfaces/strategy.py`](backend/archimedes/interfaces/strategy.py).

**Who benefits:** any Arc app publishing AI-generated strategies, decisions, or recommendations — the passport pattern works for vault strategies, trading agents, prediction-market positions, anything where "where did this come from?" matters.

### 2. Selection-bias rigor gate (DSR + PBO + walk-forward OOS + look-ahead audit)

Four-control admission gate that prevents in-sample-overfit strategies from being promoted to live. Implements the textbook multiple-testing corrections that quant academics have demanded for a decade but no other AI-portfolio submission applies.

| Where it lives | What it is |
|---|---|
| [`backend/archimedes/services/rigor_evaluator.py`](backend/archimedes/services/rigor_evaluator.py) | DSR (Bailey & López de Prado 2014), PBO via CSCV, walk-forward OOS Sharpe, Kelly fraction, Sharpe CI — 348 lines |
| [`backend/archimedes/services/selection_bias.py`](backend/archimedes/services/selection_bias.py) | The earlier `RigorGateResult` + look-ahead audit (AST-based static analysis) — 534 lines |
| [`backend/archimedes/models/backtest.py`](backend/archimedes/models/backtest.py) | `BacktestResult` dataclass with the full selection-bias contract + `passes_validation` / `passes_rigor_gate` properties |
| [`docs/specs/selection-bias-corrections-spec.md`](docs/specs/selection-bias-corrections-spec.md) | Math + thresholds + spec sanity cases |
| [`docs/rigor-methods.md`](docs/rigor-methods.md) | Plain-English companion |

**How to fork:** the math implementations are pure numpy with no Archimedes-specific assumptions. Import `compute_dsr`, `compute_pbo`, `compute_oos_sharpe`, etc. directly. Or fork the whole `BacktestResult` dataclass for an end-to-end-typed rigor contract.

**Who benefits:** any AI-decision system where the cost of acting on a curve-fit is high — DeFi yield optimizers, prediction-market agents, copy-trading platforms.

### 3. On-chain reasoning trace anchoring

`keccak256` hashing of agent reasoning traces + on-chain anchor via a dedicated registry contract. Anyone can recompute the hash from the off-chain trace and prove the trace existed at the recorded block time.

| Where it lives | What it is |
|---|---|
| [`backend/archimedes/chain/trace_publisher.py`](backend/archimedes/chain/trace_publisher.py) | `TracePublisher` — Implements `ITracePublisher`. Publishes keccak256 hashes to `ReasoningTraceRegistry` on Arc. 196 lines. |
| [`contracts/src/ReasoningTraceRegistry.sol`](contracts/src/ReasoningTraceRegistry.sol) | The on-chain anchor contract (deployed on Arc testnet) |
| [`backend/archimedes/models/trace.py`](backend/archimedes/models/trace.py) | `ReasoningTrace` dataclass + canonical-JSON hashing convention |
| [`docs/specs/ipfs-reasoning-traces-design-note.md`](docs/specs/ipfs-reasoning-traces-design-note.md) | The IPFS pinning extension (Hash → Pinata CID → on-chain anchor; Rosetta-Alpha pattern) |
| [`docs/specs/commit-reveal-trace-spec.md`](docs/specs/commit-reveal-trace-spec.md) | v1.5 upgrade: commit-before-trade / reveal-after-trade for proven causal ordering |

**How to fork:** the `TracePublisher` + `ReasoningTraceRegistry.sol` combo is the minimal viable on-chain provenance primitive. Drop in the contract, instantiate the publisher with your own trace shape, and you're publishing.

**Who benefits:** any Arc agent product where users need to audit *why* the agent did what it did — trading agents, governance bots, predictive-maintenance agents, on-chain RPA.

### 4. DB-backed q-fin corpus substrate (paper-grounded AI)

A Postgres-canonical 10,000-paper q-fin corpus with idempotent startup seed, live arXiv intake, and a persistent named volume reserved for the heavy KB-pipeline artifact (embeddings + clusters + KG). DB-first read path; file fallback. Loud degradation when components are missing — never silent.

| Where it lives | What it is |
|---|---|
| [`backend/archimedes/services/corpus_service.py`](backend/archimedes/services/corpus_service.py) | DB seed, arXiv intake, DB-first reads — 295 lines |
| [`backend/archimedes/models/corpus_store.py`](backend/archimedes/models/corpus_store.py) | `PaperRecord` + `CorpusMetaRecord` SQLAlchemy models — 79 lines |
| [`scripts/bulk_ingest_arxiv.py`](scripts/bulk_ingest_arxiv.py) | Bulk arXiv ingest with exponential backoff for 429s |
| [`docs/corpus-architecture.md`](docs/corpus-architecture.md) | The 3-layer substrate walkthrough end-to-end (seed → DB → artifact); wired-vs-not-yet table |

**How to fork:** swap the arXiv categories for your corpus's source(s); reuse the seed pattern + the DB-first read path + the loud-degradation discipline. The corpus shape (papers + meta) generalizes to any "we have a curated knowledge base feeding the LLM" pattern.

**Who benefits:** any AI app whose decisions need to be grounded in a domain knowledge base (medical AI grounded in PubMed, legal AI grounded in case law, code-generation AI grounded in a private codebase).

### 5. `LLM_*` provider-agnostic backend factory

One env-var (`LLM_PROVIDER`) switches between Anthropic, Anthropic-compatible (z.ai/GLM), OpenAI, and Ollama. Falls back to a `CannedBackend` when no credentials are present — **loud degradation**, never silent. Back-compat with legacy `ANTHROPIC_*` env vars (deprecated, WARN logged).

| Where it lives | What it is |
|---|---|
| [`backend/archimedes/services/llm_backend.py`](backend/archimedes/services/llm_backend.py) | The factory + four backend classes + canned fallback — 307 lines |

**How to fork:** drop in the file; set `LLM_PROVIDER` + provider credentials in your `.env`; call `make_llm_backend()`. Zero coupling to Archimedes-specific shape.

**Who benefits:** any AI app that wants to support multiple LLM providers (BYOK + free-tier + local-offline) without rewriting per provider.

### 6. 3-input fusion engine

Generates novel strategies by fusing three inputs: user brief + live market regime + research corpus → strategy spec with paper citations. Async generation jobs via `POST /api/strategies/generate`. Feature-flagged; falls back gracefully if disabled.

| Where it lives | What it is |
|---|---|
| [`backend/archimedes/services/strategy_fusion.py`](backend/archimedes/services/strategy_fusion.py) | `FusionBrief`, `CorpusPaper`, `load_corpus()`, the LLM prompt build — 650 lines |
| [`docs/specs/strategy-fusion-spec.md`](docs/specs/strategy-fusion-spec.md) | The spec: feature-flagged shape, novelty rationale, persistence contract |

**How to fork:** the 3-input fusion shape generalizes to any "user intent × live context × knowledge base → AI proposal" pattern. Replace the corpus + market regime with your equivalents.

**Who benefits:** any AI app where the goal is *novel synthesis*, not retrieval — research-grounded code generation, hypothesis generation in any scientific domain, creative writing grounded in a style corpus.

### 7. Circle-signer pattern (Developer-Controlled Wallets, no raw keys)

Replaces raw private-key signing with Circle's managed-wallet REST API. Submits contract executions, polls until terminal state. Production-grade error handling (HTTP retries, polling timeouts, terminal-state machine).

| Where it lives | What it is |
|---|---|
| [`backend/archimedes/chain/circle_signer.py`](backend/archimedes/chain/circle_signer.py) | `CircleSigner` — entity-secret encryption, REST submission, status polling — 246 lines |
| [`backend/archimedes/chain/oracle_updater.py`](backend/archimedes/chain/oracle_updater.py) | The same pattern applied to oracle price pushes — 303 lines |

**How to fork:** drop in `CircleSigner` + your Circle API credentials in env. Calls become `await signer.execute_contract(...)` and you're done. Avoids the operational risk of holding raw private keys in production.

**Who benefits:** any Arc app whose backend needs to sign transactions without managing private keys — agent products, automated rebalancing services, on-chain orchestration platforms.

## Why we should be a top contender

Beyond just *having* primitives:

1. **All seven primitives are documented, not just shipped.** Each links to a spec or walkthrough doc. A forker can implement against the spec without reading the full source.
2. **The docs are kept current.** This isn't a post-hoc add — the docs were maintained through every shipped commit. See [`docs/README.md`](docs/README.md) for the documentation map.
3. **The primitives compose.** Strategy Passport + Rigor Gate + Trace Anchor form a complete provenance chain for any AI-decision product. You can fork them individually OR as a stack.
4. **The codebase is fully reviewed at the architecture level.** [`docs/chuan-architecture-survey.md`](docs/chuan-architecture-survey.md) walks every file in `backend/archimedes/` with author signal + gap notes — a forker can see exactly what's load-bearing vs scaffolded.
5. **License is the most permissive possible.** Unlicense is more permissive than MIT, BSD, or Apache — no attribution, no notice file required, no warranty disclaimer to copy. Forkers don't need to think about it.
6. **The substrate is real, not aspirational.** 302 backend tests + 16 analytics-engine tests pass. Live testnet deploy. 10 contracts on Arc. 22 years of real SPY backtest data. 2 Tier-1 strategies that actually pass the rigor gate.

## What we add beyond the existing Arc reference implementations

The Canteen showcase landing page calls out the [`circlefin/arc-*` repos](https://github.com/circlefin) (`arc-commerce`, `arc-p2p-payments`, `arc-escrow`, `arc-multichain-wallet`, `arc-fintech`) as reference implementations. Those cover **payments, escrow, multi-chain wallet, commerce** — transaction-flow primitives.

Archimedes adds a different layer: **AI-decision provenance + research-grounding + selection-bias rigor**. The Arc reference implementations are the financial *plumbing*; Archimedes' primitives are the AI-decision *substrate* that sits on top of that plumbing. They compose; they don't overlap.

If you're building an Arc app that needs AI-decision-making — agent products, autonomous rebalancing, research-grounded recommendations — Archimedes is your fork-from substrate.

## How to submit

Two channels per the showcase landing page:

1. **Google Form** — <https://forms.gle/ok3Gr9zhmHnApvK48> (the team-review draft of the answers lives in [`ARC-OSS-FORM-DRAFT.md`](ARC-OSS-FORM-DRAFT.md))
2. **ARC-cli** — `arc-canteen update-product "ArcOSS: <message>"` (the `"ArcOSS:"` prefix is the trigger)

## Related docs

- [`README.md`](README.md) — project overview
- [`docs/README.md`](docs/README.md) — documentation map
- [`ARC-OSS-FORM-DRAFT.md`](ARC-OSS-FORM-DRAFT.md) — team-review draft of the Google Form answers
- [`docs/judging-rubric-assessment.md`](docs/judging-rubric-assessment.md) — Day-10 rubric self-assessment (includes Arc OSS Showcase as a dimension)
