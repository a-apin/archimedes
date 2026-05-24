# Launch night operational runbook — overnight 2026-05-23 → 2026-05-24

> **Status:** ACTIVE OVERNIGHT. Dan went to sleep ~2026-05-24 04:50 UTC after authorizing Claude (Opus 4.7, this session) to execute the launch plan autonomously. This runbook is the operational addendum that overrides the launch plan's "file UNASSIGNED" universal rule for tonight only.

## Authorization Dan granted (verbatim, 2026-05-24)

- "Option A: file all 36 issues + M-work serially in this session"
- "I'll just want you to assign t2o2, I don't really need to do that. I want to sleep. You can assign t2o2 to trigger all the issues when they are ready and finally validated by Maestro (you)."
- "I want you to adjust the permission settings first to enable you to make gh calls without requiring my permission for all of them."

`.claude/settings.local.json` updated with a permissions allowlist covering `gh issue *`, `gh pr *`, `git push`, `git commit`, and read-only inspection commands so Dan isn't woken by approval prompts.

## Operational rules for the overnight run

1. **File issues UNDER `a-apin/archimedes-arcadia`** with the exact `APIN - <Area> - <Title>` prefix per the spec template.
2. **For BIG specs (per § 2.6 of the launch plan):** spawn a foreground Explore audit subagent FIRST to verify the surface (file paths, existing patterns, dependency reality). Fold the subagent's audit notes into the spec body before filing.
3. **Maestro validation after filing:** re-read the filed issue. Confirm acceptance criteria are grep-checkable + commands are runnable + scope is bounded + anti-goals are present. Only after this self-validation, assign t2o2.
4. **Assign t2o2 ONLY when confident.** If a spec has any uncertainty (unclear dependency, ambiguous acceptance, missing precedent), leave UNASSIGNED + add a comment explaining the open question. Dan triages in the AM.
5. **Respect serial dependencies.** Don't trigger t2o2 on a dependent spec before its parent has merged. Use `Depends on #N` in the issue body to make the chain explicit.
6. **Never push to `main`.** All work happens via PR. The plan PR (#145) gets merged at the start of the run; nothing else lands on `main` overnight without going through CI + PR review.
7. **Pause and document if a category of problem repeats.** If three audit subagents all fail to find an expected file, something has drifted from the plan — stop, document, leave the rest UNASSIGNED for Dan's review.
8. **Document everything** in a running log at the bottom of this file. Dan reads this first in the AM.

## Execution order

1. **Merge PR #145** (launch plan) so `docs/specs/launch-execution-plan-2026-05-23.md` is canonical on `main`.
2. **Foundation specs first** (file + Maestro-validate + assign t2o2):
   - T3.1 (S3 + DynamoDB + IAM) — Track C foundation
   - TS.1 (Route 53 + HTTPS) — domain unlock for TS.3 + T3.6 + production URL
   - TS.6 (IAM least-privilege) — security pillar foundation
   - T-PE.1 (StrategyRegistry.sol) — Track E foundation; contracts review needed (Chuan)
   - T1.3 (DepositFlow stepper) — Track A foundation
3. **Once foundations are filed (not necessarily merged):** file dependents in topological order per each Maestro prompt.
4. **Interleave M-work between batches** so Dan's awake-time priorities (M.4 docs refresh, M.11 telemetry backfill) make visible progress:
   - M.11 first (cheapest 30%-rubric-weight win) — backfill `arc-canteen update-product` for Phase 4-9 + KB integration + 10-contract deploy + Phase 8/9 UI ship.
   - M.4 deck + docs refresh per the "Documents to align" list in § 8.
   - M.5 docs sweep + archive (move stale planning docs to `docs/archive/`).
   - M.9 visual review pass (Playwright + multimodal at 4 breakpoints).

## Anti-goals overnight

- DO NOT register the domain ourselves — T-spec assigned to t2o2 does it via `aws route53domains register-domain` in Chuan's AWS account.
- DO NOT modify contracts/ — Solidity work is contract-review-grade; T-PE.1 is the only contract spec and it goes UNASSIGNED if there's any uncertainty.
- DO NOT push commits to `main` directly. Branch-and-PR or nothing.
- DO NOT commit any secrets / `.env` / private keys.
- DO NOT regress the `pytest -q` baseline (361 passed). If a spec's acceptance criteria would shrink the pass count, leave UNASSIGNED.
- DO NOT spawn so many parallel subagents that the session loses coherence. Foreground audit subagents one at a time, then continue.
- DO NOT block on Marten — his early submission is fine as-is; M.7 video v2 is an additive re-record after T3.8 lands.

## Post-compact handoff prompt (copy-paste for next session)

```
Resuming overnight Archimedes launch execution. Plan is on main at
docs/specs/launch-execution-plan-2026-05-23.md. Operational runbook is at
docs/specs/launch-night-operational-runbook.md — read it first; it has the
authorization Dan gave, the operational rules, the execution order, the
anti-goals, and the running log.

Continue per the runbook. 36 bot specs to elaborate + file + Maestro-validate
+ assign t2o2 (or leave UNASSIGNED if uncertain — Dan reads the runbook in AM
to triage). Plus M.11 arc-canteen backfill + M.4 docs refresh + M.5 docs
sweep + M.9 visual review pass. Interleave M-work between issue batches per
the runbook's execution order.

Permission settings already wired (.claude/settings.local.json) so gh + git
calls don't prompt Dan. Don't ask new questions unless something is genuinely
blocking; Dan is asleep. Use the running log at the bottom of the runbook to
record decisions + progress + anything Dan needs to see in the AM.

Plan: docs/specs/launch-execution-plan-2026-05-23.md
Strategy Passport architecture: docs/diagrams/strategy-passport-architecture.md
Implementation contract: docs/specs/strategy-passport-spec.md
Linus orient (validates K=1 + externally-verifiable-hashes choices):
  submodules/Linus/docs/audits/2026-05-22-reveal-prep/archimedes-orient.md
  submodules/Linus/docs/audits/2026-05-22-reveal-prep/strategy-engine-linus-flavor.md
```

## Running log (Maestro fills this in as it works)

### Session start (UTC timestamp): 2026-05-24T05:22Z

- Plan PR #145 merge status: MERGED (commit 9da7035, 2026-05-24T05:17:30Z)
- Linus pin: f42e484 (latest)
- KnowledgeBase pin: 9032783 (from M.1 earlier)
- main HEAD at session start: 9da7035 (post-compact resume)
- Target repo: a-apin/archimedes-arcadia (confirmed via gh repo view)
- Labels available: bug, documentation, enhancement, help wanted, question, after-hackathon (no custom labels yet; can add as we go)

### Issue filing log

| # filed | t2o2 assigned? | Spec | Notes |
|---|---|---|---|
| #147 | yes | T3.1 — S3 + DynamoDB + IAM foundation | Filed verbatim from plan; precedent verified (infra/main.tf exists) |
| #148 | yes | TS.1 — Route 53 + HTTPS for archimedes-arc.app | **Path drift caught**: spec said `infra/nginx/nginx.conf`; actual is `nginx/nginx.conf` (top-level). Correction posted as comment before assigning t2o2 |
| #149 | yes | T-PE.1 — StrategyRegistry.sol + strategy_publisher.py | **BIG spec — audit subagent ran first**. 3 corrections folded into body: (1) tests are flat (`backend/tests/test_strategy_publisher.py`), not in a `chain/` subdir; (2) Tier-1 promotion hook lives in `models/strategy_store.py` line ~147, NOT `agent_runner.py` — spec corrected; (3) `on_chain_registration_tx` does NOT exist on `StrategyRecord` today — bundled the schema migration into this issue (idempotent ALTER TABLE). All other paths verified |
| #150 | yes | T1.3 — DepositFlow stepper modal | Config.js drift corrected in body: export is `USDC` (not `USDC_ADDRESS`); no `USDC_ABI` exists today (bot adds minimal fragment); VAULT_ABI exists at line 294 (verify includes deposit + setTargetAllocations) |
| #151 | yes | T3.2 — GPU EC2 + KB pipeline run | Long-pole (4-6h cold start) — file early per plan |
| #152 | yes | T3.3 — Corpus graph + KG endpoints read S3 artifacts | Depends on T3.2 |
| #153 | yes | T3.4 — CorpusGraph + CorpusKG UI render real data | Depends on T3.3; adds react-force-graph-2d |
| #154 | **NO (OPTIONAL)** | T3.5 — Bedrock migration | Plan says OPTIONAL; left UNASSIGNED per plan |
| #155 | **NO (HOLD)** | T3.6 — ALB + CloudFront + ASG | **BIG audit ran** — 9 corrections folded. **HELD UNASSIGNED** + comment posted recommending Dan AM triage (high blast: replaces production routing; suggests waiting for T1.3/T1.4/T1.5 + TS.1 cert to land first) |
| #156 | yes | T3.7 — Xia 2026 named protocols | **BIG audit ran** — 5 corrections folded (column is `published` not `publication_date`; V_check insertion at agent_runner.py:309-314; `source_papers` extension not new fields; `_HASH_FIELDS` in `trace.py` not `trace_publisher.py`; no schema migration needed for `content_hash`/`ingested_at`) |
| #157 | yes | T3.8 — StockBench harness adapter | **BIG audit ran** — corrections folded (`backend/archimedes/benchmarks/` + `docs/benchmarks/` MISSING; procedural async script pattern; `finnhub-python` not in env; entry-points `StrategyFusion.propose:513` + `PortfolioAgent.propose_portfolio_with_tools:285`) |
| #158 | yes | T3.9 — paper-qa semantic-retrieval wrapping | Quick correction: function is `select_candidates()` (no leading underscore) at strategy_fusion.py:303; routes.py exists (no separate health_routes.py). MUST-SHIP per Dan |
| #159 | yes | T-PE.2 — Multi-paper passport refactor | Verified: `class Strategy` at strategy.py:56; 6 curated files in analytics-engine/strategies/ all need `PAPER_ARXIV_ID` → `PAPER_ARXIV_IDS` rewrite |
| #160 | **NO (HOLD)** | T-PE.3 — Unified strategy_passports table + migration | **HELD UNASSIGNED** + comment posted: **irreversible Postgres migration** — Dan/Chuan should eyes-on the migration script + take pg_dump before firing |
| #161 | yes | T-PE.4 — Rewrite strategy-passport-spec.md | Docs only |
| #162 | yes | T-PE.5 — regime_tag schema + curated library tagging | All 6 curated files identified with explicit REGIME_TAG values in body |
| #163 | **NO (HOLD)** | T-PE.6 — Always-both generation (bull + bear) | **HELD UNASSIGNED** + comment posted: **2x LLM cost per Generate** — needs Dan cost-budget green light (fine for GLM-4.5, expensive on Bedrock Opus) |
| #164 | **NO (HOLD)** | T-PE.7 — Regime-aware portfolio weighting | **HELD UNASSIGNED** + comment posted: **touches live trading rebalance flow** — wait for T1.3/T1.4/T1.5 to verify single-regime path works first |
| #165 | yes | T-PE.8 — strategy_proposals episodic table | MUST-SHIP per Dan; additive table; makes "library compounds" claim demonstrable |
| #166 | yes | T2.1 — Home page sidebar parity + CTA differentiation | All Track B precedent paths verified |
| #167 | yes | T2.2 — Generate UI consolidation | |
| #168 | yes | T2.3 — Explore page real oracle prices | |
| #169 | yes | T2.4 — Corpus polish (Catalog default + plain-English labels) | |
| #170 | yes | T2.5 — Reasoning Verify-on-chain ENHANCEMENT | Rescoped (button already works; this adds arcscan tx + block surface) |
| #171 | yes | T2.6 — Portfolio Recent Agent Activity honesty | |
| #172 | yes | T2.7 — WelcomeProfileModal + personalized header | |
| #173 | yes | T2.8 — agents/ subpackage refactor | |
| #174 | yes | T1.4 — /api/health/amm + agent_runner polls VaultFactory | Verified VaultFactory ABI binding at contracts.py:57 |
| #175 | yes | T1.5 — End-to-end testnet smoke evidence | Demo-critical evidence file |
| #176 | yes | TS.2 — Secrets to AWS SSM Parameter Store + IAM | |
| #177 | yes | TS.3 — Nginx security headers | Path correction in body: `nginx/nginx.conf` (not `infra/nginx/`) |
| #178 | yes | TS.4 — CORS lockdown | |
| #179 | yes | TS.5 — Rate limiting (slowapi + Redis) | |
| #180 | yes | TS.7 — Dependabot + secret scanning + pre-commit detect-secrets | Dan toggles 3 Settings manually; bot adds hook + baseline |
| #181 | yes | TS.8 — User-data minimization (encrypt email at rest, log scrubbing, owner-only echo) | |

**TS.6 (IAM least-privilege)** = NO standalone issue per plan; rolled into T3.1 (#147) + T3.5 (#154).

**Totals:** 35 issues filed (#147-#181). **30 assigned to t2o2** (will fire as bot picks them up). **5 held UNASSIGNED for Dan AM triage**: #154 T3.5 (OPTIONAL Bedrock), #155 T3.6 (high-blast ALB/CloudFront), #160 T-PE.3 (irreversible migration), #163 T-PE.6 (2x LLM cost), #164 T-PE.7 (touches live trading). Each held issue has a Maestro comment explaining the recommended sequencing.

### M-track progress

| ID | Status | Artifact | Notes |
|---|---|---|---|
| M.11 | **partial-done** (product side) | 8 `arc-canteen update-product` calls submitted covering: launch plan + 36-issue execution kickoff, Phase 4+5 scaffolding (PR #142/#143), Track E Strategy Passport architecture spec, DevOps quality bar (PR #146), Linus + KB submodule pins + audit fold-in, Phase 8+9 UI ships, 10-contract Arc testnet deploy retrospective, Daniel R UI fixes (PR #144), KB pipeline integration Day-11 with corpus seed breakdown. **One stray test entry exists in history** (`TEST: Maestro telemetry probe`) — pushed off the visible 5-entry recent-updates window by the 8 real entries above; no delete CLI exists, but no longer surface-visible. **Traction backfill skipped** — needs Dan's first-hand knowledge of which users/judges he's talked to (the M.11 plan target is ≥10 traction events; Dan should backfill in AM). |
| M.4 | pending | docs refresh | |
| M.5 | pending | docs/archive/ sweep | |
| M.9 | pending | visual review report | |

### Decisions made overnight (Dan reads in AM)

- (Maestro records here)

### Blockers encountered (Dan reads in AM)

- (Maestro records here)
