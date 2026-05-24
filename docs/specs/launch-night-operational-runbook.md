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
| M.5 | **audit-done; AM execution pending** | docs/archive/ sweep punch list captured below | Mechanical `git mv` job — left for Dan AM so the `docs/README.md` index update happens in the same commit (avoid link breakage on `main`) |
| M.9 | **deferred to Dan AM** | visual review report | Marten already shipped demo video v1 + early submission; M.9 is polish-tier; live HTTPS site (`archimedes-arc.app`) doesn't exist yet (TS.1 #148 still queued); recommend Dan AM runs visual review against localhost OR after TS.1 lands so screenshots reflect the production URL bar |

### Decisions made overnight (Dan reads in AM)

- **Override on overnight t2o2 assignment authority used selectively.** Dan authorized "assign t2o2 to trigger all the issues when they are ready and finally validated by Maestro (you)." I assigned 30 of 35; held 5 UNASSIGNED with comments where blast radius warranted Dan triage (T3.5 OPTIONAL, T3.6 production routing replacement, T-PE.3 irreversible migration, T-PE.6 2x LLM cost, T-PE.7 live trading rebalance). Each held issue has a comment explaining the recommended sequencing.
- **Spec-drift handling.** 4 specs had non-trivial path drift vs `main` (TS.1 + TS.3 nginx path; T-PE.1 multiple; T1.3 config.js; T3.6 9 corrections). All corrections folded into issue bodies BEFORE assigning t2o2, so the bot reads the corrected spec, not the stale plan reference. Per runbook rule 7 ("three subagents fail = stop") — drift was correctable each time, did not trigger the stop condition.
- **M.4 sweep executed as PR #182 (not auto-merged).** Mechanical `hackagora → a-apin` rename across 16 files. Dan reviews + merges in AM. Quality gate will run on it. Note: this PR also folds in the runbook progress log (this file) so the AM read is one diff to scan.
- **M.5 docs sweep: audit-only tonight.** Subagent surfaced 14 ARCHIVE + 2 DELETE + 9 UPDATE candidates. **Mechanical `git mv` deferred to Dan AM** so the `docs/README.md` index update happens in the same commit (avoids breaking internal doc links on main between commits).
- **M.9 visual review deferred.** Marten's demo v1 already shipped + early submission filed. Recommend Dan runs M.9 either against localhost OR after TS.1 (#148) lands so screenshots show the production HTTPS URL.
- **arc-canteen telemetry: product side done (8 real updates); traction side skipped.** I don't have first-hand knowledge of which judges/users Dan has talked to. Recommend Dan logs `update-traction` calls in AM for any user/judge conversations from the past week.

### Blockers encountered (Dan reads in AM)

- **One stray arc-canteen test entry in history.** Probed `arc-canteen update product` stdin behavior with a test message; the test landed as a visible product update. No delete CLI exists. Pushed off the visible 5-entry window by 8 real updates, but it's still in `arc-canteen ls` history. **Recommend Dan asks Canteen admin (Anuhya) whether stray entries can be retroactively deleted** if it matters for the rubric. If not, the 8 real ones dwarf it.
- **None of the 30 assigned t2o2 issues had landed PRs as of session end (~05:30 UTC).** Bot system queue + parallel execution + dependency chains mean realistic landing window is hours, not minutes. Dan should expect a stream of PRs by Sun AM/early afternoon. **Verification protocol per launch plan § 2.5**: for each landed PR, run the originating issue's acceptance commands on a cold clone OR spawn a verification subagent; do NOT trust "closed" without grep proof.
- **The 5 HELD UNASSIGNED specs need explicit Dan green-light** before t2o2 picks them up. Each has a comment recommending sequencing.

### M.5 docs-sweep punch list (from M.5 audit subagent 2026-05-24)

Subagent summary: 17 KEEP / 9 UPDATE / 14 ARCHIVE / 2 DELETE / 42 total audited.

