import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { CHAIN_STATUS, deriveChainStatus } from "../src/chainStatus.js";
import {
	_resetHealthCache,
	getCachedHealth,
	HEALTH_TTL_MS,
} from "../src/healthCache.js";

// ── deriveChainStatus: the tri-state pill logic (#1321) ────────────────────
// The bug: the footer pill's label was `Object.keys(NEW_CONTRACTS).length ?
// "Arc · Testnet live" : "Arc · Connecting"` — NEW_CONTRACTS is a static
// object literal with 7 hard-coded addresses, so the count is the constant
// 7 and the "problem" branch was unreachable dead code. During the
// 2026-08-19/20 backend OOM crash loop the pill said "live" the entire
// time the site served 502/503.
//
// These tests render (i.e. compute the derived view for) each of the three
// states off the REAL runtime signal (/health's `chain_connected`), not a
// build-time constant, and assert the label text for each.

test("chain status: connected renders the live label", () => {
	const result = deriveChainStatus({ chain_connected: true }, false);
	assert.equal(result.tone, CHAIN_STATUS.CONNECTED);
	assert.equal(result.label, "Arc · Testnet live");
});

test("chain status: disconnected renders a distinct, non-live label — THE regression this issue is about", () => {
	// This is the case that was structurally unreachable before the fix:
	// chain_connected: false must NOT fall through to the "live" label.
	const result = deriveChainStatus({ chain_connected: false }, false);
	assert.equal(result.tone, CHAIN_STATUS.DISCONNECTED);
	assert.equal(result.label, "Arc · Chain disconnected");
	assert.notEqual(result.label, "Arc · Testnet live");
});

test("chain status: a failed /health fetch renders unknown, never live — mutation-check target", () => {
	// Mutation-check (CLAUDE.md § "Before you approve a merge", rule 4): force
	// the health fetch to fail (healthError=true, health=null, exactly what
	// Layout.jsx's apiGet(...).catch(() => setHealthError(true)) produces) and
	// confirm the assertion that would have read "live" now FAILS instead.
	// Verified manually against pre-fix Layout.jsx (blockLabel derived from
	// NEW_CONTRACTS, ignoring /health entirely) by stashing this fix and
	// re-running — see PR body for the transcript.
	const result = deriveChainStatus(null, true);
	assert.equal(result.tone, CHAIN_STATUS.UNKNOWN);
	assert.equal(result.label, "Arc · Status unknown");
	assert.notEqual(result.tone, CHAIN_STATUS.CONNECTED);
	assert.notEqual(result.label, "Arc · Testnet live");
});

test("chain status: the pre-resolution window (health not yet loaded, no error yet) is unknown, not live", () => {
	const result = deriveChainStatus(null, false);
	assert.equal(result.tone, CHAIN_STATUS.UNKNOWN);
	assert.equal(result.label, "Arc · Status unknown");
});

test("chain status: a malformed /health body (missing or non-boolean chain_connected) is unknown, not trusted as live", () => {
	assert.equal(deriveChainStatus({}, false).tone, CHAIN_STATUS.UNKNOWN);
	assert.equal(
		deriveChainStatus({ chain_connected: "true" }, false).tone,
		CHAIN_STATUS.UNKNOWN,
	);
	assert.equal(deriveChainStatus(undefined, false).tone, CHAIN_STATUS.UNKNOWN);
});

// ── getCachedHealth: shared TTL cache backing fetchHealth (#1333 review) ───
// The bug: Layout.jsx, Architecture.jsx, and ModelCostPanel.jsx each called
// apiGet("/health") independently. Once Layout's effect re-fetches on every
// in-app navigation (above), landing on /architecture fired /health twice
// in the same render pass, against an endpoint that does an Arc RPC
// round-trip plus several DB reads. fetchHealth() (../src/health.js) wraps
// this cache with the real apiGet; the cache logic itself lives in
// ../src/healthCache.js (zero imports, so it's real-imported and exercised
// directly here rather than only wiring-checked via readFileSync).

test("getCachedHealth: a second call within the TTL window reuses the first call's promise instead of fetching again", async () => {
	_resetHealthCache();
	let calls = 0;
	const fetcher = async () => {
		calls += 1;
		return { chain_connected: true };
	};
	const p1 = getCachedHealth(fetcher, 1_000);
	const p2 = getCachedHealth(fetcher, 1_000 + HEALTH_TTL_MS - 1);
	assert.equal(p1, p2, "the second call should return the exact same promise, not a new fetch");
	await p1;
	assert.equal(calls, 1, "fetcher should have been invoked exactly once");
});

