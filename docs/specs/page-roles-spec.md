# Page Roles Spec

> **Status:** Drafted 2026-05-22 as Phase 0 of the
> [Spine+ v2 plan](../plans/spine-plus-v2-plan.md). Authoritative for the spine UI;
> supersedes any conflicting layout description in earlier docs.
>
> **Lineage:** Locks the spine narrowed to 7 nav items in the strip-to-spine
> commit (`d4dd465`) plus the v2 additions (Explore, Strategy passport). Pairs
> with [`vault-semantics-spec.md`](./vault-semantics-spec.md) and
> [`strategy-lifecycle-spec.md`](./strategy-lifecycle-spec.md).

## Scope

Each spine page has exactly one job. **No two pages overlap.** If a screen needs
something a different page owns, it links out — it does not re-implement.

Pages in spine order:

```
/  →  /explore  →  /generate  →  /library  →  /strategy/:id  →  /portfolio  →  /reasoning  →  /corpus  →  /learnings
```

---

## `/` — Landing

**One-sentence purpose:** Marketing surface and entry point; explains the
product and offers the two primary CTAs (Generate, browse Library).

**Primary API calls:** `GET /api/config/contracts`, `GET /api/agent/status`,
`GET /api/regime/current`, `GET /api/strategies/` (count only).

**NOT for:** Wallet-gated actions. Strategy detail. Trace inspection. (The Hero
CTA navigates to `/generate`, which itself prompts wallet connection.)

**Links out to:** `/generate` (primary CTA), `/library` (secondary CTA),
`/explore` (new in v2, asset discovery).

---

## `/explore` — Explore (NEW in v2)

**One-sentence purpose:** Read-only asset discovery — what synthetic RWAs are
available, what their current oracle prices look like, plain-English summary
stats. No wallet required.

**Primary API calls:** new `GET /api/explore/assets` (synth list + oracle
prices), `GET /api/explore/asset/:symbol/stats` (plain-English stats).

**NOT for:** Strategy generation, trading, holding analytics. Explore is the
"what is the universe?" screen — the agent's tradeable asset list, demystified.

**Links out to:** `/generate?seed_asset=:symbol` so a user who finds an
interesting asset can immediately ask for a strategy around it.

**Why new:** the previous "Trade" page conflated discovery with execution and
became a placeholder. Splitting discovery out makes the spine cleaner and gives
unconnected visitors something real to look at.

---

## `/generate` — Generate

**One-sentence purpose:** Streaming strategy generation — user submits a brief,
sees the agent's reasoning unfold via SSE, lands on a Validated/Rejected strategy.

**Primary API calls:** `POST /api/generate/jobs` (create job), `GET
/api/generate/stream/:job_id` (SSE), `POST /api/generate/jobs/:job_id/cancel`.

