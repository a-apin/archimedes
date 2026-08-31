// The sidebar's real nav data — extracted out of Layout.jsx (round 4 review
// finding) so it can be imported by BOTH the rendering code and its own
// tests, instead of tests re-declaring a hand-built stand-in array. A
// hand-built `[{id: "insights"}, {id: "account"}]` fixture in a test proves
// filterInsightsNavItem() works against SOME array shaped like Layout's — it
// does not prove it works against the actual "Ops" group Layout renders, and
// the two can silently drift (e.g. a future item added to "Ops" with an id
// that happens to collide, or the group renamed) without the test noticing.
// Deliberately plain JS, zero imports, zero JSX — matching
// adminProbeCache.js / insightsGate.js's "stays unit-testable under bare
// `node --test`" discipline.
//
// Sidebar groups separate the marketing-site anchor (labelled "Marketing
// site", NOT "Home" — the breadcrumb's Home crumb already owns that label for
// the in-app anchor at Explore; two controls both reading "Home" ~40px apart
// with different destinations was #1370 item 3) from the product-state
// bands. Empty group label is intentional for that entry — it renders as a
// header-less section so it reads as the top-of-shell anchor, not a peer of
// the other groups. The five labelled groups split the remaining surfaces
// along the gating boundary:
//   DISCOVER — open to anonymous visitors (no wallet needed)
//   STRATEGY — wallet-gated: generate + your saved strategies
//   POSITION — wallet-gated: on-chain audit, post-hoc review (Portfolio and
//     Learnings are ROADMAP_PAGES, hidden by default (#1266); Quant Lab
//     defaults off separately via the backend `quant` feature flag — in the
//     shipped build this group renders as a single item, Reasoning)
//   MARKET — the strategy marketplace (ROADMAP_PAGES, hidden by default)
//   OPS — insights + account
// Item order inside DISCOVER (Explore → Corpus) follows the natural
// user-onboarding read: browse the seed strategies first, see the substrate
// they're drawn from second.
//
// Architecture is deliberately NOT a shell nav item (#1370, PR #1400):
// `pageToPath('architecture')` resolves to the PUBLIC `/architecture` route
// (routes.js PUBLIC_PATHS, not APP_PATHS), so clicking it from inside the
// shell rendered the page in PublicLayout — no sidebar, no breadcrumbs. It
// stays reachable from PublicLayout's own nav and by direct URL; #1370's
// anti-goal forbids fixing it by minting a second, /app-side route.
//
// MERGE-ORDER NOTE: #1400 removes that item from the NAV array while it still
// lives inline in Layout.jsx; this PR moves NAV out to this file. Those two
// edits do not textually conflict, so leaving the item here would have let a
// clean merge silently resurrect the entry #1400 deleted. Removed here so the
// merge in either order lands on the same shell nav.
export const NAV = [
	{
		group: null,
		items: [
			{ id: "landing", label: "Marketing site", icon: "i-lucide-home" },
		],
	},
	{
		group: "Discover",
		items: [
			{ id: "explore", label: "Explore", icon: "i-lucide-compass" },
			{ id: "corpus", label: "Corpus", icon: "i-lucide-library" },
		],
	},
	{
		group: "Strategy",
		items: [
			{ id: "generate", label: "Generate", icon: "i-lucide-sparkles" },
			{ id: "library", label: "Library", icon: "i-lucide-line-chart" },
			// Paper Trading lives in STRATEGY: it is the act-on step of the MVP
			// spine (generate → verdict → paper) — simulated, account-owned, free.
			{ id: "paper", label: "Paper Trading", icon: "i-lucide-trending-up" },
			// Leaderboard lives in STRATEGY (#1077): it ranks the strategy library —
			// discovery-friendly but strategy-native. (Quant Lab moved to Position.)
			{ id: "leaderboard", label: "Leaderboard", icon: "i-lucide-trophy" },
		],
	},
	{
		group: "Position",
		items: [
			{
				id: "portfolio",
				label: "Portfolio",
				icon: "i-lucide-layout-dashboard",
			},
			// Re-added (#1060 AC#3, Dan's call 2026-07-14): the livestream-era hiding
			// (#1061) was for the synthetic-sample-data version; this PR wires the
			// panels to live library/vault/trace data with per-section disclaimers.
			// Lives in POSITION (Dan, 2026-07-14): its panels read the user's live
			// vault/trace data, so it belongs with the deployed-state surfaces and is
			// wallet-gated like them (see App.jsx).
			{ id: "quant", label: "Quant Lab", icon: "i-lucide-flask-conical" },
			{ id: "reasoning", label: "Reasoning", icon: "i-lucide-brain" },
			{ id: "learnings", label: "Learnings", icon: "i-lucide-graduation-cap" },
		],
	},
	{
		group: "Market",
		items: [
			{
				id: "marketplace",
				label: "Marketplace",
				icon: "i-lucide-shopping-bag",
			},
			{ id: "publish", label: "Publish", icon: "i-lucide-megaphone" },
			{ id: "subscriptions", label: "Subscriptions", icon: "i-lucide-bell" },
		],
	},
	{
		group: "Ops",
		items: [
			{ id: "insights", label: "Insights", icon: "i-lucide-bar-chart-3" },
			{ id: "account", label: "Account", icon: "i-lucide-user-round-cog" },
		],
	},
];