test("getCachedHealth: a call at/after the TTL window fetches again", async () => {
	_resetHealthCache();
	let calls = 0;
	const fetcher = async () => {
		calls += 1;
		return { chain_connected: true };
	};
	await getCachedHealth(fetcher, 1_000);
	await getCachedHealth(fetcher, 1_000 + HEALTH_TTL_MS);
	assert.equal(calls, 2, "the TTL boundary must not extend the cache indefinitely");
});

test("getCachedHealth: a failed fetch is not cached — the very next call retries rather than reusing the rejection", async () => {
	// Mutation-check target: if the .catch() didn't clear cachedPromise/
	// cachedAt, this second call would return the same rejected promise and
	// `calls` would stay at 1 — an outage would pin every caller (including
	// Layout's next navigation) on the same failure for the rest of the TTL
	// window instead of getting a fresh attempt.
	_resetHealthCache();
	let calls = 0;
	const failingFetcher = async () => {
		calls += 1;
		throw new Error("network down");
	};
	await assert.rejects(getCachedHealth(failingFetcher, 1_000));
	const okFetcher = async () => {
		calls += 1;
		return { chain_connected: true };
	};
	await getCachedHealth(okFetcher, 1_001);
	assert.equal(calls, 2, "the call after a failure must re-invoke the fetcher, not reuse the rejection");
});

// ── Wiring: Layout.jsx reads the real /health signal, not NEW_CONTRACTS ────

const layout = readFileSync(
	new URL("../src/components/Layout.jsx", import.meta.url),
	"utf8",
);

test("Layout.jsx: the footer pill no longer derives from NEW_CONTRACTS (acceptance criterion #1)", () => {
	// The exact grep from the issue: no match at all, since NEW_CONTRACTS had
	// no other use in this file besides the dead-code blockLabel assignment.
	assert.doesNotMatch(layout, /NEW_CONTRACTS/);
});

test("Layout.jsx: the pill is driven by deriveChainStatus off a bounded /health re-fetch, not a new polling loop", () => {
	assert.match(layout, /from ["']\.\.\/chainStatus["']/);
	assert.match(layout, /deriveChainStatus\(health, healthError\)/);
	assert.match(layout, /fetchHealth\(\)/);
	// Anti-goal: no new polling loop — the effect only re-runs on an actual
	// in-app navigation (dep: `page`), never on a timer.
	assert.doesNotMatch(layout, /setInterval/);
	// #1333 review: an empty dep array here meant Layout — reconciled, not
	// remounted, across route changes (AuthenticatedApp.jsx renders it with
	// no `key`) — fetched /health exactly once per session, so an outage
	// beginning after mount stayed invisible for the rest of the session.
	// The effect must re-run per navigation instead.
	assert.match(layout, /\}, \[page\]\);/);
});

test("Layout.jsx: a successful re-fetch clears a prior healthError, so recovery after an outage is reachable", () => {
	// #1333 review follow-up: re-fetching on navigation is pointless if a
	// single earlier failure pins healthError=true for the rest of the
	// session — deriveChainStatus treats healthError as sticky-unknown
	// regardless of what a later successful fetch reports.
	const successBlock = layout.match(
		/fetchHealth\(\)\s*\.then\(\(d\) => \{([\s\S]*?)\}\)\s*\.catch/,
	);
	assert.ok(successBlock, "fetchHealth().then(...) block not found");
	assert.match(successBlock[1], /setHealth\(d\)/);
	assert.match(successBlock[1], /setHealthError\(false\)/);
});

test("Layout.jsx: the dot and label both carry the derived tone, so unknown/disconnected are visually distinct from live", () => {
	assert.match(layout, /live-dot live-dot-\$\{chainStatus\.tone\}/);
	assert.match(layout, /\{chainStatus\.label\}/);
});

// ── config.js: NEW_CONTRACTS itself is untouched (acceptance criterion #3) ─

const config = readFileSync(
	new URL("../src/config.js", import.meta.url),
	"utf8",
);

