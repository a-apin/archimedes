import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { parseFeatures } from "../src/features.js";
import {
	isAnonymousAppPage,

	canNavigateTo,
	pageToPath,
	passportBackLabel,
	passportBackPage,
	postAuthPath,
	resolveRoute,
	safeNextPath,
	visibleNavigation,
} from "../src/routes.js";

// Test-only override restoring the hidden roadmap surfaces (#1266): the
// build-time VITE_ROADMAP_SURFACES flag is off under node, and
// parseFeatures() never emits this key, so app code can't pass it.
const ROADMAP_ON = { quant: true, roadmapSurfaces: true };

test("landing and architecture remain public", () => {
	assert.deepEqual(resolveRoute("/").kind, "public");
	assert.deepEqual(resolveRoute("/architecture").kind, "public");
});

// Better Auth's account-linking guard (auth/auth.js disableImplicitLinking)
// redirects a rejected OAuth sign-in to `/?error=account_not_linked` — the
// bare landing route, which has no sign-in form. Landing must not swallow it:
// resolveRoute bounces it to /sign-in, the actual sign-in surface, instead.
test("landing OAuth error bounces to the sign-in surface instead of being swallowed (#1420)", () => {
	const route = resolveRoute("/", "?error=account_not_linked");
	assert.equal(route.kind, "redirect");
	assert.equal(route.redirect, "/sign-in?error=account_not_linked");
});

test("landing with no error query param stays plain public landing", () => {
	const route = resolveRoute("/", "");
	assert.equal(route.kind, "public");
	assert.equal(route.redirect, null);
});

test("an error query param reaching /sign-in directly is carried onto the route", () => {
	const route = resolveRoute("/sign-in", "?error=account_not_linked");
	assert.equal(route.kind, "auth");
	assert.equal(route.error, "account_not_linked");
});

// Mutation-prove: this assertion is worthless if it also passes with the
// `error` field never wired into the redirect target. Removing the
// `?error=...` suffix from routes.js's redirect template (or dropping the
// `pathname === '/' && query.error` guard entirely) makes this test fail —
// confirmed by hand before this file was committed.
test("the redirect target actually encodes the error value, not just any redirect", () => {
	const route = resolveRoute("/", "?error=some_other_code");
	assert.equal(route.redirect, "/sign-in?error=some_other_code");
});

test("account routes remain public", () => {
	assert.equal(resolveRoute("/sign-in").kind, "auth");
	assert.equal(resolveRoute("/sign-up").kind, "auth");
});

test("reset-password is a public auth route (#1323)", () => {
	const route = resolveRoute("/reset-password");
	assert.equal(route.kind, "auth");
	assert.equal(route.page, "reset-password");
});

test("application routes use /app boundary", () => {
	const route = resolveRoute("/app/library", "?tab=examples");
	assert.equal(route.kind, "app");
	assert.equal(route.page, "library");
	assert.equal(route.tab, "examples");
	assert.equal(
		pageToPath("library", { tab: "examples" }),
		"/app/library?tab=examples",
	);
});

test("deep application routes retain identifiers", () => {
	// vault-detail is a hidden roadmap surface (#1266) — its identifier
	// extraction is pinned under the flag-on override.
	assert.equal(
		resolveRoute("/app/portfolio/vaults/0x123", "", ROADMAP_ON).vaultAddress,
		"0x123",
	);
	assert.equal(resolveRoute("/app/strategy/alpha").strategyId, "alpha");
	assert.equal(
		pageToPath("strategy", { strategyId: "alpha beta" }),
		"/app/strategy/alpha%20beta",
	);
});

test("legacy private path redirects under app boundary", () => {
	assert.deepEqual(resolveRoute("/generate").redirect, "/app/generate");
	assert.deepEqual(
		resolveRoute("/library", "?highlight=alpha").redirect,
		"/app/library?highlight=alpha",
	);
	assert.deepEqual(
		resolveRoute("/portfolio/vaults/0x123").redirect,
		"/app/portfolio/vaults/0x123",
	);
});

