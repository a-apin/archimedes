# Archimedes — Technical Overview & Evolution Plan

> **Date:** 2026-06-22 · **Author:** Claude Code (repo technical analysis)
> **Method:** Full-tree read + 3 parallel subsystem deep-dives (backend/agents,
> contracts/chain, frontend/analytics), cross-checked against
> [`AUDIT_2026-06-14.md`](../AUDIT_2026-06-14.md) and
> [`docs/judging-rubric-assessment.md`](judging-rubric-assessment.md).
> **Posture:** Critical and honest. This is an internal engineering assessment, not a pitch.

---

## 1. Verdict in one paragraph

Archimedes is a genuinely substantial, real system — not a demo skinned over mocks.
The backend is ~37k LOC of production Python with ~1,500 test functions; there are 11
Solidity contracts (~3.1k LOC) with ~2.9k LOC of Foundry tests, really deployed to Arc
testnet with on-chain transaction artifacts; the analytics engine runs real backtrader
backtests on real yfinance data with real DSR/PBO math; the React UI wires all five
product surfaces to live endpoints. **The headline weakness is not "it's fake" — it's
"the rigor and provenance guarantees are weaker than the product claims."** Two
copies of the rigor gate disagree, the "non-custodial / rebalance-only" story is
undercut because the backend agent *is* the vault owner, and the "trace anchored on-chain
before the trade" claim is backed by a Python boolean in Redis rather than the
commit-reveal contract that exists but is never called. These are integrity-of-claim
gaps, and they sit exactly on the two pillars the pitch leans on hardest.

---

## 2. Architecture map (as built)

```
                          ┌─────────────────────────────────────────┐
   React 19 + Vite + viem │  ui/  (Generate · Library · Passport ·   │
   EIP-6963 + SIWE + Circle│  Portfolio · Reasoning · Corpus ·        │
   Passkey                │  Explore)                                 │
                          └───────────────┬───────────────────────────┘
                                          │ REST + SSE
                          ┌───────────────▼───────────────────────────┐
   FastAPI / Uvicorn      │  backend/archimedes/                       │
                          │  api/     30+ route modules + SIWE auth    │
                          │  agents/  generation_pipeline, architect,  │
                          │           fusion, portfolio_agent (K=1)    │
                          │  services/ rigor, RAG, regime, optimizer,  │
                          │           Xia-2026 protocols, corpus       │
                          │  chain/   client, executor, oracle_runner, │
                          │           circle_signer, trace/strategy pub│
                          │  models/  Strategy, Backtest, Trace, Vault │
                          └──┬─────────────┬──────────────┬────────────┘
                             │             │              │
            ┌────────────────▼──┐  ┌───────▼──────┐  ┌────▼─────────────┐
            │ Postgres + Redis  │  │ Analytics    │  │ Arc testnet      │
            │ strategies,       │  │ engine (uv,  │  │ (chain 5042002)  │
            │ backtests, traces,│  │ backtrader)  │  │ 11 contracts +   │
            │ corpus, regime    │  │ yfinance     │  │ Circle Wallets   │
            └───────────────────┘  └──────────────┘  └──────────────────┘
                                          │                    │
                                    LLM backend        AWS (EC2, Aurora,
                              (Anthropic / GLM /         S3, DynamoDB,
                               OpenAI / Ollama)          Route53, ACM)
```

Deploy model: **build-on-deploy, main-only.** Every merge to `main` rebuilds and
redeploys the EC2 docker-compose stack (postgres / redis / nginx / oracle / backend).
Live at `https://archimedes-arc.app/`.

---

## 3. Subsystem assessment — real vs. stub

Scale: **Real** = production logic, exercised on the live path · **Partial** = real but
gated/offline/degradable · **Weak/Claim-gap** = works but doesn't deliver the advertised
guarantee.

### 3.1 Strategy generation (agents/) — **Real**
- `generation_pipeline.py`, `portfolio_agent.py` make real Anthropic-SDK tool-use calls
  (12-iteration agent loop, tools: `get_asset_stats`, `get_correlation`,
  `stress_test_portfolio`, `propose_portfolio`). K=1 generation is genuine.
