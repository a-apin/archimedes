# Architecture-page redesign — Summary (2026-07-14)

## What was produced (this directory)

| File | What it is |
|---|---|
| `docs/architecture.md` | The comprehensive system map: 9 layers with file paths, 5 data flows (Generate; Deploy+Rebalance w/ commit-reveal; Marketplace; Corpus; SIWE), trust boundaries, deploy topology, and a doc-vs-code disagreement register |
| `page-design-proposal.md` | The new Architecture page: 12-section structure with **full final-quality draft copy**, visual placement, and implementation notes (incl. the "fetch, don't hardcode" anti-rot rule) |
| `flow-diagram.svg` | Headline visual: user journey over the technical substrate, off-chain/on-chain trust boundary explicit, commit→trade→reveal loop numbered, Circle/USDC rails tagged, honesty footnotes baked in. Self-contained (own dark background, system fonts only), matches the site palette (`ui/src/App.css` tokens), colorblind-safe accents (gold/sky/emerald + labels) |
| `flow-diagram.mmd` | Editable mermaid source (fallback / future edits) |
| `file-tree.svg` + `file-tree.md` | Repo map: top-level dirs + ~25 load-bearing files, one-line role each; same visual language |
| `flow-check.png`, `tree-check.png` | Rendered previews of the two SVGs (for quick review) |

Everything verified read-only against `main` on 2026-07-14. **Refresh (same day, post-train):**
the 2026-07-14 merge train landed #1071 (runners IaC), #1074 (architect removal), #1075
(num_trials), #1076 (DSL rebalance) and 30+ more; #1099 (spend-cap 429) remains the one open
draft. Markers in the map updated accordingly.

## Key design decisions

1. **Anti-rot as a first-class requirement.** The current page died of hardcoded facts. The
   proposal mandates live-fetching every stat (contracts from `/api/config/contracts`,
   universe from `/api/explore/assets`, corpus + KG + paper_rag state from `/health`), with
   honest "—" fallbacks. The honesty ledger (§11) keys off health flags so runner relocation
   and PAYMENTS_DRY_RUN self-heal into the page when they change.
2. **Off-chain vs on-chain as the page's mental model.** The diagram's dashed trust boundary
   is the organizing idea; sections walk it top to bottom. The commit→trade→reveal loop is
   drawn as the *only* way the agent crosses the boundary — which is literally true
   (`Vault.sol:422` reverts without a commitment).
3. **Honesty framed as product discipline, not disclaimer.** PAYMENTS_DRY_RUN, corpus
   hydration, ABSTAIN, and tri-state rigor verdicts are presented as evidence the badge can
   fail — the trust argument, per the repo's #1 rule and North-Star §9 ("turn the limitation
   into the draw").
4. **Kept what works.** The current page's gold-rail pipeline component and Xia-protocols
   panel survive with corrected content; the "3 agents / 6 memory layers" framing is replaced
   by the debate society + external rigor gate (the shipped architecture).
5. **file-tree.svg is repo-facing, not product-page-facing** — recommended for README/deck
   (judge-as-operator audience), not the live Architecture page.

## What's stale on the current page (`ui/src/components/Architecture.jsx`)

