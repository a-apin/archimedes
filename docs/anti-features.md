# Anti-Features — What Archimedes Is NOT Building

> **Audience:** Archimedes hackathon team.
> **Purpose:** Make scope discipline explicit. For every plausible-but-distracting feature
> someone proposes mid-week-2, this doc says NO with a reason.
> **Status:** Day-10 update (2026-05-22). The Day-2/Day-3 anti-features list below is
> **mostly still binding** — the strip-to-spine PR (#118) is the operationalization
> of much of it (deleted Marketplace, Trade, CreateVault, RiskAnalysis, FinancialAnalysis,
> and inline DeFi-primitive panels from the live UI). The anti-claim section under
> "Pitch rigor" is also surfaced live in the product per the "Honest framing" rules
> in [`user-stories.md`](user-stories.md). Reconciled with the ecosystem pivot in
> [`specs/ecosystem-design-spec.md`](specs/ecosystem-design-spec.md) and the red-team
> critique in ``archive/agora_project_analysis.md`` (routed to the private docs repo, 2026-08-19).
>
> **CORRECTION (2026-07-27) — the Marketplace anti-feature no longer holds.** The
> marketplace **shipped** and is live behind the `PAYMENTS_DRY_RUN` flag. It lives in
> `backend/archimedes/marketplace/`, `backend/archimedes/api/marketplace_routes.py`, and
> `ui/src/pages/{MarketplacePage,PublishPage,SubscriptionsPage}.jsx`. **Do not delete those
> files** on the authority of this document — the "deleted Marketplace" note below records
> the state of PR #118 in May 2026, not current scope policy.

## Why this doc exists

Hackathon scope creep is the canonical cause of weak demos. Mid-week-2, someone always
proposes "wouldn't it be cool if we also..." Each proposal sounds reasonable in isolation;
each consumes time the team doesn't have; the cumulative effect is a demo that does many
things shallowly instead of one thing well.

This doc lists the specific things Archimedes is **not** building in v1, with the
rationale. When someone proposes one, the conversation is short: "we decided not to build
this; here's why." Disagreement is fine and the list can change, but **change requires
deleting an existing line and replacing it, not silently adding scope.**

## Anti-feature list

### NOT building: a native Archimedes token

**Why not:** Our pitch's defensibility rests on "pure USDC, no token to learn." The day we
launch a token is the day we collapse into one of the competitors we differentiate
against. Take-rate on USDC settlement + USYC yield-share is the revenue model.

### NOT building: perpetual futures or any leveraged trading

**Why not:** Per ``rfb-alignment.md`` (routed to the private docs repo, 2026-08-19), we skip RFB 01 deliberately. The
v1 portfolio is spot + RWA + USYC. Leverage adds liquidation failure modes, regulatory
exposure, and demo fragility we can't manage in 12 days.

### NOT building: third-party strategy onboarding into Tier 1

**Why not:** Tier 1 vaults carry the "Archimedes Verified" badge precisely because every
strategy is curator-validated and paper-grounded. Allowing arbitrary third-party
strategies into Tier 1 dilutes the badge to meaninglessness — moderation, abuse
prevention, and methodology review at scale are real products on their own.

**Tier 2 is the carve-out.** Per [`specs/ecosystem-design-spec.md`](specs/ecosystem-design-spec.md)
§ 5, community-tier vaults are permissionless and explicitly labeled. They carry reasoning
traces and tool-call provenance like Tier 1, but they do NOT carry paper-grounding or
selection-bias-corrected backtests. The two-tier separation is the design — not a
relaxation of the original scope.

**v2 conversation:** opening a curator-application flow that lets community-tier vault
authors petition for Tier 1 review.

### NOT building: a full slashing / dispute resolution mechanic

**Why not:** Slashing requires oracle integration (where does ground truth come from?),
governance for edge cases, and dispute primitives. The strategy passport with verifiable
history + USYC floor + regime-aware deleveraging is sufficient defense for v1. Adding
slashing adds smart-contract surface, attack surface, and pitch complexity without
unblocking the wedge.

### NOT building: tax-loss harvesting

**Why not:** Mentioned in RFB 04 and in Chuan's [`design.md` § 4.3.1](archive/agora-2026-05/design.md), but
classified as v1.5. Tax-loss harvesting requires accounting infrastructure (cost basis
tracking, wash-sale rules, jurisdiction-specific tax codes) that's a real product on its
own.

**v1.5 if Week 2 has capacity; otherwise v2.**

### NOT building: a fiat on-ramp

**Why not:** Users bring USDC. Adding a fiat on-ramp means signing partnerships (Stripe,
MoonPay, etc.), KYC, and operating a regulated flow. None of which is a hackathon project.

**v2 conversation.**

### NOT building: KYC / AML for users

**Why not:** v1 is permissionless. KYC is a regulatory burden we can't satisfy in two
weeks and that conflicts with the "wallet-native" target user. If the product graduates
beyond the hackathon, KYC becomes a conversation about whether we serve regulated
jurisdictions or remain non-custodial.

### NOT building: multi-currency support beyond USDC

**Why not:** USDC only. Even EURC is deferred unless explicit team decision. Adding it
doesn't unlock the wedge and dilutes the pitch.

**Reconsider for v1.5 if a target user explicitly needs it.**

### NOT building: cross-chain bridges to non-Arc destinations

**Why not:** Arc-native settlement. RWA tokens come from source chains via CCTP/Gateway
to Arc; we don't ship a "send your portfolio to Ethereum mainnet" feature. Bridges add
attack surface and narrative confusion.

### NOT building: a custom agent framework or runtime

**Why not:** Off the shelf. Claude API for LLM, fastmcp (or similar) for tool layer if
MCP-style is needed, Python for orchestration. We don't write our own "Archimedes
framework"; we wire together best-of-class components.

### NOT building: a portfolio simulator / paper-trading mode

**Why not:** v1 ships testnet (real on-chain transactions, but no real value at risk). A
separate "paper trading" mode is unnecessary — testnet IS paper trading with the
benefit of demonstrating real on-chain primitives.

### NOT building: mobile-first UX / a mobile app

**Why not:** Web-first. Mobile is a different engineering problem and doesn't unlock
new judging value.

### NOT building: profile pages, DMs, reactions, threads, global feed

**Why not:** [`specs/ecosystem-design-spec.md`](specs/ecosystem-design-spec.md) § 4 puts
per-vault chat in scope (wallet-address identity, message persistence, AI auto-post +
@mention). Everything beyond that — profiles with PnL, direct messages, emoji reactions,
threaded replies, a global marketplace feed — is a different product shape and a v2
conversation.

**Hard line for v1:** chat is a post-investment surface inside a single vault, not a
discovery or onboarding flow.

### NOT building: chat-as-onboarding / conversational portfolio construction

**Why not:** v1 onboarding is form-driven (pick risk profile, deposit USDC). The vault
chat is a *post-investment* engagement surface — users talk to the AI about decisions
that have already been made. Conversational onboarding shifts agent failure modes from
"did it construct the right portfolio?" to "did it understand the user's prompt?" —
a harder evaluation problem we don't take on in v1.

**v2 conversation:** natural-language risk-profile elicitation, "describe your goals and
let the agent recommend a vault" flow.

### NOT building: agent-to-agent (a2a) commerce

**Why not:** Circle's Agent Marketplace targets a2a. We target a user managing their own
portfolio. Trying to do both blurs the pitch. Could be a v2 surface — "Archimedes agents
can be hired by other agents to manage sub-portfolios."

### NOT building: encrypted reasoning traces

**Why not:** v1 traces are public. The hash anchors public content. v2 can add the
encryption pattern (encrypt-trace, share-key-with-buyer) for use cases where strategy
secrecy matters.

### NOT building: model fine-tuning on our own data

**Why not:** Claude API + careful prompting handles strategy extraction and reasoning
trace generation in v1. Fine-tuning is a v2+ conversation.

### NOT building: an Arc smart contract upgrade pattern

**Why not:** Per [`docs/specs/strategy-passport-spec.md`](specs/strategy-passport-spec.md)
and following Chuan's [`design.md` § 5.2](archive/agora-2026-05/design.md), contracts are immutable in v1. If a
bug surfaces, we deploy a new contract and migrate. Upgrade patterns add their own bug
surface (the proxy / delegatecall failure mode is non-trivial); we hold the line on
immutability for v1.

### NOT building: real-time portfolio *coaching* — user-interruptible agent

**Why not:** The vault chat per [`specs/ecosystem-design-spec.md`](specs/ecosystem-design-spec.md)
§ 4 is read-mostly: the AI auto-posts on agent actions and answers questions about
*already-made* decisions. What we are NOT building is a chat where the user can
interrupt, coach, or reverse the agent's behavior in real time. That is a different
product (agency vs. interruptibility tradeoffs), and a v2 conversation.

### NOT building: per-position NFTs or per-user portfolio tokens

**Why not:** ERC-4626 vault shares per [`specs/ecosystem-design-spec.md`](specs/ecosystem-design-spec.md)
§ 3.2 are the unit of position — fungible vault-token holdings represent your share of
the vault's portfolio. We do NOT issue a per-position NFT (one token per user per
position), a per-user portfolio token, or any other position-individuation layer.
Vault-token fungibility is what makes the AMM-traded copy-trade primitive work.

