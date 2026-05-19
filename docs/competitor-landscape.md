# Competitor Landscape — Portfolio Management Agents

> **Date:** 2026-05-12 (Day 2); **overhauled 2026-05-19** with the on-chain
> curation-infrastructure tier (Gauntlet/Morpho/Upshift/Accountable), the Arc
> hackathon peer set, and the testnet-only reality.
> **Audience:** Archimedes team — pitch + strategy.
> **Sourcing:** figures below are verified in `/tmp/research/*.md` briefs (inline
> links). Crypto moves in weeks — re-validate before pitch day. Flag unverified
> numbers as unverified; we did.

## TL;DR — the one thesis

**Curation without proof is the industry's open wound, and it is exactly our
product.** The on-chain asset-management stack now has billion-dollar rails
(Morpho) and billion-dollar curators (Gauntlet) — and the **November 2025 curator
crisis proved the rails held while the curation layer above them broke precisely
on rigor.** Every funded incumbent runs **trust-based** curation. Archimedes is
the **proof-based** answer: strategies grounded in peer-reviewed research and
admitted only through a selection-bias rigor gate (DSR / PBO / walk-forward /
look-ahead), with the reasoning trace anchored on-chain.

Three competitive tiers, and we must be honest about which we're in:

| Tier | Who | Our relationship |
| --- | --- | --- |
| **0 — On-chain curation infra** (live mainnet, $B) | Morpho, Gauntlet, Upshift, Accountable | **Vision / TAM**, not today's competitor — they prove the market *and* its failure mode |
| **1 — Our real peer set** (Arc, pre-product) | Pantheon-Trades, ReasoningReceipt, CronusCapital + the hackathon field | **Actual competition right now** |
| **2 — Broader landscape** (context) | Robo-advisors, crypto-AI agents, quant platforms | Adjacent; the Day-2 framing, compressed below |

> **Testnet reality (load-bearing for honesty):** **Arc has no mainnet** — Circle's
> docs list mainnet as "upcoming"; the public testnet "mirrors mainnet behavior,
> no real assets." Every Arc project, ours included, is **pre-product**. Tier-0
> incumbents are *live on mainnet with real AUM* — a different league. Our honest
> current peer set is Tier 1. Stating this is a strength: it is the correct
> posture, and it defuses the regulatory/custody concerns (no real funds ⇒ no RIA
> trigger yet — those are mainnet/business-plan roadmap, see § Regulatory).

---

## Tier 0 — On-chain curation infrastructure (the vision, and the wound)

These are live, funded, mainnet incumbents. We do **not** compete with them in a
hackathon. They matter because they (a) prove the TAM is real and institutional,
and (b) every one of them exposes the trust-vs-proof gap Archimedes closes.

### Morpho — the rails

