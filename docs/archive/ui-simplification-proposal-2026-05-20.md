# UI simplification proposal — from 12 pages to 5

> **Status:** Day-9 draft (2026-05-20) for team review. The proposal flows from the
> Day-9 rewrite of [`user-stories.md`](user-stories.md); read that first if you
> haven't. Author: Dan; reviewers: Marten, Daniel R., Chuan, Önder.
> **Scope:** structural navigation + page consolidation. Visual polish (CSS, typography,
> spacing) is out of scope for this doc — Daniel R. is the lead on visual polish.

## TL;DR

Today the live app surfaces **12 user-facing pages** across 4 nav groups. The user
story spine (per [`user-stories.md`](user-stories.md)) only needs **5 top-level
pages plus one modal**. We can consolidate without losing functionality and the
result is more legible to the capable-non-expert archetype we're actually building
for. The structural moves are bounded enough to ship pre-launch if we decide to;
the visual polish phase can follow.

## The problem

The capable-non-expert archetype (per [`user-stories.md` § Primary archetype](user-stories.md#the-primary-archetype--the-capable-non-expert))
should be able to land on `/`, understand what Archimedes does, generate a strategy,
and decide whether to deposit — without bouncing between 12 pages or having to
infer the difference between "Explore" and "Strategies" or between "Mint/Burn" and
"Liquidity" (both of which are DeFi primitives, not product moves).

The current navigation reflects what was *built* (every implemented capability gets a
nav entry). The proposed navigation reflects what users actually *do*. Those are
different shapes.

## Current page inventory

Per [`ui/src/components/Layout.jsx`](../ui/src/components/Layout.jsx):

### Home
- **Landing** — first-visit framing

### Markets (3)
- **Explore** (a.k.a. Marketplace) — synthetic assets, vault leaderboard, trending
  strategies, all strategies, agent activity (one big scrolling page; now has the
  sticky subnav from #109)
- **Strategies** — strategy library
- **Trade** — manual AMM swap interface against deployed contracts

### Portfolio (6)
- **Dashboard** — user-specific portfolio overview
- **Mint / Burn** — synthetic asset issuance/redemption (DeFi primitive)
- **Liquidity** — LP positions (DeFi primitive)
- **Vaults** — list of available vaults
- **Create Vault** — vault creation flow
- **Financial Analysis** — financial metrics surface

### Intelligence (3)
- **Reasoning** — reasoning trace browser
- **Risk Analysis** — Kelly calculator + portfolio risk + risk-band visualization
- **Corpus Explorer** — paper catalog + overview + similarity graph + KG (new,
  landed today)

**Total: 12 pages, 4 groups.** Multiple cases of overlap (Explore vs Strategies,
Vaults vs Create Vault) and several pure-plumbing pages (Mint/Burn, Liquidity)
that are DeFi primitives, not user-facing moves on the product spine.

## Proposed page tree (5 + 1 modal)

```
┌──────────────────────────────────────────────────────────────────┐
│  TOP-LEVEL NAV (5 entries)                                       │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  /             Landing                                           │
│                                                                  │
│  /generate     Generate         ← NEW PRIMARY ACTION             │
│                                                                  │
│  /portfolio    My Portfolio     ← consolidates Dashboard + Trade │
│                                   + Vaults + Mint/Burn + Liquidity│
│                                   + personalized Risk view       │
│                                                                  │
│  /library      Library          ← consolidates Explore +         │
│                                   Strategies + Corpus Explorer   │
│                                   (three tabs)                   │
│                                                                  │
│  /learnings    Learnings        ← NEW SURFACE                    │
│                                   (winners + losers + reasoning) │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘

PLUS — modal (no nav entry, opens from anywhere):

  Reasoning trace viewer modal
    — opens from agent-activity feed entries
    — opens from any strategy passport
    — opens from any vault rebalance entry

PLUS — leaf pages (deep links, not in top-level nav):

  /strategy/:id    Strategy passport (deep-linked from Library cards
                                       and from My Portfolio cards)
  /paper/:arxiv    Paper detail       (deep-linked from passports
                                       and from Library Papers tab)
  /vault/:id       Vault detail       (deep-linked from My Portfolio
                                       cards and from Library)
```

Five top-level pages instead of twelve. Each one corresponds to a real move the
user is making on the spine.

## Consolidation rationale (per merge)

### My Portfolio (replaces Dashboard + Trade + Vaults + Mint/Burn + Liquidity + Risk-personalized)

Today these six pages are all *about your money*. The user has to navigate between
them to answer simple questions ("how much do I own?" → Dashboard; "what's it
doing?" → Vaults; "how risky is it?" → Risk Analysis; "how do I move it?" → Trade
+ Mint/Burn + Liquidity). That's 5 nav clicks for one mental task: managing my
portfolio.

**Proposal:** one **My Portfolio** page with:
- Top stat row: total value, 24h/7d change, current portfolio risk profile (computed,
  not chosen)
- Equity-curve chart (vs SPY benchmark)
- Holdings table (left) + Active strategies cards (right)
- Agent activity feed at the bottom (each entry → reasoning-trace modal)
- Sidebar with deposit/withdraw/rebalance buttons + the risk-band visualization
- "Advanced" disclosure (collapsed by default) containing:
  - Mint / Burn (synthetic primitives)
  - Liquidity (LP positions)
  - Raw Trade (AMM swap)

The "Advanced" disclosure keeps the DeFi-primitive surfaces around for power users
without putting them in the top-level nav where they confuse the non-expert.

### Library (replaces Explore + Strategies + Corpus Explorer)

Today these three pages are all *about what exists in the system that isn't yours
yet*. Same browsing intent, different content types.

**Proposal:** one **Library** page with three tabs:
- **All Strategies** (default) — what's in Strategies + the user-generated strategies
  from Marketplace's "All Strategies" section
- **Papers** — the existing Corpus Explorer catalog + graph + KG
- **Vaults & Activity** — the Vault Leaderboard + Agent Activity feed from current
  Marketplace

Left filter rail across all three tabs (asset class, risk tier, rigor verdict, sort).
The current Marketplace's "Synthetic Assets" and "Trending This Week" sections fold
into the Strategies tab as filter views ("Trending = sort by user growth").

### Generate (NEW — promotes the strategy generator from buried to primary)

The strategy generator (engine v2, `POST /api/strategies/generate`) is the single
most novel thing the product does. Today it's reachable but not surfaced as the
primary call-to-action. Promoting it to a top-level page makes the *generative*
nature of the product visible from the front door.

The Landing page's primary CTA becomes "Generate a strategy" — sending the user
straight to `/generate`, no wallet required to see a result. The wallet wall only
appears at Deposit.

### Learnings (NEW — directly endorsed by user feedback)

> "We could even build a kind of 'learnings' page where you can see which strategies
> have been successful, which have not, and maybe why."

**Proposal:** a top-level **Learnings** page that surfaces:
- **Winners** (left column) — currently profitable strategies, sorted by realized
  return, each card with one-line "what went right" generated from reasoning traces
- **Losers** (right column) — currently underperforming strategies, sorted by
  drawdown, each card with one-line "what went wrong"
- Click any card → opens the strategy passport + reasoning traces over the relevant
  period

This surface is **the proof that we don't hide losses.** Every "AI fund" silently
rotates away from losing positions; making losses first-class learning material is
the inverse of that pattern, and the inverse is the trust signal.

## Specific moves (mapping current → proposed)

| Current page | Move | New home |
|---|---|---|
| Landing | Stays | `/` Landing |
| Explore (Marketplace) | Consolidates | `/library` (Strategies tab + Vaults & Activity tab) |
| Strategies | Consolidates | `/library` Strategies tab (primary) |
| Trade | Demotes | `/portfolio` Advanced section |
| Dashboard | Consolidates | `/portfolio` (primary content) |
| Mint / Burn | Demotes | `/portfolio` Advanced section |
| Liquidity | Demotes | `/portfolio` Advanced section |
| Vaults | Consolidates | `/portfolio` (active-strategies cards link to leaf `/vault/:id`); also `/library` Vaults & Activity tab |
| Create Vault | Demotes | `/portfolio` Advanced section + deep-link from a strategy passport's "Deploy" CTA |
| Financial Analysis | Folds in | `/portfolio` (the metrics it surfaces) |
| Reasoning | Becomes modal | Reasoning trace viewer modal (no top-level page) |
| Risk Analysis | Splits | Kelly calculator → `/library` Strategies tab tools panel; portfolio risk → `/portfolio` sidebar; risk-band visualization → `/portfolio` sidebar |
| Corpus Explorer | Consolidates | `/library` Papers tab |
| — | NEW | `/generate` Generate |
| — | NEW | `/learnings` Learnings |

## Discoverability: in-line tooltips, not a glossary page

Per the user-stories rewrite, the convention is **in-line definitions, not a
glossary page**. A glossary page loses context — the user is reading about DSR on
the passport, has to leave the page to look it up, returns having lost their place.

**Convention:** any finance acronym (DSR, PBO, Sharpe, Calmar, OOS, MVO, Kelly,
CAGR, MDD, vol, IS) on first appearance within a section gets a small
dotted-underline link; hover or tap opens a 1–2 sentence definition popover with a
"learn more" link. Acronyms expanded on first use ("Deflated Sharpe Ratio, DSR").

Implementation surface: a single `<Term>` React component that:
1. Looks up the term from a central definitions registry
2. Renders the underlined word/phrase with the tooltip wired
3. Tracks first-occurrence-per-section so the underline only appears once per section
   (using context or a useEffect-registered ref)

A separate `/explain` route can come later for the user who clicks "learn more" from
a tooltip — but it's a deeper-explainer surface, not the front door.

## Implementation phasing

### Phase A — Structural moves (can ship pre-launch if we choose)

Low-risk, mostly nav + routing + page-grouping changes; no new business logic.

- [ ] Consolidate nav into 5 top-level entries; demote 7 current pages
- [ ] Add `/generate` top-level page that hosts the existing engine-v2 generator UI
- [ ] Add `/learnings` top-level page (initially populated from the existing
      strategy + backtest data — winners/losers by realized return)
- [ ] Add the "Advanced" disclosure on `/portfolio` containing demoted DeFi pages
- [ ] Switch the Landing page's primary CTA to "Generate a strategy" (deep link
      to `/generate`)
- [ ] Remove the "Reasoning" top-level page; wire the modal to open from agent-
      activity feed entries
- [ ] Update top-level nav labels accordingly

Estimated effort: ~1 day for Marten + Daniel R. pairing. No backend changes
required (all endpoints already exist).

### Phase B — In-line tooltip system (can ship pre-launch)

- [ ] Build the central definitions registry (a single JSON file or a Python
      module the backend serves as `/api/glossary`)
- [ ] Build the `<Term>` React component with the dotted-underline + popover
- [ ] Wire it into the 10 highest-leverage surfaces (passport, generator result,
      learnings cards)
- [ ] Seed the registry with the ~20 most critical acronyms

Estimated effort: ~0.5 day. Önder is the right person to draft the definitions
(stats background + already drafting the "explain the rigor gate" surface per the
standup).

### Phase C — Visual polish on the consolidated pages (post-hackathon)

- [ ] Daniel R. and Marten do a polish pass on the new consolidated `/portfolio`
      and `/library` to make them feel like single coherent pages, not stitches.
- [ ] Out of scope for the hackathon submission; surface it on a polish list for
      the post-launch week.

## Open questions / decisions needed

1. **Do we ship Phase A pre-launch, or post-launch?** Pre-launch is more impactful
   for the demo (judges see the simplified nav) but adds risk in the last 4 days.
   Post-launch is safer but the demo carries the current 12-page surface. **My
   read:** ship Phase A pre-launch, with Phase B as a stretch if Phase A lands by
   Friday.
2. **Do we keep the existing "Marketplace" terminology anywhere?** Or fully retire
   it in favor of "Library" + "My Portfolio"? "Marketplace" implies trading; we're
   not a marketplace today (no inter-user trading; that's the roadmap social
   vision). I'd retire the term for the MVP.
3. **The Strategy passport route — `/strategy/:id`?** Currently strategies are
   reachable from a few places; making this the single canonical deep-link is good
   hygiene but requires a small URL-routing change. Worth doing as part of Phase A.
4. **Vault detail — keep as a leaf page or fold into the Strategy passport?** A
   strategy IS deployed via a vault, and the vault carries on-chain state the
   strategy alone doesn't. I'd keep `/vault/:id` as a separate leaf for now, with
   the strategy passport linking to it.
5. **The Learnings page needs data.** Without enough deployed strategies and
   time-elapsed performance, "Winners + Losers" is sparse. The demo can use the 5
   reference strategies + their 22-year backtest results as the data source
   (rolling 1-year periods → clear winners + losers). Pre-launch decision: is
   that honest enough, or do we need live deployed-strategy outcomes?

## See also

- [`user-stories.md`](user-stories.md) — the user stories this proposal serves
- [`claude-design-prompts.md`](claude-design-prompts.md) — Prompts 3 (UI refinement
  toward this page tree) and 12 (current-vs-proposed page-map diagram)
- [`corpus-architecture.md`](corpus-architecture.md) — what the Library Papers tab
  is actually serving
- [`launch-plan.md`](launch-plan.md) — what needs to be true at launch
