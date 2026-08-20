// #1358: the API has served an honest tri/four-state rigor_gate_status
// ("pass"|"fail"|"pending"|"degenerate") since #1184 — but no file under
// ui/src/ read it, so a strategy on which zero statistics were computed
// (pending) rendered as though it had FAILED the rigor gate, on the Library
// table, the deployability chip, and the passport. These are source-text
// assertions (same shape as ui/test/app-visuals.test.js) — no jsdom/vitest,
// per CLAUDE.md's ui/ testing convention.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const strategies = readFileSync(
	new URL("../src/components/Strategies.jsx", import.meta.url),
	"utf8",
);
const passport = readFileSync(
	new URL("../src/components/StrategyPassport.jsx", import.meta.url),
	"utf8",
);

// ── (a) Strategies.jsx: rigor_gate_status === "pending" checked BEFORE the
//        passes_rigor_gate === false "X" (does-not-pass) branch ──────────

test("Strategies.jsx checks rigor_gate_status pending before the does-not-pass X branch", () => {
	const pendingCheckIdx = strategies.indexOf(
		"s.rigor_gate_status === 'pending'",
	);
	const xBranchIdx = strategies.indexOf("s.passes_rigor_gate === false");
	assert.notEqual(pendingCheckIdx, -1, "no rigor_gate_status pending check found");
	assert.notEqual(xBranchIdx, -1, "no passes_rigor_gate === false branch found");
	assert.ok(
		pendingCheckIdx < xBranchIdx,
		"rigor_gate_status pending must be tested before the passes_rigor_gate === false X branch",
	);
	// The rendered icon block itself: isPending must be the FIRST arm of the
	// ternary, with the false-badge X only reachable when NOT pending.
	assert.match(
		strategies,
		/\{isPending \? \(\s*<span[\s\S]{0,300}\) : s\.passes_rigor_gate === true \? \(\s*<span[\s\S]{0,300}\) : s\.passes_rigor_gate === false && \(/,
	);
	// The pending icon must not borrow the "does not pass" wording.
	assert.doesNotMatch(
		strategies,
		/isPending[\s\S]{0,200}Does not pass rigor gate/,
	);
});

// ── (b) DeployabilityChip: pending branch renders neither tag-negative nor
//        the "even at the most permissive level" title ──────────────────

function extractFunction(src, name) {
	const start = src.indexOf(`function ${name}`);
	assert.notEqual(start, -1, `${name} not found in source`);
	// Scoped to the next top-level `function`/`const` declaration after it —
	// wide enough to contain the whole small chip component, narrow enough
	// not to accidentally match unrelated code further down the file.
	const next = src.indexOf("\nconst STATUS_ORDER", start);
	assert.notEqual(next, -1, "could not bound DeployabilityChip's extent");
	return src.slice(start, next);
}

test("DeployabilityChip has a pending branch distinct from the genuine not-deployable branch", () => {
	const chip = extractFunction(strategies, "DeployabilityChip");
	assert.match(chip, /if \(deploy\.pending\) \{/);

	// The pending branch (up to its closing brace) must not render
	// tag-negative and must not carry the "even at the most permissive
	// level" wording that the genuine (never-scored vs. really-fails-every-
	// level) branch below it uses.
	const pendingBranchMatch = chip.match(
		/if \(deploy\.pending\) \{([\s\S]*?)\n\s*\}/,
	);
	assert.ok(pendingBranchMatch, "could not isolate the pending branch body");
	const pendingBranch = pendingBranchMatch[1];
	assert.doesNotMatch(pendingBranch, /tag-negative/);
	assert.doesNotMatch(
		pendingBranch,
		/even at the most permissive level/,
	);

	// The pre-existing "genuinely fails every level" branch is untouched —
	// still renders its own distinct wording, just no longer reachable for a
	// merely-never-scored strategy now that `pending` is checked first.
	assert.match(chip, /even at the most permissive level/);
});

// ── (c) StrategyPassport.jsx: a third, non-tag-positive state for pending ──

test("StrategyPassport renders a third pending state for the rigor badge, not tag-positive", () => {
	assert.match(passport, /const rigorPending = s\.rigor_gate_status === "pending"/);
	// The badge content ternary: passingRigor branch first (unchanged,
	// tag-positive), THEN a distinct rigorPending branch, THEN the genuine
	// "not Verified" fail case — three arms, not two.
	assert.match(
		passport,
		/passingRigor \? \(\s*<>[\s\S]{0,150}Verified[\s\S]{0,80}<\/>\s*\) : rigorPending \? \(\s*"pending — not yet evaluated"\s*\) : \(\s*"not Verified"\s*\)/,
	);
	// The wrapping span's className is keyed ONLY on passingRigor (unchanged)
	// — pending must never earn tag-positive by taking a shortcut through
	// that className expression.
	assert.match(
		passport,
		/className=\{`tag inline-flex items-center gap-1 \$\{passingRigor \? "tag-positive" : "tag-muted"\}`\}/,
	);
});

// ── (d) StrategyPassport.jsx: three-branch num_trials_in_selection ternary,
//        the null/unspecified-scope branch renders neither sentence ──────

test("StrategyPassport num_trials_in_selection ternary has a null/unspecified branch that asserts nothing", () => {
	// The two-branch shape from before #1358 must be gone: an ungraded
	// strategy (num_trials_in_selection == null) used to fall into the
	// `else` arm and unconditionally assert "num_trials = 1" — a statistic
	// nothing computed for it.
	assert.match(
		passport,
		/s\.num_trials_in_selection == null \|\|\s*s\.num_trials_scope === "unspecified" \? \(/,
	);

	// Isolate that first branch's rendered content and confirm it contains
	// NEITHER the num_trials=1 sentence NOR the multi-candidate-correction
	// sentence — the whole point of a third branch.
	const ternaryStart = passport.indexOf(
		's.num_trials_in_selection == null ||',
	);
	assert.notEqual(ternaryStart, -1);
	const firstBranchMatch = passport
		.slice(ternaryStart)
		.match(/\? \(\s*<>([\s\S]*?)<\/>\s*\) : /);
	assert.ok(firstBranchMatch, "could not isolate the null/unspecified branch body");
	const firstBranch = firstBranchMatch[1];
	assert.doesNotMatch(firstBranch, /num_trials = 1/);
	assert.doesNotMatch(firstBranch, /corrects the realized Sharpe/);

	// It must still be a THREE-armed ternary overall (null/unspecified →
	// N>1 real correction → N=1 self-contained), not collapsed back to two.
	assert.match(
		passport,
		/s\.num_trials_in_selection > 1 \? \(\s*<>[\s\S]{0,50}corrects the realized Sharpe/,
	);
	assert.match(
		passport,
		/graded on its own Sharpe \(num_trials = 1/,
	);
});

// ── A1: rigor_gate_status is actually read (mirrors the issue's grep -c) ──

test("rigor_gate_status is read on both surfaces (#1358 A1)", () => {
	assert.ok((strategies.match(/rigor_gate_status/g) || []).length >= 1);
	assert.ok((passport.match(/rigor_gate_status/g) || []).length >= 1);
});
