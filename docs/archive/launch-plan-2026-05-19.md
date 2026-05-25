# Launch Plan — Coordinated Reveal of Archimedes + Linus + KnowledgeBase

> **Status:** Draft, 2026-05-19. Owner: Dan (narrative) + Marten (coordination).
> **Honesty guardrails (non-negotiable — they ARE the brand):** this is a
> **research/engineering reveal**, not a financial-product launch. Arc is testnet-only
> (no mainnet); faucet USDC, no real funds, by design. AI can be wrong; the goal is to
> win more than you lose, not never lose; whether it generates *profitable* strategies is
> genuinely TBD and we say so. We launch on what is *provable today*: provenance, rigor,
> and a real research engine — not on returns. Overclaiming would destroy the exact
> credibility wedge the whole product is built on.

## 1. The reveal thesis

Three repos, one lineage — that *is* the story:

- **KnowledgeBase** — a personal scientific-paper intelligence engine (embeddings,
  clustering, similarity + knowledge graphs over ~19k papers).
- **Linus** — a personal AI orchestration backend; the research/memory substrate.
- **Archimedes** — the product: KnowledgeBase's pipeline + Linus's patterns, specialized
  to quantitative finance — a research-grounded strategy-generation instrument, rigor-gated,
  provenance-anchored on Arc.

Hook: **"On-chain curation runs on trust; November 2025 proved that breaks on rigor. We
built the proof-based alternative — and the personal research-intelligence stack under
it."** Anchor with the real numbers from [`competitor-landscape.md`](competitor-landscape.md).

## 2. Timing & sequencing (decision-driving)

Hackathon submission is **~May 25**; **Traction = 30% of judging**. A coordinated
multi-person splash *is* traction evidence. **Recommendation: launch wave lands a day or
two BEFORE/AT submission, not after judging** — so stars/engagement/feedback count toward
the score and show up in the arc-canteen telemetry. Post-judging we do a second
amplification wave.

## 3. Decisions needed from Dan (these change the plan)

1. **Launch timing vs. judging** — recommend pre/at-submission (see §2). Confirm.
2. **Public-app operating mode** (see §5) — recommend "explore + Corpus Explorer free;
   live generation = BYOK or rate-limited demo key + loud canned fallback."
3. **Domain name** — pick one (e.g. `archimedes.fund` / `.xyz` / `.ai` / `.finance`);
   ~$10–15/yr. Needed for the professional surface.
4. **Repo scope** — all three public at reveal? (KnowledgeBase + Linus contain personal
   framing — see §6.) Confirm which go public and when.

## 4. Domain & hosting (accuracy correction + the real path)

**iCloud+ "Custom Email Domain" is email-only** — it gives you `you@yourdomain.com`, it
does **not** host the app or point web DNS at the EC2 box. The instinct is right and it's
cheap; the actual minimal path:

1. Buy a domain at any registrar (~$10–15/yr).
2. Put **Cloudflare** (free tier) in front as DNS + proxy → you get **free HTTPS** *and*
   the bare EC2 IP is hidden behind Cloudflare. (Alternative: Caddy/Nginx + Let's Encrypt
   on the EC2 box for auto-TLS.)
3. Point the proxied record at the EC2 origin; keep `http://18.171…` working as fallback.

