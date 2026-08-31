import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import test from "node:test";

import {
	DEFAULT_CHAIN_ID,
	chainIdToHex,
	resolveChainId,
} from "../src/chain-config.js";

// #1240 — the frontend must stop writing the chain id as a literal, so the Arc
// mainnet cutover is a build-time variable rather than a seven-file edit.
//
// This seam existed once: ui/src/siwe.js held `VITE_ARC_CHAIN_ID ?? '5042002'`
// from 7415b245 (2026-06-13) until 95c9faf7 (2026-07-28) deleted the file and
// the seam with it. The literals it left behind are what this module removes,
// and the scan at the bottom is what stops them coming back.

const SRC = new URL("../src/", import.meta.url).pathname;

function sourceFiles(dir) {
	const out = [];
	for (const entry of readdirSync(dir)) {
		const full = path.join(dir, entry);
		if (statSync(full).isDirectory()) {
			out.push(...sourceFiles(full));
		} else if (/\.(js|jsx)$/.test(entry)) {
			out.push(full);
		}
	}
	return out;
}

/** Strip comments so a documentary mention of the id is not read as a hardcode. */
function stripComments(source) {
	return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
}

test("an unset environment resolves to the Arc testnet default", () => {
	assert.equal(resolveChainId(undefined), DEFAULT_CHAIN_ID);
	assert.equal(resolveChainId(null), DEFAULT_CHAIN_ID);
	assert.equal(DEFAULT_CHAIN_ID, 5042002);
});

test("a blank or whitespace value is the same as unset", () => {
	// `.env` files ship variables blank, so this is the copied-template case,
	// not a hypothetical. Fails against a bare Number() coercion, which turns
	// "" into 0 and would hand every caller chain 0.
	assert.equal(resolveChainId(""), DEFAULT_CHAIN_ID);
	assert.equal(resolveChainId("   "), DEFAULT_CHAIN_ID);
});

test("an unset or blank value resolves silently, without console noise", () => {
	// `.env` ships these variables blank, so blank is the NORMAL case, not an
	// error one. Reaching the malformed-value branch would log to console.error
	// on every page load of an ordinary build. Fails against removing the
	// `text === ""` early return, which is otherwise behaviourally invisible
	// because the regex below also rejects "".
	const original = console.error;
	const calls = [];
	console.error = (...args) => calls.push(args);
	try {
		resolveChainId("");
		resolveChainId("   ");
		resolveChainId(undefined);
	} finally {
		console.error = original;
	}
	assert.deepEqual(calls, [], "a blank value is unset, not malformed");
});

test("a malformed value is reported, not swallowed", () => {
	// The other half: a value someone actually typed wrong must be visible.
	const original = console.error;
	const calls = [];
	console.error = (...args) => calls.push(args);
	try {
		resolveChainId("mainnet");
	} finally {
		console.error = original;
	}
	assert.equal(calls.length, 1, "a malformed chain id must reach the console");
	assert.match(String(calls[0][0]), /malformed chain id/);
});

test("a real value is read, as a number", () => {
	assert.equal(resolveChainId("31337"), 31337);
	assert.equal(resolveChainId(" 42 "), 42);
	assert.equal(resolveChainId(1), 1);
});

test("a malformed value falls back rather than producing NaN", () => {
	// Fails against `Number(raw)`, which yields NaN here and would silently
	// give every downstream call a chain id that equals nothing, including
	// itself. Falling back is safe ONLY because the default is testnet.
	assert.equal(resolveChainId("mainnet"), DEFAULT_CHAIN_ID);
	assert.equal(resolveChainId("12abc"), DEFAULT_CHAIN_ID);
	assert.equal(resolveChainId("-1"), DEFAULT_CHAIN_ID);
	assert.equal(resolveChainId("1e3"), DEFAULT_CHAIN_ID);
});

test("hex notation is refused rather than half-accepted", () => {
	// Number('0x4cef52') is 5042002, so a bare Number() would accept hex here
	// and quietly work — until someone writes '0x1' meaning chain 1 and gets
	// it, or writes a decimal elsewhere and the two notations diverge. One
	// notation, enforced.
	assert.equal(resolveChainId("0x4cef52"), DEFAULT_CHAIN_ID);
});

test("an explicit fallback is honoured, so payments can default to execution", () => {
	// This is how PAYMENTS_CHAIN_ID defaults to EXECUTION_CHAIN_ID.
	assert.equal(resolveChainId(undefined, 31337), 31337);
	assert.equal(resolveChainId("", 31337), 31337);
	assert.equal(resolveChainId("999", 31337), 999);
});

test("the switch-chain hex is derived from the id", () => {
	// The old code carried '0x4cef52' as its own literal beside the decimal
	// one. Fails against reintroducing a written hex constant, because this
	// asserts the derivation for ids that are not Arc's.
	assert.equal(chainIdToHex(5042002), "0x4cef52");
	assert.equal(chainIdToHex(1), "0x1");
	assert.equal(chainIdToHex(31337), "0x7a69");
});

test("no source file outside chain-config.js writes the chain id as a literal", () => {
	// The regression guard. Every one of these was a literal before #1240:
	// config.js (twice, decimal and hex), circle-wallet.js, linked-wallets.js,
	// payment-session.js, api.js, AuthenticatedApp.jsx.
	const offenders = [];
	for (const file of sourceFiles(SRC)) {
		if (file.endsWith("chain-config.js")) continue;
		const code = stripComments(readFileSync(file, "utf8"));
		if (/\b5042002\b/.test(code) || /0x4cef52/i.test(code)) {
			offenders.push(path.relative(SRC, file));
		}
	}
	assert.deepEqual(
		offenders,
		[],
		`these files hardcode the chain id instead of importing it from chain-config.js: ${offenders.join(", ")}`,
	);
});

test("chain-config.js holds exactly one decimal default", () => {
	// Anti-vacuity for the scan above: if the SSOT itself stopped carrying the
	// value, the scan would pass while nothing defined the chain at all.
	const source = stripComments(readFileSync(path.join(SRC, "chain-config.js"), "utf8"));
	const matches = source.match(/\b5042002\b/g) ?? [];
	assert.equal(matches.length, 1, "the default belongs in exactly one place");
});
