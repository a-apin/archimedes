# `docs/` — Documentation Index

**A doc not listed here does not exist.** If you wrote something and it is not in this table, either add a row or delete the file. If a row is wrong, fix the row in the same commit as the doc.

Last rebuilt **2026-07-28**.

`last-verified` is the date someone last checked the doc against the running system — not the date it was last edited. `—` means nobody has verified it since it was written; treat those claims as unproven.

Everything under [`archive/`](archive/) is historical by definition and is indexed separately in [`archive/README.md`](archive/README.md). Archived docs carry an `ARCHIVED` banner naming their replacement.

| Archived doc | Status | Owner | Archived | What it is |
|---|---|---|---|---|
| [`archive/deployment-runbook.md`](archive/deployment-runbook.md) | archived | Dan Browne | 2026-07-28 | EC2-era manual / break-glass AWS deploy runbook. **Do not execute** — it routes to an instance detached from the ALB target group. Kept for its incident history and diagrams. The Fargate-era replacement is unwritten; the gap is named in [`runbooks/README.md`](runbooks/README.md). |


Repo root: [`../README.md`](../README.md) · [`../SETUP.md`](../SETUP.md) · [`../CLAUDE.md`](../CLAUDE.md) · [`../AGENTS.md`](../AGENTS.md)

Owners: **Dan Browne** — contracts, on-chain, infrastructure, architecture. **Önder Akkaya** — portfolio math and the rigor gate. **Bogdan Sivochkin** (`mnemonik-dev`) — preferred reviewer for contract changes.

---

## Architecture — start here

