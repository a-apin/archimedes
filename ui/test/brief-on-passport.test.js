import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

// Static-source pins for the "Your brief" card (v8 Lane 3.3), same pattern as
// ui/test/generation-cost.test.js's wiring tests — there is no jsdom render
// harness in this suite, so the contract is pinned against the component's
// own source text.

const passport = readFileSync(
	new URL("../src/components/StrategyPassport.jsx", import.meta.url),
	"utf8",
);

test("StrategyPassport.jsx renders the user's brief only when non-empty", () => {
	assert.match(passport, /\{s\.brief_intent\s*&&\s*\(/);
	assert.match(passport, />\s*Your brief\s*</);
	assert.match(passport, /\{s\.brief_intent\}/);
});

test("StrategyPassport.jsx does not render the brief unconditionally", () => {
	// Guard against a regression that hoists the brief card above its `&&`
	// guard (e.g. moving the JSX but leaving the condition behind) — the
	// label must appear strictly after a brief_intent truthiness check, not
	// as a bare unconditional block.
	//
	// Anchored on the RENDERED label (`>Your brief<`), not on the first
	// occurrence of the words anywhere in the file: the explanatory comment
	// above the card also names the copy, and a plain indexOf would find that
	// instead and then look for a guard that legitimately sits after it.
	const rendered = /> *Your brief *</.exec(passport);
	assert.notEqual(rendered, null, "expected a rendered 'Your brief' label in the JSX");
	const label = rendered.index;
	const guard = passport.lastIndexOf("s.brief_intent &&", label);
	assert.notEqual(guard, -1, "expected a `s.brief_intent &&` guard before the label");
	// Nothing between the guard's `(` and the label must close that
	// conditional early (a stray `)}` would end-run the guard).
	const between = passport.slice(guard, label);
	assert.doesNotMatch(between, /\)\}/);
});
