import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { CHAIN_STATUS, deriveChainStatus } from "../src/chainStatus.js";

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

test("Layout.jsx: the pill is driven by deriveChainStatus off a single /health fetch, not a new polling loop", () => {
	assert.match(layout, /from ["']\.\.\/chainStatus["']/);
	assert.match(layout, /deriveChainStatus\(health, healthError\)/);
	assert.match(layout, /apiGet\(["']\/health["']\)/);
	// Anti-goal: no new polling loop — a single effect-on-mount fetch only.
	assert.doesNotMatch(layout, /setInterval/);
	assert.match(layout, /\}, \[\]\);/); // the health-fetch effect has an empty dep array
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