### NOT building: gamified / "rewards" mechanics

**Why not:** No streaks, no points, no XP for using the platform. v1 is professional;
rewards are a different product class. (Plus, gamification mixed with money management is
ethically dicey territory.)

### NOT building: real-time price feeds via custom oracle infrastructure

**Why not:** Use Chainlink, Pyth, or RedStone for price feeds. Don't roll our own oracle
network. Off-the-shelf where reliable.

**Hackathon caveat:** the synth layer currently uses a backend-driven mock oracle (per
[`specs/ecosystem-design-spec.md`](specs/ecosystem-design-spec.md) § 3.6). This is a
demo simplification; the production path replaces it with Pyth/Chainlink, not a
home-grown alternative.

## Pitch-rigor anti-claims (Day 3 additions from red-team review)

These are not features we won't build — they are *claims we won't make* in the deck or
the public-facing copy. Each one survived the red team in
``agora_project_analysis.md`` (routed to the private docs repo, 2026-08-19) as a defensible framing line.

### NOT pitching: "blockchain as memory" as the load-bearing rhetorical claim

**Why not:** Garrison's "memory makes computation universal" is satisfied by Postgres,
git, Kafka, and S3 versioning — the bar is extraordinarily low and the framing is more
aesthetic than substantive (analysis doc § 5.1). What blockchain uniquely provides is
**multi-party tamper-evident commitment** of specific facts, not "memory" in general. The
defensible pitch line is:

