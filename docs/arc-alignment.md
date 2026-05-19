# Arc Alignment — Strategy & Build Posture

> **Date:** 2026-05-19. **Canonical strategy doc for "how Archimedes aligns to,
> and builds in, the Arc/Circle world."** Read with
> [`user-stories.md`](user-stories.md) (the spine), [`competitor-landscape.md`](competitor-landscape.md)
> (the tiered thesis), and issue #84 (Circle Agent Stack adoption).
> Sourcing: `/tmp/research/arc-circle.md` (2026-05-19 research pass) + `submodules/context-arc/`.

## Why this doc

The hackathon is **Canteen × Circle × Arc**. Judges are Arc/Circle operators and
they reward teams that *build with the sponsor stack*, not merely deploy on it.
This doc refocuses Archimedes's strategy around Arc: what's true about the
platform, what we already use, what we should use, and the narrative to echo.

## 1. The testnet truth (and why it's the right posture)

**Arc has no mainnet.** Circle's Arc docs list mainnet as *"upcoming"*; the public
testnet *"mirrors mainnet behavior, no real assets."* Every Arc project — ours and
every hackathon rival — is **pre-product by definition**.

This is a **strategic strength, stated plainly**:

- It is the *correct* posture for an Arc-stage project; pretending otherwise
  fails with operator-judges.
- It scopes Archimedes honestly: *"try the full flow on the Arc public testnet
  with faucet USDC — no real funds, by design."*
- It **parks the regulatory/custody surface as roadmap**: with no real money,
  there is no RIA trigger, no custody risk, no redemption-liquidity problem *yet*.
  Off-chain redemptions, preset-strategy / RIA posture, and exploit alerting
  (Hypernative-class) become a **designed-for-mainnet business-plan story**, not
  hackathon debt. See [`competitor-landscape.md`](competitor-landscape.md)
  § Regulatory.

## 2. What we already use (real Arc/Circle integration)

- **10 Solidity contracts deployed on Arc testnet** (chain ID `5042002`).
- `backend/archimedes/chain/` — chain client, `circle_signer.py`, executor,
  oracle runners, trace publisher.
- `submodules/context-arc/` — Circle's canonical agent docs + 5 reference
  codebases; our authoritative reference for any Arc/Circle integration question.
- **USDC settlement on Arc** with on-chain reasoning-trace anchoring.

## 3. The gap — the biggest untapped sponsor surface

We do **not** yet use Circle's newer **Agent Stack** (CLI, Agent Wallets,
Nanopayments, x402, Marketplace) or the open-source **Skills** (incl. `use-arc`).
This is the highest-ROI alignment move and is filed as a judge-grade issue:

- **Issue #84 — Evaluate/adopt Circle Agent Stack + `use-arc` skill** (assigned
  `t2o2`). Ranked moves: (1) install `use-arc` + smart-contract Skills into the
  Claude Code harness and cite it as our Arc authoring path (lowest risk, highest
  credibility); (2) document the faucet → Arc-testnet onboarding in README + demo;
  (3) spike running the signer as a Circle CLI **Agent Wallet** with a
  spending-policy cap, wrapping `circle_signer.py`.

## 4. The faucet → testnet onboarding reality (the actual user journey)

A user (and a judge) must onboard before they can touch the product:

1. Go to <https://faucet.circle.com/>; no account required.
2. Pick **USDC** + **Arc Testnet** (default), paste address, solve reCAPTCHA.
3. Receive **20 USDC / 2h**. **On Arc, USDC *is* gas** — one drip funds both
   transactions and balances; there is no separate native gas token to acquire.
4. Connect wallet to Arc testnet (chain ID `5042002`) → run the spine.

**Implication:** the demo and README must make this path obvious. A judge who
lands with zero faucet USDC and no instructions is a failed demo. This is a
concrete 🔍 item for the #39 UX walkthrough.

## 5. The Arc narrative to echo (pitch / README / demo)

Arc frames itself around an **agentic economy**: agents as *first-class economic
participants* settling in USDC with **sub-second deterministic finality**, with
ERC-8004-style reputation. Circle's own sample apps contain **no
trading/portfolio agent** — Archimedes fills that whitespace as the
research-grounded, rigor-gated portfolio citizen of the Arc agentic economy.

Pitch line: *"Arc gives agents a settlement layer; Archimedes gives the agentic
economy its first research-grounded, rigor-gated portfolio manager — every
decision hashed and verifiable on Arc."*

## 6. Strategic posture vs the curation-infra tier

Per [`competitor-landscape.md`](competitor-landscape.md): the live-mainnet
curation incumbents (Morpho/Gauntlet/Upshift) run **trust-based** curation —
Nov-2025 proved that layer breaks on rigor. Archimedes is the **proof-based**
answer, and being **Arc-native + testnet-honest** is consistent with that: we
don't overclaim live AUM; we demonstrate the *mechanism* (research-grounding +
DSR/PBO rigor gate + on-chain provenance) on the platform the sponsors are
building. Accountable is partner-shaped, not a rival.

## 7. Judge-credibility checklist (Arc/Circle alignment)

- [ ] Faucet → testnet onboarding documented in README + demo script.
- [ ] Circle `use-arc` / smart-contract Skills installed + cited (issue #84).
- [ ] Demo narrates testnet honestly ("no real funds, by design").
- [ ] On-chain reasoning traces shown live on the Arc explorer.
- [ ] Pitch echoes the Arc agentic-economy framing.
- [ ] Mainnet + regulatory architecture presented as the business-plan roadmap.

## 8. Roadmap (mainnet — not hackathon scope)

When Arc mainnet ships: real-funds custody, off-chain redemption design,
preset-strategy / RIA legal posture, exploit alerting (Hypernative-class),
multi-user + the social-network expansion (see [`user-stories.md`](user-stories.md)
§ Scope). These are the business-plan narrative; **none are v1 demo work.**

---

_Live as of 2026-05-19. Arc/Circle tooling moves fast — re-check the Agent Stack
and faucet specifics before submission._
