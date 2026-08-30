import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { signClass } from "../src/signClass.js";

// #1361: the Library table (/app/library) painted CAGR and the "$1k ->"
// projection profit-green *unconditionally* — the sign of the value was
// never consulted. 12 of 34 live strategies had a negative CAGR and still
// rendered green under a column literally headed "$1k ->". The table also
// read `sharpe_ci_95` / `drift_detected`, neither of which any backend
// endpoint has ever produced — so the Sharpe band never rendered in the
// table and the drift triangle could structurally never fire.

const strategies = readFileSync(
	new URL("../src/components/Strategies.jsx", import.meta.url),
	"utf8",
);

// ── signClass: pure sign->CSS-class mapping ────────────────────────────────
// Same shape as changeClass() in AssetModal.jsx / AssetGroupModal.jsx — this
// is the shared module those two inline copies should have been (#1361 lifts
// a would-be third copy into src/signClass.js instead of writing it again).

test("signClass: negative value maps to 'negative'", () => {
	assert.equal(signClass(-0.0432), "negative");
});

test("signClass: positive value maps to 'positive'", () => {
	assert.equal(signClass(0.0432), "positive");
});

test("signClass: null maps to '' (no colour claimed for an unknown value)", () => {
	assert.equal(signClass(null), "");
});

test("signClass: zero maps to 'positive' (matches changeClass's v >= 0 rule)", () => {
	assert.equal(signClass(0), "positive");
});

test("signClass: undefined and NaN also map to ''", () => {
	assert.equal(signClass(undefined), "");
	assert.equal(signClass(NaN), "");
});

// ── Wiring pins on Strategies.jsx ───────────────────────────────────────────
// readFileSync + regex, same pattern as a11y.test.js: the harness has no
// jsdom/testing-library, so JSX wiring is pinned on the source rather than
// on rendered output.

test("Strategies.jsx imports the shared sign helper", () => {
	assert.match(strategies, /import \{ signClass \} from '\.\.\/signClass\.js'/);
});

test("no cell hard-codes profit-green regardless of sign", () => {
	// The three original defect sites, literally: className="mono positive"
	// (desktop CAGR + mobile card) and the inline `color: 'var(--positive)'`
	// on the "$1k ->" cell.
	assert.doesNotMatch(strategies, /"mono positive"/);
	assert.doesNotMatch(strategies, /color: 'var\(--positive\)'/);
	// Both cells must instead derive colour from signClass(...).
	assert.match(strategies, /className=\{`mono \$\{signClass\(s\.cagr\)\}`\}/);
	assert.match(
		strategies,
		/className=\{`mono \$\{signClass\(endValue != null \? endValue - principal : null\)\}`\}/,
	);
});

test("the rigor-gate check icon keeps its literal var(--positive) — it encodes a boolean pass, not a signed number", () => {
	assert.match(
		strategies,
		/aria-label="Passes rigor gate" className="i-lucide-check w-3\.5 h-3\.5 text-\[var\(--positive\)\]"/,
	);
});

test("the dead grid-card export with the same hard-coded-green bug (never mounted) is gone", () => {
	assert.doesNotMatch(strategies, /export function BacktestHorizon/);
});

test("no field the API never sends is read", () => {
	assert.doesNotMatch(strategies, /sharpe_ci_95/);
	assert.doesNotMatch(strategies, /drift_detected/);
	assert.doesNotMatch(strategies, /Drift detected/);
});

test("the Sharpe CI band is wired to the real sharpe_ci_lower / sharpe_ci_upper pair", () => {
	assert.match(
		strategies,
		/s\.sharpe_ci_lower != null && s\.sharpe_ci_upper != null \? \[s\.sharpe_ci_lower, s\.sharpe_ci_upper\] : null/,
	);
	// coerceGenerated (the generated-row mapper) reports the real fields as
	// honestly null rather than continuing to emit the fabricated ones.
	assert.match(strategies, /sharpe_ci_lower: null,/);
	assert.match(strategies, /sharpe_ci_upper: null,/);
});

test("the '$1k ->' cell carries a text alternative for a loss, not colour alone (1.4.1)", () => {
	assert.match(
		strategies,
		/endValue != null && endValue < principal && \(\s*<span className="sr-only">/,
	);
});
