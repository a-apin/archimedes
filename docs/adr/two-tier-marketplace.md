# ADR: Two-tier marketplace — Verified vs Community, with rigor as the wedge

> **Audience:** Archimedes team
> **Status:** Accepted
> **Date:** 2026-05-13 (Day-3 ecosystem pivot, Agora hackathon)
> **Owner:** Dan Browne
> **Supersedes:** the single-vault architecture in [`design.md` § 5.2](../archive/agora-2026-05/design.md)
> **Superseded-by:** —
> **Question being decided:** Is Archimedes one agent managing one vault, or a marketplace — and if a marketplace, who is allowed to create a vault?
> **Related:** [`docs/specs/ecosystem-design-spec.md` § 1, § 5](../specs/ecosystem-design-spec.md), [`non-custodial-vault-owner-agent.md`](non-custodial-vault-owner-agent.md), [`docs/archive/agora-2026-05/arc-alignment.md`](../archive/agora-2026-05/arc-alignment.md). (Competitive landscape material moved to the private docs repo — see `CLAUDE.md`.)

## TL;DR

**Archimedes is a two-tier vault marketplace, not a single managed vault.** **Tier 1
(Verified)** vaults are Archimedes-curated, paper-grounded, rigor-gated and carry a
"Verified" badge; **Tier 2 (Community)** vaults are freestyle — any assets, any weights, no
paper required. Both use the same non-custodial ERC-4626 contract structure. **Rigor is the
wedge:** the Verified badge is the thing nobody else in the category can credibly issue, and
the Community tier is what makes it a marketplace rather than a product.

## Context

Through Day 2 of the Agora hackathon the design was a single agent managing a single vault
([`design.md` § 5.2](../archive/agora-2026-05/design.md)). Two problems surfaced together:

1. **A single vault is a product, not an ecosystem.** There is no supply side, no
   discovery, no copy-trading, and no reason for a second user to bring anything but money.
   The interesting on-chain primitives — vault tokens as ERC-20 shares, an AMM that trades
   them, copy-trading as simply *buying the token* — only exist if there are many vaults.
2. **Rigor is the only defensible differentiator, and it does not scale to everything.**
   The paper-grounded, DSR/PBO/walk-forward-gated strategy passport is expensive per
   strategy and requires a curated paper corpus. Requiring it of every vault would cap the
   marketplace at whatever the team can curate. Requiring it of *none* throws away the only
   claim competitors cannot copy.

The resolution is that these two problems answer each other: let anyone create a vault, and
make the rigor gate a *badge* rather than an *entry requirement*. That turns rigor from a
throughput ceiling into a scarce, visible signal — the wedge.

## Decision

**Two tiers over one shared contract structure.**

| | **Tier 1 — Archimedes Verified** | **Tier 2 — Community** |
|---|---|---|
| Created by | the platform agent address | any connected wallet |
| Strategy | paper-grounded, from the curated corpus | freestyle: any assets, any weights |
| Rigor gate | required; passport with DSR / PBO / walk-forward OOS / look-ahead audit | not required |
| Badge | "Archimedes Verified" metadata on-chain | none |
| Agent | full agent-as-a-service — regime detection, strategy rotation, reasoning traces | opt-in features (auto-rebalance, drift alerts, basic regime response) |
| Custody | non-custodial, `owner` ≠ `agent` | identical, with a real-user `creator` |

Supporting decisions taken in the same pivot
([`ecosystem-design-spec.md` § 1](../specs/ecosystem-design-spec.md)):

- **The vault token *is* the copy-trade primitive** — buying a vault's ERC-20 share on the
  AMM is investing in that manager's portfolio. No separate copy-trade mechanism.
- **Marketplace is the landing page** — journey is Explore → Invest → Create → Trade, so
  the lowest-friction action is a first investment, not a vault creation.
- **Fees are 2-and-20-shaped**, creator-set, platform takes 10% of fees.
- **The tier distinction is metadata on the same contracts**, not a separate contract
  family — Tier-1 vaults are agent-deployed with the user as `owner`; Tier-2 vaults use the
  identical structure with a real-user `creator`
  ([`non-custodial-vault-owner-agent.md`](non-custodial-vault-owner-agent.md)).

## Consequences

### Positive
- **Supply side exists.** The marketplace can grow faster than the team can curate papers.
- **The badge is scarce and therefore means something.** "Verified" is defensible precisely
  because it is not granted to everything; a badge everyone has is not a wedge.
- **One contract structure, two tiers.** The custody guarantees, oracle path and rebalance
  authority are identical, so the security surface does not double with the product surface.
- **Rigor moves from a bottleneck to an asset.** The expensive part of the product is the
  part competitors cannot copy, and it is displayed rather than assumed.

### Negative / costs we accept
- **Two tiers means two trust stories to communicate**, on one surface, to users who may
  not read the difference. A Community vault that loses money on the Archimedes marketplace
  is an Archimedes reputational event whether or not the badge says otherwise. The UI must
  make the distinction unmissable, and that is a permanent copy and design burden.
- **Tier-2 was cut from the hackathon MVP** (per the strip-to-spine page tree) and deferred
  to the roadmap. So the marketplace shipped with only the tier that does *not* need a
  marketplace — the two-tier decision is architecturally in place (contracts, metadata,
  ownership model) but has not been validated with real community supply.
- **Freestyle vaults inherit the platform's asset universe and oracle constraints**, so
  "any assets" is bounded in practice by what has a price feed.
- **The rigor gate becomes a public claim.** Once "Verified" is a visible badge, the gate's
  correctness is externally consequential — which is what
  [`rigor-gate-unification.md`](rigor-gate-unification.md) and
  [`num-trials-self-containment.md`](num-trials-self-containment.md) are protecting.

## Alternatives considered
- **Single curated vault — rejected.** No supply side, no discovery, no copy-trading; the
  on-chain primitives have nothing to operate on.
- **Curate everything (rigor as an entry requirement) — rejected.** Caps the marketplace at
  team throughput and turns the differentiator into a bottleneck.
- **Curate nothing — rejected.** Discards the only claim in the category that is expensive
  to fake, and makes the product a generic vault factory.
- **Separate contract families per tier — rejected.** Doubles the audit surface to express
  a distinction that is metadata.
