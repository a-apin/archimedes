# Chuan's Architecture Survey — `backend/archimedes/`

> **Audience:** team-internal recon document. Describes the surface, names the gaps,
> **does not act on them**. Per-file follow-ups happen on subsequent prompts.
> **Scope:** the entire `backend/archimedes/` FastAPI package. The agentic
> system (`t2o2`) holds **the majority** of commits under this tree — more than
> every other contributor combined. The `chain/` subdir is the narrow
> personal-lane focus; the rest is via the bot pipeline.
> **Method:** for every `*.py` file, extracted line count, first-commit author,
> module docstring, public symbols, and `TODO|FIXME|NotImplementedError` markers.
> Raw extraction regenerable via the per-file `git log` + `grep` recipe at the
> end of this doc. Per-file commit history via
> `git log --author='t2o2' --author='chuan@gyld.fi' -- backend/archimedes/`.
> **Status:** **Day-11 revision (2026-05-23).** Refreshes the Day-10 survey
> against the work that landed since:
>   - **Chuan / t2o2 — `bd6935b` (Strategy DSL + interpreter + fusion evaluator pipeline).** **3 new files** (`services/strategy_dsl.py`, `services/dsl_to_backtrader.py`, `services/fusion_evaluator.py`) + 37 new tests + 7-line additive change to `strategy_fusion.py`. Closes the long-standing fusion-to-backtest gap at the implementation level; the wiring into `_run_fusion_job` is open in [#133](https://github.com/a-apin/archimedes-arcadia/issues/133).
>   - **Daniel R. — UnoCSS pass (PR #124).** Frontend-only; no `backend/archimedes/` impact.
>   - **Spine-plus-v2 (Dan / Claude, branch `dbrowneup/spine-plus-v2`).** **8 new files** under `backend/archimedes/`: `api/generate_routes.py`, `api/generate_schemas.py`, `api/explore_routes.py`, `api/explore_schemas.py`, `api/corpus_routes.py`, `services/generation_pipeline.py`, `services/asset_market_service.py`, `services/corpus_categories.py`, `services/kb_runner.py`, `models/kg.py`, `scripts/run_kb_pipeline.py` (11 files including models + scripts). Streaming Generate, Explore page, corpus polish, KB pipeline skeleton.
>   - **Phase 7 follow-ups filed as t2o2 issues 2026-05-23:** [#129](https://github.com/a-apin/archimedes-arcadia/issues/129) (rigor consolidation), [#130](https://github.com/a-apin/archimedes-arcadia/issues/130) (LLM backend unification), [#131](https://github.com/a-apin/archimedes-arcadia/issues/131) (portfolio constructor retirement), [#132](https://github.com/a-apin/archimedes-arcadia/issues/132) (routes.py monolith split), [#133](https://github.com/a-apin/archimedes-arcadia/issues/133) (fusion-evaluator wiring). Five of the gap clusters below are now live work.

## Quick stats

- **Files surveyed:** ~89 `*.py` under `backend/archimedes/` at Day-11 (78 at Day-10, 75 at Day-9)
- **Top files by `t2o2` commit count (Day-10 reading; not re-run for Day-11):** `api/routes.py`, `main.py`,
  `services/vault_service.py`, `api/schemas.py`, `services/strategy_fusion.py`,
  `services/strategy_architect.py`, `chain/agent_runner.py`
- **Author distribution under this tree (Day-10 reading):** `t2o2` 54 · Önder 23 · Daniel B. 14 ·
  Chuan (personal) 3 · Dan 2 · Daniel R. 1

## File tree (Day-11)

```
backend/archimedes/
├── __init__.py
├── main.py                 ← app entrypoint + startup hooks
├── db.py                   ← SQLAlchemy async session factory
├── api/                    ← FastAPI routes + Pydantic schemas (14 files)  ⟵ +generate_routes/_schemas +explore_routes/_schemas +corpus_routes
├── chain/                  ← personal lane: web3 + Circle + on-chain (8 files)
├── interfaces/             ← frozen Protocol contracts (4 files)
├── models/                 ← SQLAlchemy ORM + dataclass models (12 files)   ⟵ +kg.py
├── scripts/                ← operational scripts (6 files + __init__)        ⟵ +run_kb_pipeline.py
└── services/               ← business logic (33 files + __init__)            ⟵ +generation_pipeline +asset_market_service +corpus_categories +kb_runner +strategy_dsl +dsl_to_backtrader +fusion_evaluator
```

---

## Package root

- **`__init__.py`** (1 line) — empty package marker. **No gap.**
- **`main.py`** (195 lines, **`t2o2`** primary) — FastAPI app entrypoint. Two startup hooks: `_startup_populate_rigor_gate()` and `_startup_seed_corpus()`. Exposes `/health`, `/`. **Gap:** uses deprecated `@app.on_event("startup")` (FastAPI deprecation warning in test output); migrate to lifespan handler.
- **`db.py`** (68 lines, **`t2o2`**) — async engine + `get_session()`. Falls back from `DATABASE_URL` to local SQLite. **Gap:** none structural, but the strip commit added idempotent `ALTER TABLE` for `papers.cluster_id/topic_label/content_hash` here — that migration approach is fragile (no Alembic). Worth tracking if the column set grows.

---

## `api/` — route handlers + frontend contracts

- **`__init__.py`** (2 lines) — empty. **No gap.**
- **`architect_schemas.py`** (84 lines, **Daniel B. / me**) — request/response schemas for the strategy architect. Deliberately separate from `schemas.py` to avoid the announce-before-changing policy on the frontend contract. **Gap:** flagged in its own docstring — fold into `schemas.py` later if it stabilizes.
- **`chat_routes.py`** (145 lines, `t2o2`) — per-vault chat endpoints. **Gap:** none obvious; well-scoped per `ecosystem-design-spec.md § 16–17`.
- **`marketplace_routes.py`** (153 lines, `t2o2`) — community strategy discovery. Lists / featured / trending / categories. **Gap:** consumes `marketplace_service.py` which is entirely seed data — endpoints work, but the data they return isn't real (see `marketplace_service.py` below).
- **`marketplace_schemas.py`** (253 lines, `t2o2`) — Pydantic models for marketplace responses. **Gap:** none structural; tied to `marketplace_service.py`'s seed-data model.
- **`risk_routes.py`** (231 lines, `t2o2`) — risk analysis aggregator (`/api/risk/portfolio`, `/api/risk/profiles`). **Gap:** none obvious; Önder's `RiskAnalysis.jsx` was the consumer (now deleted by strip — front-end consumer needs to relocate to `/portfolio` per the strip).
- **`risk_schemas.py`** (80 lines, `t2o2`) — risk band response models. **Gap:** none.
- **`routes.py`** (~2315 lines now, `t2o2`) — *the* main API surface. Asset / vault / strategy / efficient-frontier / correlation / advisor / stress / agent endpoints. Day-10 added 837 / removed 721 (net +116) — new endpoints for the agentic advisor, stress engine, and updated strategy-passport surfaces. **Gap (a):** line ~172 still has `# TODO: Implement with stored price history` (asset price history stub). **Gap (b):** at 2300+ lines it's a monolithic file — splitting by resource (assets / vaults / strategies / frontier / advisor / stress) would improve discoverability. The dedicated routers (`chat_routes`, `marketplace_routes`, `risk_routes`, `selection_bias_routes`) already prove the pattern.
- **`schemas.py`** (489 lines, `t2o2`) — the frontend contract. Asset / vault / strategy / trace response shapes. **Gap:** none structural; touching it triggers the announce-before-changing policy.
- **`selection_bias_routes.py`** (296 lines, `t2o2`) — rigor gate endpoints (per-strategy + bulk + PBO). **Gap:** `_synthetic_returns_from_stub()` and `_load_strategy_code()` are private helpers — the PBO endpoint synthesizes returns from stub metrics when no real returns are stored, which is an honest fallback but worth verifying it's still in use given Önder's `rigor_evaluator.py` work.
- **`vault_schemas.py`** (60 lines, `t2o2`) — vault create / metadata / allocation request-response models. **Gap:** no module docstring.

### Day-11 additions (spine-plus-v2)

- **`generate_routes.py`** *(NEW Day-11, Dan / Claude)* — streaming Generate endpoints (`/api/generate/start`, `/api/generate/stream/{id}`, `/api/generate/jobs`, `/api/generate/jobs/{id}/candidates`). SSE-based stream with hard-cancellation via asyncio task registry; Redis-backed event log for `Last-Event-ID` replay. **Gap:** none structural.
- **`generate_schemas.py`** *(NEW Day-11)* — request/response shapes for the streaming generation surface. **Gap:** none.
- **`explore_routes.py`** *(NEW Day-11)* — Explore page backend (`/api/explore/assets`, `/api/explore/assets/{symbol}/history`). Reads from yfinance via `asset_market_service.py`, not the on-chain oracle (oracle has no history). **Gap:** none.
- **`explore_schemas.py`** *(NEW Day-11)* — Explore response shapes. **Gap:** none.
- **`corpus_routes.py`** *(NEW Day-11)* — corpus graph + KG endpoints (`/api/papers/corpus/graph`, `/api/papers/corpus/kg`), moved out of `routes.py` per the cross-cutting "dedicated router" discipline. **Gap:** none.

---

## `chain/` — Chuan's personal lane

- **`__init__.py`** (14 lines, `t2o2`) — package docstring describing the three interface implementations. **No gap.**
- **`agent_runner.py`** (**744 lines**, `t2o2`) — `StrategyRunner` class — the autonomous loop that polls market, evaluates signals, rebalances vaults, publishes traces. *"This IS the intelligence layer."* Configured via env (`AGENT_INTERVAL_SECONDS`, `AGENT_DRY_RUN`, `AGENT_VAULT_ADDRESSES`, `AGENT_USDC_FLOOR`). **Gap:** 744 lines for one runner is a lot; observability (per-tick logs, failure modes) lives implicit in the run loop. Worth confirming the `_DRIFT_THRESHOLD = 0.15` is the intended value (the kelly-portfolio uses `0.05`).
- **`circle_signer.py`** (246 lines, `t2o2`) — `CircleSigner` — submits contract executions via Circle's managed-wallet REST API; polls until terminal. Replaces raw private-key signing for vault operations. **Gap:** 60 polls × 2s = 2 min max; if a Circle call hangs longer the runner stalls. Bounded but inflexible.
- **`client.py`** (147 lines, `t2o2`) — `ChainClient` singleton + `ChainSettings` Pydantic config. AsyncWeb3 against Arc testnet RPC. **Gap:** none obvious; settings init at module import-time has been a test-mocking pain point (two tests `SKIPPED` in CI cite "Requires chain_client.settings module-level init mocking").
- **`contracts.py`** (100 lines, `t2o2`) — `ContractLoader` reads ABIs from `contracts/abis/` and binds to deployed addresses from `ChainSettings`. **Gap:** none.
- **`executor.py`** (442 lines, `t2o2`) — `ChainExecutor` implements `IChainExecutor`. Reads portfolio state, executes rebalance trades, creates vaults. **Gap:** at 442 lines, encapsulates both reads and writes; splitting (`reader.py` + `writer.py`) would help.
- **`oracle_runner.py`** (52 lines, `t2o2`) — standalone process loop (`python -m archimedes.chain.oracle_runner`). Thin — calls `OracleUpdater.update_all_prices()` every `ORACLE_INTERVAL_SECONDS`. **Gap:** none structural.
- **`oracle_updater.py`** (303 lines, `t2o2`) — `OracleUpdater` — fetches yfinance + CoinGecko prices, pushes to `PriceOracle` contract via Circle. Maps tickers (`YFINANCE_MAP`, `CRYPTO_MAP`). **Gap:** the `YFINANCE_MAP` is hardcoded (10 symbols); adding new synth assets requires editing this file.
- **`trace_publisher.py`** (196 lines, `t2o2`) — `TracePublisher` implements `ITracePublisher`. Anchors `keccak256` hashes to `ReasoningTraceRegistry`. **Gap:** none structural; the IPFS / Pinata pinning design (per `docs/specs/ipfs-reasoning-traces-design-note.md` on the strip branch) hasn't been wired here yet.

---

## `interfaces/` — frozen Protocol contracts

These were defined early as the contract surface for the 5-person concurrent build per `docs/specs/component-interfaces-spec.md`. All authored by `t2o2`.

- **`__init__.py`** (32 lines). **No gap.**
- **`agent.py`** (90 lines) — `IAgentOrchestrator`. Docstring says "Chuan implements this" — implementation is in `chain/agent_runner.py`. **Gap:** ownership comment is now stale (the bots wrote both); update the docstrings if accuracy matters.
- **`chain.py`** (156 lines) — `IOracleUpdater`, `IChainExecutor`, `ITracePublisher`. Docstring says "Marten implements these" — currently implemented in `chain/oracle_updater.py`, `chain/executor.py`, `chain/trace_publisher.py` (by `t2o2`, not Marten). **Gap:** same stale ownership.
- **`math.py`** (134 lines) — `IRegimeDetector`, `IPortfolioConstructor`, `IBacktestEvaluator`. Docstring says "Önder implements these." `IBacktestEvaluator` is real (the analytics-engine populates `BacktestResult`); `IRegimeDetector` is implemented twice (heuristic + statistical); `IPortfolioConstructor` is partly implemented (Kelly + MVO). **Gap:** the interface and the multiple implementations are drifting — see services/ gaps below.
- **`strategy.py`** (73 lines) — `IStrategyProvider`. Docstring says "Dan implements this." Implementation is `services/strategy_provider.py` by Daniel B. (me). **No gap.**

---

## `models/` — ORM + dataclass models

- **`__init__.py`** (59 lines, `t2o2`). **No gap.**
- **`asset.py`** (78 lines, `t2o2`) — `AssetInfo`, `AssetPrice`, `MarketSnapshot` dataclasses. **No gap.**
- **`backtest_store.py`** (165 lines, **danielscoffee** primary) — `BacktestResultRecord` SQLAlchemy ORM. **Gap:** Daniel R. authored; consumes `analytics-engine` artifacts via `services/backtest_repository.py`.
- **`backtest.py`** (181 lines, `t2o2`) — `BacktestResult` dataclass with the full selection-bias contract (DSR, PBO, OOS, look-ahead audit flag). Has `passes_validation` + `passes_rigor_gate` properties. **Gap:** none structural; this is the spec-aligned shape Önder + Daniel R. populate.
- **`chat.py`** (96 lines, `t2o2`) — `VaultMetadata` + `ChatMessage` ORM (this is also where the `Base = DeclarativeBase` is exported and re-used by other model files). **Gap:** `chat.py` owning `Base` is a fragile import structure — adding a model that doesn't want chat deps still requires importing through `chat.py`.
- **`corpus_store.py`** (79 lines, `t2o2`) — `PaperRecord` + `CorpusMetaRecord` (the corpus DB substrate). **Gap:** `cluster_id`/`topic_label`/`artifact_hash`/`artifact_built_at` columns exist but are unwritten (waiting on the `#101` KB pipeline). `quality_signal` fields from the spec are absent.
- **`portfolio.py`** (149 lines, `t2o2`) — `RiskProfile` enum + `Portfolio`/`PortfolioHolding`/`TargetAllocation`/`TradeOrder`/`RebalanceDecision` dataclasses. **No gap.**
- **`regime.py`** (56 lines, `t2o2`) — `Regime` enum + `RegimeSignals` + `RegimeClassification`. **No gap.**
- **`strategy_store.py`** (188 lines, `t2o2`) — `StrategyRecord` ORM with content-hashed dedup, status transitions, source-paper provenance. **Gap:** `_compute_content_hash()` + `upsert_strategy()` are the substrate the strip's `/api/strategies/generated` reads from — verified live.
- **`strategy.py`** (173 lines, `t2o2`) — `Strategy` dataclass (passport-aware, with paper provenance fields). **Gap:** none; spec-aligned per `strategy-passport-spec.md`.
- **`trace.py`** (99 lines, `t2o2`) — `ReasoningTrace` dataclass + `DecisionType` enum. **Gap:** imports `web3` at module level (`from web3 import Web3`) which is a heavy dep for a model file — the hash computation could live in a helper instead.
- **`vault.py`** (66 lines, `t2o2`) — `VaultInfo`, `VaultMetrics`, `VaultTier`. **No gap.**

### Day-11 additions (spine-plus-v2)

- **`kg.py`** *(NEW Day-11, Dan / Claude)* — knowledge-graph ORM tables (nodes, edges, embedding refs) backing the KB pipeline skeleton. Imports `Base` from `models/chat.py` (per the existing import structure). **Gap:** schema present; the pipeline that populates it is gated behind `KB_PIPELINE_ENABLED` and waits on Dan's Linus-side iteration stabilizing.

---

## `scripts/` — operational scripts

- **`__init__.py`** (0 lines, `t2o2`). **No gap.**
- **`bootstrap_vaults.py`** (~580 lines now, `t2o2`) — bootstraps the demo ecosystem (sets oracle prices, mints synthetics, creates vaults, funds + allocates, adds AMM liquidity, verifies). Hardcoded `TARGET_PRICES` + `VAULT_PROFILES` + `MINT_BUDGET`. Day-10 added 23 lines (likely to support the new multi-asset NAV vault contracts). **Gap:** operational, works, but tightly coupled to specific demo state — re-running is non-trivial.
- **`deploy_contracts.py`** *(NEW Day-10, 370 lines, `t2o2`)* — deploys updated contracts to Arc testnet via Circle wallet. Tied to the multi-asset NAV vault update (`Vault.sol` now prices all holdings via oracles in `totalAssets()`). Auto-updates `.env` with new addresses + prints addresses for `ui/src/config.js`. **Gap:** the addresses live in two places (`.env` + `config.js`) — script does both but the sync isn't enforced anywhere; drift risk.
- **`hydrate_corpus.py`** (151 lines, `t2o2`) — deploy-time corpus PDF/text hydration. Polite (3s delay), idempotent (sha256 cache). **Gap:** not auto-invoked anywhere — operator-triggered only.
- **`run_backtests.py`** (180 lines, **Daniel R.**) — invokes the analytics-engine and persists results. **Gap:** runs the analytics suite as a separate process.
- **`seed_backtests_from_artifacts.py`** (119 lines, `t2o2`) — loads pre-existing artifact JSON into `backtest_results` table without re-running. **Gap:** deployment-time loader; operator-triggered.

### Day-11 additions (spine-plus-v2)

- **`run_kb_pipeline.py`** *(NEW Day-11)* — entry point for the KB-pipeline batch (PyMuPDF extract → embedding → cluster → KG build), gated behind `KB_PIPELINE_ENABLED`. Skeleton only at Day-11; production body waits on Dan's Linus-side iteration stabilizing. **Gap:** runs nothing meaningful until the KB substrate is finalized.

---

## `services/` — business logic (the heart of the architecture)

### Domain: corpus + arxiv

- **`arxiv_corpus.py`** (489 lines, **Dan Browne** primary) — Dan's original q-fin scraper (Stream A) that built the seed `manifest.jsonl`. **Gap:** **redundant** with `corpus_service.py` (DB-backed) + `scripts/bulk_ingest_arxiv.py` (the 10k expansion path). Worth deciding which of these stays canonical.
- **`arxiv_pipeline.py`** (324 lines, **Daniel B. / me** primary) — implements `IStrategyProvider.extract_from_paper` (PDF → text → LLM-synthesized passport → rendered strategy module). Uses `pypdf` (BSD-3) not PyMuPDF. **Gap:** wired but not invoked on a live demo path — the curated `analytics-engine/strategies/*.py` is what actually loads.
- **`corpus_service.py`** (295 lines, `t2o2`) — DB-first read/seed/intake of the q-fin corpus. **Gap:** `intake_from_arxiv()` exists but no periodic scheduler; `CORPUS_MAX` retention not enforced (no eviction). See `docs/corpus-architecture.md` for the fuller "what's wired vs not" picture.

### Domain: strategy generation

- **`strategy_provider.py`** (494 lines, **Daniel B. / me** primary) — `LocalStrategyProvider` reads `analytics-engine/strategies/*.py` via AST (no backtrader import), computes methodology hash + strategy id, loads fixtures. **Gap:** strip commit added `backtest_start/end` exposure — verify it's still parsing correctly post-rebase.
- **`strategy_architect.py`** (411 lines, **Daniel B. / me** primary) — interactive Claude-driven architect that selects + weights pre-curated strategies. **Gap:** owns its own `LLMBackend` Protocol + `ClaudeBackend` impl — duplicates the abstraction in `llm_backend.py`. Worth unifying.
- **`strategy_fusion.py`** (650 lines, **Dan Browne** primary) — multi-paper novelty-seeking synthesis. Loads corpus DB-first with file fallback. **Gap:** uses **keyword selection** over corpus (not embeddings — that's `#96`); duplicates `LLMBackend`/`ClaudeBackend`/`CannedBackend` pattern from `strategy_architect.py`. Has a feature flag `fusion_enabled()`.
- **`strategy_guardrail.py`** (169 lines, **Daniel B. / me** primary) — deterministic weight normalizer + USYC-floor reserver + max-weight cap. Step-2 of the architect path. **Gap:** none structural.
- **`strategy_signal_evaluator.py`** (~529 lines now, `t2o2`) — extracts live allocation signals from `analytics-engine/strategies/*.py` (without backtrader). Hardcoded per-strategy signal evaluators (`_faber_sma200_signal`, `_vol_managed_signal`, `_tsmom_signal`, `_buy_hold_signal`). Day-10 grew net +85 lines (likely supporting the new agent's global market scan). **Gap:** adding a new paper-grounded strategy requires editing this file (`_get_evaluator` dispatch); not data-driven.

### Domain: regime detection

- **`regime_detector.py`** (108 lines, `t2o2`) — **v1 heuristic** VIX thresholds. Docstring says "v1 until the full statistical classifier lands from [Önder's] lane." **Gap:** **superseded** by `statistical_regime.py` but both still exist on disk.
- **`statistical_regime.py`** (463 lines, `t2o2`) — **v2** Gaussian Mixture Model + multi-signal scoring + transition probabilities + confidence. **Gap:** unclear which version is wired in the runtime (Önder's #115 added a `RegimePanel` UI; need to check which detector backs it).

### Domain: portfolio construction + rigor

- **`portfolio_constructor.py`** (285 lines, `t2o2`) — `PortfolioConstructor` — given regime + strategies + risk profile, produces target allocations. Falls back to equal-weight; calls MVO when `price_histories` supplied. **Gap:** hardcoded `_DEFAULT_SYNTHS` list; `_DRIFT_THRESHOLD` differs from `agent_runner.py`'s value.
- **`portfolio_optimizer.py`** (~235 lines now, **Önder**) — pure MVO: GMV / Max Sharpe / Max Expected Return per risk profile + efficient-frontier compute. Heavily rewritten Day-10 (~225 added / ~275 removed — net -50 lines, structural refactor). **Gap:** none structural.
- **`kelly_portfolio.py`** (505 lines, `t2o2`) — `KellyRiskParityConstructor` — Kelly sizing + inverse-vol risk parity + USDC floor + regime-aware deleveraging. Day-10's "Kelly fix" commit (`a8e447f`) touched this. **Gap:** at 505 lines this is one of the largest constructors — relationship to `portfolio_constructor.py`, `portfolio_optimizer.py`, and (NEW) `portfolio_agent.py` deserves diagramming. Four constructors now.
- **`portfolio_agent.py`** *(NEW Day-10, 850 lines, `t2o2`)* — **LLM-driven agentic portfolio advisor.** Takes signals from `strategy_signal_evaluator.py` + a global market scan and asks an LLM to construct the final portfolio, picking individual stocks/bonds (not just ETFs) and anchoring each pick to a paper-grounded strategy passport. Uses tool-calling with `MAX_AGENT_ITERATIONS = 12` and a 5-minute cache. Largest single addition since the survey. **Gap:** introduces a *fourth* constructor with materially different semantics (LLM-picked vs deterministic MVO/Kelly/equal-weight) — relationship to the other three is now strictly: (1) `portfolio_optimizer.py` computes weights; (2) `kelly_portfolio.py` applies Kelly sizing on top; (3) `portfolio_constructor.py` orchestrates between regime + strategies + budget; (4) `portfolio_agent.py` replaces the deterministic chain with an LLM agent loop. Worth a one-page "which constructor when?" decision tree.
- **`stress_engine.py`** *(NEW Day-10, 380 lines, `t2o2`)* — portfolio stress-test engine. Six canonical historical/scenario shocks, per-asset-class shock vectors; computes scenario P&L = Σᵢ wᵢ · shock_class(i, scenario). Resolves picks → asset class via `GLOBAL_ASSETS`. Exposes `stress_one()`, `stress_all()`, `list_scenarios()`, `StressResult`. **Gap:** standalone and clean — but no UI integration yet (the strip-to-spine `Portfolio.jsx` would be the obvious surface to render the scenario table).
- **`rigor_evaluator.py`** (348 lines, **Önder**) — DSR / PBO / OOS Sharpe / Kelly fraction / Sharpe CI computation. **Gap:** **duplicates** `selection_bias.py` (DSR + PBO are computed in both). The newer version. Worth deciding which is canonical.
- **`selection_bias.py`** (534 lines, `t2o2`) — older `RigorGateResult` + `run_rigor_gate()` + look-ahead audit. **Gap:** see above — overlaps `rigor_evaluator.py`.

### Domain: vault + chain glue (consumed by the API layer)

- **`asset_service.py`** (59 lines, `t2o2`) — composes chain + oracle data for asset responses. **Gap:** thin; could fold into `routes.py`.
- **`vault_service.py`** (377 lines, `t2o2`) — composes `ChainExecutor` data into API responses. **Gap:** none structural; the `metadata_hash` computation pattern is repeated in a couple of services — could DRY.
- **`vault_monitor.py`** (220 lines, `t2o2`) — periodic vault metric snapshots, AUM trend, oracle staleness, McLean-Pontiff Sharpe decay. **Gap:** snapshot collection is called from `agent_runner.tick()` but the API helpers for the monitoring dashboard / SSE stream are deferred.
- **`amm_bootstrap.py`** (106 lines, `t2o2`) — quick on-demand AMM liquidity addition. **Gap:** the docstring flags it as a workaround ("the initial bootstrap didn't add AMM pool liquidity") — fix is in `bootstrap_vaults.py`; this is the compensating service.
- **`circle_service.py`** (121 lines, `t2o2`) — Circle SDK breadth showcase. **Gap:** the docstring is explicit: *"demonstrates breadth of Circle tool usage…for the rubric's 20% Circle Tool Usage category."* This is judge-oriented, not load-bearing.
- **`config_service.py`** (49 lines, `t2o2`) — serves deployed contract addresses to the frontend. **Gap:** thin.

### Domain: fusion-to-backtest pipeline (Day-11)

- **`strategy_dsl.py`** *(NEW Day-11, `t2o2` / Chuan, 250 lines)* — closed-enum JSON schema + validator for fusion-generated strategies. No `eval`/`exec`/`importlib`; static look-ahead audit. **Gap:** spec doc (`docs/specs/strategy-dsl-spec.md`) is the open item on [#133](https://github.com/a-apin/archimedes-arcadia/issues/133).
- **`dsl_to_backtrader.py`** *(NEW Day-11, `t2o2`, 197 lines)* — interpreter producing `backtrader.Strategy` subclasses at runtime from a validated `StrategySpec`. **Gap:** none structural.
- **`fusion_evaluator.py`** *(NEW Day-11, `t2o2`, 339 lines)* — orchestrator: `validate → interpret → backtest → rigor gate`. Uses canonical `services/rigor_evaluator.py` for DSR/OOS-Sharpe (PBO defaulted to 0.0 with a comment, since PBO is a library-level metric). 37 new tests, all passing. **Gap:** **not yet wired into `_run_fusion_job`** — fusion endpoint still emits text-only output in production. Tracked at [#133](https://github.com/a-apin/archimedes-arcadia/issues/133).

### Domain: streaming generation (Day-11, spine-plus-v2)

- **`generation_pipeline.py`** *(NEW Day-11, Dan / Claude)* — the agent-path streaming pipeline. Computes per-candidate buy-and-hold return series, calls `rigor_evaluator.compute_dsr` / `compute_oos_sharpe` for each, applies library-level PBO across the candidate set, validates the user brief as a JSON-mode LLM step. Wired into `generate_routes.py::POST /api/generate/start`. **Gap:** in-flight thread-bound LLM calls can't be hard-cancelled (only the surrounding `asyncio.Task` is cancellable) — documented limitation, not a bug.
- **`asset_market_service.py`** *(NEW Day-11)* — yfinance reader for the Explore page asset histories. Caches per-symbol pulls. **Gap:** none.
- **`corpus_categories.py`** *(NEW Day-11)* — labels + counts for corpus filtering in Explore. **Gap:** none.
- **`kb_runner.py`** *(NEW Day-11)* — KB-pipeline runner skeleton (called by `scripts/run_kb_pipeline.py`). Gated behind `KB_PIPELINE_ENABLED`. **Gap:** production body deferred — see scripts/ note.

### Domain: cross-cutting

- **`__init__.py`** (6 lines, **Daniel B. / me**) — package docstring naming ownership. **Gap:** ownership comment is stale (much was rewritten by `t2o2`).
- **`backtest_mapper.py`** (188 lines, **danielscoffee**) — Pydantic models + mappers for analytics-engine artifacts → `BacktestResult`. **Gap:** Daniel R. lane.
- **`backtest_repository.py`** (172 lines, **danielscoffee**) — read/write helpers for `backtest_results` table. **Gap:** Daniel R. lane.
- **`chat_service.py`** (259 lines, `t2o2`) — message persistence + AI response generation + auto-post on rebalance/regime. **Gap:** `AI_WALLET_ADDRESS = "0x0000000000000000000000000000000000000000"` is a placeholder; if the AI identity should be on-chain identifiable, this needs a real wallet.
- **`chat_routes.py` → `chat_service.py`** ties to **`models/chat.py`** — full chat stack is wired.
- **`construction_trace.py`** (102 lines, **Daniel B. / me**) — builds `ReasoningTrace` for architect output + computes integrity hash. *"This module STOPS at the hash. It never touches the chain."* Hard seam. **Gap:** none; clean.
- **`job_queue.py`** (107 lines, `t2o2`) — Redis-backed async job queue for strategy generation. **Gap:** the strip commit notes that fusion traces now flow to `/api/traces` automatically; verify the job-queue + trace persistence are coherent.
- **`llm_backend.py`** (307 lines, `t2o2`) — provider-agnostic LLM backend factory (`LLM_PROVIDER` ∈ {anthropic, anthropic_compatible, openai, ollama}); falls back to `CannedBackend`. **Gap:** `strategy_architect.py` + `strategy_fusion.py` each define their *own* `LLMBackend` Protocol + `ClaudeBackend` + `CannedBackend` instead of using this one. Three parallel abstractions.
- **`marketplace_service.py`** (~398 lines now, `t2o2`) — community strategy seed data. **Day-10 pruned 607 lines** (was 1005); the `[strategy] Wire strategy engine to marketplace + vault provenance` commit replaced the worst of the seed-data weight with real wiring to the strategy engine + vault provenance. **Gap:** still partly seed-data backed — see Gap Cluster #8 (now partially resolved).
- **`redis_state.py`** (258 lines, `t2o2`) — `AgentStateStore` over Redis. Persists regime, heartbeat, last-rebalance per vault, traces + trace index. **Gap:** none structural; this is now the trace-index backing for `/api/traces`.

---

## Aggregate gap clusters (where to look first) — Day-11 update

Five of the gap clusters below are now **live work** with t2o2 issues filed. Status column added so the table doubles as a one-glance "what's queued."

| # | Cluster | Day-11 status | Tracked at |
|---|---|---|---|
| 1 | **Redundancy: rigor implementation** (`selection_bias.py` ↔ `rigor_evaluator.py`) | **filed** | [#129](https://github.com/a-apin/archimedes-arcadia/issues/129) — open, t2o2 |
| 2 | **Redundancy: regime detection** (`regime_detector.py` ↔ `statistical_regime.py`) | **deferred** — needs Önder's read on which is wired to `RegimePanel` before specing | open question for next standup |
| 3 | **Redundancy: LLM backends** (3 parallel `LLMBackend` Protocols) | **filed** | [#130](https://github.com/a-apin/archimedes-arcadia/issues/130) — open, t2o2 |
| 4 | **Redundancy: arxiv intake paths** (`arxiv_corpus.py`, `corpus_service.py`, `bulk_ingest_arxiv.py`) | **deferred** — adjacent to Dan's Linus-side KB iteration | will fold into Phase 3c completion |
| 5 | **Multiplicity: portfolio constructors** | partially answered: decision-tree spec at [`docs/specs/portfolio-constructor-decision-tree.md`](specs/portfolio-constructor-decision-tree.md) names `portfolio_agent.py` (top-level) + `portfolio_optimizer.py` (math leaf) as canonical. Retirement of `portfolio_constructor.py` + `kelly_portfolio.py` **filed** | [#131](https://github.com/a-apin/archimedes-arcadia/issues/131) — open, t2o2 |
| 6 | **Monolith: `api/routes.py` (~2315 lines)** | **filed** — 9-file per-resource split spec'd | [#132](https://github.com/a-apin/archimedes-arcadia/issues/132) — open, t2o2 |
| 7 | **Stale interface ownership comments** (`interfaces/agent.py` / `chain.py` / `math.py`) | **resolved** in Phase 1 deferred resolution (commit `07276d3`) — reframed to Reviewer/Coverage per CLAUDE.md's lane-softening | landed |
| 8 | **`marketplace_service.py` seed-data weight** | still partial; will file follow-on once [#132](https://github.com/a-apin/archimedes-arcadia/issues/132) settles (the routes split surfaces remaining wiring) | tracked here |
| 9 | **Scheduled intake / artifact build** | **deferred** — folds into Phase 3c completion (Dan's lane) | tracked here |
| 10 | **Operational scripts not Make-able** | **resolved** — Makefile extended with `up`/`down`/`logs`/`pytest`/`lint`/`format`/`ui-dev`/`routes`/`clean` (commit `1e372f5`); README pointer added | landed |
| 11 | **TODO marker at `routes.py:172`** | **resolved** in Phase 1 cherry-pick (commit `6b5baec`) — replaced with `501 Not Implemented` + a fixed-list `available_sources` body so callers see a real schema | landed |
| 12 | **`circle_service.py` is judge-oriented** | by design — no action needed | n/a |
| 13 | **`stress_engine.py` built but not wired to the UI** | **deferred** — Phase 4 candidate; Marten's lane. Design-prompts doc calls for a horizontal "Stress-scenario strip" on the Portfolio page; backend ready, frontend strip not yet wired | tracked here |
| 14 | **Agentic advisor tool-use semantics** | unchanged — `MAX_AGENT_ITERATIONS=12`, 5-min cache. Failure modes worth understanding before the demo | tracked here |
| 15 | **NEW Day-11: fusion-to-backtest wiring** | **filed** — building blocks shipped in `bd6935b` (DSL + interpreter + evaluator + 37 tests); LLM prompt extension + `_run_fusion_job` rewire + DSL spec doc remain | [#133](https://github.com/a-apin/archimedes-arcadia/issues/133) — open, t2o2 |

---

## Reproducibility

To regenerate the raw extraction this survey is built from:

```bash
for f in $(find backend/archimedes -type f -name "*.py" | grep -v __pycache__ | sort); do
  echo "====FILE: $f"
  echo "----lines: $(wc -l < "$f")"
  echo "----firstauth: $(git log --reverse --format='%an' -- "$f" | head -1)"
  echo "----dockstr:"
  awk 'NR==1 && /^"""/ {flag=1; sub(/^"""/, ""); print; next} flag && /"""/ {sub(/""".*/, ""); print; flag=0; exit} flag {print}' "$f" | head -8
  echo "----publicsymbols:"
  grep -nE '^(class |def |async def |[A-Z_]+ = )' "$f" | head -15
  echo "----todos:"
  grep -nE 'TODO|FIXME|XXX|NotImplementedError' "$f" | head -5
done
```

---

## See also

- [`docs/corpus-architecture.md`](corpus-architecture.md) — the corpus-specific deep-dive
  that overlaps the "Domain: corpus + arxiv" section above
- [`docs/specs/component-interfaces-spec.md`](specs/component-interfaces-spec.md) — the
  original frozen-interface contract these `interfaces/*.py` files derive from
- [`docs/specs/strategy-passport-spec.md`](specs/strategy-passport-spec.md) +
  [`docs/specs/selection-bias-corrections-spec.md`](specs/selection-bias-corrections-spec.md) —
  the contracts `BacktestResult` and the rigor evaluators implement
- [`CLAUDE.md`](../CLAUDE.md) — lead+coverage table (post-lanes-softening)