- Multi-provider `llm_backend.py` (Anthropic / Anthropic-compatible/GLM / OpenAI / Ollama)
  with an explicit `CannedBackend` fallback (`available=False`).
- **Weak spot:** when no LLM credentials are present, generation silently drops to a
  hardcoded fixture path (fixed weights + canned reasoning). Acceptable for tests, a
  liability in prod — there is no loud operator alert distinguishing "real" from "fixture."

### 3.2 Rigor gate (services/rigor_evaluator, _rigor_helpers) — **Real math, Claim-gap**
- DSR (Bailey & López de Prado 2014) with skew/kurtosis correction, PBO via CSCV, and a
  walk-forward OOS split are all genuinely implemented.
- **Critical claim-gap (AUDIT Q2/Q3):** there are **three** rigor verdicts and they
  disagree. The canonical `run_rigor_gate` deflates DSR correctly and measures the OOS/IS
  cliff against the in-sample slice. But the `BacktestResult.passes_rigor_gate` property
  that drives the live "verified" badge divides OOS Sharpe by the *full-sample* Sharpe
  (trivially passable), and the streaming-generation verdict omits the cliff entirely.
  The strict gate exists one function over and is simply not wired to the surfaces a
  user/judge actually sees. **This is the single most important fix in the repo** —
  it directly undercuts the "rigor is the wedge" thesis.
- OOS is a single 20% hold-out, not rolling CSCV. Look-ahead audit is AST-static only.

### 3.3 Corpus / RAG (services/paper_rag, arxiv_*, corpus_service, kb_runner) — **Partial**
- Real arXiv API ingestion, sha256-cached PDFs, defensive page extraction, Postgres-backed
  corpus with idempotent upsert.
- Semantic retrieval is feature-flagged (`FUSION_SEMANTIC_RETRIEVAL`) and **degrades
  silently to TF-IDF** when sentence-transformers is unavailable — health check reveals
  it, logs do not.
- The real KB pipeline (SPECTER2 + HDBSCAN + REBEL) is gated on AWS infra; `/api/corpus/*`
  honestly returns 503 ("first artifact pending") until an artifact exists.

### 3.4 Portfolio math (portfolio_optimizer, _constructor, _backtester) — **Real**
- Constrained mean-variance / Kelly-type optimization via `scipy.optimize`, explicit
  per-profile risk aversion, regime-conditional γ scaling (crisis 4×, risk-off 2×).
- Real pandas/numpy backtester with strict business-day inner-join (no forward-fill
  look-ahead) and transaction costs (10 bps).

### 3.5 Regime detection (gmm_regime_detector, vix_regime_detector) — **Partial**
- GMM (4-feature, 4-component, hysteresis) is real but its fitted artifact is git-ignored
  and produced offline → **never activates in CI/tests/cold dev**; falls back to the
  rule-based VIX detector. Honest fallback, but the "statistical regime" headline rarely
  runs as advertised outside a manually-fit prod box.

### 3.6 Xia-2026 protocols (embargo_filter, time_aware_retrieval, source_tracker, v_check) — **Real (advisory)**
- Outcome embargo (filters papers published within N days of reference), time-aware
  retrieval (regime-scaled exponential decay), source-hash tracking + verification are all
  genuinely implemented and enforced *on the generation/retrieval side*. They are advisory
  gates, not hard execution blocks — which matches the spec but is worth stating plainly.

### 3.7 Smart contracts (contracts/src) — **Real, with design caveats**
- Genuine ERC-4626 `Vault` with ReentrancyGuard, Pausable, CEI ordering, first-depositor
  inflation mitigation (dead-shares lock). Real constant-product AMM (x·y=k, 30 bps fee,
  Uniswap-V2 math). `PriceOracle` with deviation cap + update cooldown. Over-collateralized
  `SyntheticVault` (120%) with pro-rata solvency haircut. ~2.9k LOC of Foundry tests.
