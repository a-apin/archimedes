# Sunday-night handoff — 2026-05-24 (post-HTTPS, pre-compact)

> **Status:** Durable artifact for the next post-compact session. Reflects state
> as of HTTPS landing on `archimedes-arc.app`. Authored by Maestro (Claude
> Opus 4.7) under Dan's steering. Compact-handoff prompt at the bottom is
> paste-ready.

## TL;DR — where we are right now

- **HTTPS landed** at `https://archimedes-arc.app` (PR #240, moonshot/Chuan). DNS
  + ACM cert + nginx TLS + certbot. **Wallet thread now unblocked end-to-end.**
- **My wallet code** (EIP-6963 #225, Circle SDK + passkey deposit #227, build-args
  + Coinbase rdns expansion #237) is all on `main`. The infra fix for the deploy
  pipeline shipped too.
- **Submission cycle**: video recorded, deck near-final, form submitted once.
  Dan plans to resubmit + coordinate launch posts. We're in **polish / fix mode**,
  not feature-build mode.
- **Personal target:** done within 12-24h from doc time, with 3h+ margin to the
  actual Monday 23:59 ET hackathon deadline.

## What CHANGED since the evening plan

Last plan (`evening-execution-plan-2026-05-24.md`) was pre-HTTPS, pre-Circle. Now:

| | Before | Now |
|---|---|---|
| HTTPS | none (raw IP) | `https://archimedes-arc.app` live (PR #240) |
| Wallet detection | legacy `isCoinbaseWallet` only | EIP-6963 + 5 Coinbase rdns variants + name-pattern fallback (PRs #225 + #237) |
| Circle Modular Wallets | not integrated | full integration: passkey auth + smart account + batched-userop deposit (PR #227) |
| Circle Console | no Client Key | Client Key created for `localhost`; Gas Station Default Arc Testnet Policy ACTIVE (50 USDC/day) |
| Build pipeline | VITE_ env vars never reached Vite build | nginx/Dockerfile + docker-compose pass them as build args (PR #237) |
| Library page | fake correlation matrix shown | `<CorrelationMatrix>` deleted (PR #220), McLean-Pontiff fake footnote purged (PR #238) |
| Regime panel | VIX 0.0 shown as data | null signals hidden; "Signal unavailable" empty state (PR #222) |
| Strategy recs | rendered hash IDs | render paper titles via new `recommended_strategy_titles` field (PR #222) |
| Trust copy | one-line bare modal | non-custodial claim + GitHub source link + testnet disclaimer (PR #221) |
| Page titles | identical on every route | per-route `document.title` (PR #221) |
| Breadcrumb | "Home / Intelligence / Corpus" | "Home / Corpus" (Intelligence group dropped, PR #221) |
| Wallet menu | bare disconnect button | dropdown w/ Profile + Disconnect (PR #213) |
| Evening planning doc | local-only | committed at `docs/specs/evening-execution-plan-2026-05-24.md` (PR #224) |
| ruff Tier-1 rules | not enabled | ARG/C4/PIE/RET/SLF surfaced as informational (PR #223) |

**The wallet UX path is essentially feature-complete.** What remains is *verification on the live HTTPS site* + *operator-side env config* + *fixing remaining backend bugs that block the agent narrative.*

## Biggest victory-gating risks (Dan-confirmed)

In Dan's own words, the two things that can sink us:

### Risk 1 — Agent doing ZERO useful work on-chain

`ReasoningTraceRegistry` has **6,477 traces, 100% `skip` + `v_check_failed: weights sum to 10001 BPS, expected 10000`**. Every rebalance for hours has been a failed skip. The "autonomous agent" pitch breaks under any judge who clicks Reasoning + sees skip-after-skip.

- **Likely fix surface:** `backend/archimedes/chain/executor.py` (or its weight normalizer upstream). One-line rounding fix — clamp the residual BPS onto the largest weight (or onto a USDC slack bucket).
- **Owner question:** I (Maestro) haven't fixed this yet because I was told it was offloaded to another CLI session. **Confirm with Dan post-compact:** has that session shipped a fix? If not, this is the highest-impact ~1-hour fix in the whole sprint.

### Risk 2 — Junk / dead code / fake data leaking to judges

Dan's words verbatim: *"We can't tolerate any of that. It's very serious."* The dead-code-audit-v2 session has been working on this in parallel. Several junk items already removed (correlation matrix, McLean-Pontiff footnote, fake regime VIX 0.0). **Still pending verification:**

- Confirm `backend/archimedes/services/strategy_fusion.py` dead-code work (`strategy_architect` import, `extract_json` undefined ref) — was on the parallel CLI session's plate
- Final 3-browser UI red-team via Safari MCP **on the HTTPS site** — last set of eyes before submit
- Walk every page, click every CTA, screenshot anything that looks fake/empty/wrong
- Documents sweep: any TODO/FIXME/placeholder text in `docs/` or in-app copy that judges would see

## Victory checklist (what HAS to be done)

Ordered by impact + effort. Anything not on this list is out of scope this weekend.

### MUST-DO (victory-gating)

1. **Verify HTTPS deploy + wallet stack on `archimedes-arc.app`** — drive Safari MCP through Connect Wallet, expect passkey option present + the trust copy + per-page titles + Coinbase Chrome auto-injection. ~20 min.
2. **Operator step: add `VITE_CIRCLE_CLIENT_KEY` to EC2 host `.env`** + rebuild nginx → triggers re-deploy of the JS bundle with the key embedded. Dan or Chuan does this. ~5 min.
3. **Add `archimedes-arc.app` as Allowed Domain in Circle Console** Client Key. Without this, passkey registration on prod fails with `SecurityError`. Dan does this in Console. ~3 min.
4. **Fix v_check 10001 BPS bug** — confirm whether parallel session already shipped this. If not, file or write directly. ~1h.
5. **Final 3-browser red-team via Safari MCP + Dan on Chrome/Firefox** — only after #1 + #2 + #3 land. Walk every page; flag fake/dead/junk. ~45 min.

### SHOULD-DO (polish; cheap points)

6. **CSP fix for Google Fonts** — `style-src` + `font-src` need to allow `fonts.googleapis.com` + `fonts.gstatic.com`. nginx config one-liner. Fixes the in-app typography fallback to system fonts.
7. **arc-canteen telemetry refresh** — log every meaningful product ship via `arc-canteen update-product`; every external conversation via `update-traction`. The 30% rubric weight reads from this telemetry. Dan owns.
8. **README + docs final sweep** — any stale references, broken links, wrong test counts. Dan has PR #234 in flight for this.

### NICE-TO-HAVE (only if everything above is done)

9. Coordinated launch posts (Dan's domain).
10. Pitch deck final polish if needed.

## Stay-OFF list — do NOT duplicate or collide

- **t2o2 issues filed THIS session** — Dan + another session filed new fanout for the bot system. Don't touch those branches; let t2o2 own them.
- **#199** Marten security PR
- **#229** Dan dead-code-audit-cleanup
- **#233** Dan M.4 substantive content
- **#234** Dan README sync
- **#235** Önder source_tracker wiring
- **#239** Önder StockBench consolidation
- **m4-content-refresh** + **m4-substantive-content** branches (Dan)
- **dead-code-audit-v2** branch (other agent)
- **archimedes-147-tests** sibling worktree (pi's old work; orphaned)
- **HTTPS / domain / infra work** (Chuan's lane; already shipped #240)

## Open PRs at handoff (snapshot)

```
#199 [danielscoffee] security/auth-hardening                   STAY OFF
#229 [dbrowneup]     dbrowneup/dead-code-audit-cleanup         STAY OFF (Dan)
#233 [dbrowneup]     dbrowneup/m4-substantive-content          STAY OFF (Dan)
#234 [dbrowneup]     dbrowneup/readme-test-count-fix           STAY OFF (Dan)
#235 [onder-akkaya]  onder/source-tracker-wiring               STAY OFF (Önder)
#239 [onder-akkaya]  onder/stockbench-consolidation            STAY OFF (Önder)
```

Recently merged ON MAIN (all good, no action):

```
#240 [t2o2 / moonshot] HTTPS setup: nginx TLS + Route 53 + ACM cert + certbot (#148)
#241 [t2o2 / moonshot] Corpus frontend: repoint to /api/corpus/* (#230)
#238 [dbrowneup]       Purge McLean-Pontiff fake footnote
#237 [dbrowneup]       VITE_ build args + expanded Coinbase detection !minor
#227 [dbrowneup]       Circle Modular Wallets passkey integration !minor
#225 [dbrowneup]       EIP-6963 wallet discovery !minor
#224 [dbrowneup]       Evening execution plan doc
#223 [dbrowneup]       ruff Tier 1 expansion
#222 [dbrowneup]       RegimePanel honesty + strategy names
#221 [dbrowneup]       Trust copy + per-page titles + breadcrumb
#220 [dbrowneup]       Remove fake Library Correlation matrix
#213 [dbrowneup]       Wallet menu dropdown + Profile view/edit !minor
```

## Post-compaction read-this-first ordering

When the next-session Maestro resumes, read in EXACTLY this order:

1. **This doc** (`docs/specs/sunday-night-handoff-2026-05-24.md`) — full state
2. **`CLAUDE.md`** — sticky context, parallel-agent discipline, ruff/test gates
3. **`docs/specs/evening-execution-plan-2026-05-24.md`** — prior plan + red-team report context
4. **`gh pr list --state open`** — current PR state
5. **`git log origin/main --oneline -15`** — what landed while we were compacting
6. **Drive Safari MCP to `https://archimedes-arc.app/`** — visually verify HTTPS + wallet stack
7. **Read `backend/archimedes/chain/executor.py`** if + only if v_check 10001 BPS bug is still unfixed (check `ReasoningTraceRegistry` traces first via the live site's Reasoning page)

Don't re-derive what's already in the doc. Don't re-read the morning + afternoon plans unless the doc points you back to them.

## Compact-handoff prompt (paste post-compact)

```
Resuming Archimedes Sunday-night push after compact. HTTPS just landed at
https://archimedes-arc.app. Wallet thread is end-to-end ready to verify on
the live site. Submission deadline is Sunday midnight CDT (~12-24h from now).

READ FIRST:
1. docs/specs/sunday-night-handoff-2026-05-24.md (THIS is the durable
   handoff; tells you what to do next and in what order)
2. CLAUDE.md (sticky context)
3. gh pr list --state open + git log origin/main --oneline -15

DO NOT TOUCH:
- t2o2 issues filed this session (parallel fanout)
- Open PRs #199 #229 #233 #234 #235 #239
- m4-content-refresh / m4-substantive-content / dead-code-audit-v2 branches
- HTTPS / domain / infra (Chuan's lane; already done)

PRIORITIES IN ORDER:
1. Drive Safari MCP to https://archimedes-arc.app/ — verify HTTPS,
   trust copy renders, passkey option appears in modal (if not, the
   VITE_CIRCLE_CLIENT_KEY env-var step on EC2 hasn't been done yet —
   ping Dan).
2. Confirm with Dan whether v_check 10001 BPS bug has been fixed by
   the parallel session. If not, write the fix (backend/archimedes/
   chain/executor.py weight normalizer — clamp residual BPS onto
   largest weight or USDC slack bucket). ~1h job.
3. Final 3-browser red-team via Safari MCP + Dan on Chrome/Firefox.
   Walk every page; screenshot anything fake/dead/junk; ship fix PRs
   immediately.
4. CSP fix for Google Fonts (nginx style-src + font-src additions).
5. Help with launch coordination + telemetry if Dan asks.

USE WORKTREES FOR ALL WRITE FANOUT (parallel agents are active).
NO JUNK. NO DEAD CODE. NO FAKE DATA. That is Dan's explicit hard
constraint — verify every change against it before commit.

Current branch: dbrowneup/sunday-night-handoff (about to merge as
a doc PR; main will move).
```

## What we will NOT do this weekend

To stay focused:

- New features beyond what's already in main + this checklist.
- Refactors that aren't trivially small + obviously correct.
- Cosmetic changes that don't improve the judges' read.
- Anything that requires teammates to do work we haven't already agreed on.
- New dependencies (npm or pip) without explicit Dan signoff.
- Touching `submodules/*` pins.

## One-line North Star

**Working wallet on every browser → user can deposit → agent makes real on-chain
decisions → judges can verify any decision on-chain. Repo is clean. Pitch is
coherent. Submit + win.**