test("config.js: NEW_CONTRACTS still has its 7 hard-coded addresses — this issue is about the label, not the address book", () => {
	assert.match(config, /export const NEW_CONTRACTS = \{/);
	const match = config.match(/export const NEW_CONTRACTS = \{([^}]*)\}/s);
	assert.ok(match, "NEW_CONTRACTS block not found");
	const keyCount = (match[1].match(/^\s*\w+:/gm) || []).length;
	assert.equal(keyCount, 7);
});

// ── Wiring: every /health caller shares fetchHealth's cache (#1333 review) ─
// Layout.jsx's per-navigation re-fetch (above) turned /health into a
// per-navigation call from the always-mounted shell. Architecture.jsx and
// ModelCostPanel.jsx also read /health, each on their own mount effect — if
// either called apiGet("/health") directly instead of the shared
// fetchHealth(), landing on their page would fire a second/third
// independent Arc RPC round-trip + DB read in the same render pass that
// Layout's effect just triggered.

const architecture = readFileSync(
	new URL("../src/components/Architecture.jsx", import.meta.url),
	"utf8",
);
const modelCostPanel = readFileSync(
	new URL("../src/components/ModelCostPanel.jsx", import.meta.url),
	"utf8",
);
// CorpusKG.jsx (#1368 second adversarial review): a fifth caller found
// making the exact direct apiGet("/health") mistake this test exists to
// forbid, on a branch that predated this file (it was 34 commits behind
// `main` when the mistake was introduced, so this guard did not yet exist
// to catch it). Added to the loop below rather than left unguarded.
const corpusKg = readFileSync(
	new URL("../src/components/CorpusKG.jsx", import.meta.url),
	"utf8",
);

const health = readFileSync(
	new URL("../src/health.js", import.meta.url),
	"utf8",
);

test("health.js: fetchHealth() delegates to the shared getCachedHealth cache with the real apiGet, not its own logic", () => {
	assert.match(health, /from ["']\.\/api["']/);
	assert.match(health, /from ["']\.\/healthCache\.js["']/);
	assert.match(health, /getCachedHealth\(apiGet\)/);
});

test("Layout.jsx / Architecture.jsx / ModelCostPanel.jsx / CorpusKG.jsx: /health is read through the shared fetchHealth cache, not independent apiGet('/health') calls", () => {
	for (const [name, src] of [
		["Layout.jsx", layout],
		["Architecture.jsx", architecture],
		["ModelCostPanel.jsx", modelCostPanel],
		["CorpusKG.jsx", corpusKg],
	]) {
		assert.doesNotMatch(
			src,
			/apiGet\(["']\/health["']\)/,
			`${name} calls apiGet("/health") directly instead of the shared fetchHealth() — this reintroduces a duplicate /health fetch`,
		);
		assert.match(
			src,
			/from ["']\.\.\/health["']/,
			`${name} does not import from ../health`,
		);
		assert.match(
			src,
			/fetchHealth\(\)/,
			`${name} does not call the shared fetchHealth()`,
		);
	}
});

// ── App.css: the tone rules must stay reachable (#1333 review) ─────────────
// The CSS half of the original fix had no guard: the review that landed
// alongside this test caught three tone rules shipping as unreachable
// bare `.live-dot-*` selectors (0,1,0) — lower specificity than the
// existing `.app-site .live-dot { ... }` base rule, so they sat dead in the
// sheet regardless of source order (see the "Tri-state chain-status pill"
// comment in App.css). A future edit to that block could silently
// reintroduce the same defect with nothing here to catch it.

const css = readFileSync(new URL("../src/App.css", import.meta.url), "utf8");

test("App.css: all three chain-status tone rules are scoped under .app-site, not bare base-scope rules", () => {
	for (const tone of ["connected", "disconnected", "unknown"]) {
		assert.match(
			css,
			new RegExp(`\\.app-site \\.live-dot-${tone}\\s*\\{`),
			`.app-site .live-dot-${tone} rule not found`,
		);
	}
	// Mutation-check target: a bare rule at the start of a line is the exact
	// unreachable shape the #1333 review caught — (0,1,0) specificity, always
	// beaten by the `.app-site .live-dot { ... }` base rule above, so it
	// would sit dead in the sheet however it's added back.
	assert.doesNotMatch(
		css,
		/^\.live-dot-(connected|disconnected|unknown)\s*\{/m,
		"a bare, unscoped .live-dot-* rule is unreachable — lower specificity than the .app-site base rule",
	);
});
