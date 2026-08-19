// Build-time feature flags (Vite env-gated).
//
// Pattern: one flag per Vite env var, exported as a plain boolean, all in
// this one file. Established by #1266 (ui/src/featureFlags.js,
// ROADMAP_SURFACES_ENABLED) — that PR is open, not yet on main, but this
// file's path/shape matches it deliberately so the two flags land in the
// same file without conflict once it merges.

// Gates the upfront cost-quote + approval step on the Generate page
// (docs/specs/generation-quote-contract.md — PROPOSED). Off by default:
// Generate submits directly with no quote step, same as before this flag
// existed. Dan flips VITE_GENERATION_QUOTE_ENABLED=true once the backend
// quote endpoint + x402 paywall (contracts session, flag-gated on that
// side too) are live enough to try against.
export const GENERATION_QUOTE_ENABLED =
	import.meta.env.VITE_GENERATION_QUOTE_ENABLED === "true";