**NOT for:** Browsing already-generated strategies (that's Library). Persistent
strategy detail (that's `/strategy/:id`). Displaying multi-strategy carousels
(per the locked decision, the agent considers N internally but surfaces 1).

**Links out to:** On success, redirects to `/strategy/:id` for the generated
strategy. On `error` event with `recoverable: true`, offers in-place Regenerate.

**Wallet:** semi-hard gate — page is reachable without wallet, but submitting
a brief prompts connection.

---

## `/library` — Library

**One-sentence purpose:** Outcome of generation. Compact table of all strategies
the user has generated (Generated tab) plus the curated paper-grounded examples
(Examples tab).

**Primary API calls:** `GET /api/strategies/` (examples), `GET
/api/strategies/generated` (user-generated).

**NOT for:** Reasoning trace inspection (that's `/reasoning`). Vault state
(that's `/portfolio`). Strategy detail (that's `/strategy/:id`). The Library
is index-only — names, status pills, rigor verdict at-a-glance, deploy CTA.

**Links out to:** `/strategy/:id` on row click (passport detail), `/generate`
on empty Generated tab.

**Tab default:** Generated for connected wallets (their work); Examples for
disconnected.

---

## `/strategy/:id` — Strategy Passport (NEW in v2)

**One-sentence purpose:** Full passport for a single strategy — paper provenance,
backtest metrics, rigor verdict (with deltas to paper claims), DSL preview,
Deploy CTA (state-dependent).

**Primary API calls:** `GET /api/strategies/:id`, `GET /api/traces/:hash` (for
the reasoning trace that produced it), `GET /api/strategies/:id/papers` (cited
arxiv ids → catalog rows).

**NOT for:** Vault deployment UI (that's a modal opened from this page). Live
performance tracking (that's `/portfolio`).

**Links out to:** `CreateVaultModal` (in-page modal; not a route), `/reasoning?
trace_hash=:hash`, `/corpus?arxiv_id=:id` for cited papers.

**State-driven CTAs:**

- `Generated` → "Run rigor gate" (auto-fires if not already)
- `Validated` → "Deploy as vault →" (primary)
- `Rejected` → "See why" (expands rigor deltas inline)
- `Deployed` / `Active` → "View vault →" (`/portfolio`)
- `Completed` → "View learnings →" (`/learnings`)
- `Expired` → "Regenerate →" (`/generate` with this brief as seed)

---

## `/portfolio` — Portfolio

**One-sentence purpose:** The user's vault footprint — which vaults they have
deployed/active/completed, current NAV, agent activity feed, stress scenarios.

**Primary API calls:** `GET /api/vaults/?wallet=:addr`, `GET /api/agent/status`,
`GET /api/regime/current`, `GET /api/traces?wallet=:addr` (recent traces for
this user's vaults).

**NOT for:** Strategy generation. Strategy detail (links out to `/strategy/:id`).
Library browsing. Cross-user data — strictly the connected wallet's vaults.

**Links out to:** `/strategy/:id` for each vault's underlying strategy,
`/reasoning?vault=:addr` for the vault's trace timeline, `/learnings` for
completed-vault retrospectives.

**Wallet:** hard gate — page is meaningless without a connected wallet.

---

## `/reasoning` — Reasoning

**One-sentence purpose:** Trace browser. Anchored trace hashes, off-chain content,
chronological timeline. Read-only.

**Primary API calls:** `GET /api/traces` (paginated), `GET /api/traces/:hash`,
`GET /api/traces/:hash/verify` (recompute + compare on-chain anchor).

**NOT for:** Strategy listing (those live in Library). Vault state (Portfolio).
Trace publishing (no UI surface — traces are anchored automatically by the agent
runner; the dev-test publish form was removed in the strip-to-spine commit).

**Links out to:** `/strategy/:id?trace=:hash` (back-navigate to the strategy
this trace belongs to), `/portfolio?vault=:addr` (vault context).

---

## `/corpus` — Corpus Explorer

**One-sentence purpose:** Paper catalog + corpus-level overview. Browse the
q-fin corpus, see how it's clustered, drill into specific papers and the
strategies that cite them.

**Primary API calls:** `GET /api/papers/`, `GET /api/papers/:arxiv_id`, `GET
/api/corpus/overview` (cluster counts, top topics), `GET /api/corpus/graph`
(scatter coords from SPECTER2 embeddings — populated by Phase 3c KB pipeline).

**NOT for:** Strategy generation (that's `/generate`, though a paper detail page
deep-links to `/generate?seed_arxiv=:id`). Personal library (Library).

**Tabs:**
- **Catalog** (default) — Papers.app-style three-pane list/detail (Phase 3b).
- **Overview** — Cluster counts + topic labels (plain-English q-fin categories).
- **Graph** — 2D scatter of embeddings (Phase 3c).
- **Knowledge Graph** — Entity/relation explorer (Phase 3c).

**Links out to:** `/generate?seed_arxiv=:id` for paper detail Generate-CTA,
`/strategy/:id` for "strategies citing this paper".

---

## `/learnings` — Learnings

**One-sentence purpose:** Post-hoc retrospective on Completed vaults — realized
P&L, what the agent actually did, what it predicted vs what happened.

**Primary API calls:** `GET /api/vaults/?state=completed&wallet=:addr`, `GET
/api/learnings/:vault_address` (LLM-generated retrospective).

**NOT for:** Active vault monitoring (Portfolio). Strategy detail. Browsing
example/seeded strategies.

**Empty state:** honest copy — "Strategies are time-bound. When your first vault
completes, its retrospective appears here." Links to `/generate`.

**Wallet:** hard gate.

---

## Acceptance

For every page X above:

- Reading X's "One-sentence purpose" and "NOT for" determines whether a piece of
  UI belongs on X or somewhere else.
- Library never lists traces. Reasoning never lists strategies. Portfolio never
  lists examples. Explore never appears on Portfolio.
- The 7 nav items in the strip-to-spine commit map 1:1 to a page above (Home,
  Generate, Library, Corpus, Portfolio, Reasoning, Learnings). Explore is the
  v2 addition (8th nav slot). `/strategy/:id` is route-only (not a nav item;
  reached via Library / Generate redirect / Portfolio links).

## Open questions

1. **Explore as nav vs deep-link only** — should Explore be a top nav slot, or
   only reachable from Landing CTAs and Generate's "seed asset" field?
2. **Strategy passport modal vs full route** — `/strategy/:id` is currently a
   full route per this spec. Should it instead open as a Library-overlay modal
   so users don't lose scroll position?
3. **Learnings empty state** — should we surface example-strategy retrospectives
   ("here's what TSMOM would have done in Q1 2026") to give the page something
   real for unconnected wallets?
