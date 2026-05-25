# Claude Design — Prompts for Archimedes

> **Audience:** Dan (and anyone on the team) using Claude Design at
> [claude.ai/design](https://claude.ai/design).
> **Purpose:** Concrete, paste-ready prompts for slides, UI prototypes, logos, and
> explanatory visualizations. Each prompt is self-contained so the design session has
> the context it needs without you re-explaining the project.
> **Status:** **Day-13 revision (2026-05-25, submission day).** Refreshes the Day-10 baseline
> against spine-plus-v2 (8 commits ahead of `origin/main`: Phases 0–3 + follow-ups
> + Makefile + the Day-11 docs cleanup) and Chuan's `bd6935b` (Strategy DSL +
> interpreter + fusion evaluator pipeline). Current snapshot the prompts reflect:
> live React/Vite UI at [`https://archimedes-arc.app/`](https://archimedes-arc.app/) on
> t3.medium EC2; 10 Arc-testnet contracts (Vault.sol multi-asset NAV via oracle
> in `totalAssets()`); **streaming Generate** (`POST /api/generate/start` →
> SSE stream from the LLM agent loop, with hard-cancellation + Redis-backed
> `Last-Event-ID` replay); **Explore page** (top-level nav; asset + history
> read from yfinance via `asset_market_service.py`, not the on-chain oracle —
> oracle has no history); **Reasoning page restructured** (the Library now
> carries the strategy detail view + deep-link via `?highlight=`); **onboarding
> tour** (Phase 6, separate branch `dbrowneup/phase6-onboarding`) — 6-card
> MetaMask-style modal with in-repo SVG illustrations + "?" topbar reopen;
> **agentic portfolio advisor** (`portfolio_agent.py` LLM tool-loop, picks
> individual stocks/bonds anchored to paper-grounded passports); **stress
> engine** (`stress_engine.py` six historical shocks, backend ready, UI strip
> still on Phase 4 candidate list); the full rigor wedge live (DSR/PBO/Kelly/
> MVO via canonical `services/rigor_evaluator.py`; 2 Tier-1 strategies — Faber
> 2007 + Moreira-Muir 2017 — pass all four gates on 22-year SPY); DB-backed
> 10,000-paper q-fin corpus with the Corpus Explorer UI shipped; "Linus for
> quantitative finance" framing locked per [`docs/user-stories.md`](user-stories.md);
> **806 backend tests** + 16 analytics-engine tests collected; **submission day**.
> UI prompts work as **refinement** prompts against the live UI, not greenfield.
>
> **Aligned with [`docs/chuan-architecture-survey.md`](chuan-architecture-survey.md)
> (Day-11):** the shipped-state language reflects the same commit set the survey
> enumerates. When the survey moves, this file moves with it.
>
> **Day-11 delta callouts for prompts below** — items the existing prompts may
> still describe in Day-10 terms; refine when running them:
>   - **SLIDE 4 inventory** should now name *fusion-to-backtest pipeline (DSL +
>     interpreter + rigor gate, `services/fusion_evaluator.py`)* alongside the
>     agentic advisor + stress engine. The wiring into `_run_fusion_job` is
>     in-flight as [#133](https://github.com/a-apin/archimedes-arcadia/issues/133)
>     so word it carefully if the demo doesn't cover that surface.
>   - **Page map / Prompt 3** should reflect: Explore is a top-level nav page;
>     Reasoning lost its Strategies tab (details moved to Library); Generate is
>     streaming with a mode toggle (Streaming agent vs Architect fast preview);
>     onboarding tour appears on first visit + via the "?" icon in the topbar.
>   - **Test counts**: 806 backend tests (as of 2026-05-25, submission day); t2o2 issues
>     [#129](https://github.com/a-apin/archimedes-arcadia/issues/129)–
>     [#133](https://github.com/a-apin/archimedes-arcadia/issues/133) resolved.

## Quick notes on using Claude Design

[Claude Design](https://claude.ai/design) is a research preview by Anthropic Labs. From
the landing page, three modes are relevant for Archimedes:

- **Prototype** — wireframe or high-fidelity. Use this for the user-facing UI flow.
- **Slide deck** — directly produces pitch slides.
- **From template / Other** — for custom needs.

A few tips before diving in:

1. **Set up a design system first** if you have brand colors / fonts in mind. There's a
   "Set up design system" CTA on the landing page. Worth 5 minutes up front to maintain
   visual consistency.
2. **Generate iteratively.** Start with the smallest scope (a single slide, a single
   screen) and expand once the style is right. Claude Design is interactive; you can
   refine.
3. **For visual references:** Claude Design can fetch URLs you give it. Screenshots
   from your end are also fine — paste them in the conversation as you would in a normal
   chat. Both work. Use URLs when you want me (this Claude session) to also see the
   reference; use screenshots when it's faster.

## How to use this doc

Each prompt below is **paste-ready**. Read the "Setup notes" first to make sure you're in
the right Claude Design mode (Prototype / Slide deck / Other), then paste the prompt into
the conversation. Edit the placeholder bits in `<angle brackets>` if anything is
inaccurate.

---

## Design system setup — paste-ready field values

The Claude Design **"Set up your design system"** page (the first step before any
prompts) asks for a company blurb, optional code/Figma/asset uploads, and a free-form
notes field. The values below are tuned to Archimedes' actual surfaces and constrain
all downstream prompt outputs without re-explaining the project each time.

**Tips on the upload fields:**

- **Link code on GitHub:** `a-apin/archimedes-arcadia` is sufficient. The repo carries
  the live `ui/` (React 19 + Vite 8 + viem 2.48), `docs/architecture-diagram.html`, and
  all the curated design context. No need to configure additional repos.
- **Link code from your computer:** skip. Requires Chrome/Edge and the GitHub link
  covers the same ground.
- **Upload a .fig file:** none — Archimedes doesn't have a Figma source of truth (and
  doesn't need one for hackathon scope).
- **Add fonts, logos and assets:** none externally. The logo is what Prompt 1 below
  generates; fonts are open-source web fonts named in the design system below.
- **Do NOT upload `ui-mockups/`.** Those are retired static-HTML prototypes from Day
  1–2 that the team kept only for archival reference. Uploading them would actively
  steer Claude Design toward stale visuals. The "any other notes" field below tells
  Claude Design to study `ui/` (the live React app) and ignore `ui-mockups/` — that's
  the correct mental model.

### Field: Company name and blurb

```
Archimedes: a research-grounded strategy-generation instrument. You describe what you
want; it fuses your intent with live market data and a ~10,000-paper quantitative-finance
research library into novel strategies, gates them through selection-bias rigor
(deflated Sharpe / overfitting probability), and lets you execute + monitor them on Arc
(Circle's stablecoin-native L1) — every reasoning step traceable to the source paper and
anchored on-chain. Currently runs on the Arc *testnet* (no mainnet yet) with faucet USDC.
Surfaces: web app (React + Vite), pitch deck, repo README + GitHub presence. Built for
the Agora Agents Hackathon (Canteen × Circle × Arc, May 11–25, 2026).
```

### Field: Any other notes

```
Tone: serious, financial-grade, academic-rigor cue. Think a crossover between
Wealthfront's marketing site and a crypto-native product like Arc.network or Circle.
No clip art, no emojis in product surfaces, no busy decoration, and specifically no
cliché Greek-temple imagery despite the name — Archimedes is the patron saint of
empirical reasoning, not a tourism brand.

Palette: dark-mode primary. Near-black background (#0E1116), off-white text (#F3F4F6),
muted gray (#9CA3AF) for secondary text, single brand accent — pick one of deep blue
(#2A4DD1) or violet (#7B2CBF) and commit. Success #10B981, warning #F59E0B, error
#EF4444. Plain CSS in the React app — we are NOT using Tailwind or any CSS framework.

Typography: serif headlines for the academic-rigor cue (Crimson Pro or Source Serif
Pro), modern sans body (Inter or Geist Sans), monospace for hashes and addresses
(JetBrains Mono or Geist Mono).

In the linked repo: the live frontend is `ui/` (React 19 + Vite 8 + viem 2.48).
Ignore `ui-mockups/` — that's a retired set of static-HTML prototypes from Day 1–2,
kept only for archival reference. Live testnet deploy: https://archimedes-arc.app/.
Architecture diagram: `docs/architecture-diagram.html`. Curated per-asset design
prompts: `docs/claude-design-prompts.md`. Pitch + demo context:
`docs/demo-script-pitch-deck-outline.md`.

Product narrative: "every claim the agent makes is wrong-able on the record." The wedge:
on-chain curation is run on *trust*; the Nov-2025 industry crisis showed that breaks on
rigor. Archimedes is the *proof-based* alternative — research-grounded generation +
selection-bias rigor gate + provenance traceable to the source paper, anchored on Arc.
Be honest in copy: AI can be wrong; the goal is to win more than you lose, not to never
lose; testnet-only by design. Strategies carry a passport (paper citation + methodology
hash + paper-claim delta surfaced honestly). Visual language should signal academic
rigor + on-chain provenance + honest risk, not crypto-speculation vibes. Canonical
narrative + competitive framing live in `docs/demo-script-pitch-deck-outline.md` and
`docs/competitor-landscape.md` — any slide/marketing copy must match those.
```

After saving the design system, run **Prompt 1 (logo)** below first — smallest scope,
fastest feedback on whether the system is steering correctly. If the logo output feels
right, scale up to the slide deck and UI refinement prompts.

---

## Prompt 1 — Logo set (multiple variants)

**Setup notes:** Use Claude Design's **Other** mode for graphics generation. If a "logo"
template exists, use that. Generate 3–4 variants and pick the strongest.

```
Project: Archimedes — an AI portfolio agent that grounds investment strategies in
bleeding-edge academic quant finance research, settled on Arc (Circle's stablecoin-native L1)
with USDC.

I need a logo for the project. Generate 4 distinct logo concepts as separate options.
Each should be:
- A standalone mark (icon) + a wordmark (the word "Archimedes")
- Suitable for both light and dark backgrounds
- Clean and professional — this is a financial product, not a consumer app
- Conveys the "rigorous empiricism + AI + on-chain finance" combination

The four concept directions:

CONCEPT A — Archimedean spiral
A stylized Archimedean spiral (the mathematical curve named after him). Clean,
geometric, modern. The spiral can suggest both: the mathematician's discovery, AND
the recursive nature of autonomous agent decision-making. Color palette: a deep blue
or violet for the spiral, with the wordmark in a clean serif (the historical
reference) or modern monospace (the on-chain reference).

CONCEPT B — The lever and fulcrum
Archimedes' famous quote: "Give me a lever long enough and a fulcrum on which to
place it, and I shall move the world." A minimal lever-and-fulcrum mark — could
abstract into a balanced scale or a Greek-letter-like glyph. Subtle but recognizable.
Color palette: gold + dark navy (the "weighty intellectual" feel).

CONCEPT C — Eureka / displacement
The bath / displacement principle — Archimedes' "Eureka!" moment. This could be a
single droplet or a wave-form mark, suggesting both the displacement principle and the
flow of capital. Color palette: blue gradient (water) + warm accent.

CONCEPT D — Mathematical glyph
A stylized mathematical symbol that suggests Archimedes (the historical figure)
without being literal. Could combine: π (he calculated it), an integral symbol, an
infinity symbol, or a stylized lowercase Greek "α" (alpha). Color palette:
monochrome with a single accent color.

For each concept, provide:
- The icon at 256×256
- The wordmark with icon (horizontal layout)
- The wordmark with icon (vertical/stacked layout)
- A favicon-sized version (32×32)

Avoid: AI-generated photorealism, busy details, cliché Greek-temple imagery, anything
that looks like it could be a generic crypto logo.
```

**What to do with the output:**

- Pick the strongest concept (or merge two).
- Confirm with the team in Discord before locking.
- Export in PNG + SVG.
- Use the chosen mark in the Discord server, the GitHub repo (README header), the pitch
  deck, and the frontend.

---

## Prompt 2 — Slide deck (full 10 slides, Day-10 inventory)

**Setup notes:** Use Claude Design's **Slide deck** mode. Make sure the design system is
set (or paste the palette/typography block inline). The deck structure mirrors
[`demo-script-pitch-deck-outline.md`](demo-script-pitch-deck-outline.md). The
substantive shifts the deck must reflect: the product is framed as **"Linus for
quantitative finance"** (a strategy-generation *instrument*, not a static menu); the
3-input fusion engine (engine v2) is live; the rigor wedge is real with 22 years of
SPY data; 2 Tier-1 strategies actually pass the gate; Day-10 additions of the
agentic portfolio advisor + stress engine + multi-asset NAV vault land in SLIDE 4's
inventory. The deck should sound like the product, not aspire to it.

**Source materials (paste these alongside the prompt below — Claude Design can fetch
or you can drop the contents inline):**

- [`docs/demo-script-pitch-deck-outline.md`](demo-script-pitch-deck-outline.md) —
  the master deck/script (Dan's canonical voice + slide order)
- [`docs/pitch-talking-points-rigor-track.md`](pitch-talking-points-rigor-track.md) —
  **Önder's rigor / agent / on-chain-provenance handout (closes issue #127).** Four
  credibility moves with verbatim lines for the rigor slides, the
  "96-other-submissions" discriminator table for SLIDE 6, an explicit don't-say list,
  the Archimedes-as-empiricist closer for SLIDE 10. The handout is the authoritative
  voice for the wedge slides (SLIDE 4 rigor row, SLIDE 6, SLIDE 7 agentic
  sophistication, SLIDE 9 honest framing).
- [`docs/portfolio-advisor-demo-cues.md`](portfolio-advisor-demo-cues.md) — Önder's
  60-second verbatim cue card for the SLIDE 5 demo moment (two paths: agent-live
  vs rule-based-only, Q&A scripts, failure backups). The slide is just the timer; the
  cue card is what you say while it's on screen.

**Where each source lands in the deck (per Önder's handout structure):**

| Slide | Primary source | What the handout adds |
|---|---|---|
| SLIDE 4 (right column "Rigor + on-chain") | handout § "Four credibility moves" | Verbatim "Every Tier-1 strategy clears four statistical bars..." line for the rigor bullets |
| SLIDE 6 (the wedge — 4-column comparison) | handout § "96-other-submissions comparison" | More concrete discriminator: "Sharpe ratio from one backtest" vs "DSR + PBO + walk-forward OOS + look-ahead audit"; the comparison rows are sharper than the current generic-category framing |
| SLIDE 7 (Agentic Sophistication) | handout § "The four credibility moves" #2 | Specific tool names — `get_asset_stats`, `get_correlation`, `stress_test_portfolio` — and the "up to 12 tool-use turns" detail. **The demo moment to engineer per the handout's closing note: the agent's tool-use trace where it says "I checked the correlation, saw it was too high, dropped the pick"** |
| SLIDE 9 (Honest framing) | handout § "The honest-frame slide" | Adds the **RIA-posture-is-roadmap** line that's missing from our current bullets; reinforces the existing testnet/no-alpha/AI-can-be-wrong points with the handout's exact phrasing |
| SLIDE 10 (closer tagline) | handout § "The closer" | The "Archimedes the mathematician was its empiricist — π by exhaustion, levers, proofs. Archimedes the product is an AI citizen who participates in the modern agora with proofs" framing |
| (Setup notes for the presenter) | handout § "Don't-say list" | Pre-emptive guard: avoid "Our model" / "Beats the market" / "Fully autonomous" / "Production-ready" / "Better than [X]" in deck copy AND in the live narration |

```
Project: Archimedes — pitch deck for the Agora Agents Hackathon (Canteen × Circle ×
Arc), submission deadline May 25, 2026. 3-minute pitch + live demo + Q&A.

The deck is 10 slides. Visual style: clean, professional, financial-grade. Think a
crossover between Wealthfront's marketing and Arc.network / Stripe / Linear. Dark-mode
primary (#0E1116 background, #F3F4F6 text), single brand accent (#2A4DD1 deep blue
OR #7B2CBF violet — pick one and commit). Serif for headlines (Crimson Pro or Source
Serif Pro — the academic cue); sans for body (Inter or Geist Sans); monospace for
hashes, addresses, paper IDs. No clip art, no emojis in the artifact, no busy
decoration, no Greek-temple imagery.

Slide-by-slide content:

SLIDE 1 — Title
Header: Archimedes
Tagline: Linus for quantitative finance.
Subtitle: A research-grounded strategy-generation instrument — fusion + rigor +
on-chain provenance, on Arc.
Attribution: Agora Agents Hackathon — Canteen × Circle × Arc, May 11–25, 2026

SLIDE 2 — The problem (one paragraph, centered, no bullets)
Title: AI portfolio tools today are unfalsifiable.
Body: "Robo-advisors are rule-based black boxes. AI-flavored crypto agents are
token-mediated speculation with opaque reasoning. Both ask you to trust. Neither
surfaces the research their claims rest on — and neither gates against the failure
mode that breaks most quant strategies in production: in-sample overfitting that
doesn't survive contact with reality."
Small footer line: "Curation crisis Nov 2025: $400M+ in DeFi vault losses driven by
exactly this gap."

SLIDE 3 — The product (the spine)
Title: Describe → Generate → Rigor-gate → Execute → Monitor → Explore
A horizontal 6-step subway map (see Prompt 9 below for the standalone version). Each
stop has an icon + one-line caption. Two of the stops are tagged "User" (Describe,
Monitor+Explore); the others are tagged "System." Below the track, a thin banner:
"Every step's reasoning is hashed and anchored on Arc — anyone can verify."

SLIDE 4 — What's actually built (status as of submission)
Title: Shipped, not planned.
Two columns:

LEFT — Generation + intelligence
- 3-input fusion engine live: user brief × live market regime × 10,000-paper q-fin
  corpus → grounded strategy
- 10,000 q-fin papers in a Postgres-canonical corpus with the Corpus Explorer UI
- 5 reference strategies (Faber 2007 SMA200, Moreira-Muir 2017 vol-managed,
  Moskowitz-Ooi-Pedersen 2012 TSMOM, George-Hwang 2004 52-week high, buy-and-hold)
  + a capital-preservation T-bill baseline
- 22 years of real SPY backtest data — no placeholder numbers anywhere
- Agentic portfolio advisor: an LLM agent loop (tool-calling, up to 12 iterations)
  that picks individual stocks/bonds from a global market scan and anchors every
  pick to a paper-grounded strategy passport
- Stress engine: six canonical historical/scenario shocks (1987 crash, 2008 GFC,
  2020 COVID, 2022 rate-shock, stagflation, deleveraging) — backend ready, UI
  surface incoming

RIGHT — Rigor + on-chain
- Selection-bias gate live: Deflated Sharpe (Bailey & López de Prado 2014) +
  Probability of Backtest Overfitting (CSCV) + walk-forward OOS + look-ahead audit
- 2 Tier-1 strategies passing all four gates today (Faber, Moreira-Muir) —
  the failures are visible, not hidden
- 10 Arc-testnet contracts deployed: Vault, VaultFactory, SyntheticVault,
  SyntheticFactory, SyntheticToken, AMMPool, AMMRouter, AssetRegistry,
  PriceOracle, ReasoningTraceRegistry. **Vault.sol is now multi-asset NAV** —
  `totalAssets()` prices every holding via the oracle, so the share price is
  honest under mixed-asset allocations
- Multi-wallet UX (MetaMask / Coinbase / generic) with profile dropdown.
  806 backend tests + 16 analytics-engine tests collected.

SLIDE 5 — DEMO
Full-bleed slide: the word "DEMO" in large serif type, brand accent, with the live
URL below in monospace: "https://archimedes-arc.app" (or the locked domain once announced).
This slide is the demo timer — minimum 90 seconds of live click-through, ending on
a strategy passport showing the DSR p-value + PBO + OOS Sharpe + the source-paper
citations.

SLIDE 6 — The wedge (why "research-grounded" is real)
Title: We fill the gap others can't.
A 3-column comparison:
- TradFi robos (Wealthfront, Betterment): rule-based · opaque · no on-chain
  settlement · no research provenance
- DeFi yield (Yearn, Morpho's curated vaults): chase live yield · curation
  without proof · documented losses in Nov-2025 curation crisis
- AI crypto agents (Virtuals, SingularityDAO, Theoriq): token-mediated
  speculation · opaque reasoning · no rigor gate
- ARCHIMEDES (highlighted column): research-grounded generation · selection-bias
  rigor gate · on-chain reasoning trace · non-custodial settlement on Arc
Bottom callout: "Curation-without-proof is the industry default. Curation-with-proof
is the wedge."

SLIDE 7 — Why we'll score well (judging criteria)
Title: How Archimedes maps to the rubric.
Four columns (one per criterion):
- Agentic Sophistication (30%): 3-input fusion, autonomous rebalancing on regime
  change, on-chain reasoning traces, agentic-issue pipeline self-iterating the
  codebase.
- Traction (30%): live testnet deploy, arc-canteen telemetry surface wired,
  coordinated launch within submission window.
- Circle Tool Usage (20%): Wallets, USYC, CCTP, Gateway, Paymaster, full
  Contracts stack on Arc.
- Innovation (20%): paper-grounded provenance, externally-verifiable rigor gate
  (DSR/PBO), commit-reveal trace anchoring (provable causal ordering).
RFB tagline: "RFB 04 primary. RFB 02 math primitive. RFB 06 adjacent."

SLIDE 8 — Why now (one slide, 3 numbers)
Title: The agent economy is real, but unaccountable.
Three big-number callouts:
- $222M — Circle Arc presale at $3B FDV, May 11, 2026 (BlackRock among investors)
- $400M+ — DeFi curated-vault losses in the Nov 2025 curation crisis
- 18,000+ — AI agents deployed via Virtuals Protocol ($470M aGDP)
Sub-tagline: "Capital is flowing into agentic finance faster than accountability is
being built. Archimedes is the accountability layer."

SLIDE 9 — Honest framing (the "anti-claims" slide)
Title: What we're NOT promising.
Three bullets, plain text, no decoration:
- We don't promise alpha. We promise *evidence-grounded generation with externally
  verifiable rigor*. Past performance, even of validated strategies, doesn't
  guarantee future returns.
- Arc is testnet — by design. Putting real funds on a chain that's <6 months from
  mainnet would be reckless. The contracts are real; the settlement is real
  testnet USDC.
- The corpus is generated-from, not retrieved-from. The LLM reads cited papers
  and produces a strategy spec; it does not lift strategies verbatim from any
  single paper.
Sub-tagline: "Honest framing is the wedge. Anyone can promise 100x. We can
*prove* what our agent reasoned about and why."

SLIDE 10 — Team + ask
Title: The team.
5 names + one-line credentials each:
- Dan Browne — Senior Scientist @ LanzaTech, PhD biochemistry. Strategy engine, pitch.
- Marten Windler — Systems Engineering, U. Bremen. Off-chain ↔ on-chain integration.
- Daniel Reis dos Santos — Backend engineer, distributed systems. Frontend lead.
- Chuan Bai — CTO @ Gyld Finance; ex-CoinShares trading platform. Contracts + infra.
- Önder Akkaya — ASA Statistical Insight World Champion. Portfolio math + rigor.
5 timezones, 5 days, a day-job constraint on two of us, and we shipped it.

Ask: "Feedback on the strategy passport as a candidate open standard. Introductions
to quant researchers who'd contribute strategies. Partnerships with RWA-token
issuers who want their assets in research-grounded portfolios."

End-of-deck tagline: "The lever is academic research. The fulcrum is autonomous AI.
The world is your portfolio."

Generate all 10 slides with consistent palette / typography / information density.
Where I've called out a comparison diagram (Slides 3, 6) or a status grid (Slide 4),
preserve the layout — those visual structures are load-bearing for the pitch.
```

**What to do with the output:**

- Review each slide; iterate the ones that don't land. Slides 4 (status) and 6 (the
  wedge) are the two most likely to need refinement — both carry a lot of substance.
- Export to a deck format (Keynote / PowerPoint / PDF) for the actual pitch.
- Pull the wedge diagram from Slide 6 as a standalone image for the launch tweet and
  the Discord pin.

---

## Prompt 3 — UI refinement toward the simplified page tree (Day-9 generator-first)

**Setup notes:** Use Claude Design's **Prototype** mode with **High fidelity** selected.
This is a refinement prompt against the live UI at
[`https://archimedes-arc.app/`](https://archimedes-arc.app/), targeting the proposed
simplification in [`docs/archive/ui-simplification-proposal-2026-05-20.md`](archive/ui-simplification-proposal-2026-05-20.md) (now shipped via spine-plus-v2 Phases 0–7; see [`docs/specs/page-roles-spec.md`](specs/page-roles-spec.md) for the current page model).
**The Day-4 risk-tier-cards onboarding flow is retired** — the product is now
generator-first: users describe what they want, fusion produces a candidate strategy,
the rigor gate admits or rejects, and the user inspects + deposits. Risk tolerance is
*an input to the brief*, not a top-level page.

```
Project: Archimedes — frontend visual refinement toward a simplified page tree.

Live reference URL (please fetch + study before generating): https://archimedes-arc.app/
This is the shipped UI — React 19 + Vite 8 + viem 2.48 + plain CSS (no Tailwind,
no Next.js). Wallet connect (MetaMask / Coinbase / generic), Marketplace, Trade,
Vaults, Intelligence (Corpus Explorer + Risk Analysis) all live.

Goal: refine the live UI toward a tighter top-level navigation:
- LANDING (current, lightly polished)
- GENERATE (NEW top-level page — promotes the strategy generator from buried to
  primary call-to-action)
- MY PORTFOLIO (consolidates current Trade + Vaults + the personalized Risk view)
- LIBRARY (consolidates current Marketplace + Strategies + Corpus Explorer)
- LEARNINGS (NEW page — surfaces winning AND losing strategies with reasoning
  traces fully readable; "what worked, what didn't, why")

Generate four connected screens that walk a non-expert user through the spine.
Information density should feel like a serious financial product — like Wealthfront
crossed with Linear — not like a crypto degen app.

SCREEN 1 — Generate (the new top-level page)
The page that replaces "where do I start" with "tell us what you want."
Layout:
- Header: "Generate a strategy"
- Subheader: "Describe what you want. We'll fuse your intent with live market data
  and 10,000 q-fin papers into a candidate strategy, then run it through our
  rigor gate."
- Input area: a single large prose textarea — "Tell us about your goal" — with
  3-4 example chips below it the user can click ("Steady income, low drawdown",
  "Growth-oriented, OK with volatility", "Capital preservation", "Diversified
  cross-asset").
- Three optional structured inputs below (collapsible "Advanced"):
  - Asset class slider (Fixed Income → Equities → Crypto)
  - Risk tolerance slider (Conservative → Hyper-Risky)
  - Time horizon (3 mo / 1 yr / 5 yr / 10 yr)
- A single "Generate" CTA button
- Below: a small panel showing what fusion will see — "Your brief" + "Current
  market regime: VIX 14, equity-bond corr +0.3" + "Corpus: 10,000 papers across
  9 q-fin categories" — making the 3 inputs visible

SCREEN 2 — Generate (live result, after click)
Shows the candidate strategy as it comes back from the engine.
Layout:
- Top: the strategy name + a colored rigor badge (Tier-1 / Tier-2 / Rejected)
- The strategy spec card (entry/exit rules, sizing, asset universe, 3-4 source
  papers cited with arXiv IDs in monospace)
- Backtest results in a strip: Sharpe, Max DD, CAGR, Win Rate, all with the
  paper-claim deltas visible
- Rigor gate verdict panel (collapsible — open by default for Tier-1):
  - DSR p-value (with "what this means" inline tooltip)
  - PBO score (same)
  - OOS Sharpe / IS Sharpe ratio
  - Look-ahead audit check
  - Trade count
- Two CTAs: "Deploy to vault" (primary, only for Tier-1) and "Generate another"
- "Why these papers?" expandable section listing the cited papers with one-line
  reasoning for each — surfaces fusion's selection logic

SCREEN 3 — My Portfolio (consolidates Trade + Vaults + personalized Risk)
Layout:
- Top stat row: Total USDC value · 24h change · 7d change · current portfolio
  risk profile (computed, not chosen)
- Performance chart (large) — portfolio equity curve with SPY benchmark line
- Two-column body:
  LEFT: "Holdings" — table of vault tokens with weight, value, last rebalance.
  Each row is an actual asset priced live via the multi-asset NAV vault oracle —
  no synthetic-only fiction.
  RIGHT: "Active strategies" — cards of the strategies in your portfolio with
  one-line status and a link to each strategy's detail page (Prompt 4). Cards
  driven by the agentic advisor surface a small "agent picked" pill +
  paper-citation chip so the LLM's pick is wrong-able on the record.
- Stress-scenario strip (below the body): a horizontal row of six cards, one per
  canonical shock (1987 / 2008 GFC / 2020 COVID / 2022 rate-shock / stagflation /
  deleveraging). Each card shows the modeled portfolio drawdown in this scenario,
  monospace, with a click-through to a per-scenario asset-class breakdown. Backed
  by stress_engine.py (Σᵢ wᵢ · shock_class(i, scenario)). This panel is the
  rigor-gated answer to "what happens if conditions break."
- Bottom: "Agent activity feed" — chronological list of agent decisions
  (rebalances, risk-off rotations, position adjustments, advisor recommendations),
  each with timestamp, one-line summary, and "View reasoning trace" link (opens
  Prompt 5's modal). Advisor-loop entries show iteration count + tool-call count
  to make the multi-turn deliberation visible.
- Sidebar: deposit / withdraw / rebalance buttons + a small "Risk profile
  comparison" card (the band visualization currently on the standalone Risk
  page — consolidated here)

SCREEN 4 — Library (consolidates Marketplace + Strategies + Corpus Explorer)
The "browse what exists" surface. Three tabs at the top: All Strategies / Papers /
Vault Leaderboard. Each shipping today; here we just consolidate them into one
top-level nav entry.
Default tab: All Strategies.
- Left filter rail: Asset class · Risk tier · Rigor verdict (Tier-1 only / All) ·
  Sort (Sharpe / OOS Sharpe / Recently added)
- Main: grid of strategy cards, each showing name, rigor badge, key metrics
  (Sharpe, max DD, CAGR), source paper count, and "View detail" link
- Empty-state copy that nudges back to Generate: "Don't see one that fits?
  → Generate a custom strategy"

Add ONE new convention across all screens: **in-line term definitions, not a
glossary page.** Any finance acronym (DSR, PBO, Sharpe, Calmar, OOS, MVO, Kelly,
CAGR, MDD) on first appearance gets a small dotted-underline link; hover or tap
opens a 1-2-sentence definition popover. Acronyms expanded inline on first use
within a section.

Visual style: Dark mode (#0E1116 background, #F3F4F6 text, #2A4DD1 or #7B2CBF
accent), serif headlines (Crimson Pro / Source Serif Pro), sans body
(Inter / Geist Sans), monospace for arXiv IDs and hashes. Information density:
medium-high. Each screen should feel like one coherent picture, not a wall of
panels.

Make the prototype interactive enough that the user can flow Generate → result →
Deploy → My Portfolio → strategy detail → reasoning trace. Don't worry about real
data; placeholder values are fine.
```

**What to do with the output:**

- Share with Marten + Daniel R. as the **refinement target** for the post-MVP UI
  polish pass (not a from-scratch reimplementation — incremental moves toward this
  page tree). The page consolidation moves (Trade + Vaults → My Portfolio;
  Marketplace + Strategies + Corpus Explorer → Library) are the structural lift.
- The Generate screen (Screen 1) is the biggest user-facing move — feedback on that
  one is highest-leverage.
- Use Screen 2 (the generated-strategy result) as the screenshot for the Library /
  README hero — it visually carries the "research-grounded" claim.

---

## Prompt 4 — Strategy passport / detail page (Day-10 refresh, real numbers + multi-asset NAV vault execution)

**Setup notes:** Single screen. Use **Prototype / High fidelity**. This is the most
important content surface in the entire product — judges will land here when they
click any strategy from the demo. The example uses Moreira-Muir 2017 because it's
one of the 2 Tier-1 strategies that actually passes the rigor gate today (real
numbers, not aspirational).

```
Single screen: the Archimedes Strategy Passport.

Context: a user clicked on a strategy from the Library or from their portfolio.
This screen surfaces the strategy's "passport" — the verifiable provenance + the
full rigor verdict.

Use Moreira-Muir 2017 as the worked example (it's one of our 2 currently-Tier-1
strategies). Layout:

Header strip:
- Strategy name: "Moreira-Muir Volatility-Managed"
- Rigor badge: large pill saying "TIER-1 — Archimedes Verified" with a small
  checkmark icon, brand-accent fill
- Sub-header: paper citation, formatted like an academic reference —
  "Moreira, A., & Muir, T. (2017). Volatility-Managed Portfolios.
  Journal of Finance, 72(4), 1611–1644. [arxiv: 1604.05601 | doi:10.1111/jofi.12513]"

Three-column metadata strip (each card monospace for the numeric):
- Backtest Sharpe (2004–2026, real SPY, 10bps costs): 0.769
- Paper-claimed Sharpe: 0.60
- Delta vs paper: +28% (small green chip — we BEAT the paper)
- Sub-line: "Computed against 22 years of SPY data. Backtest code hash: 0x4f7a…2c91
  on Arc."

Methodology section (~3 paragraphs of plain-English explanation):
- What the strategy does
- Why it's expected to work (the economic intuition)
- The implementation details (vol-targeting window, leverage cap, costs assumed)
- Key terms bolded with the same dotted-underline inline-definition convention
  used elsewhere

Rigor gate verdict panel (the headline differentiator — give it visual weight):
- "Selection-bias gate: PASSED" header
- A 2-column grid of the four gates with the value + threshold + pass indicator:
  - DSR (Deflated Sharpe Ratio): p = 0.995 · threshold > 0.95 · ✓
  - PBO (Probability of Backtest Overfitting): 0.39 · threshold < 0.5 · ✓
  - Walk-forward OOS Sharpe ratio: 1.26× in-sample · threshold ≥ 0.5× · ✓
  - Look-ahead audit: passed · ✓
- Below the grid: "What this means" plain-English explainer (~2 sentences) —
  "DSR says the observed Sharpe is statistically distinguishable from zero after
  correcting for non-normal returns and the number of strategies considered. PBO
  says this strategy is not expected to underperform the median strategy out of
  sample."

Backtest section:
- Equity curve chart (large): our backtest vs SPY buy-and-hold benchmark, with
  the paper's claimed equity curve as a third line (dashed) where available
- Below the chart: a strip of the 22-year stats — CAGR · Max DD · Calmar · Win
  Rate · Volatility · Correlation to SPY

Provenance section (the on-chain layer):
- "Reasoning trace anchored on Arc"
- Trace hash (monospace, truncated, with copy-icon): 0x4f7a…2c91
- Block: 12,847,331 · Timestamp: 2026-05-20T14:23Z
- "View on Arc Explorer" link (opens https://explorer.testnet.arc.network/tx/...)
- "Verify trace" button — when clicked, animates a recomputation of the hash from
  the published off-chain trace and shows a green-checkmark "Verified" badge
- Execution surface: "Deploys into a non-custodial multi-asset NAV vault on Arc
  (Vault.sol prices each holding via the oracle in `totalAssets()` — share price
  reflects the live mark of every asset, not just a synthetic stand-in)."

Source-papers section:
- 3-4 small cards, each with: arXiv ID (monospace), title (truncated), authors,
  primary category, and a "Why fusion chose this" one-line annotation
- Click → opens the paper detail (from Corpus Explorer)

Visual style: dark mode primary, brand accent for the Tier-1 badge + the
verification button, serif for big headlines, monospace for numerics + hashes +
arXiv IDs. This screen should feel like a research report, not a marketing page —
that's the "we're serious about academic rigor" cue.
```

---

## Prompt 5 — Reasoning trace viewer modal (Day-9 refresh)

**Setup notes:** Modal / overlay component opened from the "Agent activity feed"
on the My Portfolio screen (Prompt 3 Screen 3) or from any strategy passport
(Prompt 4). Use **Prototype / High fidelity**.

```
Component: the Reasoning Trace Viewer modal.

Context: the user clicked "View reasoning trace" on a specific agent decision.
The modal opens showing the full trace — every step the agent considered, every
tool it called, what it decided, what it did on-chain, and how to verify.

Layout (header sticky, body scrolls):

Header (sticky):
- Decision type + trigger + UTC timestamp
  (e.g. "Autonomous Rebalance — Vol-targeting threshold crossed — 2026-05-23 14:32 UTC")
- Status pill: "Executed" or "Considered, not executed"
- Close button (X) in the top-right

Market context section:
- A compact grid showing the metrics the agent saw at decision time —
  VIX · S&P 50/200 crossover · credit spreads · BTC dominance · cross-asset
  correlation · USYC yield · realized vs implied vol delta
- Each value monospace, with a small "↑ / ↓ / ↔" indicator vs the previous decision

Source signals section:
- Which corpus papers the fusion engine referenced when this decision was
  considered. 2-3 small cards with arXiv ID + title + the specific claim invoked
  ("paper claims vol-targeting reduces tail risk in this regime")

Reasoning section:
- The LLM-generated explanation as plain prose. Should read like a thoughtful
  analyst wrote it, not a bullet-pointed checklist. 2-4 paragraphs typically.
- Inline definitions for any acronym on first use (same dotted-underline pattern
  as Prompts 3 and 4)

Action taken section:
- "Before vs After" portfolio weights comparison (two stacked horizontal bars)
- A short list of trades executed, each with: from-asset → to-asset, USDC
  amount, slippage, on-chain tx hash (monospace, truncated, copyable, link to
  Arc Explorer)

Tool calls section (collapsible, collapsed by default):
- Header: "9 tool calls" with expand chevron
- When expanded: a table of (tool name, input hash, output hash, latency)
- Useful for technical judges; doesn't clutter the default view

Verification footer (always visible):
- Trace content hash (monospace) — the hash that was anchored on Arc
- Storage pointer (where the full off-chain trace lives — could be S3 URL or
  IPFS CID, monospace)
- "Verify trace" button — when clicked, animates a recomputation of the hash
  from the published off-chain JSON and shows pass/fail with a green-checkmark
  or red-X animation
- "View on Arc Explorer" link to the on-chain anchor tx

Visual style: dense but organized. Header sticky. Hash strings always monospace
with copy-to-clipboard buttons. Generous whitespace between sections so the modal
doesn't feel like a wall of text.
```

---

## Sources for visual reference (if Claude Design supports URL fetch)

If Claude Design lets you reference live sites, these are reasonable inspirations to
cite:

- [Arc Network](https://www.arc.network/) — clean dark-mode crypto-finance aesthetic.
- [Stripe](https://stripe.com/) — gold standard for professional financial product UI.
- [Linear](https://linear.app/) — exemplary dark-mode SaaS visual language.
- [Cleantype hero pages](https://wealthfront.com/) — for the consumer-facing
  trustworthy-finance feel.
- [Etherscan](https://etherscan.io/) — for how on-chain data should be presented
  (compact, monospace, copy-friendly).
- [DefiLlama](https://defillama.com/) — for portfolio dashboards in the DeFi context.

You can paste those URLs into the design conversation and ask Claude Design to draw
inspiration from them, particularly for color palette / typography / information
density.

---

## Explainer prompts (Day-9 addition, 2026-05-20)

These prompts produce **explanatory visualizations** rather than UI screens or branding.
The intent: make the corpus-→-fusion-→-strategy chain visually graspable for the team,
for judges, for non-finance audiences. Suitable for embedding in the deck, the README,
the live app's "How it works" section, or as standalone images to share in Discord /
on social during the launch.

All of these reference the design system above. If you've already done the design-system
setup in your Claude Design workspace, the prompts will inherit it; otherwise paste the
palette + typography block above into the conversation first.

### Prompt 6 — Corpus substrate diagram (3 layers)

**Mode:** From template / Other → "Diagram" (or "Slide" if you want it deck-ready)

```
Create a 3-layer architecture diagram showing how the Archimedes q-fin paper corpus is
built and consumed. Vertical orientation, top-to-bottom data flow with arrows.

LAYER 1 (top) — "Seed (committed, deterministic)"
- A file labeled "data/corpus/manifest.jsonl"
- Subtitle: "10,000 curated arXiv papers, 14 MB, ships with the repo"
- Small icon for "no network required"

LAYER 2 (middle) — "Truth (Postgres, mutable)"
- A database cylinder labeled "papers + corpus_meta tables"
- Subtitle: "Persisted in pgdata volume, survives redeploys"
- Two arrows entering from the top:
  - Left arrow from Layer 1: "Idempotent seed at every startup"
  - Right arrow from a small arXiv logo (outside Layer 1): "Live intake (incremental)"

LAYER 3 (bottom) — "Heavy artifact (persistent volume, lazy)"
- A volume icon labeled "archimedes-corpus-artifact"
- Subtitle: "Embeddings, clusters, knowledge graph, summaries"
- Small badge: "Built out-of-band, never on the deploy path"
- Note in italics: "Empty for now — boots fine if missing (loud degradation)"

Below all three layers, a single arrow pointing down to a box labeled:
"Consumed by: strategy_fusion.load_corpus() + /api/papers + Corpus Explorer UI"

Visual style: clean technical diagram, dark background per the design system, single
brand accent for arrows, monospace for table/file names, serif for layer titles. No
clip art. Should read like an architecture diagram an engineer would draw on a
whiteboard, not a marketing illustration.

Use case: Slide in the pitch deck explaining "the substrate"; also embedded in
docs/architectural-principles.md as the canonical reference.
```

### Prompt 7 — 3-input fusion explainer (the wedge in one image)

**Mode:** From template / Other → "Diagram"

```
Create a fusion diagram showing how Archimedes generates a research-grounded strategy
from three inputs. This is the single most important "what makes us different" image
in the deck — it must read clearly in 5 seconds.

CENTER: A large hexagon or rounded square labeled "FUSION" with subtitle
"strategy_fusion.py + LLM (GLM)".

THREE INPUTS feeding in from the left, top, and right (or all from the left, stacked
— pick whichever reads cleaner):

INPUT 1 — "User brief"
- Icon: person silhouette or speech bubble
- Caption: "What you want — asset class, risk tolerance, time horizon"
- Example bubble: "Steady income, low drawdown, 5-year horizon"

INPUT 2 — "Live market regime"
- Icon: candlestick chart or volatility curve
- Caption: "Current conditions — volatility, asset correlations, regime indicators"
- Example bubble: "VIX 14, rates flat, equity-bond corr +0.3"

INPUT 3 — "Research corpus"
- Icon: stack of papers or a small database cylinder
- Caption: "10,000 q-fin papers — methods, evidence, paper-claimed Sharpe"
- Example bubble: "Moreira-Muir 2017, Faber 2007, George-Hwang 2004…"

OUTPUT (arrow leaving the right or bottom of the fusion box):
- A strategy spec card showing:
  - Strategy name
  - Entry/exit rules (1-2 lines)
  - Citations: 3-4 arXiv IDs in monospace
  - A small badge: "Reasoning trace → on-chain"

Beneath the output, a thin downward arrow labeled "Rigor gate (next prompt)" so this
diagram is composable with Prompt 8.

Visual style: dark background, brand-accent arrows, the three inputs sized equally so
no one input dominates (that's the point — none of them alone gives you the answer).
Monospace for the arxiv IDs and rule snippets. Serif for the section headings.

Use case: Deck slide titled "How fusion works"; also the hero image for the
"research-grounded" section of the live app's landing page.
```

### Prompt 8 — Rigor gate flowchart

**Mode:** From template / Other → "Diagram" (flowchart)

```
Create a flowchart showing the rigor gate that every Archimedes-generated strategy
passes through before being admitted to the library. Vertical orientation, top-down.

START at the top with a card labeled "Candidate strategy"
- Subtitle: "From fusion (Prompt 7)"

Then a series of decision diamonds, each in sequence, each with a green check (pass)
arrow continuing down and a red X (fail) arrow exiting to the right into a single
"Reject + preserve trace" terminal box on the right side:

GATE 1: "DSR (Deflated Sharpe Ratio)"
- Subtitle: "Bailey & López de Prado 2014"
- Pass criterion: "p-value > 0.95 — Sharpe credibly > 0 after multiple-testing correction"

GATE 2: "PBO (Probability of Backtest Overfitting)"
- Subtitle: "CSCV framework"
- Pass criterion: "< 0.5 — not expected to underperform median out-of-sample"

GATE 3: "Walk-forward OOS"
- Subtitle: "70/30 train/test split"
- Pass criterion: "OOS Sharpe within 50% of in-sample — no cliff"

GATE 4: "Look-ahead audit"
- Subtitle: "Static analysis of strategy code"
- Pass criterion: "No future-data leakage detected"

GATE 5: "Trade count + paper-claim delta"
- Subtitle: "Sanity checks"
- Pass criterion: "Trades ≥ 10 (or always-on) AND backtest Sharpe ≥ 0.5 × paper claim"

If all five pass → green terminal box at the bottom: "ADMITTED — Tier-1 (Archimedes
Verified 🏆)"

To the right of the entire pipeline, a small panel listing "What's surfaced in the
strategy passport regardless of pass/fail": DSR value, PBO score, OOS/IS Sharpe ratio,
paper-claim delta, look-ahead audit result, the reasoning trace.

Visual style: clean flowchart, dark background, green = pass, red = fail, brand
accent for the gates themselves. Serif for gate names, sans for criteria, monospace
for thresholds (>0.95, <0.5, etc.).

Use case: Deck slide titled "How we gate strategies"; also the canonical reference
diagram in docs/specs/selection-bias-corrections-spec.md and on the strategy passport
page in the live app.
```

### Prompt 9 — End-to-end user journey

**Mode:** Slide deck (single slide, horizontal)

```
Create a 5-step horizontal user-journey diagram for Archimedes. This is the spine of
the product — every other prompt feeds into one of these steps. Should read like a
subway map: clean, sequenced, each stop has a name and a one-line outcome.

LEFT TO RIGHT, FIVE STOPS connected by a single horizontal track:

STOP 1: "Describe"
- Icon: chat bubble
- Caption: "Tell Archimedes what you want — asset class, risk, horizon"

STOP 2: "Generate"
- Icon: small spark / lightbulb on top of the fusion hexagon
- Caption: "3-input fusion produces a research-grounded strategy"
- Tiny note below: "See Prompt 7"

STOP 3: "Rigor-gate"
- Icon: shield with a checkmark
- Caption: "DSR + PBO + OOS + audit. Pass → admit. Fail → preserve trace."
- Tiny note below: "See Prompt 8"

STOP 4: "Execute"
- Icon: vault / safe (the non-custodial ERC-4626 vault)
- Caption: "Deploy into your non-custodial vault on Arc. USDC settlement."

STOP 5: "Monitor + Explore"
- Icon: dashboard / line chart
- Caption: "Watch performance, inspect reasoning traces, explore the library that grew with you"

BELOW THE TRACK, a horizontal banner spanning all 5 stops:
"Every step's reasoning is hashed and anchored on Arc via ReasoningTraceRegistry —
anyone can verify."

Visual style: brand accent for the track, dark background, each stop styled like a
subway-station node (filled circle + label above + caption below). Stops 1 and 5 are
labeled "User" (the person), stops 2/3/4 are labeled "System" (the agent) — make this
distinction visible (small subtitle under each station).

Use case: The single most important slide in the deck — appears right after the
problem framing, before any architecture detail. Also the hero image on the live app's
landing page and the README. If this image works on its own, the rest of the deck is
support.
```

### Prompt 10 — On-chain reasoning trace anchoring

**Mode:** From template / Other → "Diagram"

```
Create a diagram showing how Archimedes anchors a reasoning trace on Arc — the
provenance primitive that makes our claims externally verifiable.

LEFT SIDE — "Off-chain (full trace)"
- A document/scroll icon labeled "Reasoning trace"
- Bullets inside:
  - "Fusion brief (your intent + market + papers)"
  - "Selected source papers (arXiv IDs)"
  - "LLM reasoning steps"
  - "Backtest results (DSR, PBO, OOS Sharpe)"
  - "Rigor-gate verdict"
- Subtitle: "Stored in Postgres + Redis; full text recoverable"

MIDDLE — a hash function symbol with an arrow leading into it from the left and out
of it on the right
- Caption: "SHA-256"

RIGHT SIDE — "On-chain (anchor)"
- A small smart-contract icon labeled "ReasoningTraceRegistry"
- Subtitle: "Deployed on Arc testnet"
- Inside the contract box:
  - "traceHash: 0x4f7a…2c91"  (monospace, truncated)
  - "blockNumber: 12,847,331"
  - "blockTime: 2026-05-20T14:23Z"
- Bottom annotation: "Atomic. Sub-second finality. USDC for gas."

BELOW THE WHOLE THING, a small verification panel:
"Anyone can: recompute the hash of the published off-chain trace, compare against the
on-chain anchor, and prove the trace existed at the recorded block time."

Optional callout (top right corner): "v1.5 upgrade — commit-reveal — proves the trace
existed BEFORE the trade (causal ordering, not just temporal coexistence)."

Visual style: dark background, monospace for hashes/block numbers, the SHA-256 step is
the visual centerpiece (slightly larger, brand accent), arrows are subtle.

Use case: Deck slide titled "Verifiable provenance"; also the canonical reference
in docs/specs/commit-reveal-trace-spec.md.
```

### Prompt 11 — One-page explainer (launch-ready)

**Mode:** Slide deck (single landscape slide) OR Prototype (single 1200×1600 page)

```
Create a single-page explainer for Archimedes — the artifact we share in Discord, on
social, and as a thumbnail for the deck. One page. Should be self-contained: a person
who knows nothing about us should grasp the product in 30 seconds.

LAYOUT (top to bottom):

HEADER STRIP
- Logo (left) + "Archimedes" (serif headline) + tagline (right): "Research-grounded
  strategies, generated on demand, gated by selection-bias rigor, executed on Arc."

PROBLEM (one paragraph, centered)
- "AI portfolio tools today are either black-box robo-advisors, opaque copy-trade
  influencer plays, or LLM 'just trust me' strategy generators. None of them surface
  the research their claims rest on, and none of them gate against the most common
  failure mode: in-sample overfitting that doesn't survive contact with reality."

WHAT WE DO (the 5-step subway-map from Prompt 9, simplified and compressed)
- Just the 5 stops with their captions, horizontal strip

THE WEDGE (3 columns, equal weight)
- Column 1: "Research-grounded"
  - "Every strategy cites the q-fin papers that informed it. 10,000-paper corpus.
    Generated, not retrieved."
- Column 2: "Rigor-gated"
  - "Every Tier-1 strategy passes DSR + PBO + walk-forward OOS + look-ahead audit
    before admission. No black box."
- Column 3: "Provenance-anchored"
  - "Every reasoning trace is hashed and anchored on Arc. Verifiable history, not
    marketing copy."

CURRENT STATUS (small footer band, italic)
- "Built for the Agora Agents Hackathon — Canteen × Circle × Arc, May 11–25, 2026.
  Currently on Arc testnet (no mainnet — that's the correct posture). Open-source under
  the Unlicense."
- Small QR or short URL pointing to the live app and the GitHub repo.

Visual style: dark background, generous white-space, brand accent used sparingly to
draw the eye to the 3-column wedge section. Serif headlines, sans body. No emojis in
the artifact itself.

Use case: Discord pin, social-share image, README hero. Generate once, use everywhere.
```

### Prompt 12 — Page map: current vs proposed UI

**Mode:** From template / Other → "Diagram" (two-column comparison)

```
Create a two-column visual page-map comparison for the Archimedes web app — current
state on the left, proposed simplification on the right.

LEFT COLUMN — "Current (as shipped)"
Show the existing top-level navigation as a tree, listing every primary page:
- Landing
- Marketplace (Explore)
  - Synthetic Assets
  - Vault Leaderboard
  - Trending This Week
  - All Strategies
  - Agent Activity
- Strategies
- Trade
- Vaults
- Intelligence
  - Corpus Explorer
  - Risk Analysis
- (any other pages — check ui/src/components/Layout.jsx)

RIGHT COLUMN — "Proposed (simplified)"
Show a tighter tree with 4-5 top-level sections:
- Landing
- Generate (the strategy generator, prominently surfaced)
- My Portfolio (consolidates Trade + Vaults + Risk Analysis personalized view)
- Library (consolidates Marketplace + Strategies + Corpus Explorer)
- Learnings (NEW — what worked, what didn't, why; reasoning traces fully accessible)

BETWEEN THE COLUMNS, a few annotation arrows showing where current pages map into
proposed sections (e.g., "Trade + Vaults → My Portfolio").

BELOW THE COMPARISON, a 2-sentence rationale:
"Current navigation reflects what was built; proposed reflects what users do. Generate
moves from buried to top-level. Performance, allocation, and risk for YOUR positions
consolidate into one place. Public corpus + strategies become a single 'Library' surface.
A new 'Learnings' page makes losing strategies first-class learning material, not
hidden failures."

Visual style: tree diagram, dark background, current side in muted secondary text
color, proposed side in primary text + brand accent for the "Generate" and "Learnings"
nodes (the structural moves). Sans throughout.

Use case: PR description for the UI simplification proposal; also a slide in the deck
showing "we know our own UX, here's how we'd polish it post-hackathon."
```

---

## Workflow recap

1. **Set up a design system** in Claude Design with the palette/typography above.
2. **Run Prompt 1** (logo) first — pick a logo, lock it before doing slides/UI.
   Smallest-scope test of whether the design system is steering correctly before
   you commit deck/UI iteration to it.
3. **Run Prompt 2** (slide deck) — generate, iterate, export. The Day-10 SLIDE 4
   inventory (agentic advisor + stress engine + multi-asset NAV vault alongside
   fusion + rigor + 10 contracts) is the substantive shift from the older Day-9
   framing; verify every bullet matches what
   [`docs/chuan-architecture-survey.md`](chuan-architecture-survey.md) describes
   as shipped before exporting.
4. **Run Prompts 3, 4, 5** (UI screens) — give Claude Design the live URL
   (`https://archimedes-arc.app/`) as starting reference. These prompts are
   **refinement** prompts against the shipped React UI, not greenfield. Screen 3
   (My Portfolio) now includes a stress-scenario strip and agentic-advisor signals
   — both Day-10 additions where the backend exists but the UI surface is still
   open.
5. **Run Prompts 6–12** (explainer set) when you want explanatory visualizations for
   the deck, README, Discord pin, or social-share assets. They're independent of each
   other — pick the one(s) the moment calls for.
6. **Iterate** — Claude Design is conversational; refine outputs based on your eye.

If you take screenshots of references along the way, drop them in the conversation —
both Claude Design and this Linus session can ingest them. URLs work too. Either is
fine; mix freely.
