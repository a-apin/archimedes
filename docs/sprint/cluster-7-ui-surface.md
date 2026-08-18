# Cluster 7 — UI surface shrink (B1, re-cut)

**Re-cut vs the source doc.** The full 6-way consolidation is 1.25d and touches more files than
any other item; the claim-honesty subset is ~0.4d and captures most of the value. Ship the subset
in-sprint, consolidation in the buffer.

Read [README](README.md) session rules first.

## Correction to the original premise — carried from the source doc

A `VITE_*` flag will not tree-shake anything: `App.jsx` statically imports all 20 page components
(25 imports, zero `React.lazy`). **Bundle size isn't the problem; claim-honesty and
discoverability are.** The flag is a *policy selector*, not a code-elimination device.

## Coordinate first

**#1237 (danielscoffee) already fixes `Breadcrumbs.jsx` CRUMB_MAP #1219** — which the full B1 was
going to kill as a side effect. Check its state before touching `Breadcrumbs.jsx`. Let it land;
build on it.

## Sprint subset (~0.4d) — claim-honesty only

| Do | Why |
|---|---|
| `/corpus` — **hide the Graph + KG tabs** | They 503 (`corpus_kg_built: false`). A live 503 inside the product is worse than an absent tab. Component-level, not route-level — `/corpus` stays live |
| `/quant` — wip-stub, drop from nav | Nine `mockReturns`/`seededRng` sites: **the single biggest claim-integrity liability in the UI**. Stub it; do not repair the mocks |
| `/reasoning` — wip-banner, drop from nav | Real but empty until traces exist; traces need vaults; vaults are testnet. **Keep reachable** for the provenance claim |
| `/learnings` — wip-stub, drop from nav | — |
| `/portfolio`, `/portfolio/vaults/:address` — wip-banner, **stay in nav** | — |
| `/marketplace`, `/marketplace/strategy/:id`, `/publish`, `/subscriptions` — live + wip-banner | The meter + spend-cap UI lands on `/subscriptions` |
| **Delete** (zero importers, verified) | `FusionResult.jsx` · `PortfolioAdvisor.jsx` (477L, last caller of `/api/strategies/advisor`) · `data/promptLibrary.js` · `assets/hero.png` · `assets/logo_old.svg` |
| `sitemap.xml` | Currently lists `/marketplace`, which is `WalletGate`d — **violating the file's own documented exclusion rule.** Post-shrink public set: `/`, `/explore`, `/leaderboard`, `/architecture`, `/corpus`, `/insights` |
| Generate page copy | State the cap: the picker offers 281 assets; Engine C caps at 6 per spec and grades them as **independent sleeves** |

**`Architecture.jsx` is 896 lines — surgical edit at `:627` only. Do not rewrite it.** (Path
correction: it is at `ui/src/components/Architecture.jsx`, not `pages/`.)

## Four coupling traps — each with its fix

1. `GenerationStream.jsx:286 → onNavigate('library', …)` and `RejectedCandidates → 'strategy'` —
   **both targets must stay `live`.**
2. `App.jsx:158`'s funnel beacon keeps firing **unconditionally** — it is the only measured
   evidence in the system. Add `"paid"` as a fifth **server-recorded** stage in
   `services/funnel_store.py:55 STAGES`; keep `vault_deployed` separate as a testnet counter.
   **Do not conflate them.**
3. `sitemap.xml` — see table above.
4. **`OnboardingTour` has a live trap.** `OnboardingTour.jsx:195` queries
   `[data-tour="reasoning"]`; with reasoning out of nav the anchor is null, the effect at `:221`
   calls `setPage('reasoning')` to drive it into view, the anchor still doesn't exist, and the
   step is **permanently dead.** Drop that step and add a guard skipping any step whose anchor
   isn't `live`.

## Buffer (~0.85d) — the consolidation

The same fact — route X exists, is called Y, sits in group Z, is wallet-gated, is publicly
indexable — is asserted independently in **six places**: `App.jsx:31 PAGE_TO_PATH`,
`App.jsx:198 titles`, `App.jsx:225-355 renderPage`, `Layout.jsx:22 NAV`,
`Layout.jsx:59 PAGE_LABELS`, `Breadcrumbs.jsx:14 CRUMB_MAP`, plus `sitemap.xml`. **A shrink that
edits some-but-not-all of these is the failure mode.**

Create `ui/src/routes.js` — one manifest of
`{id, path, label, icon, group, status, gate, gateCopy, title, indexable}` where `status` is
`live | wip-banner | wip-stub | hidden`. `App.jsx`, `Layout.jsx`, `Breadcrumbs.jsx` all derive
from it. Extract `ui/src/components/WorkInProgress.jsx` **verbatim** from `Learnings.jsx:15-40`'s
existing "Roadmap · post-hackathon" treatment, then rewrite `Learnings.jsx` as a ~20-line consumer
— **that rewrite is the proof the extraction is faithful.** One env var `VITE_SURFACE_PROFILE`
(default `mainnet`, dev sets `full`) selects between two status overlays; statuses stay hardcoded
in source so they are reviewable in a diff.

## Tests — do NOT stand up vitest

Three Node check scripts wired as `npm run check:routes` catch the actual regression class at ~5%
of the cost:

- `check-routes.mjs` — every `onNavigate('X'` / `setPage('X'` literal resolves to a `live` id.
  **This is what catches traps 1 and 4 mechanically.**
- `check-sitemap.mjs` — sitemap ≡ the derived public set. **Assert, don't generate.**
- `check-orphans.mjs` — every component reachable from `main.jsx`, catching the next
  `FusionResult.jsx`.

## Verify — the artifact, not the source

```bash
cd ui && npm run build && grep -c "mockReturns" dist/assets/*.js   # expect 0
npm run check:routes                                              # all three pass
```

**Grep the artifact from the actual nginx image that deploys, not a local build.** Note that
#1229 (vite 8.2.1) and #1233 (cssnano) already merged ahead of this check existing — the bundler
changed before the integrity check was written, so establish the baseline fresh.

## Backend routers — leave every one registered, unchanged

`swap_routes.py` exposes only `GET /quote` and `GET /pools` — no signer, no executor. **Nothing
orphaned can move funds, spend LLM budget, or write state.** Instead add
`docs/api-surface-status.md` (one row per router: prefix, UI consumer, status, plan) plus a test
asserting every router registered in `main.py:471-495` appears in it — **documentation with teeth
rather than prose that drifts.**

One exception, handled in [cluster-4](cluster-4-strategies-route.md): `strategies_routes.py:1962`
is a second live LLM-spending generation endpoint with no UI consumer. **Route it through the
meter; do not delete it.**

## Anti-goals

No vitest/playwright · no `React.lazy` · don't rewrite `Architecture.jsx` · don't repair
QuantLab's nine mock sites (stub it) · don't unregister any backend router.