| Doc | Status | Owner | Last verified | What it is |
|---|---|---|---|---|
| [`architecture.md`](architecture.md) | current | Dan Browne | 2026-08-31 | System architecture map. ECS Fargate + ALB + CloudFront + WAF, Aurora PostgreSQL 18.3, ElastiCache Redis 7.1. Every claim is a link to a file. Amended 2026-08-31 for the leaderboard research/live-paper split (#1563), ratified `num_trials` self-containment (#1560), the four-state `paper_rag` signal, and `deploy.yml`'s explicit rollout verdict (#1532/#1544). |
| [`reference/file-tree.md`](reference/file-tree.md) | reference | Dan Browne | 2026-07-14 | Repository map generated alongside the architecture map. |
| [`reference/flow-diagram.mmd`](reference/flow-diagram.mmd) | reference | Dan Browne | 2026-07-14 | Request/generation flow, Mermaid source (`flow-diagram.svg`, `file-tree.svg` render it). |
| [`database-architecture.md`](database-architecture.md) | current | Dan Browne | 2026-08-31 | Data stores, schemas, migration posture. The § 2.3 table list is a 2026-06-28 cutover inventory and has drifted ~15 tables — `backend/archimedes/db.py` is the live source; see `database-relations.md` for FKs and deletion policy. |
| [`database-relations.md`](database-relations.md) | current | Dan Browne | 2026-08-31 | Identity/ownership/money-table relational structure: the schema-relations audit (corrections + gap found), the Phase 1 indices + FKs from [PR #1438](https://github.com/a-apin/archimedes/pull/1438) — **merged 2026-08-31**, with #1429 reconciling the account-deletion policy — the target ERD, and the Phase 2 proposal (G1 shipped; the rest not built). |
| [`deployment.md`](deployment.md) | current | Dan Browne | 2026-07-28 | Local vs production topology from one compose file. |
| [`architectural-principles.md`](architectural-principles.md) | current | Dan Browne | 2026-07-28 | The four primitives the product is built to defend. |
| [`anti-features.md`](anti-features.md) | current | Dan Browne | 2026-07-28 | What Archimedes deliberately does not build. |

## API reference

| Doc | Status | Owner | Last verified | What it is |
|---|---|---|---|---|
| [`api/README.md`](api/README.md) | current | Dan Browne | 2026-08-20 | Index of the API reference: per-surface docs, the auth-model overview table, and the `/docs` (Swagger) production-gate note. |
| [`api/auth-and-accounts.md`](api/auth-and-accounts.md) | current | Dan Browne | 2026-08-20 | The Better Auth sidecar (`/api/auth/*`): email/password + OAuth, session lookup, email verification. |
| [`api/wallets.md`](api/wallets.md) | current | Dan Browne | 2026-08-20 | `/api/wallets/*` — EIP-4361 wallet-link challenge/verify. |
| [`api/generation.md`](api/generation.md) | current | Dan Browne | 2026-08-20 | `/api/generate/*` — the debate-society generation pipeline, its x402 payment gate, and daily quotas. |
| [`api/strategies-and-rigor.md`](api/strategies-and-rigor.md) | current | Dan Browne | 2026-08-20 | `/api/strategies/*` and `/api/selection-bias/*` — the strategy library, portfolio advisor, stress testing, and the rigor gate. |
| [`api/paper-trading.md`](api/paper-trading.md) | current | Dan Browne | 2026-08-20 | `/api/paper/*` — deploy a strategy to an append-only, never-rewritten forward-return ledger. |
| [`api/vaults-and-chain.md`](api/vaults-and-chain.md) | current | Dan Browne | 2026-08-20 | `/api/vaults/*`, `/api/traces/*`, `/api/swap/*`, `/api/config/contracts`, and the health/root endpoints. |
| [`api/leaderboard-and-metrics.md`](api/leaderboard-and-metrics.md) | current | Dan Browne | 2026-08-20 | `/api/leaderboard` and the public, PII-free `/api/metrics/*` traction surface. |
| [`api/admin-private.md`](api/admin-private.md) | current | Dan Browne | 2026-08-31 | `/api/metrics/private/*` — the platform-admin-gated cost/ops dashboard (incl. the measured `$/generation`) and per-wallet identity roster. |
| [`api-surface-status.md`](api-surface-status.md) | current | Dan Browne | 2026-08-20 | Census of every router `backend/archimedes/main.py` registers: prefix, auth model, status, and whether a detailed doc above covers it (14/30 do). Backed by a completeness test that fails CI if a registered router has no row. |

## Product

| Doc | Status | Owner | Last verified | What it is |
|---|---|---|---|---|
| [`user-stories.md`](user-stories.md) | current | Dan Browne | 2026-08-31 | The locked product spine. Canonical statement of what the product is. Re-verified against `/api/health` 2026-08-31; the Day-9 body carries dated inline corrections (fusion-preview surface, "GLM-backed", library size, KG demo claim) and reads vault execution in the present tense, which is roadmap (#1469). |
| [`agent-api.md`](agent-api.md) | current | Dan Browne | — | Driving the full journey programmatically; the agent-native surface. |
| [`agent-quickstart.md`](agent-quickstart.md) | current | Dan Browne | 2026-08-31 | Zero to paper-traded for an external agent: eleven steps, exact response shapes, and an error table (401/402/409/422/429). Includes the live x402 paywall — production charges $2.00 USDC per generation, so steps 6a–6b link a wallet and pay. Narrower than `agent-api.md` on purpose: no vault, no capital deployed. Route strings and worked commands are drift-guarded by `backend/tests/test_agent_quickstart_drift.py`. |
| [`claims-ledger.md`](claims-ledger.md) | current | Dan Browne | 2026-08-31 | Every public claim — Landing, `/security`, `README.md`, `agent-quickstart.md`, `Architecture.jsx`, `index.html` meta, `llms.txt`, `agent.json`, `user-stories.md` — with a per-claim verdict (`TRUE` / `CHANGED` / `RETRACTED` / `OVER-CLAIMED` / `PENDING ADR MERGE`) and the file:line that backs it. Records what #1469's rebrand retracted, the open over-claims the scrub did not reach, and the market-data position. Citations are enforced by `backend/tests/test_claims_ledger.py`. |
| [`asset-universe.md`](asset-universe.md) | current | Dan Browne | — | Tradable universe and how it is assembled. |
| [`demo-script-lepton.md`](demo-script-lepton.md) | current | Dan Browne | 2026-07-06 | Current demo video script. |

## Quant and rigor — the math layer

| Doc | Status | Owner | Last verified | What it is |
|---|---|---|---|---|
| [`quant/README.md`](quant/README.md) | current | Önder Akkaya | 2026-08-31 | Index for the quant docs. Read this before any strategy claim. Now indexes the four dated findings notes as well as the four living references — it previously described itself as "these four docs" while the directory held eight (#1598). |
| [`quant/methodology.md`](quant/methodology.md) | current | Önder Akkaya | 2026-08-31 | The math layer end to end. DSR gate threshold corrected 0.95 → 0.90 (PR #901) 2026-08-31. |
| [`quant/admission-criteria.md`](quant/admission-criteria.md) | current | Önder Akkaya | 2026-08-31 | Tier-1 admission. DSR badge threshold is 0.90 — now stated as the level-1 row of the `rigor_profiles` strictness ladder rather than a literal, and the promotion flow no longer passes the library's length as the trial count, a convention reversed on 2026-07-09 (#1598). |
| [`quant/backtest-interpretation.md`](quant/backtest-interpretation.md) | current | Önder Akkaya | 2026-08-31 | How to read a backtest without fooling yourself. The doc's two DSR thresholds disagreed with each other; the stale 0.95 was corrected to 0.90 on 2026-08-31. |
| [`quant/strategy-library.md`](quant/strategy-library.md) | current | Önder Akkaya | 2026-08-31 | Curated library reference. Pass/fail status is whatever the live gate returns, not a number in a doc — three headings that carried ✅/❌ verdicts contradicting the status lines beneath them were removed, and Faber's 0.612 was corrected from an "OOS Sharpe" to the DSR p-value it actually is (#1598). |
| [`quant/library-pbo.md`](quant/library-pbo.md) | findings | Önder Akkaya | 2026-08-31 | Library-level PBO findings (fourth wave), measured 2026-06-11. Stamped historical and its pass-count phrasing retracted 2026-08-31 (#1598): the 0.047 headline is a 22-strategy figure and CSCV PBO moves with every library addition. |
| [`quant/third-wave-retest.md`](quant/third-wave-retest.md) | findings | Önder Akkaya | 2026-08-31 | Third-wave candidates through the cost model and walk-forward, measured 2026-06-11. Stamped historical and its two pass-count phrasings retracted 2026-08-31 (#1598). |
| [`quant/second-wave-universe-experiment.md`](quant/second-wave-universe-experiment.md) | findings | Önder Akkaya | 2026-08-31 | Does a bigger universe rescue the second-wave strategies — measured 2026-06-11. Stamped historical 2026-08-31 (#1598): the pass count is retracted, and the `num_trials` sweep now shows the live 0.90 bar beside the 0.95 bar it was computed against (the p = 0.941 row flips to passing). |
| [`rigor-methods.md`](rigor-methods.md) | current | Önder Akkaya | 2026-07-28 | The four gates: DSR, PBO, walk-forward OOS, look-ahead audit. |
| [`analysis/faber-dsr-finding.md`](analysis/faber-dsr-finding.md) | findings | Önder Akkaya | — | Why Faber 2007 fails the gate, and why that is the correct outcome. |
| [`benchmarks/stockbench-results.md`](benchmarks/stockbench-results.md) | findings | Önder Akkaya | — | StockBench evaluation results (`stockbench-results.json`, `stockbench-vs-baselines.png`). |
| [`specs/selection-bias-corrections-spec.md`](specs/selection-bias-corrections-spec.md) | spec | Önder Akkaya | 2026-07-28 | DSR + PBO + walk-forward + look-ahead audit math and thresholds. |
| [`specs/transaction-cost-turnover-model.md`](specs/transaction-cost-turnover-model.md) | shipped | Önder Akkaya | 2026-06-11 | Transaction-cost and turnover model in the analytics engine. |
| [`cited-literature.md`](cited-literature.md) | current | Dan Browne | 2026-08-20 | The five load-bearing papers behind the gate, including the two that are cited against us. |

## Corpus and generation

| Doc | Status | Owner | Last verified | What it is |
|---|---|---|---|---|
| [`generation-cost-instrumentation.md`](generation-cost-instrumentation.md) | current | Dan Browne | 2026-08-31 | What one generation actually consumes: per-job token counts, per-stage wall/CPU seconds, peak RSS, row writes — plus the measured `$/generation` those counts price to on the admin-only cost endpoint. The customer-facing quote seam stays `flat_v1`. |
| [`corpus-architecture.md`](corpus-architecture.md) | target-state | Dan Browne | 2026-08-20 | 10,000 arXiv preprints (not peer-reviewed), metadata + abstracts only. **Describes embeddings/clusters/KG as built; in prod none of the three exist** (#778). Selection is a **keyword filter** and only that candidate set is re-scored at request time — nothing is precomputed; `/health` `paper_rag` names the live scorer, and the graph/KG endpoints 503 or return empty. |
| [`specs/multi-agent-debate-spec.md`](specs/multi-agent-debate-spec.md) | shipped | Dan Browne | 2026-07-28 | The debate society — the sole generation pipeline. |
| [`specs/strategy-fusion-spec.md`](specs/strategy-fusion-spec.md) | shipped | Dan Browne | 2026-07-28 | Multi-paper synthesis feeding the debate proposals. |
| [`specs/strategy-passport-spec.md`](specs/strategy-passport-spec.md) | shipped | Dan Browne | — | Paper-grounding contract carried by every strategy. |
| [`specs/strategy-dsl-spec.md`](specs/strategy-dsl-spec.md) | spec | Dan Browne | — | Strategy DSL. |
| [`specs/strategy-lifecycle-spec.md`](specs/strategy-lifecycle-spec.md) | spec | Dan Browne | — | Draft → generated → published lifecycle. |
| [`specs/generation-streaming-spec.md`](specs/generation-streaming-spec.md) | spec | Dan Browne | — | SSE contract for the Generate page. |
| [`specs/generation-quote-contract.md`](specs/generation-quote-contract.md) | spec | Dan Browne | — | Public cost quote + 409/402 x402 paywall on `/api/generate/start`, ratified in #1296. Frontend ships behind `VITE_GENERATION_QUOTE_ENABLED`. |
| [`specs/kb-integration-spec.md`](specs/kb-integration-spec.md) | spec | Dan Browne | — | KnowledgeBase submodule integration. |
| [`specs/page-roles-spec.md`](specs/page-roles-spec.md) | spec | Dan Browne | — | What each page is for. |
| [`specs/component-interfaces-spec.md`](specs/component-interfaces-spec.md) | spec | Dan Browne | — | Component interfaces and the team work split. |
| [`specs/ecosystem-design-spec.md`](specs/ecosystem-design-spec.md) | spec | Dan Browne | — | Marketplace/ecosystem design. Marketplace ships behind `PAYMENTS_DRY_RUN`. |
| [`specs/xia-2026-protocols.md`](specs/xia-2026-protocols.md) | spec | Dan Browne | — | Xia et al. 2026 named protocols as implemented here. |
| [`specs/architecture-page-design.md`](specs/architecture-page-design.md) | implemented | Dan Browne | 2026-07-28 | Design for the Architecture page, **implemented in PR #1192**. Should leave `specs/` for `architecture/` or `archive/` once #1192 merges, per `CONVENTIONS.md` § 1. |
| [`diagrams/strategy-passport-architecture.md`](diagrams/strategy-passport-architecture.md) | reference | Dan Browne | — | Passport architecture diagram + reference. |
| [`bedrock-model-cost-comparison.md`](bedrock-model-cost-comparison.md) | reference | Dan Browne | — | Bedrock model costs, us-east-1 on-demand. LLM is `bedrock_converse` / `amazon.nova-micro-v1:0`. |
| [`cost-estimates/generate-llm-costs.md`](cost-estimates/generate-llm-costs.md) | reference | Dan Browne | — | Per-generation LLM cost estimate. |
| [`infrastructure-provider-analysis.md`](infrastructure-provider-analysis.md) | reference | Daniel Reis | 2026-08-16 | Modeled infra cost comparison across providers (list prices, not invoice). |

## On-chain and Arc

| Doc | Status | Owner | Last verified | What it is |
|---|---|---|---|---|
| [`arc-integration.md`](arc-integration.md) | current | Dan Browne | 2026-07-28 | Arc testnet reference and Circle integration. |
| [`specs/vault-semantics-spec.md`](specs/vault-semantics-spec.md) | spec | Dan Browne | 2026-07-28 | Vault lifecycle and trade-window semantics. |
| [`specs/commit-reveal-trace-spec.md`](specs/commit-reveal-trace-spec.md) | spec | Dan Browne | — | Commit-before-trade reasoning-trace anchoring. Contract review: Bogdan Sivochkin. |
| [`specs/ipfs-reasoning-traces-design-note.md`](specs/ipfs-reasoning-traces-design-note.md) | design note | Dan Browne | — | IPFS pinning for reasoning traces. Not a spec. |
| [`specs/execution-trading-agent-society-spec.md`](specs/execution-trading-agent-society-spec.md) | draft | Dan Browne | 2026-06-28 | Execution/trading agent society. Seed-and-refine draft. |

## Security

| Doc | Status | Owner | Last verified | What it is |
|---|---|---|---|---|
| [`security/auth-model.md`](security/auth-model.md) | current — open gap | Dan Browne | — | What authentication is actually enforced and the known testnet gap. Read before exposing anything. |
| [`runbooks/github-security-toggles.md`](runbooks/github-security-toggles.md) | runbook | Dan Browne | — | Repository security settings. |

## Runbooks and operations

| Doc | Status | Owner | Last verified | What it is |
|---|---|---|---|---|
| [`runbooks/README.md`](runbooks/README.md) | current | Dan Browne | 2026-07-28 | Index of every runbook, and an explicit list of the runbooks that do **not** exist yet — including the missing Fargate break-glass procedure. |
| [`runbooks/operations.md`](runbooks/operations.md) | current | Dan Browne | 2026-07-28 | Run the stack, RPC deep-dive, LLM backends, security notes. |
| [`runbooks/arc-testnet-e2e.md`](runbooks/arc-testnet-e2e.md) | runbook | Dan Browne | — | End-to-end testnet smoke test. |
| [`runbooks/arc-testnet-e2e-evidence.md`](runbooks/arc-testnet-e2e-evidence.md) | evidence | Önder Akkaya | 2026-05-26 | Replayable on-chain evidence for SPEC-1. |
| [`runbooks/spec-1-walkthrough.md`](runbooks/spec-1-walkthrough.md) | runbook | Dan Browne | — | SPEC-1 user-journey walkthrough. |
| [`runbooks/t3.2-contract-redeploy.md`](runbooks/t3.2-contract-redeploy.md) | runbook | Dan Browne | — | Contract redeploy procedure and secret handling. |
| [`runbooks/docs-site-setup.md`](runbooks/docs-site-setup.md) | runbook | Dan Browne | 2026-08-20 | GitHub Pages docs site (#1381, option B): Dan's two manual steps (Pages source + Route 53 CNAME), local `mkdocs serve` preview, and why `mkdocs build --strict` isn't used. |
| [`operations/feature-flag-fliplist.md`](operations/feature-flag-fliplist.md) | current | Dan Browne | 2026-08-31 | The go-live checklist (#834): every feature flag in the tree, classified LIVE / FLIP-AT-LAUNCH / DEAD, with its deployed value, its reader, and the precondition for flipping it. Enforced — `backend/tests/test_feature_flag_fliplist_drift.py` re-derives the inventory and fails CI on any flag with no row. |
| [`runbooks/backtest-results-retention.md`](runbooks/backtest-results-retention.md) | runbook | Dan Browne | 2026-08-30 | `backtest_results` archive-then-prune procedure (v8 Lane 3.1): keep policy, `archive_backtest_results.py`'s `--plan`/`--archive`/`--prune` flags, the manifest-verification guard, and the post-prune VACUUM step. |
| [`runbooks/erc8004-identity-registration.md`](runbooks/erc8004-identity-registration.md) | runbook | Dan Browne | 2026-08-31 | Minting the ERC-8004 agent identity on Arc (#1527): the live-verified registry facts, `--plan`/`--verify`/`--execute`, the Circle-signed owner step, and why `ERC8004_AGENT_ID` points at a token rather than making a claim. |
| [`runbooks/runner-ec2-wedge.md`](runbooks/runner-ec2-wedge.md) | runbook | Dan Browne | 2026-08-31 | `archimedes-runner` wedge (#1402): the impaired-instance-check + dead-SSM-agent signature, read-only diagnosis commands, the recovery ladder, what the new `ec2:reboot` alarm automates, and the operator-only steps (`terraform apply`, the one-time live reboot test). |

## Decisions (ADRs)

| Doc | Status | Owner | Last verified | What it is |
|---|---|---|---|---|
| [`adr/README.md`](adr/README.md) | current | Dan Browne | 2026-08-31 | ADR index and status vocabulary. All twenty-two records are listed there. |
| [`adr/unlicense-public-domain.md`](adr/unlicense-public-domain.md) | accepted | Dan Browne | initial commit | The Unlicense as a public-domain dedication, and its ownership/contributor consequences. |
| [`adr/arc-settlement-chain.md`](adr/arc-settlement-chain.md) | accepted | Dan Browne | 2026-05-13 | Arc testnet 5042002; USDC as settlement asset and native gas token. |
| [`adr/two-tier-marketplace.md`](adr/two-tier-marketplace.md) | accepted | Dan Browne | 2026-05-13 | Verified / Community tiers; rigor as the wedge. |
| [`adr/backtrader-backtest-engine.md`](adr/backtrader-backtest-engine.md) | accepted | Dan Browne | 2026-05-13 | backtrader chosen as the v1 backtest engine. |
| [`adr/build-on-deploy-main-only.md`](adr/build-on-deploy-main-only.md) | accepted | Dan Browne | 2026-05-18 | Build-on-deploy, main-only branch model. |
| [`adr/portfolio-constructor-decision-tree.md`](adr/portfolio-constructor-decision-tree.md) | accepted | Önder Akkaya | 2026-05-22 | Which constructor runs when. |
| [`adr/k1-generation-external-rigor-gate.md`](adr/k1-generation-external-rigor-gate.md) | accepted | Dan Browne | 2026-05-23 | K=1 generation with an externalised rigor gate. |
| [`adr/aws-account-migration.md`](adr/aws-account-migration.md) | accepted | Dan Browne | 2026-06-24 | Production moved to account 037613907429 / us-east-1. |
| [`adr/generation-payment-credit-not-refund.md`](adr/generation-payment-credit-not-refund.md) | accepted | Önder Akkaya | 2026-08-29 | An undelivered generation is repaid as a durable credit, never a refund; the claim is taken before the money moves. |
| [`adr/glm-to-bedrock-llm-migration.md`](adr/glm-to-bedrock-llm-migration.md) | accepted | Dan Browne | 2026-06-24 | GLM → Bedrock. |
| [`adr/non-custodial-vault-owner-agent.md`](adr/non-custodial-vault-owner-agent.md) | accepted | Dan Browne | 2026-06-26 | Owner ≠ agent; non-custodial vaults. **Amended 2026-08-31** (product framing only, decision unchanged): the contracts are deployed; the user-facing vault journey is roadmap, gated off public surfaces (#1266/#1354/#1469). |
| [`adr/portfolio-constructor-consolidation.md`](adr/portfolio-constructor-consolidation.md) | accepted | Önder Akkaya | 2026-06-26 | Legacy constructor paths retired; dual-signal sizer activated. |
| [`adr/rigor-gate-unification.md`](adr/rigor-gate-unification.md) | accepted | Dan Browne | 2026-06-26 | One source of selection-bias truth. |
| [`adr/fusion-primary-generation.md`](adr/fusion-primary-generation.md) | superseded | Dan Browne | 2026-06-26 | Fusion-primary routing. Superseded by the debate-society record (2026-07-09). |
| [`adr/chainlink-primary-oracle.md`](adr/chainlink-primary-oracle.md) | accepted | Dan Browne | 2026-07-01 | Chainlink-primary oracles with a thin, bounded admin fallback. |
| [`adr/ec2-to-ecs-fargate-cutover.md`](adr/ec2-to-ecs-fargate-cutover.md) | accepted | Dan Browne | 2026-07-09 | Serving tier moved from one EC2 box to ECS Fargate behind the existing ALB. |
| [`adr/debate-society-sole-generation-pipeline.md`](adr/debate-society-sole-generation-pipeline.md) | accepted | Dan Browne | 2026-07-09 | The debate society is the only generation path; no fallback. |
| [`adr/num-trials-self-containment.md`](adr/num-trials-self-containment.md) | accepted, **pending quant sign-off** | Dan Browne | 2026-07-09 | DSR trial count depends only on the strategy's own search; curated strategies grade at num_trials = 1. |
| [`adr/aurora-postgres-alembic-datastore.md`](adr/aurora-postgres-alembic-datastore.md) | accepted | Dan Browne | 2026-07-28 | Aurora Serverless v2 (18.3) + Alembic; Redis 7.1 ephemeral-only. |
| [`adr/strategy-dsl-hardening-over-lean4.md`](adr/strategy-dsl-hardening-over-lean4.md) | accepted | Dan Browne | 2026-08-30 | No Lean 4 on the emission path; harden the existing closed-enum DSL instead. Sandbox reserved for inexpressible shapes; languages re-evaluated only on a trigger. |
| [`adr/market-data-sourcing.md`](adr/market-data-sourcing.md) | accepted | Dan Browne | 2026-08-31 | Market data is sourced **by surface**: Tiingo (Free tier, for testing) for backtesting and paid analysis; yfinance for the free, ungated Explore viewer, which sells and redistributes nothing. Flags a **Tiingo commercial plan as a mainnet prerequisite**, and records that the split is reversible by build — a full vendor swap is a config + adapter change, not surgery (#1218). |
| [`adr/lambda-generation-offload.md`](adr/lambda-generation-offload.md) | proposed (verdict: defer) | Dan Browne | 2026-08-30 | Measured spike (#1411): a real Lambda container built from the production backend image reaches Redis/Aurora/Bedrock/MiniLM from inside the VPC, but cold start is 13.6 s steady-state and 51 s after a deploy. Defers the lane; adopts the lane-agnostic worker entrypoint and the measured-cost model, and records why the quote seam is `_price()` rather than `quote()`. |

## Plans and roadmaps (intent, not state)

| Doc | Status | Owner | Last verified | What it is |
|---|---|---|---|---|
| [`account-authentication.md`](account-authentication.md) | runbook | Daniel Reis | 2026-08 | Better Auth deploy runbook: secrets, ECR, rollback (#1194); account linking, explicit link/unlink (#1420 follow-up; implicit auto-link stays off); account management — email/password change, session revocation, deletion (#1367). |
| [`plans/2026-07-28-account-auth-app-boundary.md`](plans/2026-07-28-account-auth-app-boundary.md) | plan | Daniel Reis | 2026-07-28 | The #1194 account-auth boundary plan. |
| [`plans/2026-08-15-core-app-visual-refresh.md`](plans/2026-08-15-core-app-visual-refresh.md) | plan | Daniel Reis | 2026-08-15 | Core-app visual refresh plan. |
| [`plans/2026-08-22-calm-precision-rebrand.md`](plans/2026-08-22-calm-precision-rebrand.md) | plan | Daniel Reis | 2026-08-22 | Calm-precision rebrand plan (PR #1469). |
| [`plans/2026-08-23-phantom-inspired-public-landing.md`](plans/2026-08-23-phantom-inspired-public-landing.md) | plan | Daniel Reis | 2026-08-23 | Public landing redesign plan (PR #1469). |
| [`plans/2026-08-23-security-posture-page.md`](plans/2026-08-23-security-posture-page.md) | plan | Daniel Reis | 2026-08-23 | Static /security posture page plan (PR #1469). |
| [`plans/2026-08-30-relations-phase2.md`](plans/2026-08-30-relations-phase2.md) | plan | Dan Browne | 2026-08-30 | Relations Phase 2 (builds on PR #1438): passport ↔ proposal ↔ user entity graph, FK/index plan with orphan-audit SQL, dead-column drops, `brief_intent` promotion, sizing and ordering. |
| [`plans/2026-08-30-intraday-paper-trading.md`](plans/2026-08-30-intraday-paper-trading.md) | draft | Dan Browne | 2026-08-30 | Intraday paper trading (v8 Lane 3.5). Recommends 15-min mark-to-market on open positions (`paper_marks` + retention + a runner-box loop) and defers intraday *signal* evaluation to v2 behind an ADR — bar-counted rebalance cadence and indicator warmup would silently change strategy semantics. |
| [`plans/2026-08-30-interpreter-unification.md`](plans/2026-08-30-interpreter-unification.md) | draft | Dan Browne | 2026-08-30 | Collapsing the two DSL interpreters (backtrader backtest vs live signal FSM) into one shared decision core: divergence inventory, architecture options, and a migration ratcheted on the parity suite. Cross-references the intraday-paper-trading plan (§4.1). |
| [`plans/2026-08-30-paper-trading-reasoning-traces.md`](plans/2026-08-30-paper-trading-reasoning-traces.md) | draft | Dan Browne | 2026-08-30 | Wiring paper-trading decisions into the commit-reveal trace pipeline (#1575). Where a paper decision is born (the settle-path rebalance boundary — marks never decide), the trace body next to the house agent's, owner stamping via #1556 (and why the zero-address sentinel would leak), passport reachability via #1569's `trace_references_strategy`, why on-chain anchoring is default OFF (consent + cost), the loud-failure design for an unpublished decision, and a numbered build plan. |
| [`plans/quant-roadmap.md`](plans/quant-roadmap.md) | plan | Önder Akkaya | — | The portfolio-math and backtest-rigor lane. |
| [`plans/spine-plus-v2-plan.md`](plans/spine-plus-v2-plan.md) | plan | Dan Browne | — | Spine+ v2 phase plan. |
| [`plans/second-wave-multi-asset-strategies.md`](plans/second-wave-multi-asset-strategies.md) | plan | Önder Akkaya | 2026-06-11 | Second-wave multi-asset strategies. |
| [`plans/paper-replication-spec.md`](plans/paper-replication-spec.md) | plan | Önder Akkaya | — | Replication and original-extension workflow. |
| [`plans/future-strategy-language-reeval-issue.md`](plans/future-strategy-language-reeval-issue.md) | draft issue | Dan Browne | 2026-08-30 | Unfiled Future Plans issue body: the trigger conditions under which the emission-language decision re-opens. Dormant by design — do not close for inactivity. |

## Sprint session cards (current)

| Doc | Status | Owner | Last verified | What it is |
|---|---|---|---|---|
| [`sprint/README.md`](sprint/README.md) | current | Önder Akkaya | 2026-08-21 | Index of per-session working cards for the Arc-mainnet sprint, plus the per-card completion status and the corrections to the cards' own claims. |

## Audits and findings

| Doc | Status | Owner | Last verified | What it is |
|---|---|---|---|---|
| [`audits/2026-06-14-full-tree-audit.md`](audits/2026-06-14-full-tree-audit.md) | audit — lineage head | Dan Browne | 2026-06-14 | Full-repo audit. Carries the only resolution ledger; supersedes the earlier audit chain. |
| [`audits/2026-06-14-gpt-oss-findings-verified.md`](audits/2026-06-14-gpt-oss-findings-verified.md) | audit | Dan Browne | 2026-07-28 | GPT-OSS findings, verified. |
| [`audits/2026-06-13-Onder-findings.md`](audits/2026-06-13-Onder-findings.md) | audit | Önder Akkaya | 2026-06-13 | Resilience and stress-test report. |
| [`audits/2026-07-09-curated-consolidation.md`](audits/2026-07-09-curated-consolidation.md) | audit | Önder Akkaya | 2026-07-09 | Curated-example consolidation build and verification. |
| [`audits/merge-handoff-2026-06-10.md`](audits/merge-handoff-2026-06-10.md) | historical log | Dan Browne | 2026-06-10 | Merge handoff for the 2026-06-10 remediation PRs. |
| [`audits/rigor-gate-fixes.md`](audits/rigor-gate-fixes.md) | needs owner | Önder Akkaya | — | A bare patch list with no findings, owner or dates. Should be tracked issues, then deleted. |

## Handovers and session logs (historical — not current state)

| Doc | Status | Owner | Last verified | What it is |
|---|---|---|---|---|
| [`handovers/2026-07-14-architecture-review.md`](handovers/2026-07-14-architecture-review.md) | historical log | Dan Browne | 2026-07-28 | Architecture-page redesign summary; evidence behind `architecture.md` §10. Its "what's stale" section describes a page that no longer exists — the redesign was **implemented in PR #1192**. |
| [`handovers/second-wave-handover.md`](handovers/second-wave-handover.md) | historical log | Önder Akkaya | — | Second-wave brief. |
| [`handovers/third-wave-handover.md`](handovers/third-wave-handover.md) | historical log | Önder Akkaya | — | Third-wave (fidelity) brief. |
| [`handovers/fourth-wave-handover.md`](handovers/fourth-wave-handover.md) | historical log | Önder Akkaya | — | Fourth-wave (gate-as-product) brief. |
| [`handovers/phase8-9-landing-and-fusion-spec.md`](handovers/phase8-9-landing-and-fusion-spec.md) | historical log | Dan Browne | 2026-05-24 | Phase 8/9 end-of-context session log. Named like a spec; it is not one. |

## Research and prompts

| Doc | Status | Owner | Last verified | What it is |
|---|---|---|---|---|
| [`research/README.md`](research/README.md) | reference | Dan Browne | — | Research artifacts index. |
| [`research/linus-archimedes-comparison.md`](research/linus-archimedes-comparison.md) | reference | Dan Browne | — | Bidirectional architecture comparison with Linus. |
| [`research/archimedes-to-linus-portbacks.md`](research/archimedes-to-linus-portbacks.md) | reference | Dan Browne | — | What Archimedes sends back to Linus. |
| [`prompts/quant-audit-prompt.md`](prompts/quant-audit-prompt.md) | template | Önder Akkaya | — | LLM prompt template for a quant audit. Not an audit. |
| [`prompts/agentic-issue-skeleton.md`](prompts/agentic-issue-skeleton.md) | template | Dan Browne | 2026-07-28 | Copy-paste skeleton for a judge-grade issue spec dispatched to the agentic system. |

---

## Working with the repo and the team

| Doc | Status | Owner | Last verified | What it is |
|---|---|---|---|---|
| [`CONVENTIONS.md`](CONVENTIONS.md) | current | Dan Browne | 2026-07-28 | Where a new doc goes, how it is named, its front-matter, and the ADR lifecycle. Read before adding any file. |
| [`team.md`](team.md) | current | Dan Browne | 2026-07-28 | Roster, lanes, review coverage, timezones, sync window. Extracted from `CLAUDE.md`. |
| [`agent-gotchas.md`](agent-gotchas.md) | current | Dan Browne | 2026-07-28 | Character-limited message surfaces (`wc -m`, not `wc -c`) and zsh quoting traps. Both were paid for. |
| [`submodules.md`](submodules.md) | current | Dan Browne | 2026-07-28 | The three submodules, what each is for, and the sticky-config one-liner a fresh clone needs. |
| [`agent-wiki.md`](agent-wiki.md) | current | Dan Browne | 2026-08-31 | Provenance note for the agent-generated wiki (`openwiki/`, landed in [#1597](https://github.com/a-apin/archimedes/pull/1597)) that the docs site publishes as its own nav section: what generated it (OpenWiki 0.4.3, coding-agent mode, no provider spend), the `docs/quant/`-only read boundary, that its claims are grounded in *documents* rather than in code, and that the pages are **not** line-by-line human-reviewed. Read this before citing anything under `/openwiki/`. |
| [`decisions/tooling-adoptions-2026-08.md`](decisions/tooling-adoptions-2026-08.md) | current | Dan Browne | 2026-08-31 | Register of developer tooling adopted in August 2026 and what each one cost and produced. Holds the standard "run it against a real corpus slice and record what it cost and what it produced, or remove it — installed is not a resting state," plus the OpenWiki row: the Bedrock blocker (Anthropic use-case form not submitted for the account), the `docs/quant/` run, and its verdict. **Placement is off-convention** — a closed-off decision belongs in [`adr/`](adr/README.md); this file sits at the path it was named at, and folding it into an ADR is open follow-up. |

---

## Conventions

- **No dates in top-level filenames.** Dated files belong in `audits/`, `handovers/` or `archive/`, named `YYYY-MM-DD-slug.md`.
- **A decision is stated once, in one ADR.** Prose docs link to the record; they do not
  restate or re-argue it. If a spec disagrees with an `Accepted` ADR, the spec is wrong —
  open a superseding ADR, do not patch the prose.
- **One kind per directory.** `specs/` holds specs. Session logs go to `handovers/`, roadmaps to `plans/`, findings to `quant/`, decisions to `adr/`, prompt templates to `prompts/`.
- **Never state a curated-library pass count in a doc.** Strategy pass/fail is whatever the live rigor gate returns; a number written here goes stale silently and has been wrong before. Point readers at the gate.
- **Contract counts come from `GET /api/config/contracts`**, not from prose. The tree currently holds 12 Solidity sources / 22 `.sol` files / 21 ABIs.
- **Archiving is a move plus a banner**, not a deletion: `> **ARCHIVED <date> — historical. Current: <path>**`.
