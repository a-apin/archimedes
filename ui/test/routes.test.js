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

test("landing and architecture remain public", () => {
	assert.deepEqual(resolveRoute("/").kind, "public");
	assert.deepEqual(resolveRoute("/architecture").kind, "public");
});

test("account routes remain public", () => {
	assert.equal(resolveRoute("/sign-in").kind, "auth");
	assert.equal(resolveRoute("/sign-up").kind, "auth");
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
	assert.equal(
		resolveRoute("/app/portfolio/vaults/0x123").vaultAddress,
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
		"/app/portfolio",
		"/app/account",
		"/app/portfolio/vaults/0x123",
	]) {
		const route = resolveRoute(path);
		assert.equal(route.kind, "app", path);
		assert.equal(route.anonymousOk, false, path);
	}
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