**ARCHIVE (14 files — mechanical `git mv` to `docs/archive/`):**
- `docs/launch-plan.md` — Day-8 reveal narrative; superseded by `docs/specs/launch-execution-plan-2026-05-23.md`
- `docs/ui-simplification-proposal.md` — Day-9 proposal; PR #118 shipped strip-to-spine
- `docs/specs/fusion-to-backtest-t2o2-issue.md` — shipped (#128, #133)
- `docs/specs/ipfs-reasoning-traces-design-note.md` — never implemented; superseded by keccak256 anchor that did ship
- `docs/specs/phase7-llm-backend-unification-t2o2-issue.md` — shipped (#130, dc91b43)
- `docs/specs/phase7-portfolio-constructor-retirement-t2o2-issue.md` — shipped (#131, a4a09fb)
- `docs/specs/phase7-rigor-consolidation-t2o2-issue.md` — shipped (#129, e030ee4)
- `docs/specs/phase7-routes-py-split-t2o2-issue.md` — shipped (#132, be9260b)
- `docs/specs/phase8-9-landing-and-fusion-spec.md` — shipped (PR #140 + #141)
- `docs/specs/portfolio-constructor-decision-tree.md` — decision was executed in #131
- `docs/specs/spine-plus-v2-plan.md` — Phases 0,1,2,3a,3b,6,7 LANDED; substantive content is now history

**DELETE (2 files — stale HTML artifacts):**
- `docs/architecture-diagram.html` — May-13; superseded by `docs/diagrams/strategy-passport-architecture.md`
- `docs/specs/ecosystem-architecture.html` — May-13; superseded by `docs/specs/ecosystem-design-spec.md`

**UPDATE (9 files — content refresh per launch plan § 8 M.4; defer to M.4 refresh pass):**
- `README.md` — top-fold + `archimedes-arc.app` URL + Cited-literature + RFB-04 statement
- `CLAUDE.md` — pitch frame § 3.1 + HTTPS domain + K=1+rigor as 5th primitive
- `ARC-OSS-SHOWCASE.md` — TS + T3.6 + T3.7 + T3.9 + T-PE.8 forkable primitives
- `docs/README.md` — index trim after archive sweep
- `docs/anti-features.md` — security/BYOK/no-returns + StockBench non-claim
- `docs/architectural-principles.md` — Xia/StockBench + K=1+rigor
- `docs/competitor-landscape.md` — rosetta-alpha + Xia 19-study positioning
- `docs/demo-script-pitch-deck-outline.md` — Xia slide 1, RFB-04 slide 2, StockBench bar, K=1 + substrate slides
- `docs/judging-rubric-assessment.md` — Day-12 score with TS + T3.6/7/8/9 + T-PE.8
- `docs/specs/strategy-fusion-spec.md` — Day-12 truth-up (post-#128/#133 fusion-to-backtest)

**KEEP (17 — leave alone):** all `docs/adr/`, `docs/diagrams/`, `docs/research/` files; `docs/{arc-alignment,chuan-architecture-survey,claude-design-prompts,corpus-architecture,infra-setup,pitch-talking-points-rigor-track,portfolio-advisor-demo-cues,rigor-methods,traction-logging,user-stories}.md`; `docs/specs/{commit-reveal-trace-spec,component-interfaces-spec,ecosystem-design-spec,generation-streaming-spec,kb-integration-spec,launch-execution-plan-2026-05-23,launch-night-operational-runbook,page-roles-spec,paper-replication-spec,phase5-execution-runbook,selection-bias-corrections-spec,strategy-dsl-spec,strategy-lifecycle-spec,strategy-passport-spec,vault-semantics-spec}.md`; top-level `ARC.md`, `ARC-OSS-FORM-DRAFT.md`, `OPERATIONS.md`, `SETUP.md`.

**Caveat from subagent:** `docs/specs/phase5-execution-runbook.md` is referenced by canonical launch plan § 4 + T1.5 spec. KEEP through Track A landing; auto-archives once T1.5 ships (handled by M.10 final-polish subagent).

### Session-end summary (Maestro, 2026-05-24 ~05:35 UTC)

**Filed:** 35 bot issues #147-#181 across all 5 tracks (T1.x, T2.x, T3.x, T-PE.x, TS.x).
**Assigned t2o2:** 30 issues — bot system will process per Depends-on chain.
**Held UNASSIGNED for Dan AM triage:** 5 — T3.5 (OPTIONAL Bedrock), T3.6 (high-blast ALB+CloudFront), T-PE.3 (irreversible migration), T-PE.6 (2x LLM cost), T-PE.7 (live trading). Each has a Maestro comment.
**M.11 partial:** 8 real product updates submitted; traction backfill needs Dan first-hand.
**M.4 partial:** hackagora→a-apin sweep done (PR #182 open); doc-content refresh deferred to AM.
**M.5 audit-done:** 14 ARCHIVE + 2 DELETE + 9 UPDATE punch list above; mechanical execution deferred to AM.
**M.9 deferred:** Marten already shipped demo v1; M.9 polish-tier; recommend post-TS.1.

**Recommended first AM moves for Dan (in order):**
1. Read this runbook (you're doing that).
2. Review + merge (or close + comment) PR #182 (M.4 sweep + runbook progress).
3. Triage the 5 HELD UNASSIGNED specs: comment-by-comment for each, decide assign-now / assign-after-X-lands / re-spec.
4. Execute M.5 archive sweep (15-20 min): `git mv` the 14 files + `git rm` the 2 + update `docs/README.md` index in one PR.
5. Check landed t2o2 PRs (likely a stream by Sun AM): for each, run the originating issue's acceptance commands on cold-clone OR spawn verification subagent per launch plan § 2.5.
6. Backfill arc-canteen traction with any user/judge conversations from past week.
7. Then M.4 content refreshes (deck, README, etc.) + M.9 visual review when ready.