> The ReasoningTraceRegistry is the agent's externalized memory for the specific
> financial-decision artifacts no party — including Archimedes — can later rewrite.

Use that framing. Do not pitch "blockchain as substrate for universal computation."

### NOT claiming: predicted alpha or future-return guarantees

**Why not:** McLean & Pontiff (2016) — published cross-sectional predictors lose 26%
out-of-sample and 58% post-publication. Bailey & López de Prado (2014) — backtest-
optimized strategies often do not exceed the median out-of-sample. Any claim that our
agent "delivers" a target Sharpe is structurally unsupportable. We claim **auditability
of past decisions plus statistical rigor of strategy admission** — not future returns.

### NOT claiming: that an on-chain trace hash proves the agent used the trace

**Why not:** A hash anchored at time T proves the trace existed at time T. It does NOT
prove the agent's trade was caused by that trace's reasoning (analysis doc § 5.2). Until
[`commit-reveal-trace-spec.md`](specs/commit-reveal-trace-spec.md) is implemented (v1.5),
do not claim causation. The honest pitch is "verifiable record of the reasoning at the
moment of the trade" — not "proof that the trade followed from the reasoning."

### NOT claiming: regulatory clarity or production-readiness

**Why not:** Per the regulatory survey in
``agora_project_analysis.md`` (routed to the private docs repo, 2026-08-19) § 6, a managed-portfolio vault
with curator discretion likely satisfies all four prongs of Howey under current SEC
interpretation. This is fine for a hackathon prototype with test users; it is **not** a
production stance. Any pitch should explicitly frame this as a research prototype, not a
launchable investment product. (The 2026-05 SEC + MiCA + Cayman survey in the analysis
doc is the reference; cite it if asked.)

