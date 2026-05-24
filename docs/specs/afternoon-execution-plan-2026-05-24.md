# Afternoon execution plan — 2026-05-24 (post-merge-train, pre-compact handoff)

> **Status:** Durable artifact written before context compaction to preserve
> session state, agent research findings, and the PR-2 / PR-4 / eslint plans.
> Authored by Maestro (Claude Opus 4.7) under Dan's steering after the merge
> train of 7 PRs landed clean main + 3 parallel research subagents reported.
> **Next step after this file lands: compact, then execute PR-2 → PR-4 → ESLint cleanup → PR-3 → PR-5 per the order below.**

## TL;DR

Today's afternoon:
- **7 PRs merged to main** in one batch: PR-0 ruff (#197), IAM pre-stage (#202), PR-1 Landing junk purge (#203), CLAUDE.md provenance + release-tag (#198), Morning plan (#196), M.4 sweep (#182), release-tag regex fix (#204). Current main tag: **v0.0.15** at commit `05e51c3`.
- **One PR still open**: #205 quality-gate regex fix + CLAUDE.md submodule sticky-config — patch bump expected, waiting for CI.
- **Pi shipped #200 (T2.3 oracle) + #201 (KB cleanup) overnight** direct-to-main. Pi is responsive and aligned on the new pre-close protocol + release-tag conventions.
- **Three Maestro mistakes acknowledged**: (a) PR #198 accidentally tagged v1.0.0 from a regex bug; v1.0.0 deleted + fix landed in #204. (b) Maestro's earlier #169/#172 "fake-ship" flags this morning were wrong; pi correctly verified. (c) #167 "fake-ship" flag was also stale; pi shipped the single-input form correctly per audit.

Going into PR-2 (Portfolio simplification), PR-4 (Generate `list_strategies` bug), and ESLint cleanup. Detailed plans below from 3 parallel research subagents.

## Merge state — main after the train

| Commit | Tag | PR | Title |
|---|---|---|---|
| `05e51c3` | **v0.0.15** | #204 | release-tag regex fix |
| `3e9fe14` | (intentionally untagged; was bad v1.0.0 — deleted) | #198 | CLAUDE.md KB provenance + release-tag conventions |
| `ade4ea1` | v0.0.14 | #182 | M.4 sweep |
| `26f52e5` | v0.0.13 | #196 | Morning execution plan |
| `a7290e1` | v0.0.12 | #203 | PR-1 Landing junk purge |
| `ef7de4e` | v0.0.11 | #202 | IAM pre-stage |
| `bd2054c` | v0.0.10 | #197 | PR-0 ruff cleanup |
| `84b6fe7` | (no tag — direct push) | — | Pi: #200 progress.md update |
| `173e12b` | (no tag — direct push) | — | Pi: #201 progress.md update |
| `04033d6` | (no tag — direct push) | — | Pi: #201 KB cleanup + #200 oracle fix (bundled) |

**Tagging audit:** all 7 PR merges got patch bumps (correct per convention — none warranted !minor since they were infra/docs/cleanup, no new user-facing capability). Pi's direct-to-main commits got NO tag — this is the documented gap (release-tag.yml skips direct pushes silently per CLAUDE.md, and pi was reminded of the PR-over-direct convention in this morning's Discord exchange).

## Open PR(s) at handoff

- **#205** — Fix Quality Gate ruff regex (false 196 errors) + document submodule sticky config. Waiting for CI. Should be safe to merge as soon as gates pass. Patch bump expected.

## Open issues at handoff (unchanged from morning plan triage)

| # | Title | State | Recommended action |
|---|---|---|---|
| #147 | T3.1 AWS S3 + DynamoDB + IAM | In Progress (Chuan provisioning today) | **DO NOT TOUCH.** Pi has it. The IAM pre-stage #202 we merged accelerates this. |
| #148 | TS.1 HTTPS + Route 53 + ACM cert | Blocked on #147 | Wait |
| #151 | T3.2 GPU EC2 + KB pipeline run | Blocked on #147 | Wait |
| #176 | TS.2 SSM secrets migration | Blocked on #147 IAM role | Wait |
| #154 | T3.5 Bedrock OPTIONAL | HELD | Keep held |
| #155 | T3.6 ALB + CloudFront + ASG | HELD until Sunday afternoon | Re-evaluate after T1.x verified |
| #160 | T-PE.3 unified store migration | HELD pending T-PE.2 + Chuan eyes | Keep held |
| #163 / #164 | T-PE.6 / T-PE.7 always-both + regime weighting | HELD permanently for v1 | Keep held |
| #43 / #41 | Old Platform issues | Stale | Likely to close as obsolete |
| #16 | DEMO tracker | Long-running umbrella | Leave open |

