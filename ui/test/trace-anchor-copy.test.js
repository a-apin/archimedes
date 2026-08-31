// Claim-integrity guard for Portfolio's trace anchor-state copy (#714).
//
// #714 retired the v1 `publishTrace` fallbacks from the agent tick. The
// SKIP/error path (`_publish_trace`) now makes no chain call at all and
// persists `arc_tx_hash: None` / `is_verified: False` permanently, by design:
// with no trade there is no tradeId for `commit()` to bind, so no registry
// write is ever attempted for a skip trace.
//
// Portfolio.jsx rendered every `is_verified === false` trace as "anchor
// pending — registry write didn't complete yet — usually transient". For a
// skip that is now false in both halves: nothing is pending, and nothing is
// transient. The agent ticks emit skips continuously while pools are thin, so
// this is the *common* row, not an edge case — the flagship portfolio page
// would promise an anchor that is never coming.
//
// Same idiom as oracle-copy.test.js / roadmap-copy.test.js: a raw source-text
// scan (readFileSync, no JSX parsing) with anti-vacuity coverage — the
// predicate is run against the exact pre-#714 branch shape and must reject it,
// so a pin that stops discriminating fails loudly instead of guarding nothing.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

function repoFile(rel) {
	return new URL(`../${rel}`, import.meta.url);
}

const portfolio = readFileSync(repoFile("src/components/Portfolio.jsx"), "utf8");

// The anchor-state ternary, sliced from its `is_verified` discriminator up to
// the "anchor pending" label. Anything the skip case needs in order NOT to
// reach that label has to live inside this slice — which is what makes the
// check structural (branch ordering) rather than "the string exists somewhere
// in a 900-line file".
function anchorStateSlice(src) {
	const start = src.indexOf("{t.is_verified ? (");
	assert.ok(start !== -1, "no is_verified anchor-state branch found in Portfolio.jsx");
	const end = src.indexOf("anchor pending", start);
	assert.ok(end !== -1, "no 'anchor pending' label found after the anchor-state branch");
	return src.slice(start, end);
}

// True when a skip trace is peeled off BEFORE the pending fallback — i.e. the
// pending copy is unreachable for `decision_type === "skip"`.
function skipBranchedBeforePending(src) {
	return /t\.decision_type === "skip"/.test(anchorStateSlice(src));
}

//: The exact branch shape Portfolio.jsx carried before #714 (whitespace
//: normalised — every predicate above is indentation-independent). Every
//: assertion that passes on the live source is re-run against this to prove it
//: discriminates; see test_the_pin_rejects_the_pre_714_branch below.
const PRE_714_ANCHOR_STATE = `{t.is_verified ? (
	<span className="flex items-center gap-1 text-[var(--positive)]">
		<span className="i-lucide-check-circle w-3.5 h-3.5" /> anchored on Arc
	</span>
) : (
	<span
		className="flex items-center gap-1 text-[var(--text-3)]"
		title="Trace hashed + persisted off-chain; on-chain anchor pending (registry write didn't complete yet — usually transient)."
	>
		<span className="i-lucide-clock w-3.5 h-3.5" /> anchor pending
	</span>
)}`;

test("a skip trace never falls through to the 'anchor pending' copy", () => {
	assert.ok(
		skipBranchedBeforePending(portfolio),
		"the anchor-state ternary must peel off decision_type === 'skip' before the pending fallback",
	);
});

test("the skip branch states the honest reason, not a failure or a pending write", () => {
	const slice = anchorStateSlice(portfolio);
	assert.ok(
		slice.includes("not anchored (no trade to bind)"),
		"the skip branch must name the real reason: there is no trade for a commitment to bind",
	);
	// The reason must survive a hover, not just the label: the title is the only
	// place with room to say *why* nothing is anchored.
	assert.ok(
		/title="No trade was made[^"]*no anchor is attempted or pending\."/.test(slice),
		"the skip branch's title must say no anchor is attempted or pending",
	);
});

test("the skip branch borrows neither the verified affordance nor the pending one", () => {
	const slice = anchorStateSlice(portfolio);
	const skipStart = slice.indexOf('t.decision_type === "skip"');
	assert.ok(skipStart !== -1, "no skip branch to inspect");
	// Bound the branch at the `) : (` that opens the pending fallback, or the
	// assertions below would read the fallback's own copy and never fail.
	const skipEnd = slice.indexOf(") : (", skipStart);
	assert.ok(skipEnd !== -1, "could not find the end of the skip branch");
	const skipBranch = slice.slice(skipStart, skipEnd);
	// The claim under test is the RENDERED copy; the code comments legitimately
	// quote the pending wording to explain why it is wrong here, so strip them.
	const rendered = skipBranch.replace(/^\s*\/\/.*$/gm, "");

	// Not a success: no green check / positive styling for a trace with no anchor.
	assert.ok(
		!rendered.includes("i-lucide-check-circle"),
		"the skip branch must not reuse the anchored-on-Arc check icon",
	);
	assert.ok(
		!rendered.includes("--positive"),
		"the skip branch must not reuse the anchored-on-Arc positive styling",
	);
	// Not a failure and not a wait: no clock, and none of the pending branch's
	// affirmative claims about a write that is still coming.
	assert.ok(
		!rendered.includes("i-lucide-clock"),
		"the skip branch must not reuse the pending clock icon — nothing is being waited on",
	);
	assert.ok(
		!rendered.includes("anchor pending"),
		"the skip branch must not carry the pending label",
	);
	assert.ok(
		!/registry write didn't complete|\btransient\b|\bfailed\b/i.test(rendered),
		"the skip branch must not imply an incomplete or failed registry write",
	);
});

test("the pending copy survives for the states it is still true of (anti-goal)", () => {
	// #714 did not make "anchor pending" false everywhere. `is_verified` is
	// `reveal_tx is not None` (agent_runner._reveal_trace), so a rebalance that
	// committed but whose reveal has not landed persists unverified with a real
	// commit_tx_hash — genuinely pending, and #1276's reveal reconciliation
	// retries it on a later tick. Widening this fix past `decision_type ===
	// "skip"` would deny an anchor that really is still coming.
	assert.ok(
		portfolio.includes("anchor pending"),
		"the pending copy must remain for non-skip traces that really are awaiting a write",
	);
	assert.ok(
		portfolio.includes("i-lucide-clock w-3.5 h-3.5"),
		"the pending clock affordance must remain for the states it still describes",
	);
});

// ── Anti-vacuity ─────────────────────────────────────────────────────────
//
// The pin above is only worth anything if it rejects the code it was written
// against. Run the same predicate over the pre-#714 branch shape.

test("the pin rejects the pre-#714 branch, where every unverified trace read as pending", () => {
	assert.equal(
		skipBranchedBeforePending(PRE_714_ANCHOR_STATE),
		false,
		"the pre-#714 branch routed skips straight to 'anchor pending' — the pin must reject it",
	);
	assert.ok(
		!PRE_714_ANCHOR_STATE.includes("not anchored (no trade to bind)"),
		"the honest skip copy did not exist before #714 — the pin must not match it vacuously",
	);
});