test("unknown route is not silently treated as landing", () => {
	assert.equal(resolveRoute("/gone").kind, "not-found");
});

test("post-auth redirect accepts only local app paths", () => {
	assert.equal(
		safeNextPath("/app/portfolio?tab=mine"),
		"/app/portfolio?tab=mine",
	);
	assert.equal(
		postAuthPath("?next=/app/library&highlight=alpha&tab=generated"),
		"/app/library?highlight=alpha&tab=generated",
	);
	assert.equal(
		postAuthPath("?next=https://evil.example/app&tab=generated"),
		"/app",
	);
	assert.equal(safeNextPath("https://evil.example/app"), "/app");
	assert.equal(safeNextPath("//evil.example/app"), "/app");
	assert.equal(safeNextPath("/architecture"), "/app");
});

test("feature payload accepts booleans only", () => {
	assert.deepEqual(parseFeatures({ quant: false }, { quant: true }), {
		quant: false,
	});
	assert.deepEqual(parseFeatures({ quant: "false" }, { quant: true }), {
		quant: true,
	});
});

test("quant navigation and direct route share feature result", () => {
	const nav = [{ id: "library" }, { id: "quant" }];
	// Third arg = user. Passing one exercises the authenticated filter; the
	// anonymous default is pinned separately below.
	assert.deepEqual(visibleNavigation(nav, { quant: false }, { id: "u1" }), [
		{ id: "library" },
	]);
	assert.equal(
		resolveRoute("/app/quant", "", { quant: false }).kind,
		"not-found",
	);
	assert.equal(resolveRoute("/app/quant", "", { quant: true }).page, "quant");
});