## Pi bot status (Discord context for next session)

- Pi shipped **#200 + #201 within ~2 hours** of being assigned. Pre-close protocol (the 3-bullet gate we asked it to add to CLAUDE.md this morning) is being followed.
- Pi explicitly acknowledged the release-tag convention this morning and committed to PRs-over-direct going forward, BUT then immediately shipped #200 + #201 as direct-to-main with no PR (pre-existing batch from before the alignment message; or pi defaulted back to direct-push for these specific tasks).
- **No new Discord message needed right now.** If we file new issues for pi, mention "use PR with marker convention" in the body. Otherwise next session can simply observe whether pi switches to PRs.
- **Pi is responsive + helpful.** Standing by for new assignments. The 4 in-flight (#147/#148/#151/#176) all gated on #147.

## Workstream priorities for the next session (post-compact)

**HIGHEST PRIORITY:** PR-2 (Portfolio simplification). Big surface PR. Subagent audit findings folded in below.

**Order:**

1. **PR-2** — Portfolio simplification (alignment Q's → execute)
2. **PR-4** — Generate `list_strategies` bug (3-line fix in `generation_pipeline.py`)
3. **ESLint cleanup PR** — split into 2 commits (mechanical + manual)
4. **PR-3** — Wallet menu dropdown + Profile view/edit (still pending)
5. **PR-5** — Library Examples surface (still pending; pi may have shipped some of this — verify first)

## PR-2: Portfolio simplification (subagent audit folded in)

### Current state — 8 sections on Portfolio.jsx top-to-bottom

1. Page header (lines 105–111)
2. **RegimePanel** (line 114) — the "RISK ON" banner Dan wants shrunk
3. Status strip (lines 118–143) — 4 metric cards (Vaults / Total AUM / Agent / Tier)
4. Your Vaults grid (lines 146–184) — from `/api/vaults/`
5. Your Vault Positions (lines 187–205) — wallet-gated; possibly redundant with #4
6. **Allocation Advisor / Portfolio Advisor** (lines 208–219) — Dan called this "awful UI"
7. Stress Scenarios (lines 223–225) — **renders DEMO ALLOCATION** when no real vaults (this is the source of phantom "6 vaults / $10.11 AUM" Dan saw)
8. Your Traces feed (lines 228–278) — from `/api/traces/?limit=20`

### Cuts recommended (per agent + Dan's "less is more")

| Cut | Action | Risk |
|---|---|---|
| `<RegimePanel />` full card → small header pill + tooltip | Refactor RegimePanel.jsx to support `compact` mode; or wrap in collapsible header | Low — data fetching stays; only render shrinks. Dan keeps kill right. |
| `<PortfolioAdvisor />` from Portfolio.jsx | **DELETE the render** (lines 208–219). Keep the component file. File tech-debt ticket to move it to VaultDetail OR a "Portfolio Lab" page. | Low — no other page imports it. Backend `/api/strategies/advisor` endpoint stays alive for future re-wire. |
| `<StressScenarioPanel />` when zero vaults | Conditional render: only show when `allVaults.length > 0` AND with real allocations passed in. Honest "deploy a vault to see stress" when empty. | Low — pure UI conditional. |
| Total AUM metric in status strip | Delete (system-wide, not user-relevant) | Trivial |
| "Your Vault Positions" section (line 187–205) | **Ask Dan** — keeper (wallet-vs-marketplace distinction) or duplicate of "Your Vaults"? | Need decision |

### Files PR-2 will touch (in order)

1. **Read** `ui/src/components/Portfolio.jsx` (full)
2. **Read** `ui/src/components/RegimePanel.jsx` (full — for the shrink refactor)
3. **Read** `ui/src/components/StressScenarioPanel.jsx` (full — for empty-state wiring)
4. **Edit** `Portfolio.jsx` — delete PortfolioAdvisor render, conditionally hide StressScenarioPanel, shrink RegimePanel, optionally remove Total AUM
5. **Edit** `RegimePanel.jsx` — add compact-mode render variant (if shrinking via prop)
6. **Edit** `StressScenarioPanel.jsx` — add empty-state when no allocations passed in

### Critical risks

- `/api/vaults/`, `/api/regime/current`, `/api/strategies/advisor`, `/api/traces/` schemas all stay the same — we're only changing UI
- Backend traces published by Advisor still work even if the UI render is gone
- No backend test depends on Portfolio's response shape

### Dan + Maestro alignment questions to confirm BEFORE shipping PR-2

These need explicit answers before we start editing:

1. **RegimePanel shrink treatment:** small header pill + tooltip, OR collapsed accordion with expand-on-click? Recommendation: pill + tooltip (lighter touch, easier to kill later if Dan still hates it).
2. **PortfolioAdvisor disposition:** delete render entirely (recommend), OR collapse to a single "Open advanced advisor" button that pops a modal? Recommendation: delete the render; file tech-debt ticket.
3. **"Your Vault Positions" section:** keeper or duplicate? Need Dan's call.
4. **Total AUM metric in status strip:** delete (recommend)?
5. **Stress Scenarios empty state copy:** "Deploy a vault to see stress-test results" OK?
6. **Any other junk to purge in this PR** that the subagent missed?

## PR-4: Generate `list_strategies` bug (subagent root-cause)

### The bug

`backend/archimedes/agents/generation_pipeline.py` has THREE sites that call `default_provider.list_strategies()` when `default_provider` is a **factory function**, not an instance:

| Line | Buggy code | Correct code |
|---|---|---|
| 96 | `lib = default_provider.list_strategies()` | `lib = default_provider().list_strategies()` |
| 427 | `strategies = default_provider.list_strategies()` | `strategies = default_provider().list_strategies()` |
| 561 | `lib = default_provider.list_strategies()` | `lib = default_provider().list_strategies()` |

Other 18+ call sites correctly use `default_provider()`. Only these 3 are broken.

### Test coverage

**No test exercises `_pick_pipeline()`** (lines 64–103). Add one:

```python
# backend/tests/test_generation_pipeline.py — NEW
from archimedes.agents.generation_pipeline import _pick_pipeline
from archimedes.api.generate_schemas import GenerateBrief

def test_pick_pipeline_does_not_attribute_error_on_provider():
    brief = GenerateBrief(intent="test", risk_appetite="moderate", asset_classes=[], max_papers=5)
    name, _ = _pick_pipeline(brief)
    assert name in {"fusion", "architect", "agent"}
```

### #167 verification (subagent)

**Pi's #167 closure was correct** — Generate.jsx HAS the single-input form (lines 233–298). My morning flag of "still has setMode" was from a stale working tree. Pi was right; no T2.2-frontend follow-up needed.

### PR-4 scope

- Fix 3 lines in `generation_pipeline.py`
- Add 1 test in `backend/tests/test_generation_pipeline.py`
- Add `F82` (undefined-name) to the ruff-gate `--select` list (the `consulted_hashes` bug was fixed in PR-0; the gate should now enforce F82 going forward)

Trivial PR. ~20 minutes.

## ESLint cleanup PR (subagent analysis)

### Config

- Modern flat config (`eslint.config.js`)
- Rules: `@eslint/js.recommended` + `react-hooks/recommended` + `react-refresh/vite`
- NO `react/prop-types` enabled (good — would have flooded errors)

### Estimated category breakdown of the 55 errors

| Rule | Est. count | Auto-fix safe? | Notes |
|---|---|---|---|
| `react-hooks/exhaustive-deps` | ~20–22 | NO (risky; can cause re-render loops) | Mostly intentional stale closures in polling loops |
| `no-unused-vars` | ~15–18 | YES | Mostly leftover imports after refactors |
| `no-empty` catch blocks | ~8–10 | Partial | Intentional silent failures; add intent comments |
| `prefer-const` | ~5–8 | YES | `let` that should be `const` |
| `no-console` | ~0–2 | YES | None obvious in grep |

### Proposed PR structure (2 commits)

**Commit 1: Mechanical auto-fixes** — `npx eslint . --fix` (carefully review the diff). Drops ~15-20 errors. Low risk.

**Commit 2: Manual fixes** — exhaustive-deps decisions case-by-case:
  - For polling loops (Portfolio refresh, Generate fusion polling): stabilize callbacks with `useCallback` instead of disabling
  - For intentional disables, add explanatory comments
  - For `no-empty` catches, add `// intentional silent fail — degraded mode` comments OR add logger.warn
  - Manual QA: test polling loops + error paths

**Files most likely to need attention:**
- `Generate.jsx` (2 exhaustive-deps disables, multiple fetch chains)
- `GenerationStream.jsx` (1 disable, SSE polling)
- `Portfolio.jsx` (multiple useCallbacks with complex deps)
- `config.js` (module-level `let` that's intentional)
- `CorpusExplorer.jsx`, `Explore.jsx` (silent error catching)

### Risks
- **HIGH**: Auto-adding missing deps to `useEffect` in polling loops can cause infinite re-fetch cascades. Use `useCallback` to stabilize identity instead.
- **LOW**: Removing unused imports, swapping `let`→`const`, cosmetic.

## Dan's process questions (answered)

### "Should we use squash or merge commits?"

I switched to squash without explicit alignment. Dan's preference: **merge commits** (preserves git graph history). Squash compresses the commit graph for the PR; merge preserves it as a branch+merge bubble. **Going forward: use `gh pr merge ... --merge`** (the default) instead of `--squash`. The exception: PRs with many noise commits (like PR-0 with 11 ruff iterations) can still squash — that's an explicit per-PR judgment call.

### "We're not doing a good job of release tagging"

Audit:
- All 7 of our merged PRs today correctly got patch tags — none warranted `!minor` (they were infra / docs / cleanup, no new user-facing capability)
- Pi's direct-to-main commits got NO tag (release-tag.yml skips direct pushes) — that's the real gap
- v1.0.0 was incorrectly created by a regex bug; fix landed in #204; v1.0.0 deleted from origin

**Going forward:** be deliberate about `!minor` markers. Likely candidates in the upcoming PRs:
- PR-3 (Wallet menu + Profile view/edit) — **!minor** (new UI surface)
- PR-5 (Library Examples surface + functional rewire) — **!minor** (new functional surface)
- PR-2 (Portfolio simplification) — patch (cleanup, no new capability)
- PR-4 (Generate bug fix) — patch (bug fix)
- ESLint cleanup — patch (style cleanup)

## Compact handoff prompt (paste post-compact)

```
Resuming Archimedes work post-compact. Read
docs/specs/afternoon-execution-plan-2026-05-24.md FIRST — it has the
full state + workstream order + subagent research for PR-2 / PR-4 /
ESLint + pi status + Dan's process preferences.

ALSO read docs/specs/morning-execution-plan-2026-05-24.md for the
broader plan + pi's verification context from this morning.

EXECUTION ORDER (per the afternoon plan):
1. Verify PR #205 (quality-gate regex fix + submodule doc) merged
   cleanly. If not, merge it.
2. PR-2 Portfolio simplification — START with 5 alignment questions
   to Dan (RegimePanel shrink treatment, PortfolioAdvisor disposition,
   etc.). Subagent audit findings in the afternoon plan §"PR-2".
3. PR-4 Generate list_strategies bug — 3-line fix in
   generation_pipeline.py + 1 new test + add F82 to ruff-gate.
4. ESLint cleanup PR — 2 commits per the afternoon plan §"ESLint".
5. PR-3 Wallet menu dropdown + Profile view/edit.
6. PR-5 Library Examples surface (verify whether pi shipped any of
   this already first).

DAN PREFERENCES TO HONOR:
- Merge commits (not squash) by default; squash only for noisy PRs
- !minor for new user-facing capability; patch for cleanup/docs
- Less is more on every visual surface; junk extermination is OUR
  work, not pi's
- Page-by-page incremental PRs we author together with Dan reviewing
- Junk hunt actively for each PR; flag anything that looks aspirational
  or demo-fake

PI STATUS:
- 4 in-flight: #147 (Chuan unblocking AWS today), #148/#151/#176 (all
  blocked on #147)
- Pi is responsive + aligned on pre-close + release-tag conventions
- Don't reassign held specs (#154/#155/#160/#163/#164)

CURRENT MAIN: v0.0.15 (commit 05e51c3). PR #205 still open if not
yet merged.
```

## Things NOT done that we should pick back up

- **Discord status reply to pi** about #147 timeline / #148 domain acquisition / #151 + #176 ETA — pi answered all of these this morning; no new questions outstanding right now. Next reach-out only when we have something specific.
- **arc-canteen telemetry backfill (M.11)** — still pending. Should run `arc-canteen update-product` for each major ship today: PR-0 ruff cleanup, IAM pre-stage, Landing purge, #200 oracle, #201 KB cleanup. ~5-10 events to log. Quick win for 30%-rubric weight.
- **M.4 docs refresh** — full pitch deck + competitor-landscape + judging-rubric refresh still pending. Sunday-afternoon target per morning plan.
- **M.9 visual review pass** — Playwright screenshots at 4 breakpoints + multimodal review. Pending; run after PR-2 + PR-3 + PR-5 land so we screenshot the final-state UI.

## Submodule sticky config (CLAUDE.md §submodules update — landing in #205)

Run ONCE per clone:
```bash
git config submodule.recurse true
git config diff.submodule log
git submodule update --init --recursive
```

This stops the recurring "Linus and KnowledgeBase out of sync" annoyance Dan flagged twice today.

---

Session-end stats: 7 PRs merged + 1 PR open + 0 outstanding pi questions + 3 subagent research reports captured. Ready for compact.
