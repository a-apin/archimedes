# ADR: The Unlicense — a public-domain dedication, and what it costs

> **Audience:** Archimedes team, and anyone considering a commercial structure around this code
> **Status:** Accepted
> **Date:** repository initial commit (`292f543`) — the date the licence was chosen deliberately, rather than inherited from a template, is [unestablished — needs Dan]
> **Owner:** Dan Browne
> **Supersedes:** —
> **Superseded-by:** —
> **Question being decided:** Under what licence is the Archimedes source released?
> **Related:** [`LICENSE`](../../LICENSE), [`docs/archive/agora-2026-05/ARC-OSS-SHOWCASE.md`](../archive/agora-2026-05/ARC-OSS-SHOWCASE.md).

## TL;DR

**Archimedes is released under the Unlicense — a dedication of the work to the public
domain**, not a permissive licence. [`LICENSE`](../../LICENSE) is the Unlicense text
verbatim ("This is free and unencumbered software released into the public domain"). This is
the maximally open choice and it fits the OSS-showcase posture. **It also means the code is
not an asset the company can own or exclusively license, and it does not, by itself, obtain
any rights from contributors.** Those are the consequences worth recording.

## Context

Archimedes was built in public for the Agora hackathon with an OSS-showcase posture: the
rigor claims are meant to be *checkable*, the provenance anchors are meant to be
*recomputable by anyone*, and the reference value of the code is part of the pitch. A
licence that lets a reader copy a mechanism without a lawyer serves that directly. The
Unlicense is the strongest available expression of it — it does not grant permissions
subject to conditions, it disclaims the copyright interest entirely.

The reason this needs a decision record is not the choice. It is that a public-domain
dedication has downstream consequences that are easy to discover late — specifically at the
moment someone asks what the company owns, or what an investor or acquirer is buying.

## Decision

**Keep the Unlicense as the project licence** ([`LICENSE`](../../LICENSE), present since the
initial commit `292f543`). The dedication is to the public domain: anyone may copy, modify,
publish, use, compile, sell or distribute the software, in source or binary form, for any
purpose, commercial or non-commercial, by any means.

## Consequences

These are the point of this ADR.

### Positive
- **Zero friction for readers and reusers.** No attribution obligation, no
  licence-compatibility analysis, no CLA to sign to copy a mechanism. For a project whose
  claim is "you can check this yourself", that is aligned rather than merely generous.
- **No copyleft contamination risk** for anyone integrating the code.
- **Consistent with the showcase posture** and with the verifiability argument the product
  makes about its own outputs.

### Negative / consequences we are accepting, stated plainly
- **The code is not an asset the company can own.** A public-domain dedication means there
  is no exclusive right left to hold. It cannot be exclusively licensed to a customer,
  contributed to a joint venture as consideration, or sold as IP. In a diligence process,
  the answer to "what IP does the company own?" is not this repository. What remains
  ownable is elsewhere: trademarks, the curated corpus and its licensing, operational data,
  deployed contract addresses and their governance, the hosted service, and the team.
- **Anyone may run a competing instance, commercially, without permission or attribution** —
  including a fork that keeps the "Verified" framing. The defensibility of
  [`two-tier-marketplace.md`](two-tier-marketplace.md) therefore cannot rest on the code; it
  rests on the corpus, the operational record and the brand. If the wedge is ever argued as
  "our code", that argument is unavailable.
- **Contributors retain copyright in their own contributions, independent of this file.**
  The Unlicense is a dedication *by the person who applies it* to *their own* work. It is
  not a contributor licence agreement and it does not operate on code the project did not
  author. Absent a CLA or an explicit per-contributor dedication, a contribution merged into
  this tree is the contributor's copyrighted work, sitting inside a repository labelled
  public-domain. **The repository's licence statement and the actual rights position may
  therefore diverge**, and that divergence grows with every outside contribution. This is
  the sharpest edge of the choice and it is currently unaddressed.
- **Public-domain dedications are not uniformly effective across jurisdictions.** Several
  civil-law jurisdictions do not permit an author to abandon copyright (notably moral
  rights). The Unlicense includes a fallback permissive grant for exactly this reason, but
  the practical effect in those jurisdictions is "a very permissive licence", not "no
  copyright" — so downstream users in those jurisdictions are relying on the fallback.
- **No patent grant.** Unlike Apache-2.0, the Unlicense says nothing about patents. Neither
  we nor contributors grant patent rights, and neither is protected by a patent-retaliation
  clause.
- **No trademark reservation in the licence.** Trademark is a separate regime and is not
  waived by the Unlicense, but nothing in `LICENSE` says so — a reader could reasonably
  assume the name travels with the code. If the name matters, it needs to be asserted
  somewhere other than here.

### Open follow-ups (not decided by this ADR)
- **Whether to adopt a CLA or a DCO** so that contributions are unambiguously covered by the
  same dedication. Doing this changes nothing about the licence and closes the contributor
  divergence above. **[unestablished — needs Dan]**
- **Whether the current licence is still the right one** now that there is a marketplace and
  a hosted service, rather than a hackathon showcase. Re-licensing away from a public-domain
  dedication is **not retroactive** — every version already published stays public domain —
  and would additionally require the agreement of every contributor whose copyrighted
  contribution is in the tree. The cost of changing this decision rises monotonically with
  time and contributor count. **[unestablished — needs Dan]**

## Alternatives considered
- **MIT / BSD-3 — not chosen.** Nearly as permissive, but preserves a copyright the company
  could hold and requires attribution downstream. This would have kept an ownable (if
  weak) asset and a name-preservation hook.
- **Apache-2.0 — not chosen.** Adds an explicit patent grant, patent retaliation, and a
  trademark carve-out — the standard choice where a company intends to build a business
  around the code. It is the most likely destination if this decision is ever revisited.
- **AGPL / copyleft — not chosen.** It would prevent a closed competing hosted instance,
  which is the risk named above, but it conflicts with the showcase-and-reuse posture and
  deters exactly the reader the project wants.
- **Dual licence (open core) — not chosen**, and now hard to reach: it requires holding
  rights the dedication gave away for everything already published.
