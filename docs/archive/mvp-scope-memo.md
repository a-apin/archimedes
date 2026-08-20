# MVP Scope Memo

> **Date:** 2026-05-12 (Day 2). **Last revised 2026-05-13 (Day 3)** — see § "Day 3
> updates" below for the marketplace expansion and the rigor-as-wedge decision.
> **Audience:** Archimedes hackathon team
> **Purpose:** Formalize the locked scope decisions that came out of the Day-2 standup
> and the Day-3 design pivot. This is the document the team commits to — when scope creep
> starts, this is the back-pressure reference.

## TL;DR

1. **Primary RFB: 04 — Adaptive Portfolio Manager.** Adjacent: RFB 02 (Kelly/+EV math
   primitive); RFB 06 (strategy leaderboard).
2. **Both on-chain stories are in scope:** vault contracts for portfolio allocation
   **and** ReasoningTraceRegistry for verifiable provenance. Shoot for the moon.
3. **Strategy library v1 = curated, ~5–10 hand-built strategies.** Arxiv ingest pipeline
   runs as a demo feature on 2–3 papers — shown in the pitch, not relied on for the wow
   moment.
4. **(Day 3 addition) Rigor is the wedge.** Selection-bias-corrected backtests
   (DSR + PBO + walk-forward OOS + look-ahead audit) gate every Tier-1 strategy. The
   passport surfaces paper-claim deltas honestly. This is what distinguishes Archimedes
   from the 96 other "AI portfolio agent" submissions at the last Arc HackMoney.
5. **(Day 3 addition) Two-tier marketplace.** Tier 1 = Archimedes-Verified, paper-grounded,
   full agent autonomy, selection-bias-corrected. Tier 2 = community, permissionless,
   opt-in agent features. The single-vault original scope is replaced by VaultFactory
   per [`specs/ecosystem-design-spec.md`](../specs/ecosystem-design-spec.md).

These five decisions cascade through every other doc in `docs/`. Treat them as locked
unless the team explicitly agrees to change one (and updates this memo).

## Decision 1 — RFB lock-in

### Choice

- **Primary RFB:** 04 — Adaptive Portfolio Manager
- **Math primitive cited:** RFB 02 — Prediction Market Trader Intelligence (Kelly Criterion
  / +EV / position sizing). Önder's work on the portfolio-math module is structurally the
  RFB 02 mechanic applied inside RFB 04's portfolio construction.