test("public shell lazy-loads wallet and protected application code", () => {
	const app = readFileSync(new URL("../src/App.jsx", import.meta.url), "utf8");
	const authenticated = readFileSync(
		new URL("../src/AuthenticatedApp.jsx", import.meta.url),
		"utf8",
	);
	assert.match(app, /lazy\(\(\) => import\('\.\/AuthenticatedApp'\)\)/);
	assert.doesNotMatch(app, /from '\.\/config'/);
	assert.match(authenticated, /from ["']\.\/config["']/);
});

test("anonymous browse pages resolve as app routes that allow no session (#1194 rev d)", () => {
	for (const path of ["/app", "/app/explore", "/app/leaderboard", "/app/corpus"]) {
		const route = resolveRoute(path);
		assert.equal(route.kind, "app", path);
		assert.equal(route.anonymousOk, true, path);
	}
	// Strategy DETAIL deep links are anonymous too — a leaderboard row must be
	// openable by the skeptic who clicked it.
	assert.equal(resolveRoute("/app/strategy/alpha").anonymousOk, true);
});

test("auth-required pages stay auth-required", () => {
	for (const path of [
		"/app/generate",
		"/app/library",
		"/app/paper",
		"/app/account",
	]) {
		const route = resolveRoute(path);
		assert.equal(route.kind, "app", path);
		assert.equal(route.anonymousOk, false, path);
	}
	// The hidden surfaces stay auth-required in flag-on builds too.
	for (const path of ["/app/portfolio", "/app/portfolio/vaults/0x123"]) {
		const route = resolveRoute(path, "", ROADMAP_ON);
		assert.equal(route.kind, "app", path);
		assert.equal(route.anonymousOk, false, path);
	}
});

test("roadmap surfaces are hidden by default: flat routes, deep links, nav (#1266)", () => {
	for (const path of [
		"/app/portfolio",
		"/app/marketplace",
		"/app/publish",
		"/app/subscriptions",
		"/app/learnings",
	]) {
		assert.equal(resolveRoute(path).kind, "not-found", path);
	}
	// The #1194 handoff case: a feature-disabled page must NOT stay reachable
	// via its deep link — deepRoutes shares the same featureEnabled() gate.
	assert.equal(
		resolveRoute("/app/marketplace/strategy/alpha").kind,
		"not-found",
	);
	assert.equal(resolveRoute("/app/portfolio/vaults/0x123").kind, "not-found");
	// Nav: Portfolio drops and the Market group empties even for a signed-in
	// user (Layout skips emptied groups, so no bare "Market" header remains).
	const nav = [
		{ id: "portfolio" },
		{ id: "marketplace" },
		{ id: "publish" },
		{ id: "subscriptions" },
		{ id: "learnings" },
		{ id: "library" },
	];
	assert.deepEqual(visibleNavigation(nav, { quant: true }, { id: "u1" }), [
		{ id: "library" },
	]);
	// And the flag-on build restores all of it unchanged.
	assert.equal(resolveRoute("/app/marketplace", "", ROADMAP_ON).page, "marketplace");
	assert.equal(
		resolveRoute("/app/marketplace/strategy/alpha", "", ROADMAP_ON).strategyId,
		"alpha",
	);
	assert.equal(visibleNavigation(nav, ROADMAP_ON, { id: "u1" }).length, 6);
});

test("Library's in-page Published tab hides with the marketplace surface it leads into (#1324)", () => {
	// #1266's dead-door audit only checked route/nav call sites
	// (onNavigate/setPage/navigateToPage); it couldn't see a tab that
	// renders and fetches inline with no route of its own. Strategies.jsx
	// (the Library page, itself NOT a hidden route) must gate its Published
	// tab the same way routes.js gates a hidden page: import the one flag
	// and use it, not a second bespoke check.
	const strategiesSrc = readFileSync(
		new URL("../src/components/Strategies.jsx", import.meta.url),
		"utf8",
	);
	assert.match(
		strategiesSrc,
		/import \{ ROADMAP_SURFACES_ENABLED \} from '\.\.\/featureFlags\.js'/,
	);
	// 1. The fetch must not fire unconditionally — a hidden tab that still
	// calls the hidden API on every Library load is the exact bug filed.
	assert.match(
		strategiesSrc,
		/ROADMAP_SURFACES_ENABLED \? apiGet\('\/api\/marketplace\/my-published'\) : Promise\.resolve\(\[\]\)/,
	);
	// 2. The tab button must not render unconditionally. Anchored directly to
	// the Published button's own attributes (no `[\s\S]*?` gap) — a lazy gap
	// here previously let an unrelated gated button elsewhere in the file
	// satisfy the assertion while the real Published tab stayed ungated.
	// Demonstrated: inserting `{ROADMAP_SURFACES_ENABLED && (<button ...>Publish
	// to marketplace</button>)}` above the tab row while removing the Published
	// tab's own gate passed the old regex and fails this one.
	assert.match(
		strategiesSrc,
		/\{ROADMAP_SURFACES_ENABLED && \(\s*<button\s+type="button"\s+className=\{`tag \$\{activeTab === 'published'/,
	);
	// 3. The tab panel must not render either — belt-and-suspenders against
	// a stale ?tab=published deep link coercing activeTab directly.
	assert.match(
		strategiesSrc,
		/activeTab === 'published' && ROADMAP_SURFACES_ENABLED && \(/,
	);
	// 4. The activeTab deep-link fallback: with the flag off, a stale
	// ?tab=published link must not leave activeTab pinned to 'published' —
	// every panel (generated/examples/now-gated published) would then
	// evaluate false and Library would render an empty content area.
	assert.match(
		strategiesSrc,
		/defaultTab === 'published' && !ROADMAP_SURFACES_ENABLED\) return 'generated'/,
	);
});

test("the strategy passport's back control never signs out an anonymous visitor (#1370)", () => {
	// The passport is deliberately deep-link reachable with no session
	// (#1194 rev d), but 'library' is wallet-gated and not anonymous-OK —
	// resolving the back button straight to onNavigate('library') tripped
	// App.jsx's anonymous-page redirect and bounced a visitor who was never
	// signed in out to /sign-in. The helper must resolve anonymous visitors
	// to a page isAnonymousAppPage() actually allows.
	assert.equal(isAnonymousAppPage(passportBackPage(null)), true);
	// A signed-in visitor keeps going back to their own Library.
	assert.equal(passportBackPage({ id: "u1" }), "library");
	// The button's own label must track where it actually navigates — a
	// control that says "Back to Library" while landing on Explore is a
	// mislabeled affordance, not a fixed one.
	assert.equal(passportBackLabel(null), "← Back to Explore");
	assert.equal(passportBackLabel({ id: "u1" }), "← Back to Library");
});

test("the strategy passport component actually wires the back-navigation helpers at both back-button call sites (#1370)", () => {
	// The test above only proves routes.js's helpers are correct in
	// isolation — it imports nothing from StrategyPassport.jsx, so reverting
	// the component's two `onClick`/label call sites back to the literal
	// regression (`onNavigate("library")` / hard-coded "← Back to Library")
	// would still leave it fully green. Read the component source directly,
	// precedent: this file's own "Library's in-page Published tab..." test
	// above and backend/tests/test_breadcrumbs.py's source-parsing tests.
	const passportSrc = readFileSync(
		new URL("../src/components/StrategyPassport.jsx", import.meta.url),
		"utf8",
	);
	const wiredNavigate = passportSrc.match(
		/onClick=\{\(\) => onNavigate\(passportBackPage\(user\)\)\}/g,
	);
	assert.equal(
		wiredNavigate?.length,
		2,
		`expected both back buttons to call onNavigate(passportBackPage(user)), found ${wiredNavigate?.length ?? 0}`,
	);
	const wiredLabel = passportSrc.match(/\{passportBackLabel\(user\)\}/g);
	assert.equal(
		wiredLabel?.length,
		2,
		`expected both back buttons to render {passportBackLabel(user)}, found ${wiredLabel?.length ?? 0}`,
	);
	assert.doesNotMatch(
		passportSrc,
		/onNavigate\(\s*["']library["']\s*\)/,
		"a back button still hard-codes onNavigate(\"library\") instead of the anonymous-safe helper",
	);
	assert.doesNotMatch(
		passportSrc,
		/←\s*Back to Library\s*</,
		"a back button still hard-codes the Library label instead of passportBackLabel(user)",
	);
});

test("anonymous nav shows browse + the Generate conversion path, nothing stateful", () => {
	const nav = [
		{ id: "explore" },
		{ id: "generate" },
		{ id: "portfolio" },
		{ id: "marketplace" },
		{ id: "account" },
	];
	assert.deepEqual(visibleNavigation(nav, { quant: true }, null), [
		{ id: "explore" },
		{ id: "generate" },
	]);
});

test("canNavigateTo gates on ANON_APP_PAGES for a null user, always true when signed in (#1364)", () => {
	// The onboarding tour's card 3 ('library') and card 4 ('reasoning') are
	// app pages an anonymous visitor may not open (routes.js ANON_APP_PAGES).
	// Before this predicate existed, the tour navigated to them unconditionally
	// and App.jsx's anonymous-app-page guard replaced the whole page with
	// /sign-in — this is the pure check that now gates that navigation.
	assert.equal(canNavigateTo("library", null), false);
	assert.equal(canNavigateTo("reasoning", null), false);
	// 'generate' is in ANON_NAV_IDS (visible in the anon nav, so it measures
	// fine on desktop) but NOT in ANON_APP_PAGES — the exact trap the issue's
	// anti-goal warns against gating on an id allowlist instead of this.
	assert.equal(canNavigateTo("generate", null), false);
	assert.equal(canNavigateTo("explore", null), true);
	assert.equal(canNavigateTo("leaderboard", null), true);
	assert.equal(canNavigateTo("corpus", null), true);
	// Any page is navigable once a user is present — canNavigateTo does not
	// re-derive ANON_APP_PAGES for the signed-in branch.
	assert.equal(canNavigateTo("library", { id: "u1" }), true);
	assert.equal(canNavigateTo("reasoning", { id: "u1" }), true);
	assert.equal(canNavigateTo("generate", { id: "u1" }), true);
});
