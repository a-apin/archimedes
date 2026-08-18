# Arc mainnet sprint — session cards

Working cards sharded from `arc-goes-mainnet-sleepy-marble.md` (2026-08-10 measurement).
Purpose: **one card per session, ~50 lines instead of a 45k-token plan re-read.**

> Not committed by default. `git status` will show this directory untracked — commit it or
> add to `.gitignore`, your call. New subdirectory, so collision risk with #1225's docs
> restructure is low.

## State — 2026-08-16

Zero sprint work has landed. `origin/main` since Aug 8 = 20 commits, all dependabot (10 PRs
merged as a batch, including #1229/#1233/#1234 which the plan told us to hold). #1224, #1226,
#1201, #1095 — all marked "merge now" — still open.

**All doc anchors re-verified 6/6 fresh on 2026-08-16.** Trust them.

Path corrections vs the source doc:
- `spend_cap.py` → `backend/archimedes/marketplace/spend_cap.py` (not `services/`)
- `Architecture.jsx` → `ui/src/components/Architecture.jsx` (not `pages/`)

~20 working days remain to Sept 16 against a 24-day estimate. See the re-cut at the bottom.

## Session rules — apply to every card

1. **Anchor-trust.** Re-anchor with `grep -n "<symbol>" <file>` (~200 tok), then `Read` with
   `offset`/`limit` for a ±40-line window. Never `Read` a file whole to "get oriented."
2. **Never read whole** — ~8,400 lines ≈ 105k tokens:
   `strategies_routes.py` (2357) · `_rigor_helpers.py` (1328) · `portfolio_backtester.py` (1180)
   · `rigor_evaluator.py` (956) · `Architecture.jsx` (896) · `fusion_evaluator.py` (864)
   · `main.py` (821)
3. **One cluster per session.** Do not drift into an adjacent item because it is nearby.
4. **Test narrow:** `pytest backend/tests/test_<module>.py -q`. Full suite once, pre-merge.
5. **No subagents, no workflows.** No discovery left to parallelize.
6. **Universal anti-goals** (from the source doc's "Explicitly not doing"): no vitest/playwright
   · no `React.lazy` · no `python-multipart` · no server-side user Python · no DSL-JSON upload
   · don't delete `_run_fusion_job` · don't repair QuantLab's mocks · no KB pipeline · no
   distributed meter reaper · don't un-pin `circlekit` · no vectorbt · don't rewrite
   `Architecture.jsx`. **Never weaken a rigor threshold.**
7. **Merge discipline is a token rule too.** Max 2 merges/day, ≥40 min apart, never during a
   deploy, verify `/health` version matches the SHA before the next. A killed deploy costs a
   ~50k-token diagnosis session.

## Order — by value-per-token, not workstream letter

| Session | Card | Doc items | Est. |
|---|---|---|---|
| 1 | [cluster-0-unblock](cluster-0-unblock.md) | D1 asks · merges · B0 checks · A6 diagnostic | 0.5d, ~0 code tokens |
| 2 | [cluster-1-cost-ssot](cluster-1-cost-ssot.md) + [cluster-3-backtest-models](cluster-3-backtest-models.md) | A1 · A7-surgical · A2-lite · A3/A4 mapper | 1.5d — **best ratio in the sprint** |
| 3 | [cluster-2-fusion-engine](cluster-2-fusion-engine.md) | A1c · A4 · A8 label | 0.75d |
| 4 | [cluster-4-strategies-route](cluster-4-strategies-route.md) | A3 fixture kill · DEGENERATE | 1.5d |
| 5 | [a6-rerun](a6-rerun.md) | A5 memo · A6 re-run + before/after table | 1.25d |
| 6–8 | [cluster-5-meter](cluster-5-meter.md), [cluster-6-boot-paywall](cluster-6-boot-paywall.md) | B2 · B5 · B0-boot · B3 | 4.0d |
| 9 | [cluster-8-returns-csv](cluster-8-returns-csv.md) | B4 | 1.5d |
| 10 | [cluster-7-ui-surface](cluster-7-ui-surface.md) | B1 (re-cut) | 0.4d sprint / 0.85d buffer |

## Re-cut vs the source doc

- **B1 splits.** Claim-honesty subset in-sprint (~0.4d); the `routes.js` 6-way consolidation +
  3 check scripts to buffer. #1237 already fixes CRUMB_MAP #1219 — coordinate, don't collide.
- **A2 stays lite.** `backtest_engine` surfaced + `cost_model_id` only. The 9-column migration
  to buffer.
- **A7 surgical only.** Full unification (`cohort_results`, golden vectors) to buffer.
- Everything already on the doc's cut list stays cut.