- **"Three top-level agents" with a "Rigor Gate" sub-agent inside the generator** — generation
  is the multi-agent **debate society**, the sole path (`agents/generation_pipeline.py`;
  Strategy Architect removed, PR #1074 merged 2026-07-14), and the rigor gate runs **external** to the
  generator (CLAUDE.md primitive 5; `services/live_rigor_gate.py`). No K=1/considered-rejects/
  ABSTAIN surface.
- **"10 Smart contracts deployed on Arc testnet"** — full hardened suite redeployed 2026-07-09:
  **12 contract sources → 570 live instances** (8 core singletons + 281 SyntheticTokens + 281
  AMMPools; census live at `GET /api/config/contracts`) on chain 5042002, plus user vaults
  minted on demand. `PaymentSplitter` and `StrategyRegistry` absent from the page entirely.
- **Corpus panel: "keyword/TF-IDF today... embeddings not live"** — MiniLM semantic rerank IS
  live (`services/paper_rag.py`; TF-IDF is only the degraded fallback). The honest gap is
  different: prod hydration is sparse (#778) and the KG artifact is pending. The hardcoded
  category counts (1,360 / 1,292 / …) are manifest-only numbers presented as corpus content.
- **"60s tick rebalance loop"** — `chain/agent_runner.py` default is **300 s**
  (`AGENT_INTERVAL_SECONDS`); and the loop's current operational reality (stranded runners,
  relocation IaC merged in PR #1071, apply pending #1065) isn't reflected anywhere.
- **"4 wallet signatures: create → approve → deposit → set allocations"** — the shipped flow is
  `createVault` → `setAgent` (2 sigs) then approve → deposit → allocate (3 more): **5
  signatures** (`CreateVaultModal.jsx:386-388`), plus a second backend-signed
  ownership-transfer path for headless agents (`vaults_routes.py:275`).
- **Trace publishing described as publish-after** ("canonical hash → ReasoningTraceRegistry") —
  the live path is **commit-before-trade, enforced by the Vault contract** (#589), then reveal
  with on-chain hash verification. The strongest provenance claim the product has is missing.
- **No marketplace at all** — x402 nanopayments, 90/10 split, publish/subscribe pages, spend
  caps, and the PAYMENTS_DRY_RUN honesty state are absent.
- **No SIWE / agent-native story** — CTA says "sign in with a passkey"; SIWE wallet auth is
  the auth spine and the agent-usable API is a differentiator (`docs/agent-api.md`).
- **Memory-pillar layer E** ("embeddings + clusters + KG pending") — half-stale (embeddings
  live at retrieval). The 6-layer memory model itself is fine but no longer the page's best
  organizing idea.
- **Stat cards hardcoded in JSX** — the root cause of all of the above.

## Notable doc-vs-code findings (beyond the page)

- `CLAUDE.md` still describes EC2/docker-compose as the live deploy and "11 contracts" — both
  superseded (Fargate cutover 2026-07-09; T3.2 redeploy). Worth a revision pass.
- `docs/specs/commit-reveal-trace-spec.md` points at `services/trace_publisher.py` (actual:
  `chain/trace_publisher.py`) and still calls commit-reveal a proposal — it's implemented and
  contract-enforced.
- `agents/strategy_fusion.py`'s module docstring still describes fusion as flag-gated-OFF
  beside the architect — it's now the heart of the sole pipeline.
- Full register: `docs/architecture.md` §9.

## Open questions for Dan

1. **Live-fetch vs. build-time constants — RESOLVED (Dan, 2026-07-14): live-fetch, yes.**
   The page live-fetches stats with honest fallbacks, including a small config field exposing
   `PAYMENTS_DRY_RUN` + a runner-health flag so the honesty ledger is self-updating.
2. **How much infra on a public page? — RESOLVED (Dan, 2026-07-14): infra is not the story.**
   One footnote line (CI → ECR → Fargate) stands; the page leads with the agent architecture
   and the academic/statistical rigor machinery. ALB/Aurora details stay in repo docs.
3. **Marketplace custody wording — STILL OPEN (awaiting Dan's call; options laid out in the
   2026-07-14 session report).** Draft keeps the honest-but-soft framing: "subscription fees
   settle through a Circle-managed wallet while fee custody moves to a fully non-custodial
   design (#975); vault principal is non-custodial today and always." Alternative is the #958
   decision-record wording with "custodial-interim" verbatim.
4. **Contract count — RESOLVED (Dan, 2026-07-14): full breakdown, not one number.** The page
   lists the census explicitly — 12 sources → 8 core + 281 synths + 281 pools = 570 live
   instances + on-demand vaults — and derives every count at render time from
   `GET /api/config/contracts` so it can never rot.
5. **Memory pillar — RESOLVED (Dan, 2026-07-14): inline where it fits, only if real.** The
   6-layer story stays out — the memory-first substrate is a decided direction, not shipped
   code, and the page only claims what runs. The substrate line (episodic `strategy_proposals`
   compounding + corpus artifacts) stays because those are real today.
6. **file-tree.svg placement — RESOLVED (Dan, 2026-07-14): README/deck.** README now links
   the figure from its architecture section (same branch); it stays off the product page.
