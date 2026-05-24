# Morning execution plan — 2026-05-24 (post-overnight + post-pi-verification)

> **Status:** Durable artifact written before compaction to preserve the plan.
> Authored by Maestro (Claude Opus 4.7) under Dan's steering, after morning
> inventory + verification round-trip with Chuan's pi bot (moonshot).
> **Next step after this file lands: compact, then execute Workstream 1 PR-by-PR.**

## TL;DR

Overnight: 26 of 35 bot specs shipped. Morning verification (Maestro + pi bot
cross-check) found **3 true fake-ships** that need follow-up (T2.1, T2.2 frontend
half, T2.3), down from 5-6 I initially flagged. Two of my flags (#169 Catalog
default, #172 user_routes registration) were **wrong** — bot verified actual main
state and those did ship correctly. Going forward: Dan + Maestro execute
page-by-page surface PRs ourselves; Chuan's bot handles the small grep-checkable
fix (T2.3 #168 zero-commit reopen); 5 held specs stay held per recommendation.

## Pi bot exchange takeaways (2026-05-24 AM)

Bot verified each of my 6 flagged closures by running actual code state checks
on current main. Net result:

| Issue | Maestro's flag | Pi bot's verification | Final verdict |
|---|---|---|---|
| #166 T2.1 Layout wrap | 1-line edit, no `<Layout>` | ✅ Confirmed — commit `92f2231d` only changed a CTA nav param; Landing.jsx has no `<Layout>` import or wrap | **Fake — file follow-up** |
| #167 T2.2 Generate single input | Wrong files touched | ✅ Confirmed bad on frontend; `_pick_pipeline` DOES exist in backend from a prior commit | **Fake — file follow-up for frontend half only** |
| #168 T2.3 Explore oracle prices | Zero commits | ✅ Confirmed — `git log --grep="#168"` returns nothing | **Fake — file follow-up for t2o2** |
| #169 T2.4 Catalog default | `defaultTab` 0 matches | ❌ **Maestro was wrong.** Commit `fcc6938c` correctly changed `useState('overview') → useState('catalog')`. Maestro's grep used the wrong pattern (`defaultTab` vs `useState('catalog')`). | **Keep closed — shipped fine** |
| #172 T2.7 user_routes 62 errors | Not registered in `__init__.py` → 62 errors | ❌ **Maestro was wrong.** `user_router` IS registered correctly in `main.py:177` (FastAPI pattern doesn't use `__init__.py`); all 12 user_routes tests pass; full pytest = 560 passed / 1 failed locally on bot's env. Maestro's earlier "62 errors" came from a stale local working tree. | **Keep closed — shipped fine** |
| #173 T2.8 agents/ subpackage | Self-retracted earlier | ✅ Bot confirmed shipped fine | **Keep closed** |

**Net real fake-ships: 3** (#166, #167-frontend, #168). My initial 5-6 read was
inflated by stale-working-tree pytest + wrong-grep-pattern.

**In-flight status (4 issues):** All 4 still OPEN + assigned. **#147 is the
critical path** — #148 (TS.1 cert), #151 (T3.2 GPU pipeline), #176 (TS.2 SSM)
are all explicitly blocked on #147 landing first. Bot has no commits on #147
yet; awaits AWS credential surface from Chuan.

**Calibration outcome:** Bot acknowledged the calibration feedback and
committed to a new pre-close protocol:
1. Run every acceptance-criteria command from the issue spec
2. Grep-verify every anti-goal ("DO NOT keep X") is absent before closing
3. If any check fails → don't close, comment with evidence

This is the right fix. Going forward we trust the bot more on small specs and
verify anti-goals first ourselves on the structural ones.

## Workstream 1: Page-by-page surface PRs (Dan + Maestro author)

Order optimized for visual ROI + smallest diffs + avoiding merge conflicts
with t2o2 in-flight work.

### PR-1: Landing junk purge + T2.1 Layout follow-up
**Issue this closes:** #166 follow-up
- Remove the "Confidence 92% / Regime risk_on / Strategies 6 / Contracts 10"
  ribbon above hero — it's a `RegimePanel` dump that means nothing without
  context. Either delete or move + explain. Dan's call.
- Wrap Landing in `<Layout>` so sidebar + topbar render (T2.1 acceptance)
- Remove the local `<header>` in `Landing.jsx:41-64` (Layout's topbar
  replaces it)
- Confirm hero CTAs route correctly (Generate vs Library?tab=examples
  already shipped per #166's incomplete fix)

### PR-2: Portfolio page simplification (shrink + explain pass, no kills yet)
**Issue this closes:** none (no original spec; surface cleanup)
- **Shrink** the huge green "RISK ON" banner to a sidebar pill with a
  short explanation tooltip; don't kill yet (Dan reserves the right to
  kill after shrink if still unhappy)
- **Honest empty state** for vaults: route to "No vaults yet — Generate
  a strategy to deploy your first" when 0 real vaults; remove the fake
  "6 vaults / $10.11 AUM" cards
- **DEFER Portfolio Advisor structural change** to a separate decision
  (see "Portfolio Advisor user-journey design" section below)
- Less-is-more: cut anything that doesn't directly serve "show me my
  vaults + their state"

### PR-3: Wallet menu + Profile view/edit
**Issue this closes:** none (UI gap; T2.7 backend is done)
- Click wallet button → dropdown menu with "Profile" + "Disconnect"
  (currently click-to-disconnect is the only behavior; lost-after-save
  modal complaint)
- "Profile" opens the same `WelcomeProfileModal` in edit mode with
  current values pre-filled
- Topbar displays "Welcome, `<display_name>`" when set; falls back to
  truncated wallet address (`0x...5105`) otherwise

### PR-4: Generate `list_strategies` bug fix
**Issue this closes:** none (real bug observed in screenshot)
- Investigate the `'function' object has no attribute 'list_strategies'`
  error in fusion candidate ranker. Likely a `strategy_provider` shim
  wiring issue from T-PE.2/T-PE.3 cascade — the provider is being
  called as a function instead of an object, or the migration shim
  swapped a class for a function.
- Demo-critical: Generate is the spine entry point.

### PR-5: Library Examples surface + T2.2 frontend follow-up
**Issue this closes:** #167 follow-up (frontend half)
- Library `Examples` tab is currently empty — surface the 6 curated
  strategies (Faber 2007 SMA200, Moskowitz TSMOM, Moreira-Muir
  vol-managed, George-Hwang 52W high, buy-and-hold baseline,
  capital-preservation T-Bill). Wire to existing `strategy_provider`.
- Remove the mode picker from Generate.jsx (the T2.2 frontend half
  that wasn't shipped). Backend `_pick_pipeline` already exists (per
  pi bot verification), so frontend just needs to render the result
  the backend picks based on the `pipeline_selected` SSE event.

### PR-6: CI fixture cleanup
**Issue this closes:** none (bot-introduced regression)
- Fix `test_run_backtests_is_idempotent` — fixture likely doesn't
  set `REGIME_TAG` after T-PE.5 enforcement landed
- Investigate + fix `test_stockbench_adapter test_dry_run_exits_zero`
  (if still failing on current main; pi bot didn't mention this one in
  the verification round)

### PR-0 (CONSIDER): Ruff cleanup + linting hardening (NEW — added 2026-05-24)
**Why:** Dan ran `ruff check` locally → **244 errors found**, 170 auto-fixable.
This is the reason for things like the unresolved `strategy_architect` import
in `strategy_fusion.py` slipping through. The CI ruff job runs but is
**informational only** (continue-on-error per PR #146).

**Recommended approach:**
1. One bulk-cleanup PR: `ruff check --fix --line-length 120` +
   `ruff format --line-length 120` on the whole repo. Review the diff
   carefully (auto-fix can be wrong on edge cases).
2. Address the 74 remaining manual-fix errors in a follow-up commit
   (these are usually real bugs ruff is flagging).
3. Update `.github/workflows/quality-gate.yml` to make ruff **blocking**
   (remove `continue-on-error`).
4. Add pre-commit hook for ruff alongside detect-secrets (TS.7 #180).

**Order question for Dan:** do PR-0 BEFORE PR-1 (cleans the surface for
everything else) OR AFTER PR-1 (don't let lint cleanup block visual wins)?
Maestro recommends BEFORE: the lint cleanup will reveal real bugs (like
the strategy_architect import resolution) that we'd otherwise hit blind
on PR-4 (Generate bug fix). Plus it's a single mechanical PR — review
takes 5 min on the diff.

## Workstream 2: Follow-up issue filed for t2o2

Only one issue qualifies as "simple enough for the bot + grep-checkable
acceptance":

- **T2.3 #168 follow-up** — Explore page real oracle prices. Original
  closed with zero commits; full re-execution needed. Acceptance criteria
  identical to original #168 (oracle wiring + Explore.jsx stale badges +
  empty state). File new issue + assign t2o2 with explicit "the original
  closure was premature, here's what specifically still needs to happen."

## Workstream 3: Branch + PR rebase/merge cleanup (EXPANDED)

**State (verified 2026-05-24):**
- 12 merged remote dependabot branches lingering (origin/dependabot/...)
  — code is on main, branches not deleted
- **PR #189** (redis dep bump) — ONLY open dependabot PR; MERGEABLE
  but CI failing on main regression (`test_run_backtests_is_idempotent`)
- **PR #182** (overnight M.4 sweep) — open; 40+ commits behind main;
  pure docs; safe to rebase
- **PR #196** (this morning plan artifact) — open; pure docs

**Sub-plan (explicit, ordered):**

| Step | Action | Depends on | Risk |
|---|---|---|---|
| 3a | Delete 12 merged remote dependabot branches (`git push origin --delete origin/dependabot/...` for each except `redis-gte-7.4.0`) | none | zero — pure cleanup |
| 3b | Rebase PR #182 onto current main: `git checkout dbrowneup/m4-hackagora-sweep && git pull --rebase origin main && git push --force-with-lease` | none | low — pure docs; rebase will likely have minor conflicts in runbook (m4 branch has overnight runbook updates; main has empty stub) |
| 3c | Retitle PR #182 to `[docs] M.4 sweep half — hackagora → a-apin + runbook log` (gh pr edit) | 3b | none |
| 3d | Verify PR #182 CI green; if green, merge | 3b, 3c | none |
| 3e | After PR-6 lands and `test_run_backtests_is_idempotent` passes on main, PR #189 CI auto-re-runs | PR-6 | none — let CI gate; do NOT manually rebase |
| 3f | If PR #189 CI clears, merge | 3e | low — safe minor dep bump |
| 3g | Delete `origin/dependabot/pip/backend/redis-gte-7.4.0` branch after merge | 3f | zero |
| 3h | Merge PR #196 (this artifact) when Dan reviews | Dan review | zero |

**Anti-goals for Workstream 3:**
- DO NOT rebase PR #189 manually — Dependabot manages its own rebases
  via comments (`@dependabot rebase`); manual rebase fights the bot
- DO NOT delete `origin/dependabot/pip/backend/redis-gte-7.4.0` until
  PR #189 merges or is closed
- DO NOT force-push main; PR #182 + PR #196 + PR #189 each go through
  PR merge flow

## Workstream 4: M.4 content refresh (defer to Sunday afternoon)

9 docs to refresh per launch plan § M.4 "Documents to align":
- `docs/demo-script-pitch-deck-outline.md` — deck refresh (Xia slide 1,
  RFB-04 slide 2, StockBench bar, K=1 + substrate slides)
- `docs/specs/claude-design-prompts.md`
- `docs/competitor-landscape.md` (add rosetta-alpha; Xia 19-study positioning)
- `docs/judging-rubric-assessment.md` (Day-12 score with TS + T3.6/7/8/9 + T-PE.8)
- `docs/anti-features.md` (BYOK + StockBench non-claim)
- `README.md` (top-fold + Cited-literature + RFB-04 statement)
- `ARC-OSS-SHOWCASE.md` (TS + T3.6 + T3.7 + T3.9 + T-PE.8 forkable primitives)
- `CLAUDE.md` (K=1 as 5th architectural primitive)
- `docs/benchmarks/stockbench-results.md` + `docs/specs/xia-2026-protocols.md`
  (committed by T3.7/T3.8 PRs)

Goes into one polished PR after the page-by-page surface work + Track A/C/E
in-flight all land. Reasonable target: Sunday afternoon, post-T-tracks.

## Workstream 5: Close stale issues (NOT reopen — per Dan's directive)

For each true fake-ship (#166, #167, #168), close the original with a
comment linking to the follow-up:

```
Closed — superseded by follow-up #<new-#> with explicit "what
specifically still needs to happen." Original closure was premature per
Maestro+pi-bot verification 2026-05-24 (see
docs/specs/morning-execution-plan-2026-05-24.md for details).
```

Original issues #166, #167, #168 are ALREADY closed by t2o2. We don't
reopen — just leave them closed + comment with the follow-up link.

## Portfolio Advisor user-journey design — needs discussion

**Dan's question:** How is the user supposed to use Portfolio Advisor?
What purpose does it serve? Where does it fit in the user journey?

**Maestro's analysis:**

Current state appears to be: Portfolio Advisor lives at `/portfolio`
behind a small expandable text tab, takes a risk profile, returns an
allocation breakdown (Kelly + risk parity). It's structurally
disconnected from the Generate page and from the actual vaults the user
has deployed.

**Three possible re-framings** for discussion:

1. **Merge into Generate** as a "second opinion" panel. After Generate
   produces a strategy, render Portfolio Advisor inline showing the
   Kelly + risk-parity sizing for the same brief. Two views of one
   decision: fusion's paper-grounded allocation vs Portfolio Advisor's
   math-first allocation. Lets the user compare. Cost: Generate page
   gets busier.

2. **Repurpose as per-vault inspector.** On the Portfolio page, clicking
   a vault drills into a detail view showing: current allocation,
   regime tilt, recent rebalances, paper provenance, rigor verdict.
   The "Portfolio Advisor" name goes away; the functionality becomes
   "vault detail view." This is probably what users actually want when
   they land on `/portfolio` after deploying.

3. **Kill it entirely.** Fusion engine + Reasoning page already cover
   the user need ("here's what I would do + here's why"). Portfolio
   Advisor as a separate concept adds confusion. Honest answer: it
   shipped as a hackathon-week sub-feature that didn't get integrated
   into the spine.

**Maestro recommends option 2** — vault inspector. The user journey is:
Generate (produces strategy) → Deploy (CreateVaultModal + DepositFlow) →
Portfolio (list of my vaults; click one for detail) → Reasoning (why
the agent made each decision). Portfolio Advisor as a separate concept
doesn't fit this flow. Repurposing the math (Kelly + risk parity sizing)
as part of vault detail is a clean integration that doesn't waste the
work.

**Dan: bring your own thoughts. We decide together before PR-2 ships.**

## 5 HELD UNASSIGNED triage (recommendation)

| # | Spec | Recommendation | Reason |
|---|---|---|---|
| #154 | T3.5 OPTIONAL Bedrock | **Keep held permanently** | GLM works; Bedrock is post-hackathon. Pitch as "v2 AWS-native LLM path on roadmap." |
| #155 | T3.6 ALB+CloudFront+ASG | **Hold until Sunday afternoon**, after T1.x verified working + TS.1 cert ISSUED | High-blast change to production routing; do not fire during merge thrash. |
| #160 | T-PE.3 unified store migration | **Hold pending T-PE.2 PR landing + Chuan eyes-on migration script** | Irreversible Postgres migration; needs `pg_dump` first. |
| #163 | T-PE.6 always-both (bull+bear) | **Hold permanently for v1** | 2x LLM cost per Generate; ship single-output v1; pitch dual-output as v2. |
| #164 | T-PE.7 regime-aware weighting | **Hold permanently for v1** | Depends on T-PE.6; touches live trading. Single-regime works fine for demo. |

**Net:** 0 of 5 fire tonight. T3.6 may fire Sunday afternoon if Track A
verified. Others go to roadmap.

## Launch plan audit — what's still missing or unclear

After re-reading the launch plan § 8 manual deliverables + this morning's
verification:

- **M.6 ARC-OSS-FORM-DRAFT submission** — Dan owns; needs draft pass +
  Google Form submission Sunday afternoon
- **M.7 Demo video v2** — Marten's v1 shipped; v2 is "clean cut after
  Saturday merges settle" — pending T3.8 fix + M.12 deck slide
- **M.8 Main hackathon submission form** — Dan owns; Sunday afternoon
- **M.10 Final repo polish + doc audit subagent** — Sunday afternoon
  after T-tracks land
- **M.11 Traction backfill** — Dan owns; needs `arc-canteen update-traction`
  calls for any user/judge conversations from past week
- **M.12 StockBench in deck** — needs T3.8 fix + Sortino bar slide +
  video v2 narration

**Not in original plan but should be (NEW):**
- **M.13 Ruff cleanup + linting hardening** — 244 errors found 2026-05-24;
  CI is informational; needs bulk-cleanup PR + blocking gate (see PR-0
  in Workstream 1 above)
- **M.14 Portfolio Advisor decision** — keep / merge into Generate /
  repurpose as vault inspector / kill (see "Portfolio Advisor user-journey
  design" section above)

## Post-compact handoff prompt (Dan pastes after `/compact`)

```
Resuming Archimedes work post-compact. Read docs/specs/morning-execution-plan-2026-05-24.md
first — it has the full state + workstream order + pi-bot verification
results + Portfolio Advisor analysis + held-unassigned triage.

THIS SESSION'S JOB:
Execute Workstream 1 (page-by-page surface PRs), in order:
  PR-0 (recommended first): Ruff cleanup + linting hardening
  PR-1: Landing junk purge + T2.1 Layout follow-up
  PR-2: Portfolio simplification (shrink+explain RISK ON; honest empty
        state; DEFER Portfolio Advisor structural change)
  PR-3: Wallet menu + Profile view/edit
  PR-4: Generate list_strategies bug fix
  PR-5: Library Examples surface + T2.2 frontend follow-up
  PR-6: CI fixture cleanup (test_run_backtests_is_idempotent)

PLUS Workstream 2 — file 1 follow-up for t2o2 (T2.3 #168 zero-commit re-execute).
PLUS Workstream 3 — delete 12 merged dependabot branches; rebase + retitle PR #182.
PLUS Workstream 5 — close #166, #167, #168 originals with follow-up links.

DAN'S CONFIRMED PREFERENCES (this session):
- Page-by-page incremental PRs we author together; Dan reviews each
- Junk extermination is OUR work (not Chuan's bots); less is more
- Portfolio: shrink+explain before kill (Dan reserves kill right)
- Portfolio Advisor: discuss user journey BEFORE changing anything
  (Maestro recommends option 2 = repurpose as vault inspector)
- Wallet UX: dropdown menu (Profile + Disconnect)
- Discord: Dan is the channel (manual paste); Maestro drafts
- Hold all 5 UNASSIGNED specs per Maestro recommendation in this file

DRAFTED BUT NOT SENT (Dan controls):
- Discord reply to pi bot acknowledging Maestro's 2 false flags
  (#169 + #172) and confirming we'll file 3 follow-ups not reopen
  (see Workstream 5)

GO ORDER: read this file; confirm Dan ready; start PR-0 ruff cleanup.
```

## Session-end summary

35 issues filed overnight. 26 closed-and-shipped (3 fake-shipped + 23 real).
4 in-flight (all blocked on #147 AWS foundation). 5 held for triage (all
recommended to stay held per this file).

Morning work scoped into 6 page-by-page PRs (PR-0 ruff cleanup + 5 surface
PRs) + 1 follow-up issue for t2o2 + branch cleanup + close-stale-with-link.
M.4 content refresh deferred to Sunday afternoon. M.13 (ruff) + M.14
(Portfolio Advisor decision) added as new M-track items.

Pi bot calibrated and committed to grep-verify anti-goals before closing
issues going forward. Real fake-ship rate going forward should drop.