A bare IP reads as "unfinished" to operator-judges; a domain + TLS is the single
highest-ROI polish item. **Touches infra (Chuan's lane)** — small coordination or a
judge-grade issue; do it before the public push, not during.

## 5. Public-app operating mode (operational risk)

A *public* app means strangers hit it. Two real risks: (a) shared GLM key consumption /
rate-limit / abuse; (b) the key must **never** be in the now-public repo. Recommended
public mode:

- **Free for everyone:** browse, the Corpus Explorer (~10k papers, clusters/graph),
  example strategies, reasoning-trace/provenance views — all read-only, no key needed.
  This is already the most impressive, lowest-risk surface.
- **Live generation:** BYOK (paste your own Anthropic-compatible key) **or** a
  rate-limited shared demo key with a loud canned fallback (`/health` shows
  live-vs-canned). Never silently burn the shared key.

## 6. Pre-launch readiness checklist (per repo — all must be true before the push)

- [ ] **Archimedes:** README accurate to the spine + testnet-honest (already in flight);
      zero secrets; LICENSE present; the Corpus Explorer + a generated-strategy w/
      provenance demoable; domain+TLS live; `/health` honest.
- [ ] **Linus:** README reframed from "personal project, not a product" to a
      reveal-appropriate framing (it's the substrate of a real product now); secrets/keys
      audited; the Archimedes lineage cross-linked.
- [ ] **KnowledgeBase:** README presentable for a public audience; no private data/keys;
      cross-linked as the engine under Archimedes.
- [ ] **Shared:** demo video (≤3 min, the locked spine, testnet-honest); 3–4 hero
      screenshots (Corpus Explorer, a generated strategy + rigor badge + source papers,
      the provenance trace, the architecture diagram); a one-pager.

## 7. Channels & the coordinated push

Synchronized, same ~2-hour window, everyone amplifies (like/retweet/comment each other
within the first hour — the algorithmic window):

- **X** — primary. A thread (copy in §8) from the project/Dan; team quote-posts.
- **LinkedIn** — Dan + team; the "what we built + why it matters" angle (numbers-anchored).
- **Discord** — **Canteen** + **Build on Arc** channels (where the judges/community are).
- **Facebook / Instagram** — optional, lower priority; the demo video + one-liner.
- The **ask** in every post: *try it on testnet → ⭐ the repos → tell us what breaks.*

## 8. Draft post copy (honest, hooky — lift & edit)

**X thread (5 posts):**

1. *On-chain asset management already has billion-dollar rails (Morpho ~$7.5B) and
   billion-dollar curators. November 2025 proved the curation layer above them breaks on
   rigor. We built the proof-based alternative — and open-sourced the research stack
   under it. 🧵*
2. *Archimedes: describe what you want → it fuses your intent + live market data + a
   ~10,000-paper quant-finance research library into novel strategies → gates them with
   deflated-Sharpe / overfitting-probability rigor → every reasoning step traces to the
   source paper, anchored on Arc.*
3. *The honest part: it runs on Arc **testnet** (no mainnet yet) — faucet USDC, no real
   money, by design. AI can be wrong; the goal is to win more than you lose, not never
   lose. We make every decision auditable so performance accrues as verifiable history.*
4. *Under it: KnowledgeBase (a 19k-paper intelligence engine) + Linus (a personal AI
   orchestration backend). The same stack, specialized to quant finance. All three open.*
5. *Try it on testnet: <domain>. Repos: <links>. Tell us what breaks. ⭐ if the
   curation-with-proof thesis resonates.*

**LinkedIn (short):** *Curation in on-chain finance is run on trust — and the Nov-2025
crisis showed that breaks on rigor. We built Archimedes: a research-grounded,
rigor-gated strategy generator with provenance anchored on Arc, on top of a personal
paper-intelligence stack we're open-sourcing. Testnet-only and honest about it. [link]*

**Discord (Canteen / Build on Arc):** *Revealing Archimedes (Agora hackathon) + the two
research repos under it. Research-grounded strategy generation, DSR/PBO rigor gate,
on-chain provenance, ~10k-paper corpus explorer. Testnet, faucet USDC, honest about
what's TBD. Try it: <domain> · repos: <links> · feedback very welcome.*

## 9. Assets needed (owners)

Demo video ≤3 min (Daniel) · hero screenshots (Daniel/Marten) · architecture +
competitor-curation slide (from `demo-script-pitch-deck-outline.md`, Dan) · domain+TLS
(Chuan/infra) · the three READMEs polished (Dan + repo owners).

## 10. Roles

Marten: coordination + timing. Dan: narrative + X/LinkedIn lead. Daniel: demo video +
screenshots + the live app. Chuan: domain/TLS + app stability for public traffic.
Önder + all: amplify within the first hour.

## 11. Risks & guardrails

- **Overclaiming** → mitigated by §honesty guardrails; every post carries the testnet +
  AI-fallibility framing. This is a strength, not a disclaimer.
- **Shared-key abuse / cost** → §5 operating mode.
- **Secrets in now-public repos** → pre-launch secret audit (§6) is a hard gate.
- **A public bug during the splash** → freeze risky merges in the launch window; have the
  fallback IP + the demo video; `/health` tells the truth.
- **Don't let launch prep starve the product** — readiness checklist items that are also
  product work (honest READMEs, /health) double-count; pure-marketing tasks are
  time-boxed and parallelized, not on the engineering critical path.