### NOT claiming: that selection-bias correction makes our strategies "right"

**Why not:** DSR, PBO, and OOS Sharpe per
[`specs/selection-bias-corrections-spec.md`](specs/selection-bias-corrections-spec.md)
*reduce* the false-positive rate; they do not eliminate it. The honest claim is that
we apply the corrections and surface the numbers — not that the corrections make any
specific strategy a true positive. The wedge is the rigor, not a guarantee.

## What we ARE building (for explicit contrast)

To make this doc complete: the v1 scope is the inverse of the above. Updated 2026-05-13
to reflect the two-tier marketplace pivot in
[`specs/ecosystem-design-spec.md`](specs/ecosystem-design-spec.md). Specifically:

**Ecosystem layer (Chuan + Marten):**
- Synthetic protocol: 5 oracle-priced synthetic assets (sSPY, sNIKKEI, sGLD, sTREASURY,
  sOIL) backed 1:1 by USDC in a shared collateral pool
- AMM exchange: Uniswap-V2-style constant-product pools, USDC-paired
- VaultFactory + ERC-4626 Vault contracts with 2-and-20-style fees and a 10% platform cut
- Two-tier marketplace:
  - **Tier 1 (Archimedes Verified):** paper-grounded strategies + full agent autonomy +
    selection-bias-corrected backtests + reasoning traces
  - **Tier 2 (Community):** permissionless vault creation + opt-in agent features +
    reasoning traces (paper-grounding optional)
- Vault-token AMM pools enable copy-trading (buy vault tokens = invest in the manager)
- Per-vault chat with wallet-address identity and AI auto-post + @mention (Tier 1)

**Strategy + agent layer (Dan + Önder + Chuan):**
- Curated v1 library: 5–10 paper-grounded strategies, each with full strategy passport
  (paper provenance + methodology hash + curator wallet signature)
- Selection-bias-corrected backtests (DSR, PBO, OOS Sharpe split, look-ahead audit) per
  [`specs/selection-bias-corrections-spec.md`](specs/selection-bias-corrections-spec.md)
- Arxiv ingest pipeline demo on 2–3 papers (not relied on for live portfolio decisions)
- Portfolio agent: regime detection, autonomous rebalancing, strategy rotation, reasoning
  traces, tool-call provenance
- On-chain anchoring of reasoning traces via ReasoningTraceRegistry on Arc

**Settlement + UX (Marten + Daniel + Chuan):**
- USDC settlement on Arc + Paymaster for USDC-denominated gas
- USYC as the risk-off yield anchor in every portfolio (per risk-profile floor)
- Next.js frontend: marketplace landing, vault detail, swap UI, vault creator, reasoning
  trace viewer with "verify trace hash" UI element
- Pitch deck + live demo + Q&A prep grounded in
  ``agora_project_analysis.md`` (routed to the private docs repo, 2026-08-19)

That's the updated v1. Everything else is v2.

## How to use this doc

When someone in the team proposes a feature mid-week-2:

1. Check this doc. If it's listed as anti-feature, the conversation is short.
2. If it's a NEW feature not listed, ask: "what current scope item does this displace?"
   Hackathons are zero-sum on time; if you add X, you must subtract Y.
3. If the team agrees the proposed feature is more important than something currently in
   scope, edit this doc and the in-scope list to reflect the change. Date the change.

## Open questions

- Should we be explicit that **no native token, ever** is the long-term commitment, or
  is "no v1 token" only a v1 scope decision? Default: **v1 commitment, v2+ revisitable.**
- Should the arxiv pipeline graduate from demo to product in v2? **Yes, that's the
  natural v2 — productize the ingest pipeline as a developer-facing API.**
