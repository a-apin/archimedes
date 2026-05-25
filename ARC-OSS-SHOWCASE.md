# Arc OSS Showcase — Archimedes Submission

> **Status:** Day-14 refresh (2026-05-25, submission day). Submission target: [Arc OSS Showcase](https://arc-oss.thecanteenapp.com/) — Canteen's parallel competition for open-source codebases that other Arc builders can fork.
> **License:** [Unlicense](LICENSE) — full public-domain dedication. Use, modify, distribute freely, no warranty, no attribution required.
> **Repo:** <https://github.com/a-apin/archimedes-arcadia>
> **Live testnet deploy:** <https://archimedes-arc.app/>

## Why Archimedes belongs in the Arc OSS Showcase

The showcase rewards codebases that **expose useful primitives other Arc builders can adopt** with **clear documentation explaining functionality and usage**. Archimedes ships **twelve distinct primitives**, each with a dedicated spec or walkthrough doc, each forkable as a unit. Together they form the substrate any Arc app that wants research-grounded + selection-bias-corrected + provenance-anchored AI-decision-making would need.

We hit the showcase criteria explicitly:

- ✅ **Fully open** under the Unlicense (more permissive than MIT — no attribution required)
- ✅ **Stays open during and after the event** — this is the only license we've ever shipped
- ✅ **Exposes useful primitives** — twelve distinct ones, listed below
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

## The twelve forkable primitives

> The original seven below shipped Day 4 → Day 9. **Primitives 8–12** were added 2026-05-24 as part of the Day-12 ship train and are the more recent forks. Each is independently consumable: pick what you need.

### 1. Strategy Passport schema + validation

A passport-aware data model + provenance binding for AI-generated strategies. Every strategy carries its source paper(s) (arXiv ID), methodology hash, curator signature, paper-claim deltas, and on-chain registration tx. The opposite of "trust me bro" AI claims.

| Where it lives | What it is |
|---|---|
| [`backend/archimedes/models/strategy.py`](backend/archimedes/models/strategy.py) | `Strategy` dataclass — passport fields, status enum, signal definition |
| [`backend/archimedes/models/strategy_passport_record.py`](backend/archimedes/models/strategy_passport_record.py) | `StrategyPassportRecord` SQLAlchemy ORM + `passport_paper_refs` FK table — the Postgres-canonical passport store |
| [`backend/archimedes/services/passport_loader.py`](backend/archimedes/services/passport_loader.py) | `ingest_passport()` + `list_passports()` + `get_passport()` — write/read path used by curated seed + generation pipeline |
| [`backend/archimedes/services/strategy_provider.py`](backend/archimedes/services/strategy_provider.py) | `LocalStrategyProvider` — AST-parses `analytics-engine/strategies/*.py` to extract passport metadata, computes methodology hash + strategy id; syncs to the unified table at startup |
| [`docs/specs/strategy-passport-spec.md`](docs/specs/strategy-passport-spec.md) | The full spec: required fields, semantics, integration with on-chain anchoring |

**How to fork:** copy the `Strategy` dataclass + `StrategyPassportRecord` ORM + the AST-parse pattern + the methodology hash convention; point the loader at your own strategies directory. The Protocol contract is in [`backend/archimedes/interfaces/strategy.py`](backend/archimedes/interfaces/strategy.py).

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

`keccak256` hashing of agent reasoning traces + on-chain anchor via a dedicated registry contract. Anyone can recompute the hash from the off-chain trace and prove the trace existed at the recorded block time. **Live proof:** the autonomous agent has been writing rebalance traces against the deployed contract right now — `curl https://archimedes-arc.app/api/traces/?limit=10` returns real `arc_tx_hash` values verifiable on `testnet.arcscan.app`.

| Where it lives | What it is |
|---|---|
| [`backend/archimedes/chain/trace_publisher.py`](backend/archimedes/chain/trace_publisher.py) | `TracePublisher` — implements `ITracePublisher`. Publishes `keccak256` hashes to `ReasoningTraceRegistry` on Arc. Includes `get_trace_by_tx_hash` for O(1) verify (single `eth_getTransactionReceipt` + `TracePublished` log decode). |
| [`contracts/src/ReasoningTraceRegistry.sol`](contracts/src/ReasoningTraceRegistry.sol) | The on-chain anchor contract (deployed on Arc testnet) |
| [`backend/archimedes/models/trace.py`](backend/archimedes/models/trace.py) | `ReasoningTrace` dataclass + canonical-JSON hashing convention |
| [`backend/archimedes/api/traces_routes.py`](backend/archimedes/api/traces_routes.py) | `GET /api/traces/{trace_id}/verify` — server-side recomputes the keccak256 + compares to the on-chain `TracePublished` event from the cached tx receipt |
| [`docs/specs/ipfs-reasoning-traces-design-note.md`](docs/specs/ipfs-reasoning-traces-design-note.md) | The IPFS pinning extension (Hash → Pinata CID → on-chain anchor; Rosetta-Alpha pattern) |
| [`docs/specs/commit-reveal-trace-spec.md`](docs/specs/commit-reveal-trace-spec.md) | v1.5 upgrade: commit-before-trade / reveal-after-trade for proven causal ordering |

**How to fork:** the `TracePublisher` + `ReasoningTraceRegistry.sol` combo is the minimal viable on-chain provenance primitive. Drop in the contract, instantiate the publisher with your own trace shape, and you're publishing. The O(1) verify path in `traces_routes.py` is the user-facing trust affordance — copy the receipt-decode pattern for any anchor-then-verify flow.

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

### 8. Xia 2026 named-protocol implementation (Outcome Embargo + Time-Aware Retrieval + Hierarchy of Truth + Source Tracking + V_check)

Implements the five protocols Xia et al. 2026 formalize as the prerequisites for an R3-reproducible trading-agent system. **Every protocol is an enforced mechanism, not advisory guidance.**

| Where it lives | What it is |
|---|---|
| [`backend/archimedes/services/embargo_filter.py`](backend/archimedes/services/embargo_filter.py) | Outcome Embargo — papers published after the decision timestamp are filtered out of retrieval. |
| [`backend/archimedes/services/time_aware_retrieval.py`](backend/archimedes/services/time_aware_retrieval.py) | Time-Aware Retrieval — SPECTER2 similarity scores decay exponentially with paper age; decay rate scales with regime volatility. |
| [`backend/archimedes/chain/v_check.py`](backend/archimedes/chain/v_check.py) | `V_check` on-chain validator — rejects agent actions that violate deterministic constraints regardless of agent confidence. |
| [`backend/archimedes/services/source_tracker.py`](backend/archimedes/services/source_tracker.py) | Source Tracking — every trace records `consulted_paper_hashes` (sorted `arxiv_id:content_hash` list), which is part of the canonical trace hash anchored on-chain. |
| [`docs/specs/xia-2026-protocols.md`](docs/specs/xia-2026-protocols.md) | Full reference — maps each protocol to the section of the Xia paper it implements. |

**How to fork:** drop the four modules into any retrieval-augmented agent that consumes time-stamped sources. The Hierarchy of Truth is enforced structurally (curated academic literature > narrative > social), so adopt the curation rule too.

**Who benefits:** any LLM-agent product that retrieves from a corpus and emits actions audited against a benchmark. The protocols close the "Oracle Fallacy" and "Provenance Loss" failure modes Xia identifies in 15/19 surveyed studies.

### 9. StockBench harness adapter

A clean adapter that wraps Archimedes' real `PortfolioAgent.propose_portfolio` against the [StockBench](https://arxiv.org/abs/2510.02209) (Chen et al. 2026) closed-loop benchmark protocol. Calls the live LLM agent on a weekly cadence (every 5 trading days × 3 seeds ≈ 36 LLM calls per full eval) — defensible per-tick cost vs. the unbounded 246-call-per-seed alternative. V_check enforced on every decision; momentum fallback if the agent is unavailable. Emits Sortino, return, and max-drawdown metrics comparable to the 14 published baselines.

| Where it lives | What it is |
|---|---|
| [`backend/archimedes/evaluation/stockbench/adapter.py`](backend/archimedes/evaluation/stockbench/adapter.py) | The adapter — wires Archimedes to the StockBench protocol surface. Real agent calls (not simulated momentum) since PR #311. |
| [`backend/archimedes/evaluation/stockbench/__main__.py`](backend/archimedes/evaluation/stockbench/__main__.py) | `python -m archimedes.evaluation.stockbench` — one-command harness run. |
| [`docs/benchmarks/stockbench-results.md`](docs/benchmarks/stockbench-results.md) | Our published result + methodology so a forker can re-derive. |

**How to fork:** point the adapter at your agent's `propose` interface. Replaces 200 lines of brittle "wrap my agent in their loop" plumbing with a tested seam. **Reproducibility-grade evidence:** when you ship a number, it's comparable to Chen et al. 2026's published baseline table — not a vibes claim.

**Who benefits:** any trading-agent project that wants to surface an honest performance number against a contamination-free benchmark instead of a cherry-picked backtest. The honest "we underperformed passive in this window" framing is itself a Tier-1 signal — Xia et al. document that *all* LLM agents underperform passive in many windows; pretending otherwise loses credibility.

### 10. paper-qa semantic-retrieval wrap (defense-in-depth ranker behind fusion.select_candidates)

Wraps the Apache-2.0 [paper-qa](https://github.com/Future-House/paper-qa) library as a second-pass semantic ranker behind keyword filtering in the fusion candidate selection step. Uses local `sentence-transformers` embeddings (no external API calls). Falls back gracefully to keyword-only when deps are missing.

| Where it lives | What it is |
|---|---|
| [`backend/archimedes/services/paper_rag.py`](backend/archimedes/services/paper_rag.py) | The wrap — `SemanticReranker` + the seam into `strategy_fusion.select_candidates()`. |
| `FUSION_SEMANTIC_RETRIEVAL` env var | Feature flag — set to `true` to enable the second-pass ranker. |

**How to fork:** drop the file into any RAG project that needs a defense-in-depth ranker. The fallback pattern (degrade loudly + keep shipping) is the part most "RAG demos" skip and the part you actually want in production.

**Who benefits:** any paper-grounded AI product. Closes the failure mode where keyword filtering returns plausible-but-irrelevant matches because the keyword surface is sparse.

### 11. Strategy episodic memory (`strategy_proposals` table — substrate that compounds)

A Postgres table that persists every fusion proposal + every rigor-gate verdict + every user-reject, content-hashed. Makes the "library compounds" pitch claim demonstrable rather than aspirational. The Considered Alternatives panel on every Strategy Passport reads from this table — judges can see what was rejected and why.

| Where it lives | What it is |
|---|---|
| [`backend/archimedes/services/strategy_memory.py`](backend/archimedes/services/strategy_memory.py) | The episodic-memory wrap (72 statements, 79% covered) — write-side. |
| `/api/strategies/proposals` | Read-side endpoint — the Considered Alternatives panel reads from here. |
| `strategy_proposals` table | The Postgres schema; one row per proposed strategy with status `(WINNER, REJECTED_RIGOR, REJECTED_USER)` + rationale. |

**How to fork:** mirror the table shape + the write hook in your generation pipeline. Lifts you from "agent restarts cold every session" to "library compounds across sessions" without changing the user-facing surface beyond adding the Considered Alternatives panel.

**Who benefits:** any agent product whose pitch involves "the system gets smarter over time" but whose code restarts cold every call. This is the smallest scope of episodic memory that makes the compounding claim defensible.

### 12. Security pillar — first-class, fork-grade

Treats security as an architectural pillar the same way the statistical pipeline is. Zero-trust posture, secrets out of `.env`, IAM-scoped resource access, HTTPS-everywhere, security headers + CORS + rate limits, dependency scanning, secret-leak detection, user-data minimization with KMS encryption.

| Where it lives | What it is |
|---|---|
| [`backend/archimedes/services/email_crypto.py`](backend/archimedes/services/email_crypto.py) | Fernet at-rest encryption for any user PII at rest. |
| [`backend/archimedes/services/log_scrubber.py`](backend/archimedes/services/log_scrubber.py) | PII redaction wrapper — all profile-touching loggers must route through it. |
| [`backend/archimedes/api/limiter.py`](backend/archimedes/api/limiter.py) + `slowapi` decorators across `/api/generate/start`, `/api/user/profile`, public GETs | Rate limiting (5/min, 1/min, 60/min respectively) — Redis-backed. |
| [`backend/archimedes/main.py`](backend/archimedes/main.py) CORS middleware | Locked to `PUBLIC_DOMAIN` only (no wildcard); preflight cache 600s. |
| [`nginx/nginx.conf`](nginx/nginx.conf) | HSTS + CSP + X-Frame-Options + X-Content-Type-Options + Referrer-Policy + Permissions-Policy. Plus `proxy_read_timeout 30s` on `/api/*` so a hung backend fails fast with 504 instead of stalling the connection. |
| [`infra/iam/archimedes-backend-policy.json`](infra/iam/archimedes-backend-policy.json) | IAM least-privilege for backend → S3 / DynamoDB / SSM. |
| [`.pre-commit-config.yaml`](.pre-commit-config.yaml) + `.secrets.baseline` | Pre-commit detect-secrets baseline — secret-leak detection at commit time. |

**How to fork:** the eight pieces above compose into a forkable security baseline for any Arc / Circle / FastAPI app. Most of them are 10–20 lines each; the discipline is in shipping them as a set, not picking-and-choosing.

**Who benefits:** any Arc app that handles user PII or signs transactions. The judges read this repo like operators — visible security posture is itself a Traction signal.

## Why we should be a top contender

Beyond just *having* primitives:

1. **All twelve primitives are documented, not just shipped.** Each links to a spec or walkthrough doc. A forker can implement against the spec without reading the full source.
2. **The docs are kept current.** This isn't a post-hoc add — the docs were maintained through every shipped commit. See [`docs/README.md`](docs/README.md) for the documentation map.
3. **The primitives compose.** Strategy Passport + Rigor Gate + Trace Anchor form a complete provenance chain for any AI-decision product. You can fork them individually OR as a stack.
4. **The codebase is fully reviewed at the architecture level.** [`docs/chuan-architecture-survey.md`](docs/chuan-architecture-survey.md) walks every file in `backend/archimedes/` with author signal + gap notes — a forker can see exactly what's load-bearing vs scaffolded.
5. **License is the most permissive possible.** Unlicense is more permissive than MIT, BSD, or Apache — no attribution, no notice file required, no warranty disclaimer to copy. Forkers don't need to think about it.
6. **The substrate is real, not aspirational.** 806+ backend tests + 16 analytics-engine tests pass. Live HTTPS testnet deploy at <https://archimedes-arc.app/>. 10 contracts on Arc testnet. 22 years of real SPY backtest data. 2 Tier-1 strategies that actually pass the rigor gate. **The autonomous agent is writing real on-chain rebalance traces against `ReasoningTraceRegistry` right now** — `curl https://archimedes-arc.app/api/traces/?limit=10` and you'll see real `arc_tx_hash` values verifiable on `testnet.arcscan.app`. Nothing on this list is mocked.

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
- [`docs/judging-rubric-assessment.md`](docs/judging-rubric-assessment.md) — Day-13 rubric self-assessment (submission day; includes Arc OSS Showcase as a dimension)