- **Adjacent showcase:** RFB 06 — Social Trading Intelligence. The strategy-performance
  leaderboard in the frontend (per Chuan's [`design.md` § 4.2](agora-2026-05/design.md)) is RFB 06
  flavored — users follow top-performing strategies via paper-grounded reasoning traces.

### Why not the others

- **RFB 01 — Perp futures trading.** High regulatory risk; demo-fragile (liquidation
  events look catastrophic on stage); requires real-time risk infrastructure we can't
  ship cleanly in 12 days. Chuan's RWA/spot framing avoids the leverage failure modes
  entirely.
- **RFB 03 — Prediction market verticals.** Different product shape (creating new markets,
  not managing portfolios). Adjacent to where we'd go in v2, not where v1 lives.
- **RFB 05 — Cross-platform arbitrage.** Latency-sensitive; demo would be either a
  recorded video of arbitrage or a stale screenshot. Doesn't showcase paper-grounded
  reasoning.

### Detail

See ``rfb-alignment.md`` (routed to the private docs repo, 2026-08-19) for the full per-RFB analysis with judging-
criteria mapping.

## Decision 2 — Both on-chain stories are in scope

### Choice

Build both Arc-native components:

1. **ArchimedesVault contracts** (per Chuan's [`design.md` § 5.2](agora-2026-05/design.md)) — user
   deposits USDC, agent has rebalance authority, RWA tokens flow through CCTP/Gateway
   between Arc and source chains.
2. **ReasoningTraceRegistry contract** — every agent decision is hashed and anchored on
   Arc; the off-chain trace can be verified against the on-chain hash by anyone.

### Why both

The team is now 5 committed people with deep skill coverage:

- **Chuan** has built production crypto-trading infrastructure (CoinShares); the smart
  contracts are within his routine engineering effort, not a stretch.
- **Marten** is paired with Chuan on off-chain ↔ on-chain integration via Arc CLI.
- **Daniel** has full backend chops to handle the off-chain side around the contracts.
- **Önder** can do the math (Kelly sizing, regime-detection thresholds, rebalance triggers)
  that drives what the contracts execute.
- **Dan** is on strategy curation (the input layer) and pitch (the output layer), with the
  capacity to spike into any component.

Two team members run Claude at high tier (Chuan 20×, Dan 5×), which significantly multiplies
implementation throughput. Shooting for both on-chain stories is the call that distinguishes
this team from a single-component hackathon project.

### Why this is achievable

The architecture is clean:

- The two contracts are **independent** — the vault doesn't need to know about the trace
  registry, and vice versa. Either can ship without the other; failure modes are isolated.
- The contracts are **small** — Chuan's [`design.md` § 5.2](agora-2026-05/design.md) sketches both at
  under 10 functions total. Each is a 1–2 day Solidity build with thorough testing.
- The off-chain integration is **scoped per Circle tool** — Wallets and Paymaster are
  routine; CCTP/Gateway are the harder integrations but well-documented.

### What we'd cut if we run out of time

If by end of Week 1 only one of the two contracts is solidly working, **prioritize the
vault** (because that's what enables the live demo's "judges deposit and watch portfolio
construction happen" moment). The trace registry is a v1.5 ship if necessary; the off-
chain trace data is still valuable and the on-chain anchor is an additive verifiability
property.

But the plan is: ship both.

## Decision 3 — Curated v1 strategy library, arxiv pipeline as demo

### Choice

- **Library:** 5–10 strategies, hand-curated by Dan, each tied to a real published quant
  paper with arxiv ID, backtest validated against historical data.
- **Arxiv pipeline:** Built and demonstrated end-to-end on 2–3 papers during the pitch,
  showing the path from paper to strategy. **Not relied on** for the live demo's portfolio
  construction.

### Why curated v1

- **Trust at demo time matters more than scale at demo time.** A judge who sees a strategy
  that quotes "Jegadeesh & Titman 1993, ten paragraphs of methodology summary, 4.2 Sharpe
  in our backtest, paper claimed 4.6 Sharpe" trusts the platform. A judge who sees an
  LLM-extracted strategy from a random arxiv paper that hasn't been vetted is wondering if
  the strategy hallucinated.
- **Curation lets Dan apply scientific judgment.** Dan's background is biochemistry and
  bioinformatics — he is structurally well-equipped to evaluate published research for
  methodology rigor. The seed corpus benefits from his read.
- **Curation closes the "what if the LLM-extracted strategy is wrong" risk** Chuan flagged
  in [`design.md` § 10](agora-2026-05/design.md) — human validation gate.

### Where the arxiv pipeline goes

It still gets built. We demo it. We just don't bet the live portfolio on it.

In the pitch:

- Live demo uses the curated library (10 strategies, all paper-grounded, all backtested).
- A separate "arxiv ingest" segment shows the pipeline running on a fresh paper, extracting
  a strategy, validating it, adding it as a "candidate" to the library awaiting human
  vetting. **The pipeline is a v2 story** — the v1 demo is a portfolio agent grounded in
  curated research.

### Curation criteria

See [`qfin-paper-corpus-seed.md`](qfin-paper-corpus-seed.md) for the categorized seed list
and the per-paper rationale. Headline criteria for a paper to make the v1 library:

1. **Published in a peer-reviewed journal or arxiv with significant citations.**
2. **Strategy is implementable** (well-defined entry/exit signals, available data, no
   proprietary feeds).
3. **Backtest period ≥ 10 years** so we can validate in-sample / out-of-sample on liquid
   markets.
4. **Sharpe ≥ 0.5 in our re-run** at conservative transaction costs (10bps round-trip).
5. **No look-ahead bias** in our re-run (walk-forward only).

## Day 3 updates (2026-05-13)

Two new commitments were locked on Day 3, prompted by Chuan's ecosystem-design pivot
([`specs/ecosystem-design-spec.md`](../specs/ecosystem-design-spec.md)) and Dan's red-team
synthesis (``agora_project_analysis.md`` (routed to the private docs repo, 2026-08-19)). Both are now
treated as locked alongside the original three decisions.

### Decision 4 — Rigor is the wedge

**Choice:** Every Tier-1 strategy passes the four selection-bias controls before
admission to the library: Deflated Sharpe Ratio (Bailey & López de Prado 2014),
Probability of Backtest Overfitting (Bailey/Borwein/López de Prado/Zhu 2014),
walk-forward out-of-sample Sharpe with no in-sample/test cliff, and a look-ahead static
audit. Paper-claim deltas (`sharpe_vs_paper`, `cagr_vs_paper`, McLean-Pontiff
post-publication decay estimate) are surfaced in the passport — never hidden behind an
aggregate score.

**Why:** The strongest red-team critique of any LLM-extracted strategy pipeline is the
multiple-testing inflation problem. McLean & Pontiff (2016) showed published predictors
lose ~58% of in-sample Sharpe post-publication on average; Bailey, Borwein, López de
Prado & Zhu (2014) showed in-sample-optimal strategies frequently underperform the OOS
median under realistic conditions. Either failure mode produces a "validated" library
full of curve-fit artifacts. Applying the textbook corrections — and surfacing the
results — is the single change that turns the analysis-doc critique from a *risk* into
a *differentiator*. See [`specs/selection-bias-corrections-spec.md`](../specs/selection-bias-corrections-spec.md)
for Önder's implementation contract.

### Decision 5 — Two-tier marketplace

**Choice:** Replace the single-vault original scope (ArchimedesVault holding pooled USDC
managed by one agent) with a two-tier vault marketplace per the ecosystem-design-spec:

- **Tier 1 (Archimedes Verified 🏆):** strategies are paper-grounded and selection-bias-
  corrected; the agent has full autonomy (regime detection, autonomous rebalancing,
  strategy rotation); every decision produces an anchored reasoning trace.
- **Tier 2 (Community 👥):** permissionless vault creation by any wallet, any asset
  combination, opt-in agent features (drift alerts, basic regime response). Tier 2
  vaults carry reasoning traces if agent-assisted but are not paper-grounded.

**Why:** A two-tier marketplace deepens the Circle SDK surface (VaultFactory + ERC-4626
+ AMM-traded vault tokens = copy-trade primitive), opens an organic traction channel
(community vault authors), and clarifies the architectural moat (Tier 1 = primitives
1+2+3+4; Tier 2 = primitives 2+3 only — see `architectural-principles.md`). The single
vault wasn't the goal; verifiable, paper-grounded, selection-bias-corrected portfolio
management was. The marketplace just makes the moat legible.

### What gets cut if Day 3 ambition outruns the timeline

If by end of Week 1 the ecosystem layer is at risk, the cut order is (best → worst):

1. **AMM-traded vault tokens** — cut last. Without these, vaults still work; users
   deposit/withdraw at NAV. The copy-trade UX degrades but the demo holds.
2. **Tier 2 community vaults** — cut second. Tier 1 + the agent + the passport are the
   pitch. Tier 2 is the marketplace dressing.
3. **Per-vault chat** — cut third. Reasoning trace viewer is the verifiability primitive;
   chat is the engagement layer on top.
4. **Synthetic protocol** — cut last-resort. If we can't ship synthetics, fall back to
   the original ArchimedesVault holding USYC + a subset of bridged RWAs. This is a
   meaningful retreat — flag immediately if it becomes likely.

The four primitives in `architectural-principles.md` are NOT on this cut list. The whole
pitch dies without them.

## Cross-decision implications

- These three choices imply **a sharper demo wow-moment** than the agent-marketplace v1 we
  had earlier. The user-facing flow is: "connect wallet → pick risk profile → see a
  portfolio of paper-grounded strategies → deposit USDC → watch the agent build the
  portfolio on Arc → audit the reasoning trace." That's a coherent 60-second narrative.
- They also imply **the team owns its own corpus.** The Q-fin paper corpus becomes an
  Archimedes asset that compounds — better curation = better strategy library = better
  portfolios = better moat.
- They imply **anti-features** we should be explicit about — see
  [`anti-features.md`](../anti-features.md). No native token, no perp leverage, no third-party
  strategy onboarding in v1, no fiat on-ramp, no mobile app.

## What changes if the team disagrees

This memo is the negotiation surface. If by Day 4 there's strong evidence one of these
decisions is wrong (e.g., the vault contract is taking longer than expected and we need to
drop trace-registry scope), we update this memo with the new decision and the date. **Don't
let scope silently drift** — make the change visible.

## Open decisions explicitly deferred

These aren't covered by this memo and should be decided by Day 4 or Day 5:

- **Backtesting library:** backtrader vs. vectorbt vs. custom numpy. See
  [`adr/backtrader-backtest-engine.md`](../adr/backtrader-backtest-engine.md).
- **Backend ownership:** vacant since Shimon's departure. Decide by Day 3.
- **EURC inclusion:** USDC-only for v1, or USDC + EURC for international users? Decide
  based on regulatory and integration cost; default to USDC-only.
- **Public beta vs invite-only beta** for the traction push in Week 2.

## What this memo doesn't cover

- Implementation details — see Chuan's [`design.md`](agora-2026-05/design.md) and the specs in
  [`specs/`](../specs/).
- The full pitch deck — see
  ``demo-script-pitch-deck-outline.md`` (routed to the private docs repo, 2026-08-19).
- Day-by-day milestones — see Chuan's [`design.md` § 8](agora-2026-05/design.md).
