# Evening execution plan — 2026-05-24 (post-red-team, pre-compact handoff)

> **Status:** Durable artifact written before context compaction. Captures the
> red-team report, the parallel-subagent fanout plan that was about to fire,
> and Dan's accumulated preferences through the session. Authored by Maestro
> (Claude Opus 4.7) under Dan's steering. Next-step prompt at the bottom.

## TL;DR

After PR-3 (#213) merged, Dan asked for a deep-dive of the live site via Safari
MCP. I walked every page, hit the wallet modal, probed JS state. Findings are
brutal:

- **3 BLOCKERS** + **6 HIGH** + **7 MEDIUM** + **5 LOW** + **3 dead-code** items
- The single most important fix is a 1-line backend bug (v_check 10001 BPS) —
  but Dan offloaded backend / dead-code work to another CLI session for THIS
  session's window. Focus here is **UI-facing only**.
- We were about to fire a parallel-foreground-subagent fanout on 3-4 UI tasks
  when Dan stopped to compact. **That fanout is the post-compact entry point.**

## State at handoff

- **Current branch**: `main` (`409cc0f`, last merge PR-3 #213)
- **Open PRs touching the repo** (NOT mine to merge — others' work):
  - **#199** `security/auth-hardening` — Marten / open ages
  - **#214** `moonshot/147-aws-s3-dynamodb-iam` — pi's AWS IAM work (in flight)
  - **#215** `dbrowneup/doc-prune-leak-scrub` — doc-prune branch is now a real PR; **needed for H1+H4+M2 fix** (fold-in target)
  - **#216** `onder/resurrect-portfolio-advisor` — **someone (Önder) acted on issue #210 I filed** — resurrects PortfolioAdvisor on /generate as preview-before-deploy
  - **#217** `onder/regime-aware-gamma` — Önder shipping T-PE.7 math (regime-aware Kelly γ scaling)
- **STAY OFF** these branches (cross-session contamination risk): #199, #214, #216, #217.
- **#215 (doc-prune)** is OK to fold in — it was originally my queued work, just shipped as a PR by the doc-prune branch earlier today.
- **Submodule pins**: held; do NOT bump until Dan's all-clear. Linus + KB ruff alignment confirmed shipped per Dan but we haven't updated pins.

## Red-team report (full — preserve verbatim)

### 🚨 BLOCKERS

**B1. Agent has done ZERO useful work in production.** Every rebalance =
`skip` + `v_check_failed: weights sum to 10001 BPS, expected 10000`. 6,477
traces in `ReasoningTraceRegistry`, ALL failures. New skip every 5 min for
hours. → **FIX**: weight normalizer in `backend/archimedes/chain/executor.py`
(or upstream) computes 10001 BPS; clamp residual onto largest weight (or USDC
slack). ~1-line fix + test. **BACKEND** — offloaded to other CLI session.

**B2. /explore = 84 STALE rows, 421 em-dashes, ZERO prices.** Root cause:
`chain_client.settings.oracle_addresses` empty + yfinance fallback also
failing. → **FIX + REDESIGN (Dan-aligned: ONE PR)** data plumbing fix + table
→ card grid + click-to-expand detail panel + hand-rolled SVG sparkline.

**B3. Library "Library Correlation" matrix is literal fake data.** 6×6 matrix
of mostly 1.00s + 0.14 column, with footnote: *"Returns simulated from
backtest summary statistics. Raw daily series not stored in fixture file."*
Reads as a confession of fraud. → **REMOVE**: delete `<CorrelationMatrix>`
import + render from `Strategies.jsx`. ~5 lines. (Dan + I to discuss what
replaces Library page content after deletion.)

**B4. Safari users can't connect a wallet.** Modal shows fallback links to
metamask.io + coinbase.com/wallet — neither supports Safari. Plus our
detection misses EIP-6963 even in Chrome (per Dan). → **FIX**: integrate
`@circle-fin/modular-wallets-core` (Circle Modular Wallets SDK). Passkey-based
smart account; works in any browser including Safari; native Arc Testnet
support; gasless via Circle Gas Station. Reference:
`submodules/context-arc/docs/circlefin-skills/use-modular-wallets.md`. Setup:
`npm install @circle-fin/modular-wallets-core viem` + Circle Console Client Key.

### 🔥 HIGH

**H1. Doc-path leaks to end users.** Library renders
`docs/specs/fusion-to-backtest-t2o2-issue.md` as `<code>`. Learnings renders
`docs/specs/strategy-expiry-spec.md`. → **FOLD IN** PR #215 (doc-prune).

**H2. VIX Level: 0.0 while regime says 92% confidence Risk On.** Learnings
RegimePanel shows VIX 0.0 + 92% Risk On + 85% stay probability — internally
inconsistent. → **FIX** RegimePanel.jsx: hide signal rows whose source is
null; never render `0.0` as data. Same honesty rule as Explore.

**H3. Strategy hash rendered as "Best strategy".** Learnings shows: `Best
strategies for this regime: 3a57a8b5a53965b3f9047e6e057058c6` (raw hash).
→ **FIX**: backend `regime_routes.py` already does a strategy_provider
lookup (line 49-55); it appends to `recommended_ids` (IDs only). Add a
parallel `recommended_titles` field returning `paper_title` for each
recommended strategy. Frontend RegimePanel: render titles, fall back to
id-prefix.

**H4. Internal planning copy leaks.** Learnings has paragraph: *"Roadmap
layout: two columns — currently-profitable strategies on the left,
currently-underperforming on the right; plus an 'expired un-deployed'
section..."* — reads as a TODO note in a user page. → **REMOVE**. Check PR
#215 doc-prune already does this; if not, add to its scope.

**H5. 6 Marketplace vaults, 5 at $0.00 AUM, 1 at $10.11.** Looks abandoned.
→ **DECISION**: hide $0 AUM vaults? Mark as "Demo"? Or operator-seed real
demo deposits? Defer to Dan.

**H6. Coinbase Wallet Chrome extension not detected.** Our detection only
checks legacy `window.ethereum.isCoinbaseWallet` patterns. Modern wallets
use EIP-6963 multi-provider discovery. → **FIX**: add EIP-6963 listener.
Bundle with Circle SDK PR (B4) for one PR.

### ⚠️ MEDIUM

**M1. /generate is minimalist.** Empty textarea + no example prompts.
→ **REDESIGN small**: 3-4 example-brief chips one-click.

**M2. Library empty-state copy** refers to "fusion-to-backtest pipeline" jargon. → covered by #215 + post-fold-in prose check.

**M3. Single demo vault $10.11 AUM.** → Decision: pre-seed 2-3 real demo deposits OR mark as demo OR hide.

**M4. No "About / How it works" surface.** Sidebar is all product, no narrative. → DEFER OR add /about.

**M5. Breadcrumb "Home / Intelligence / Corpus".** "Intelligence" is internal grouping; users don't know. → **FIX** Layout.jsx: rename or hide.

**M6. Connect Wallet modal copy is bare.** Currently just "Select a wallet to interact with Arc Testnet contracts." → **REDESIGN**: non-custodial claim + source-link + testnet-only banner.

**M7. Every page title identical.** `<title>` is "Archimedes — Paper-Grounded Portfolio Agent on Arc" on every route. → **FIX**: per-page `useEffect` updating `document.title`.

### 🪲 LOW

**L1.** Sidebar subtitle "Portfolio Intelligence" generic.
**L2.** No favicon (browser tab default).
**L3.** Agent status text-only; no pulse indicator.
**L4.** "Open onboarding tour" icon-only; discoverability low.
**L5.** Onboarding tour content untested.

### 🦴 DEAD CODE (offloaded to other CLI session)

**D1.** `strategy_architect` import in `backend/archimedes/services/strategy_fusion.py` — module doesn't exist.
**D2.** `extract_json` referenced in `strategy_fusion.py`; not defined.
**D3.** Ruff doesn't catch these; Pylance does. Add `pyright` CLI to environment.yml + informational lint job in CI.

## The parallel-subagent fanout plan (FIRE THIS POST-COMPACT)

### Permission config — verified to allow foreground subagent writes

- `~/.claude/settings.json` shows `permissions.allow` has only `Bash(gh issue *)` + `Bash(gh run *)`. That's user-scoped. **Foreground subagents inherit Maestro's tool permissions** — they will be prompted-but-allowed for Edit/Write on repo files. No config change required as long as we use **foreground** agents.
- Per system prompt: *"Background subagents are filesystem-sandboxed here (no writes). Use foreground agents for implementation fan-out."* → **always foreground for writes.**

### Fanout — 3 parallel foreground subagents

**Single message with 3 Agent tool calls (parallel). Each subagent owns ONE focused PR:**

#### Agent A — Kill the Library correlation matrix

```
You are shipping a small focused frontend PR titled "[frontend] PR-LIB-CORR:
remove fake Library Correlation matrix" against the Archimedes repo at
/Users/dbrowne/Desktop/Programming/GitHub/Agora/archimedes.

Context: The Library page renders a <CorrelationMatrix> widget that shows
a 6x6 table of mostly 1.00 values + 0.14, with a footnote literally
admitting "Returns simulated from backtest summary statistics. Raw daily
series not stored in fixture file." Dan (the owner) called this fake/
fraud-flavored and wants it deleted ASAP.

Steps:
1. cd /Users/dbrowne/Desktop/Programming/GitHub/Agora/archimedes
2. git fetch origin --prune; git checkout main; git pull --ff-only
3. git checkout -b dbrowneup/pr-lib-corr-kill
4. Edit ui/src/components/Strategies.jsx:
   - Remove the `import CorrelationMatrix from './CorrelationMatrix'` line
   - Remove the <CorrelationMatrix selectedStrategyId={highlightStrategyId || null} />
     render call (it's inside the "page-level analytics panels" block, around
     line 766-770; the EfficientFrontier render stays)
   - Do NOT delete CorrelationMatrix.jsx — leave the component file for
     potential future use; just stop rendering it.
5. cd ui && npm run lint  → must exit 0 errors
6. cd .. && git add ui/src/components/Strategies.jsx
7. git commit using a HEREDOC; message:
   "[frontend] PR-LIB-CORR: remove fake Library Correlation matrix

   The widget rendered a 6x6 table of mostly 1.00 values + a column of
   0.14 with footnote explicitly admitting 'Returns simulated from
   backtest summary statistics. Raw daily series not stored in fixture
   file.' Reads as fake data presented as analysis — trust hit if a
   judge sees it.

   Removes the render + import only. CorrelationMatrix.jsx stays for
   potential re-use once real daily series are persisted; what replaces
   it on the Library page is a separate design decision.

   Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
8. git push -u origin dbrowneup/pr-lib-corr-kill
9. gh pr create --title "[frontend] PR-LIB-CORR: remove fake Library Correlation matrix" --body "(short summary + test plan checklist)"
10. Report back: PR URL + lint exit code + commit SHA.

Anti-goals: don't touch any other file. Don't delete CorrelationMatrix.jsx.
Don't merge (Dan will merge).
```

#### Agent B — RegimePanel honesty + strategy-hash → human name

```
You are shipping a focused frontend+backend PR titled "[frontend+backend]
PR-REGIME-HONEST: hide null signal rows + show strategy names instead of
hashes" against the Archimedes repo at
/Users/dbrowne/Desktop/Programming/GitHub/Agora/archimedes.

Context (two coupled bugs the red-team report flagged):
- HIGH H2: Learnings/Portfolio RegimePanel shows "VIX Level: 0.0" while
  claiming 92% confidence Risk On. The VIX feed is null in prod; rendering
  it as 0.0 is dishonest. Fix: hide signal rows whose source value is null;
  never render 0.0 as data.
- HIGH H3: RegimePanel shows "Best strategies for this regime:
  3a57a8b5a53965b3f9047e6e057058c6" — the raw strategy hash. Should show
  the paper title.

Steps:
1. cd /Users/dbrowne/Desktop/Programming/GitHub/Agora/archimedes
2. git fetch origin --prune; git checkout main; git pull --ff-only
3. git checkout -b dbrowneup/pr-regime-honest
4. Edit backend/archimedes/api/regime_routes.py:
   - Currently it returns `recommended_strategies: list[str]` (IDs only,
     line 76). The same handler already looks up strategies via
     strategy_provider (lines 45-55).
   - Add a parallel `recommended_strategy_titles: list[str]` field in
     the response that returns the `paper_title` for each recommended_id.
   - Update both the Pydantic response model (in
     backend/archimedes/api/schemas.py — find RegimeResponse) AND the
     handler return.
   - Do the same for the /transitions endpoint if it returns recommendations.
5. Edit ui/src/components/RegimePanel.jsx:
   - In the signal-breakdown section (around lines 152-182), wrap each
     signal row (VIX Level, VIX Momentum, Composite Score, MA200/MA50
     checks) in a render condition that checks `value != null` AND
     `Number.isFinite(value)` AND `value !== 0` for fields where 0
     means "no data" (VIX level specifically — VIX is never literally 0).
     Render an explicit "Signal unavailable — agent feed not connected"
     empty state when no signals are present at all.
   - In the "Recommended strategies" section (around lines 211-225), use
     `regime.recommended_strategy_titles[i]` when present; fall back to
     id-prefix (first 8 chars) otherwise.
6. Backend test: edit or add backend/tests/test_api_routes.py (or a new
   file in backend/tests/test_regime_routes.py) to verify the new
   `recommended_strategy_titles` field is populated when the fixture
   strategy provider has recommendations.
7. cd ui && npm run lint → 0 errors
8. cd .. && pytest -m "not integration" --tb=short -q -k "regime" → all pass
9. ruff format --check . && ruff check --select E9,F63,F7,F40,F82 . → all pass
10. git add the changed files; commit with HEREDOC message explaining
    both fixes:
    "[frontend+backend] PR-REGIME-HONEST: hide null signal rows + show strategy names

    Two coupled bugs from the 2026-05-24 PM red-team report.

    H2 — RegimePanel.jsx no longer renders signal rows whose source value
    is null/NaN; VIX in particular is never literally 0 (it's a price-of-
    insurance index that floors around 10), so a 0 value is the agent
    feed reporting 'no data' — render 'Signal unavailable' instead of a
    misleading 0.0 bar.

    H3 — regime_routes.py returns a parallel recommended_strategy_titles
    field next to the existing recommended_strategies IDs; RegimePanel
    renders the title (e.g. 'Volatility-Managed Portfolios') instead of
    the strategy hash.

    Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
11. git push -u origin dbrowneup/pr-regime-honest
12. gh pr create --title "[frontend+backend] PR-REGIME-HONEST: hide null signal rows + show strategy names" --body "..."
13. Report back: PR URL + lint + pytest results + commit SHA.

Anti-goals: don't change regime classification logic. Don't change the
RegimePanel compact pill (it stays as-is — only the full panel changes).
Don't merge.
```

#### Agent C — Trust signals on Connect Wallet + per-page titles + breadcrumb fix

```
You are shipping a focused frontend PR titled "[frontend] PR-TRUST-TITLES:
trust copy on Connect Wallet + per-page document.title + breadcrumb rename"
against the Archimedes repo at
/Users/dbrowne/Desktop/Programming/GitHub/Agora/archimedes.

Context (three small UI fixes from the red-team report):
- MEDIUM M6: Connect Wallet modal copy is one bare line. Needs trust signals.
- MEDIUM M7: Every page tab title is the same "Archimedes — Paper-Grounded
  Portfolio Agent on Arc". Should be per-route.
- MEDIUM M5: Breadcrumb shows "Home / Intelligence / Corpus" — "Intelligence"
  is internal grouping; users don't know what it means.

Steps:
1. cd /Users/dbrowne/Desktop/Programming/GitHub/Agora/archimedes
2. git fetch origin --prune; git checkout main; git pull --ff-only
3. git checkout -b dbrowneup/pr-trust-titles
4. Edit ui/src/components/WalletConnect.jsx — in the modal body (between
   the <h3>Connect Wallet</h3> and the wallet options), add a small
   trust-signal block:
     "Archimedes only reads your wallet address — it never custodies your
      USDC. Deposits live in your non-custodial vault contract; the agent
      has rebalance authority only, not withdraw-to-platform. Source open
      at github.com/a-apin/archimedes-arcadia. Testnet only — fake USDC,
      no value at risk."
   Keep the existing options + Cancel button. Style as small caption text.
5. Edit ui/src/App.jsx — add a useEffect that sets document.title based
   on the current page id. Suggested mapping:
     landing → "Archimedes"
     explore → "Explore · Archimedes"
     generate → "Generate · Archimedes"
     library → "Library · Archimedes"
     corpus → "Corpus · Archimedes"
     portfolio → "Portfolio · Archimedes"
     reasoning → "Reasoning · Archimedes"
     learnings → "Learnings · Archimedes"
     vault-detail → "Vault · Archimedes"
     strategy → "Strategy · Archimedes"
6. Edit ui/src/components/Breadcrumbs.jsx (if it exists; otherwise it's
   in Layout.jsx) — change the "Intelligence" grouping label to something
   meaningful like "Research" or remove the grouping entirely so the
   breadcrumb reads "Home / Corpus" instead of "Home / Intelligence /
   Corpus". Grep for "Intelligence" in ui/src/components/ to find the
   source.
7. cd ui && npm run lint → 0 errors
8. cd .. && git add + commit with HEREDOC explaining all three fixes
9. git push -u origin dbrowneup/pr-trust-titles
10. gh pr create with title "[frontend] PR-TRUST-TITLES: trust copy on Connect Wallet + per-page titles + breadcrumb rename"
11. Report back: PR URL + lint + commit SHA + screenshots-of-changes worth flagging.

Anti-goals: don't change wallet detection logic (Circle SDK is a separate
PR). Don't touch any backend file. Don't merge.
```

### Maestro's parallel work (while subagents run)

Maestro stays focused on **doc-prune fold-in** (PR #215) which is a more
involved rebase + verify operation that's better as one human-driven flow:

1. Check PR #215 CI status — if it's already passing, fast-forward to merge prep
2. Rebase #215 onto current `main` (which has PR-3 #213 + everything after) — handle any conflicts
3. Verify the JSX changes still make sense in the rebased state
4. If clean, ask Dan to merge — OR if it has any concerning conflict, propose resolution
5. Coordinate subagent returns + ensure no merge conflicts between the 3 subagent PRs + #215

## Compact-handoff prompt (paste post-compact)

```
Resuming the Archimedes UI red-team fix sprint post-compact. Read
docs/specs/evening-execution-plan-2026-05-24.md FIRST — full state +
red-team findings + the parallel subagent fanout plan + Dan's
preferences accumulated through the session.

Also read these for the broader plan:
- docs/specs/morning-execution-plan-2026-05-24.md
- docs/specs/afternoon-execution-plan-2026-05-24.md

EXECUTE in order:

1. Verify nothing changed on main since compact (`git log --oneline -5`).
   Sync if needed.

2. Check PR #215 (doc-prune-leak-scrub) status. If it has merge conflicts
   with current main, rebase it locally onto main, push, ping Dan to
   merge. If clean, ping Dan to merge.

3. Fire the parallel-subagent fanout from the evening-plan doc § "The
   parallel-subagent fanout plan" — three foreground general-purpose
   subagents, single message, three Agent tool calls:
   - Agent A: kill Library correlation matrix
   - Agent B: RegimePanel honesty + strategy-hash → name
   - Agent C: trust signals + per-page titles + breadcrumb rename
   Each opens its own PR; reports back PR URL + lint + commit SHA.

4. As each subagent returns, verify the PR CI is green, lint passes,
   then ping Dan to merge.

5. After all three land, propose next batch from the red-team report:
   - B2: Explore data + card UX rebuild (the big one, ~1 day)
   - B4 + H6: Circle Modular Wallets SDK + EIP-6963 (~half day)
   - M1: Generate example chips (small)
   - M3: Demo vault seeding (decision needed from Dan)

DAN PREFERENCES TO HONOR (accumulated through session):
- Merge commits (not squash) by default; squash only for noisy PRs
- !minor marker for new user-facing capability; patch for cleanup
- Less is more; junk hunt aggressively; honest empty states
- Page-by-page incremental PRs we author together with Dan reviewing
- DO NOT touch: PR #214 (pi IAM), PR #216 (Önder advisor), PR #217
  (Önder gamma), PR #199 (security) — other sessions
- Backend dead-code work (D1-D3 in red-team report) is offloaded to a
  separate CLI session — do NOT touch
  backend/archimedes/services/strategy_fusion.py here
- Submodule pins are HELD until Dan's all-clear; Linus + KB ruff
  alignment shipped but we haven't bumped Archimedes pins
- Foreground subagents only for writes (background is sandboxed)

OPEN PRs AT HANDOFF (do not merge — other-session ownership):
- #199 security/auth-hardening (Marten)
- #214 moonshot/147-aws-s3-dynamodb-iam (pi)
- #215 dbrowneup/doc-prune-leak-scrub (mine; fold in if conflict-free)
- #216 onder/resurrect-portfolio-advisor (Önder; acts on issue #210)
- #217 onder/regime-aware-gamma (Önder)

CURRENT MAIN: 409cc0f (Merge pull request #213 from a-apin/dbrowneup/pr-3-wallet-menu)
```

## Dan's session preferences (sticky)

- **"Less is more" + "junk hunt aggressively"** — every PR Dan reviews, he
  pushes us to delete more.
- **Honest empty states** beat full-of-fake-data states. Render null as
  "unavailable", never as 0. (VIX 0.0 / em-dashes are dishonest.)
- **Page-by-page incremental PRs** — small reviewable diffs preferred over
  one big PR.
- **Merge commits**, not squash, by default (preserves git graph history).
  Exception: PRs with many noise commits (PR-0 ruff was OK to squash).
- **!minor marker** for new user-facing capability; patch (no marker) for
  cleanup / docs / bug fix.
- **Dan reviews every PR himself** before merge. I open, he merges.
- **Subagents need foreground** to write. Background is sandboxed.
- **Cross-session contamination** is a real concern — STAY OFF branches owned
  by other sessions.
- **Trust signals** matter — wallet-connect is "where the user decides if
  they trust us." Multiple separate asks today about this.

## Session-end stats (at handoff)

- PRs merged today: 7 (PR-4 #207, PR-2 #208, PR-ENV #209, PR-ESLINT #211,
  PR-3 #213, plus #205 + #206 from earlier batch)
- Issues filed: 2 (#210 PortfolioAdvisor relocate — already being acted on by
  Önder in #216; #212 supply-chain roadmap)
- Live-site bugs identified: 21+ (BLOCKER 3, HIGH 6, MEDIUM 7, LOW 5)
- Open PRs awaiting Maestro action: 1 (#215 doc-prune fold-in)
- Open PRs to STAY OFF: 4 (#199, #214, #216, #217)
- Subagent fanout PRs planned (post-compact): 3 (Library correlation kill,
  RegimePanel honesty, trust-titles-breadcrumb)
- Bigger PRs queued for later: 2 (Explore card rebuild, Circle Modular
  Wallets SDK)

Ready for compact. The fanout fires the moment we resume.
