# Phase 8 + Phase 9 — Landing polish + Fusion UI surface

> **Status:** spec-only, drafted 2026-05-24 (Day-12) at end of context window for fresh-session execution. Demo-blocking; both phases are pure frontend with no Marten/Chuan dependency — distinct from Phases 4 + 5 which remain blocked on on-chain alignment.
>
> **Why this exists:** the spine-plus-v2 PR (#135) covers the streaming Generate, Explore, page-roles split, and rigor wiring — but post-merge testing surfaced three landing-page UX bugs and one product-level gap that the existing phase plan didn't cover. Phase 4 (vaults + trade windows) and Phase 5 (on-chain agent + USDC-as-gas) are orthogonal — they ship the on-chain story; they don't fix landing/wallet/CTAs or expose the fusion engine. This doc specifies the two missing UX phases.

## Problem statement

Four demo-blocking issues on the live UI at <https://archimedes-arc.app/>:

1. **Landing "Strategies" CTA does nothing useful** — clicking it changes the URL but lands the user on the Landing component wrapped in Layout (sidebar appears unexpectedly). Cause: `onNavigate('strategies')` is called but the spine's route key is `library`. The unknown page-id falls to App.jsx's renderPage default branch, which returns `<Landing>` — now rendered inside Layout because the `if (page === 'landing')` early-return bypass doesn't fire.

2. **Landing "Dashboard" CTA has the same bug** — `onNavigate('dashboard')` should be `onNavigate('portfolio')`. Same fall-through to Layout-wrapped Landing.

3. **Landing "Connect Wallet" button is broken** — calls `onConnect()` with no argument. App.jsx wires `onConnect = handleConnect = (addr) => setWalletAddr(addr)`, which is a state setter, not a wallet-flow trigger. Result: clicking does nothing visible (or, depending on browser cache, may set `walletAddr` to undefined).

4. **Strategy fusion engine has no UI surface.** Generate.jsx exposes two modes: `Streaming agent` (path B — `portfolio_agent.py` tool-use) and `Architect (fast preview)` (path A — `strategy_architect.py` library picker). The fusion path (C — `strategy_fusion.py`) has a working backend endpoint (`POST /api/strategies/generate` with `mode=fusion`, now fully wired to `fusion_evaluator.py` + rigor gate per #128 + #133), but no front-end ever invokes it. The wedge — "novel synthesis from multiple papers, then rigor-gated" — is invisible to users and judges.

## Phase 8 — Landing polish + functional CTAs + wallet connect

### Scope

Files to modify:
- `ui/src/components/Landing.jsx` — fix the 3 CTA page-id bugs; fix the wallet-connect button; design polish pass.
- `ui/src/App.jsx` — verify that the early-return for `page === 'landing'` does NOT fire when the user has clicked through from Landing to a real spine page; verify that the default branch in `renderPage` doesn't render Landing (use a 404-style component or a redirect to '/' instead).
- `ui/src/components/WalletConnect.jsx` — confirm there's an entry point Landing can call to open the wallet modal (or extract the modal-open logic so Landing can re-use it).

Files to NOT touch:
- Anything backend (this phase is pure frontend).
- The onboarding tour (PR #134 already shipped + merged).
- Any other spine page (Generate, Portfolio, Library, Corpus, Reasoning, Learnings, Explore).

### The three bug fixes (mechanical)

**Bug 1 + 2 — wrong page IDs in Landing.jsx:**

```diff
- onClick={() => onNavigate('strategies')}
+ onClick={() => onNavigate('library')}

- <button … onClick={() => onNavigate('dashboard')}>Dashboard →</button>
+ <button … onClick={() => onNavigate('portfolio')}>Dashboard →</button>

- <button … onClick={() => onNavigate('trade')}>…</button>
+ <button … onClick={() => onNavigate('portfolio')}>…</button>
```

(The button labels — "Strategies", "Dashboard" — can stay; only the internal page-id needs to match `PAGE_TO_PATH` in App.jsx.)

**Bug 3 — wallet connect from Landing:**

The cleanest fix is to give Landing its own wallet button that uses the same modal-open path as the topbar's `WalletConnect`. Two acceptable implementations:

- **(A) Embed `<WalletConnect>` directly in Landing's header**, the same way Layout does in its topbar. Pass `address={walletAddr}`, `onConnect={handleConnect}`, `onDisconnect={handleDisconnect}` from App.jsx. The modal it owns is portal-rendered, so it works fine outside Layout.
- **(B) Lift the modal-open trigger from `WalletConnect` into a prop**, and let Landing render its own button that calls it. More invasive; only do this if (A) somehow doesn't work.

Recommended: **(A)**. Smallest diff, reuses tested code.

**Bug-adjacent — default route in App.jsx:**

The fall-through of unknown page IDs to `<Landing>` inside Layout is the root cause of the "side panel opens" observation. After fixing the Landing CTAs the unknown IDs go away, but the fall-through is still a footgun. Two acceptable fixes:

- **(A)** Change `renderPage`'s default to return a small "404 — page not found" card with a CTA back to `onNavigate('landing')`. Keeps Layout (with sidebar) visible so the user can recover.
- **(B)** Detect unknown page in App.jsx and `navigateToPage('landing', { replace: true })`. Cleaner but slightly silent.

Recommended: **(A)**. Honest failure modes per CLAUDE.md.

### Design polish (separate concern, same phase)

Landing.jsx is 343 lines today and was rewritten in Daniel R.'s UnoCSS PR (#124). It works — but reads more like a feature-list landing page than a hackathon product narrative. Polish opportunities (none blocking; pick what time allows):

- **Tighten the hero copy.** Lead with the one-line "Linus for quantitative finance" framing from [`docs/user-stories.md`](../user-stories.md), then the locked sentence — research-grounded strategies → rigor gate → non-custodial vault — and one CTA: "Generate a strategy →". Demote the secondary CTAs to a horizontal strip below.
- **Single brand-accent color across the page.** Today the gold + violet + various greens/reds mix can feel busy. Pick one accent (var(--accent)) and use the others sparingly as status signals only.
- **Replace generic "Paper-Grounded Strategies · SMA200 · TSMOM · Vol-Managed" cards with a curated 3-card row** that names the three primitives the deck argues: paper provenance, rigor gate, on-chain trace. Each card linkable to the page that demonstrates it (Library / Library?highlight= a rigor-passing strategy / Reasoning).
- **Footer trust block** — Arc testnet status, last-deploy timestamp, GitHub link. Honest framing that we're testnet-only by design.
- **Honesty section** — pull two anti-claims from [`docs/anti-features.md`](../anti-features.md) (e.g., "We don't promise alpha. We promise evidence-grounded generation with externally verifiable rigor.") Set the right expectations before the user generates.

If time-boxed, the bug fixes alone are sufficient to unblock the demo. The polish work is incremental.

### Sub-issue — Corpus Catalog tab: cards → compact table

The Catalog tab in CorpusExplorer.jsx today renders each paper as a chunky card with title + meta line + 200-char abstract preview + strategies count. With 10,000 papers that's unscannable — at 5 cards visible per screen on a laptop the user has to scroll ~2000 times to traverse the corpus.

**Reference UX:** the Papers app pattern (the user's reference image): dense table with columns *Authors · Year · Title · Journal*, monospace-feeling rows, no abstract preview in the row body. Abstract + tags + identifiers live in a right-side detail panel that opens on selection.

**Resolution — adopt the Strategies.jsx table pattern** (which already proves the dense-row + click-to-expand approach on this codebase):

- Convert `CatalogTab` from a `paper-card` list to a `<table>` with columns:
  - **arxiv ID** (mono, `e.g.` `2108.00275`)
  - **Authors** (first 2 + " et al." if more)
  - **Year** (right-aligned, mono)
  - **Title** (truncated to ~80 chars with full title on hover)
  - **Category** (plain-English `category_label`, raw arxiv code on hover — same affordance as today's `paper-cat` badge)
  - **Cited by** (count of `citing_strategies`, e.g. "2 strategies" — link-styled, clickable to filter Library)
- Click row → existing `PaperDetail` view (unchanged — keeps the full PDF + abstract + strategy provenance flow).
- Keep the existing search + category-filter controls above the table.

Precedent to copy: `ui/src/components/Strategies.jsx::StrategyTable` (lines ~528–552 after the rebase). Same table shape, same `lib-table` CSS class, same `rounded-lg border border-[var(--glass-border)]` framing. Drop-in.

**Backend note:** the API at `GET /api/papers/` already returns the fields needed (`arxiv_id`, `title`, `category_label`, `primary_category`, `published`, `abstract`). No backend change required for the table redesign. The Authors field needs to be added to the response — today's list endpoint omits authors (see `papers_routes.py::list_papers`'s response shape, lines 43-53). Add `authors: json.loads(r.authors) if r.authors else []` to both the DB and file-fallback dict literals. ~2-line backend tweak.

**Acceptance:**
- [ ] Catalog tab renders rows, not cards. At 24px line-height ~40 rows visible per screen (8× density vs today).
- [ ] Each row click opens the existing `PaperDetail` view.
- [ ] Authors field comes back from `/api/papers/` (DB + file fallback paths both).
- [ ] Existing search + category filter still work.
- [ ] Page builds + no regression on Overview / Graph / Knowledge-Graph tabs.

### Sub-issue — RegimePanel duplication across pages

The current market-regime indicator appears in **three** places across the spine after the #119 panel-integration merge:

1. `Generate.jsx` — `<RegimePanel />` rendered as a "Compact regime strip" at the top of the page.
2. `Portfolio.jsx` — `<RegimePanel />` rendered above the status strip.
3. `Portfolio.jsx` — a separate `Market Regime` stat-card inside the 4-card status strip (text-only, just the label + confidence).

Two of these on one page is the more glaring duplication. Per [`docs/specs/page-roles-spec.md`](page-roles-spec.md), each page owns one job — and market context is a Portfolio (Monitor) concern, not a Generate concern. The agent already incorporates regime into its decision-making internally; the user constructing a strategy doesn't need a visible regime widget alongside the input form.

**Resolution:**

- **Drop `<RegimePanel />` from Generate.jsx.** Keep the page focused on intent → result. The mode toggle + form + streaming UI is enough; a regime strip is decoration that competes for attention with the actual primary action.
- **Drop the bare Market-Regime stat card from Portfolio.jsx's status strip.** The richer `<RegimePanel />` above the strip already shows the regime + confidence + narrative + signals. The stat-card is redundant.
- **Keep `<RegimePanel />` on Portfolio.jsx only.** That's where market context belongs.

Code-level diff in `Generate.jsx`:

```diff
- import RegimePanel from './RegimePanel'
…
-       {/* Compact regime strip */}
-       <div className="mb-4 fade-up fade-up-2" style={{ fontSize: '0.88rem' }}>
-         <RegimePanel />
-       </div>
```

Code-level diff in `Portfolio.jsx`:

```diff
        {/* Status strip — agent + regime are real (Redis-backed) regardless of wallet */}
-       <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
+       <div className="grid grid-cols-2 md:grid-cols-3 gap-4 mb-6">
          <div className="card-flat p-4">
            <div className="label mb-2">Your Vaults</div>
            …
          </div>
          <div className="card-flat p-4">
            <div className="label mb-2">Total AUM</div>
            …
          </div>
          <div className="card-flat p-4">
            <div className="label mb-2">Agent</div>
            …
          </div>
-         <div className="card-flat p-4">
-           <div className="label mb-2">Market Regime</div>
-           …
-         </div>
        </div>
```

The 3-card grid in Portfolio still shows: Your Vaults / Total AUM / Agent. Regime moves to the rich `<RegimePanel />` block above.

### Sub-issue — onboarding tour cards (Phase 6 follow-up)

Two complaints on the Phase 6 onboarding tour (`ui/src/components/OnboardingTour.jsx`):

1. **Cards go semi-transparent over loading content.** The modal-overlay uses `background: rgba(0,0,0,0.55)` and `backdropFilter: blur(2px)` — too thin. When a page is loading behind the tour (e.g., Generate's streaming card spinner pulsing), the card body looks washed-out and hard to read.
2. **CTAs ("Open Corpus", "Open Generate", etc.) navigate and close the tour with no clear re-entry path while the user is on Landing.** The "?" reopen button lives in the Layout topbar, which doesn't render on Landing. So if the user is on Landing and clicks card 2's "Open Corpus" CTA, the tour closes and they're suddenly looking at Corpus with no obvious way to get back to the tour.

Fixes (both small):

- **Overlay opacity:** `rgba(0,0,0,0.78)` (or higher) + `backdropFilter: blur(6px)`. The card body itself uses `card-elevated` which gives it a solid dark panel — make sure that style hasn't picked up an unintended transparency from a UnoCSS class.
- **CTA behavior change:** clicking "Go to <Page>" should **navigate AND advance to the next card** (not close the tour). The tour stays open over the new page. User completes the tour normally on `Done` (last card). If the user clicks "Skip", same as today — tour closes + dismissal sticks. This way the user always has a forward path; the tour visually walks them through the spine while they see each destination behind the modal.
- **Optional polish:** add a small "← Back to tour" affordance to Landing (since Landing has no topbar). Probably overkill given the CTA-advances-tour fix above; only add if the CTA fix doesn't fully resolve the disorientation.

Code-level diff in `OnboardingTour.jsx`:

```diff
- const handleCta = useCallback(() => {
-   if (card.cta.target) {
-     setPage(card.cta.target)
-     finish()
-   } else {
-     handleContinue()
-   }
- }, [card, setPage, finish, handleContinue])
+ const handleCta = useCallback(() => {
+   if (card.cta.target) {
+     setPage(card.cta.target)
+     // Advance to next card instead of closing — keep the tour
+     // visible so the user can see each destination behind the modal
+     // and still finish the walkthrough.
+     if (!isLast) setCardIndex(i => i + 1)
+     else finish()
+   } else {
+     handleContinue()
+   }
+ }, [card, setPage, isLast, finish, handleContinue])
```

And in the overlay style block:

```diff
- style={{ background: 'rgba(0,0,0,0.55)', backdropFilter: 'blur(2px)' }}
+ style={{ background: 'rgba(0,0,0,0.78)', backdropFilter: 'blur(6px)' }}
```

### Acceptance criteria (Phase 8)

- [ ] **CTAs navigate to real pages:** clicking "Strategies" lands on `/library`; clicking "Dashboard" lands on `/portfolio`; the second "Trade" CTA (or whatever the duplicate calls) lands on `/portfolio`. None of these render Landing-inside-Layout.
- [ ] **Wallet button on Landing opens the wallet modal** and on successful connect, the button updates to show the short address (same behavior as the Layout topbar).
- [ ] **Unknown page IDs do not silently render Landing-in-Layout.** Either a 404 card renders, or App.jsx redirects to `/`.
- [ ] **Onboarding cards are readable over animating content.** Overlay opacity ≥ 0.75, blur ≥ 6px; card body has a solid (non-transparent) background.
- [ ] **Onboarding CTAs keep the tour open.** Clicking "Open Corpus" / "Open Generate" / etc. navigates the page AND advances the card index; the tour modal stays visible until Skip, Done, or Esc.
- [ ] **Regime indicator appears exactly once on the spine.** Visible on Portfolio (rich `<RegimePanel />` block); absent from Generate; the bare stat-card in Portfolio's status strip is removed.
- [ ] **Corpus Catalog tab is a dense table, not cards** — ~8× density gain (40 rows/screen vs 5 cards). Authors field populated by a small `papers_routes.py` tweak.
- [ ] **No regressions on other spine pages** — Layout topbar wallet button still works, sidebar nav still works, onboarding tour still triggers on first visit.
- [ ] **Frontend build passes** — `cd ui && npm run build` exits 0. (Or via docker: `docker compose up -d --build` and verify <http://localhost> serves cleanly.)

### Anti-goals (Phase 8)

- Do NOT touch the backend.
- Do NOT add new pages or new navigation entries.
- Do NOT refactor `WalletConnect.jsx` internals beyond what bug 3 requires.
- Do NOT bundle Phase 9's fusion-mode UI here — separate commit / separate PR.

### Suggested precedent / shape to copy

- For embedding `<WalletConnect>` in a header outside Layout, copy how Layout itself does it in `ui/src/components/Layout.jsx` topbar block. The component is self-contained — it owns its own modal, address shortening, and chain-mismatch warning.
- For 404 page handling, the simplest pattern is an inline functional component in App.jsx — no new file needed.

## Phase 9 — Fusion engine UI surface

### Scope

The wedge — "multi-paper synthesis, rigor-gated" — has been backend-complete since #133. The UI just doesn't expose it. This phase adds a third mode to the existing Generate.jsx mode toggle.

Files to modify:
- `ui/src/components/Generate.jsx` — add a third tag in the existing mode-toggle bar; add a `mode === 'fusion'` branch that renders a fusion-specific input form + job-status display.
- Likely a new component `ui/src/components/FusionResult.jsx` for the rendered fusion proposal + backtest + rigor verdict.

Files to NOT touch:
- The streaming agent mode (path B) and architect mode (path A) — both work; don't refactor.
- Backend — `POST /api/strategies/generate?mode=fusion` already exists in `backend/archimedes/api/strategies_routes.py` (post-#132 split). The fusion path inside that handler now calls `evaluate_fusion_spec` (per #133) so the response carries a real backtest + rigor verdict, not just text.

### UX shape

```
┌─ Generate ─────────────────────────────────────┐
│ [🔴 Streaming agent] [⚡ Architect (fast)] [🧪 Fusion (novel)]   ← mode toggle
│
│ === when mode === 'fusion' ===
│
│ ┌────────────────────────────────────────────┐
│ │ Asset classes  [equities ▾] [bonds ▾] …    │
│ │ Risk appetite  [moderate ▾]                │
│ │ Strategic direction (optional):            │
│ │ [_____________________________________]    │
│ │ Max papers to fuse: [4 ▾]                  │
│ │                                            │
│ │             [ Fuse → ]                     │
│ └────────────────────────────────────────────┘
│
│ === while job running ===
│ Status: queued → running → done
│   (poll /api/strategies/generate/{job_id} every 2s)
│
│ === when job done ===
│ ┌─ FusionResult ─────────────────────────────┐
│ │ Strategy name + thesis                     │
│ │ Fusion reasoning (which papers, why)       │
│ │ Source papers (arxiv chips linkable)       │
│ │ Backtest metrics (if strategy_spec valid)  │
│ │ Rigor verdict (DSR / PBO / OOS / look-ahd) │
│ │ Novelty rationale + risk notes             │
│ │ [Deploy as vault] (gated on rigor-pass)    │
│ └────────────────────────────────────────────┘
└────────────────────────────────────────────────┘
```

### API contract (already shipped)

```bash
# Submit a fusion job — async, returns a job_id
POST /api/strategies/generate
{
  "mode": "fusion",
  "asset_classes": ["equities", "bonds"],
  "risk_appetite": "moderate",
  "strategic_direction": "...",
  "max_papers": 4
}
→ 202 { "status": "queued", "job_id": "..." }

# Poll for status + result
GET /api/strategies/generate/{job_id}
→ { "status": "queued"|"running"|"done"|"failed", "result": {...} }
```

The `result` shape (when status=`done` and the fusion produced a valid `strategy_spec`):

```jsonc
{
  "mode": "fusion",
  "status": "ok",
  "strategy_name": "...",
  "thesis": "...",
  "source_arxiv_ids": ["...", "..."],
  "fusion_reasoning": "...",
  "novelty_rationale": "...",
  "risk_notes": "...",
  "strategy_spec": { /* DSL — for the deploy button */ },
  "backtest": { "sharpe_ratio": ..., "cagr": ..., "max_drawdown": ..., "equity_curve": [...] },
  "rigor": { "passing": true|false, "dsr": ..., "dsr_p_value": ..., "pbo_score": ..., "oos_sharpe": ..., "look_ahead_clean": true }
}
```

If `strategy_spec` is missing (LLM didn't comply), the result has only the prose fields (thesis, fusion_reasoning, etc.) and no backtest/rigor blocks. Display the prose + a small honest note: *"This is a pre-backtest hypothesis. Strategy specification was not produced; rerun for a backtested result."*

### Component decomposition

- **`Generate.jsx`** — extend mode toggle; add `fusion` branch with the form + job tracker. Most of the streaming-job-polling infrastructure can be lifted/copied from `GenerationStream.jsx`, but fusion's lifecycle is simpler (no SSE — just GET poll every 2s).
- **`FusionResult.jsx`** *(new)* — renders the result body. Includes:
  - Strategy name + thesis (top — the headline)
  - Source-paper chips (link to `/corpus?arxiv_id=...` if exists, else arxiv.org/abs/)
  - Backtest metrics card (if present) — Sharpe / CAGR / Max DD / equity curve sparkline
  - Rigor verdict pill — green "rigor passed" / amber "pending" / red "failed" with the four sub-metrics under
  - Fusion reasoning + novelty rationale + risk notes — expandable cards
  - Deploy CTA — gated: enabled only if `rigor.passing === true`; on click, scaffold a vault-deploy flow (out of scope for Phase 9 — see "Out of scope" below)

### Acceptance criteria (Phase 9)

- [ ] **Third mode visible:** opening `/generate` shows three tags in the mode strip: Streaming agent · Architect (fast preview) · Fusion (novel synthesis).
- [ ] **Fusion form submits cleanly:** filling the form + clicking "Fuse →" issues `POST /api/strategies/generate` with `mode=fusion` and the form fields, receives a `job_id`, transitions to a "Status: queued" view.
- [ ] **Polling works:** the UI polls `GET /api/strategies/generate/{job_id}` every 2s, updates the status, and stops on `done`/`failed`. The `Last-Event-ID` pattern from streaming Generate is not needed here (no SSE).
- [ ] **Backtested fusion result renders:** when `result.backtest` is present, the UI shows Sharpe + CAGR + Max DD + a small equity-curve sparkline.
- [ ] **Rigor verdict renders honestly:** when `result.rigor.passing === true`, a green badge appears; when `false`, an amber/red badge with the failing sub-metric highlighted; when absent (fallback path), the user sees *"pre-backtest hypothesis"* framing.
- [ ] **Pre-backtest fusion still renders gracefully:** if `result.strategy_spec` is missing, the prose fields (thesis, fusion_reasoning, novelty_rationale, risk_notes) render with the honest pre-backtest note.
- [ ] **No regression on the other two modes** — Streaming agent and Architect both still work end-to-end.

### Anti-goals (Phase 9)

- Do NOT change the backend. The fusion endpoint and `fusion_evaluator.py` are settled (post-#133).
- Do NOT implement the deploy-to-vault flow here. That's Phase 4 territory. Render a *"Deploy as a vault — coming in Phase 4"* disabled button so the UX flow is visible but the action is honest about not shipping yet.
- Do NOT add a fourth mode or rename the existing two.
- Do NOT touch `strategy_fusion.py` or `fusion_evaluator.py`.

### Suggested precedent / shape to copy

- For the form + job-poll loop, the simplest precedent is the streaming Generate's `GenerationStatus.jsx` (job tracker + result rendering); fusion is just simpler (no SSE).
- For the rigor verdict badge, copy the pattern from `Strategies.jsx` row-expansion's "Rigor metrics" block. Same DSR / PBO / OOS shape; same `?` affordance opening `RigorExplainer`.
- For the asset-class multi-select, copy `Generate.jsx`'s existing risk-appetite select (single-select is fine for v1 — fusion's backend accepts an array but a single asset class is a reasonable default).

## Suggested execution order

1. **Phase 8 first** — bug fixes are mechanical (~30 min total) and unblock the demo navigation. Polish can be incremental.
2. **Phase 9 second** — depends on a working Generate page (Phase 8 makes sure landing → generate works), and benefits from the same session's familiarity with Generate.jsx.
3. Both phases ship as a single PR off `dbrowneup/spine-plus-v2` (after #135 merges) or as two separate small branches off `main` — author's call.

## Out of scope (deferred — Phase 4 + 5 territory)

- Deploying a fusion-generated strategy into an actual vault — needs the trade-window contract semantics from Phase 4 + the on-chain signing from Phase 5. The button stays disabled / honest in Phase 9.
- Vault-side UI for fusion strategies — Library already renders fusion-generated entries via the `is_example=False` query path; vault-detail UI changes (if any) are part of Phase 4.

## Owner suggestion

Dan (or whoever picks this up in a fresh session). Both phases are pure frontend; no on-chain dependency; no Marten/Chuan blocker. Önder or Daniel R. could pick up either if Dan is sleeping.