- **Caveats:** single-source oracle (no secondary feed / no 2-of-3); no liquidation engine
  for synth vaults (insolvency cliff above ~20% appreciation); `getSpotPrice()` is a stub;
  no Echidna/Medusa fuzzing; no vault↔AMM↔oracle adversarial integration test.

### 3.8 Chain integration (chain/) — **Real, Claim-gap on provenance**
- Live Arc testnet deployment with real tx hashes in `contracts/broadcast/`. Oracle runner
  really pushes prices on-chain via Circle Wallets (RSA-OAEP entity-secret encryption per
  Circle spec). Trace + strategy publishers have a dual path (Circle, then raw-key fallback).
- **Critical claim-gaps (AUDIT B1/B2):**
  - **Non-custodial is weaker than stated.** Every vault is created by the backend agent
    signer, so `creator == owner == agent`. The `onlyOwner` oracle-rewiring guards added
    on 06-14 are moot because the agent *is* the owner. Pure custody holds (agent holds no
    shares), but a compromised agent key enables economic drain via oracle re-pointing.
    There is no `transferOwnership` to the depositing user anywhere in the tree.
  - **On-chain provenance is off-chain.** The `commit()`/`reveal()` functions on
    `ReasoningTraceRegistry` (which would prove "trace existed before trade" with a
    hash-binding time-lock) are **never called**; both phases use legacy `publishTrace`.
    The UI's green "Temporal Binding ✓ VERIFIED" badge is backed by a Python boolean in
    Redis, not the chain.
- Contract addresses are hardcoded in `client.py` with no env override.

### 3.9 Frontend (ui/) — **Real**
- All five product surfaces (Generate, Rigor/Passport, Execute/Deploy, Monitor,
  Explore/Corpus) are built and wired to live endpoints. Real EIP-6963 multi-wallet
  discovery + Circle Passkey, SIWE session cookies, viem reads/writes, SSE agent stream,
  ERC-4626 deposit/approval flow.
- **Weak spots:** `RiskAnalysis`/`BacktestVisualizer` fall back to `buildMockResult()`
  mock data when `/api/risk/*` fails (silent); vault marketplace was cut (discovery via
  curated library only); reasoning-trace "verification" is read from `arc_tx_hash` state,
  not recomputed against the contract in-browser.

### 3.10 Analytics engine (analytics-engine/) — **Real but offline**
- Production-grade backtrader harness: real yfinance data, cost model, look-ahead audit,
  walk-forward OOS, DSR/PBO. The 4 seeded strategies (Faber SMA200, Moreira-Muir
  vol-managed, Moskowitz TSMOM, buy-and-hold) are honest implementations with documented
  paper-vs-realized deltas. 31 strategies have baked backtest fixtures (2004→2026).
- **Critical gap:** the engine is a **CLI tool, not in the request path.** A
  freshly-generated strategy's Python code is **not backtested before it is persisted /
  shown as a passport** — fixtures are pre-baked offline. The "generate → rigor-gate →
  deploy" loop is therefore not closed for novel strategies at runtime.

### 3.11 Infra / CI (infra/, .github/) — **Real**
- Terraform for ACM, Route53, S3 (papers), DynamoDB (paper index), backend IAM. 6 CI
  workflows (quality-gate hard block, complexity-gate informational, deploy, format-guard,
  release-tag, import-guard). S3 Terraform backend (versioned, encrypted, TLS-only).
- **Caveat:** integration tests don't run in the gate (build-on-deploy means `main` can
  break the live stack on a bad merge); single EC2 host (no HA).

---

## 4. Strengths (what is genuinely good)

1. **Depth is real.** 1,500+ tests, 11 deployed contracts with heavy Foundry coverage,
   real on-chain tx artifacts, real LLM tool-use. This is far past vaporware.
2. **Intellectual honesty as a habit.** The repo audits itself adversarially, surfaces
   paper-vs-realized Sharpe deltas instead of hiding them, and documents fallbacks
   explicitly. The StockBench #15/15 result is published rather than buried.
