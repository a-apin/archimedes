import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { parseFeatures } from "../src/features.js";
import {
	pageToPath,
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
