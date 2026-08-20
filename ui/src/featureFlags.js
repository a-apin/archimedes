// Build-time feature flags (Vite env-gated).
//
// Pattern: one flag per Vite env var, exported as a plain boolean, all in
// this one file. Env access uses `import.meta.env?.` (the features.js
// pattern) — this module is imported by routes.js and therefore loads under
// plain node in ui/test/, where import.meta.env is undefined.

// Gates the upfront cost-quote + x402 paywall step on the Generate page
// (docs/specs/generation-quote-contract.md — RATIFIED, #1296). Off by
// default: Generate submits directly with no quote step, same as before
// this flag existed. Dan flips VITE_GENERATION_QUOTE_ENABLED=true once
// the backend's GENERATION_PAYMENT_REQUIRED flag (independently
// flag-gated on that side, see #834's flip-list) is worth trying against
// — this frontend flag works fine against a backend where that's still
// off, since GET /api/generate/quote always reports payment_required
// honestly either way.
export const GENERATION_QUOTE_ENABLED =
	import.meta.env?.VITE_GENERATION_QUOTE_ENABLED === "true";

// Single flag gating the UI surfaces that are out of scope for the MVP
// (#1266): vaults (Portfolio + the vault-detail deep link), the strategy
// Marketplace (+ market-strategy deep link), Publish, Subscriptions, and
// Learnings. All the code for these stays in the tree — this flag just
// keeps them out of the shipped nav/routes/CTAs until they're back in
// scope. Set VITE_ROADMAP_SURFACES=true at build time (see
// ui/.env.example) to preview the full app.
export const ROADMAP_SURFACES_ENABLED =
	import.meta.env?.VITE_ROADMAP_SURFACES === "true";

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