3. **The rigor math is correct where it's wired.** DSR/PBO/CSCV are textbook-faithful.
4. **Clean layering.** Frozen `interfaces/` Protocols, service boundaries, ABI caching,
   dual-path chain signing — the architecture is legible and reviewable.
5. **Security culture.** detect-secrets baseline, gitignored key globs, SIWE auth,
   "security ships with the product" values commitment.

---

## 5. Weaknesses (ranked by impact on the core thesis)

| # | Weakness | Pillar it undercuts | Source |
|---|----------|---------------------|--------|
| 1 | Three rigor gates disagree; the live "verified" badge uses the weakest | "Rigor is the wedge" | AUDIT Q2/Q3 |
| 2 | Agent == vault owner; oracle-rewiring guards moot; no `transferOwnership` to user | "Non-custodial" | AUDIT B1 |
| 3 | Commit-reveal never called; "Temporal Binding ✓" is a Redis boolean | "On-chain provenance" | AUDIT B2 |
| 4 | Analytics engine not in request path; new strategies un-backtested before passport | "generate→gate→deploy" loop | §3.10 |
| 5 | Fixture / mock fallbacks fire silently (LLM, GMM, semantic RAG, risk UI) | Operator trust | §3.1/3.3/3.5/3.9 |
| 6 | Single-source oracle; no synth-vault liquidation engine | Funds safety | §3.7 |
| 7 | Funds-adjacent secret leak permanent in git history (rotation unverifiable) | Security | AUDIT |
| 8 | Single EC2 host, no HA; integration tests not gated | Reliability | §3.11 |

---

## 6. Implemented vs. not — at a glance

**Implemented and live:** LLM strategy generation (K=1 + considered-rejects) · DSR/PBO/OOS
math · arXiv corpus ingestion · portfolio optimizer + backtester · 11 Arc contracts +
real deploys · Circle Wallets oracle pushes · trace/strategy on-chain anchoring (v1) ·
SIWE auth · all 5 UI surfaces · CI/CD + Terraform infra · 31 baked backtest fixtures.

**Partial / offline / degradable:** GMM regime (offline-fit only) · semantic RAG
(TF-IDF fallback) · KB pipeline (gated on AWS infra, returns 503) · analytics engine
(CLI, not in request path).

**Claimed but not truly delivered:** strict rigor gate on the live badge · non-custodial
ownership (agent owns vaults) · commit-reveal temporal binding on-chain · in-browser trace
verification.

**Not built / out of scope:** liquidation engine · secondary oracle feeds · contract
fuzzing · multi-host HA · social/marketplace network (roadmap) · live arxiv→strategy
runtime pipeline · vectorbt migration.

---

## 7. Evolution plan

Sequenced so that the highest-leverage credibility fixes (which are mostly small diffs on
existing code) land before the larger build-outs. Each item maps to an issue-grade spec.

### Phase 0 — Make the claims true (1–2 weeks, mostly wiring, highest ROI)
These are the cheapest points and they protect the entire thesis.

- **P0.1 — Unify the rigor gate.** Route `BacktestResult.passes_rigor_gate` and the
  streaming-generation verdict through the canonical `run_rigor_gate` (or have both compute
  `compute_in_sample_sharpe` and enforce `oos/is ≥ 0.5`). Delete the two weak copies. Add a
  test asserting an IS-overfit fixture (IS 5.0 / OOS 0.1) FAILS on every surface. *(AUDIT Q2/Q3)*
- **P0.2 — Fix the non-custodial story.** Create vaults with the depositing user's wallet
  as owner and call only `setAgent(backendAgent)` so `owner != agent`; or move ownership to
  a cold governance key distinct from the hot agent signer. Add `transferOwnership` flow.
  Until then, correct the misleading `Vault.sol` comments. *(AUDIT B1)*