[morpho.org](https://morpho.org). Modular lending substrate: immutable Morpho Blue
primitive + curator-run MetaMorpho/V2 vaults. **~$7.5B TVL, 30+ chains**, powering
Coinbase / Gemini / Société Générale credit rails. Funding **~$73.6M**: $18M (2022,
a16z + Variant) + **$50M (2024, Ribbit-led**; a16z, Coinbase, Pantera, Brevan
Howard). *This $50M round is the raise rumored in the team chat — it's Morpho's,
not Gauntlet's.*

**Nov-2025 crisis (the evidence slide):** Stream Finance / xUSD (~$93M) cascaded
ecosystem-wide — ~$160M frozen, Euler ~$137M bad debt. Morpho's isolation
architecture **contained it to ~$700K across ~1 of ~320 vaults**. The rails held;
**curator judgment failed.** Response: Vaults V2 hardened curator controls
(ID caps, role separation, Sentinels).

**Read:** Morpho is the infra layer *below* curation — potential rail and the
clearest proof that the gap above it (rigorous curation) is unsolved. *That gap is
Archimedes' product, not its competitor.*

### Gauntlet — the curator, and the failure mode

[gauntlet.xyz](https://www.gauntlet.xyz/) (founded 2018, Tarun Chitra). Pivoted
from risk-simulation consulting to **vault curation** — now the largest curator by
TVL (**~$1.5–1.9B**), deeply tied to Morpho. Funding **~$44.7M total**; last
confirmed is **$23.8M Series B (2022, ~$1B valuation, Ribbit-led)**. **The ~$50M
Gauntlet raise is unverified** — no public Series C in any database (that figure is
Morpho's).

**The "big mistake":** the confirmed, acrimonious **Feb 2024 Aave divorce** —
quit a ~$1.6M/yr Aave risk-steward role and moved to rival Morpho days later
(conflict-of-interest optics). Survived the Nov-2025 curator crisis (claims zero
bad debt) but the episode tarred the **whole fee-incentivized curator model** as
"cosplaying risk."

**Read:** Gauntlet *is* the trust-based curation failure mode, embodied. Our
one-liner: *the auditable, research-grounded, rigor-gated alternative to
"trust-me" black-box curation.*

### Upshift — curated yield, still trust-based

[upshift.finance](https://upshift.finance/) — retail-facing spinout of prime
broker **August Digital** (ex-FalconX founders). A vault-infra layer where
institutional curators (Sentora, UltraYield, M1, MEV Capital) build ERC-4626
strategies on non-custodial prime-broker rails. **No separate "$50M Upshift
raise"** — only August's **~$10M Series A (Dragonfly, Mar 2025)** + ~$6M seed.
TVL ≈ **$287M / 12 chains**; revenue down ~6× from a Q4'25 peak.

**Read:** proves institutions pay for curated on-chain yield rails — but **trusts
curators instead of proving them.** Don't rebuild Upshift's plumbing; out-rigor it.

### Accountable — partner-shaped, not rival

[accountable.capital](https://www.accountable.capital/) (CEO Wojtek Pawlowski). A
ZK/FHE/zkTLS real-time financial-verification network proving solvency, reserves,
and yield **without exposing strategy**. Funding: $2.3M seed (MitonC / Zee Prime,
2024) + **$7.5M led by Pantera (Oct 2025)**; >$1B verified for Galaxy, Amber, K3;
OKX on the cap table.

**Read:** Accountable verifies *capital is real*; Archimedes verifies *the method
is real*. Same verifiable-finance thesis, different proof object — **adjacent rails
of the same stack, partner-shaped not rival-shaped**, and live proof the
"auditable trust" TAM is funded and institutional.

> **Strategic implication.** Max's read is right: serious money, likely-acquisition
> targets, a race to ship the trustworthy product first. Our wedge isn't building
> rails (Morpho) or plumbing (Upshift) — it's being the **proof layer for
> curation** the Nov-2025 crisis showed is missing. Vision narrative for the
> README/deck; not a claim that we compete with $7.5B TVL today.

---

## Tier 1 — Our actual peer set: the Arc hackathon field

Given testnet-only reality, *this* is who we're really up against now. Full sweep
in `/tmp/research/rivals.md`. Threat-ranked:

| Rival | What it is | Threat | Why |
| --- | --- | --- | --- |
| **Pantheon-Trades** | Council-of-agents trade gating with on-chain Proof-of-Restraint (live, Arc block 42,337,549) | **High** | Competes on agentic sophistication + rigor-proof + Arc settlement; shipped & working |
| **ReasoningReceipt** | Reasoning-trace-as-product: 3000+ live receipts, Merkle proofs, x402 paywall on Arc | **High** | Hits our auditable-trace angle with simplicity + existing traction |
| **CronusCapital** | 3-agent prediction-market trader on Arc, decision logging | **Med** | Less sophisticated but fast-shipped; judges reward velocity |
| Field (regimeshift-fx, trading-r1, rosetta-alpha, vrp-agent, signal-to-settlement, ArcLayer, storescope, arc-agent-pay) | Various Arc trading/agent angles | Low–Med | None claim research-grounded rigor |

**The uncontested wedge:** Pantheon *deliberates*, ReasoningReceipt *attests* —
**none anchor to peer-reviewed methodology with a DSR/PBO selection-bias gate.**
If we visibly ship live peer-reviewed signals + on-chain rigor proof + verifiable
traces, we own "AI agents with research credibility" outright. Defend the trace
narrative against ReasoningReceipt by leading with *rigor*, not just *receipts*.

---

## Where Archimedes wedges (the honest claims)

1. **Research-grounded + rigor-gated provenance.** No Tier-0 or Tier-1 player
   sources strategies from peer-reviewed research and gates them through
   DSR/PBO/walk-forward/look-ahead. This is the answer to the Nov-2025 curation
   failure and the single cleanest differentiator.
2. **Verifiable per-decision reasoning trace anchored on Arc.** ReasoningReceipt
   is closest; we differentiate by *rigor first, receipt second*.
3. **Pure-USDC settlement on Arc, no native token.** Structurally different from
   token-mediated products; aligned with Circle's agentic-economy framing.
4. **Consumer UX (with humility).** If the UI reads like a regulated TradFi
   product crossed with an on-chain explorer, that's the right aesthetic.

## Regulatory / risk — mainnet roadmap, not hackathon scope

Per Max, parked here deliberately (testnet ⇒ no real funds ⇒ not triggered yet):

- **Off-chain redemptions** to reduce risk — note for the business-plan/mainnet
  architecture.
- **Preset strategies** keep us out of RIA territory; *or* the company explicitly
  plans to **register as an RIA**. State the chosen posture in the business plan.
- **Hypernative** (or equivalent) for exploit/hack alerting on approved
  strategies — mainnet operational requirement.

These strengthen the pitch as a *designed-for-mainnet* risk architecture; they are
not v1 demo work.

---

## Tier 2 — Broader landscape (Day-2 context, compressed)

Still valid as "what a non-crypto user compares us to." Detail retained from the
Day-2 brief; not the competitive front line.

- **TradFi robo-advisors** — [Wealthfront](https://www.wealthfront.com/) (~$50B+
  AUM, 0.25%, rule-based), [Betterment](https://www.betterment.com/), Schwab/
  Vanguard/Fidelity. No on-chain, no provenance. The competitive *foil* ("not a
  robo-advisor"), not a comp.
- **Crypto-native AI portfolio** — Yield Seeker (USDC yield on Base, no academic
  provenance — closest legacy comp on "USDC + autonomous rebalancing"),
  SingularityDAO DynaSets (shared vaults, native token),
  [Theoriq](https://www.theoriq.ai/) (DeFi attestations, THQ token — closest on
  the verifiable axis; [OpenLedger partnership Jan 2026](https://www.prnewswire.com/news-releases/openledger-partners-with-theoriq-to-bring-verifiable-ai-agents-into-live-defi-markets-302664498.html)),
  Olas/Pearl (staking-based), Virtuals (own-the-token, not hire).
- **Quant-research platforms** — [QuantConnect](https://www.quantconnect.com/)
  (developer-facing; potential v2 ecosystem partner), [Numerai](https://numer.ai/)
  (crowdsourced ML hedge fund), Quantopian (defunct 2020 — strategy-*curation*
  beats strategy-*volume*; the cautionary tale that validates our rigor gate).

Cross-cutting matrix and per-player detail preserved in git history (pre-2026-05-19
revision) if needed for a deep pitch Q&A.

---

## Recommended pitch-deck slide

```
THE CURATION LAYER IS BROKEN                  ARCHIMEDES CLOSES IT
──────────────────────────────                ──────────────────────
Morpho — $7.5B rails held; curation broke     Strategies from peer-reviewed research
Gauntlet — largest curator, "cosplaying risk" Admitted only via DSR/PBO rigor gate
Upshift — trusts curators, doesn't prove them  Every decision hashed + on-chain
Accountable — proves capital is real          We prove the *method* is real
(Nov-2025 crisis: rails OK, rigor failed)     Verifiable history, not promised alpha

→ Trust-based curation, billion-dollar wound   → Proof-based curation on Arc (USDC)
```

## Open data points to confirm before pitch day

- Gauntlet Series C / valuation refresh (the ~$50M is unconfirmed).
- Live AUM/TVL deltas for Morpho/Gauntlet/Upshift (move weekly).
- Which Arc hackathon rivals ship a portfolio surface before submission.
- Circle Agent Stack roadmap — first-party portfolio primitives?

---

_Live as of 2026-05-19. The on-chain curation landscape moves in weeks —
re-validate Tier 0/1 figures before submission._
