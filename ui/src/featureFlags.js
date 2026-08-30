// Build-time feature flags (Vite env-gated).
//
// Pattern: one flag per Vite env var, exported as a plain boolean, all in
// this one file. Env access uses `import.meta.env?.` (the features.js
// pattern) — this module is imported by routes.js and therefore loads under
// plain node in ui/test/, where import.meta.env is undefined.

// Gates the upfront cost-quote + x402 paywall step on the Generate page
// (docs/specs/generation-quote-contract.md — RATIFIED, #1296). ON by
// default as of Dan's 2026-08-19 directive: payment enforcement at
// $2.00/generation is going live on testnet, so the upfront quote + real
// payment flow (Generate.jsx + ../x402.js) should be visible by default
// rather than opt-in. Set VITE_GENERATION_QUOTE_ENABLED=false explicitly to
// suppress it (e.g. a build that must not show payment UI at all) — this
// frontend flag works fine either way against the backend's independently
// flag-gated GENERATION_PAYMENT_REQUIRED (see #834's flip-list), since GET
// /api/generate/quote always reports payment_required honestly regardless.
export const GENERATION_QUOTE_ENABLED =
	import.meta.env?.VITE_GENERATION_QUOTE_ENABLED !== "false";

// Single flag gating the UI surfaces that are out of scope for the MVP
// (#1266): vaults (Portfolio + the vault-detail deep link), the strategy
// Marketplace (+ market-strategy deep link), Publish, Subscriptions, and
// Learnings. All the code for these stays in the tree — this flag just
// keeps them out of the shipped nav/routes/CTAs until they're back in
// scope. Set VITE_ROADMAP_SURFACES=true at build time (see
// ui/.env.example) to preview the full app.
export const ROADMAP_SURFACES_ENABLED =
	import.meta.env?.VITE_ROADMAP_SURFACES === "true";

// Gates the Topic Clusters (knowledge-graph) tab inside /app/corpus (#1406).
//
// Deliberately NOT a ROADMAP_PAGES entry, and not routed through
// featureEnabled(): those gate *pages* — ids that resolve through the router
// as /app/<page> — and this is one entry in CorpusExplorer.jsx's local TABS
// array inside the single /app/corpus page. featureEnabled('knowledge-graph')
// would silently no-op, hiding nothing.
//
// It is also a different KIND of gate. ROADMAP_SURFACES_ENABLED means "out of
// scope for the MVP"; this tab is in scope and simply has no data yet —
// kg_entities/kg_relations are 0 rows until #1090 produces a KB pipeline
// artifact and #1092 backfills Postgres from it. Folding it into the roadmap
// umbrella would mean previewing vaults also reveals an empty Topic Clusters
// tab, and that when the data lands the fix is deleting a ROADMAP_PAGES entry
// rather than flipping a flag.
//
// Off by default (the ROADMAP_SURFACES_ENABLED convention). Set
// VITE_KNOWLEDGE_GRAPH_TAB=true to preview it; CorpusKG.jsx's /health-branched
// zero-state (#1392) stays the honest fallback for anyone who does.
export const KNOWLEDGE_GRAPH_TAB_ENABLED =
	import.meta.env?.VITE_KNOWLEDGE_GRAPH_TAB === "true";

// Page ids the flag hides. Consumed by routes.js featureEnabled() — the
// single gate for nav visibility, flat routes, and deep links alike — plus
// the two spots routing can't reach: the Breadcrumbs mid-crumb and in-page
// CTAs (StrategyPassport deploy card, onboarding tour).
export const ROADMAP_PAGES = new Set([
	"portfolio",
	"vault-detail",
	"marketplace",
	"market-strategy",
	"publish",
	"subscriptions",
	"learnings",
]);

export function roadmapSurfaceHidden(page) {
	return !ROADMAP_SURFACES_ENABLED && ROADMAP_PAGES.has(page);
}