- **P0.3 — Wire commit-reveal or stop claiming it.** Either call
  `ReasoningTraceRegistry.commit()/reveal()` on the live agent tick (with
  `claimedExecutionTime` + hash-binding) so "trace existed before trade" is chain-provable,
  or relabel the UI badge to "anchored on-chain (post-trade)" until it is. *(AUDIT B2)*
- **P0.4 — Loud fallback telemetry.** Emit a structured WARN + a `/health` flag whenever
  generation hits the fixture path, RAG drops to TF-IDF, GMM is unfit, or risk UI mocks.
  No silent degradation on a funds-adjacent product.

### Phase 1 — Close the generate→gate→deploy loop (2–4 weeks)
- **P1.1 — On-demand backtest in the request path.** Import the analytics engine (or call
  it as a job) so a freshly-generated strategy is backtested on real data and rigor-gated
  *before* its passport renders the Deploy button. Today the gate grades pre-baked fixtures.
- **P1.2 — Rolling CSCV OOS.** Replace the single 20% hold-out with rolling combinatorial
  purged CV for the OOS/cliff measurement.
- **P1.3 — Portfolio-level (multi-asset) backtest.** The engine backtests one asset at a
  time; lift position-sizing/weighting into the backtest so the passport reflects the
  portfolio the user actually deploys.

### Phase 2 — Funds safety hardening (contract-review-grade, gated on Chuan)
- **P2.1 — Secondary oracle feed + 2-of-3 agreement** before on-chain price push.
- **P2.2 — Synth-vault liquidation / dynamic-collateral governor** to remove the
  insolvency cliff above 20% appreciation.
- **P2.3 — Fuzzing** (Echidna/Medusa invariants) + a vault↔AMM↔oracle adversarial
  integration test under rapid price swings.
- **P2.4 — Externalize contract addresses** to env/on-chain factory lookup (no hardcoded
  addresses in `client.py`).

### Phase 3 — Reliability & ops
- **P3.1 — Integration-test gate** before deploy (build-on-deploy currently lets a bad
  merge break the live stack).
- **P3.2 — Multi-host / HA** for the EC2 stack (or document the single-host risk as
  accepted for hackathon scale).
- **P3.3 — Migrate to AWS IAM Identity Center** (no long-lived keys); rotate Circle entity
  secret on a schedule with isolated wallets per role (oracle vs. trace vs. strategy).

### Phase 4 — Product depth (the roadmap vision)
- **P4.1 — KB pipeline to first artifact** (unblock SPECTER2/HDBSCAN/REBEL graph on AWS).
- **P4.2 — Live arXiv→strategy pipeline** beyond the 2–3 demo papers.
- **P4.3 — Social / shared-strategy network** (the canonical roadmap in `user-stories.md`).
- **P4.4 — vectorbt migration** if parameter-sweep speed becomes the constraint.

### Traction (orthogonal, highest rubric ROI per `judging-rubric-assessment.md`)
- Traction scores 4/10 and is "telemetry-bound, fixable via discipline, not code." Pair
  every meaningful ship with `arc-canteen update-product` and log every user conversation
  via `arc-canteen update-traction`. This is the cheapest point recovery available.

---

## 8. On CLAUDE.md

The repo's [`CLAUDE.md`](../CLAUDE.md) (62 KB) is already an exemplary, living context
document — accurate, current to 2026-06-13, and richer than anything a fresh pass would
produce. **It does not need rewriting.** The one substantive drift worth folding back in:
the architectural-primitives section states non-custodial ("agent has rebalance authority
only, not withdraw-to-platform") and on-chain commit-reveal provenance as *delivered*,
while §3.8 / AUDIT B1–B2 show both are aspirational on the live path. Recommend adding a
one-line honesty caveat to primitives #2 and #3 pointing at this overview and the audit,
so parallel Claude sessions don't over-trust the claims. That edit is deferred to a human
(it touches the contract-review-grade provenance/vault narrative) rather than made here.

---

*This document is a snapshot. When it drifts from shipped reality, update it or delete it —
don't let it rot. The companion audit is [`AUDIT_2026-06-14.md`](../AUDIT_2026-06-14.md).*
