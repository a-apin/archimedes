// The Library status pill, and the one field it is allowed to read.
//
// #1747: the Generated tab's green "Live ✓" and its `passes_rigor_gate` came
// from the SAME generation-time blob (`row.rigor_verdict`), so the pill's own
// demotion rule — "status says live, the gate says fail" — could never fire on
// that tab. Twenty-one rows read "Live ✓" beside their own passports reading
// "Reference only — gate failed".
//
// Two kinds of assertion here, deliberately:
//   * REAL BEHAVIOUR, by importing ui/src/libraryStatus.js. That module is plain
//     `.js` precisely so this is possible — `.jsx` is not importable under
//     `node --test` (see ui/test/account-management.test.js).
//   * SOURCE TEXT, for the wiring inside Strategies.jsx / StrategyPassport.jsx,
//     which are `.jsx` and can only be asserted this way (same shape as
//     ui/test/rigor-tristate.test.js).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
	DEGENERATE_LABEL,
	GATE_FAILED_LABEL,
	NOT_GRADED_LABEL,
	statusLabel,
	statusTag,
} from "../src/libraryStatus.js";

const strategies = readFileSync(
	new URL("../src/components/Strategies.jsx", import.meta.url),
	"utf8",
);
const passport = readFileSync(
	new URL("../src/components/StrategyPassport.jsx", import.meta.url),
	"utf8",
);

// ── (a) The four states, as behaviour ──────────────────────────────────────

test("a graded pass on a live row is the only way to green", () => {
	// MUTATION: return 'tag-positive' from any other arm.
	assert.equal(statusTag("live", true, "pass"), "tag-positive");
	assert.equal(statusLabel("live", true, "pass"), "Live");

	for (const [status, passes, gate] of [
		["live", false, "fail"],
		["live", null, "pending"],
		["live", null, null],
		["live", false, "degenerate"],
	]) {
		assert.notEqual(
			statusTag(status, passes, gate),
			"tag-positive",
			`green must be unreachable for (${status}, ${passes}, ${gate})`,
		);
	}
});

test("a failed gate demotes a live row with the shared label", () => {
	// MUTATION: restore the local `status === 'live' && passesRigor === false`
	// pair inside Strategies.jsx, or change either literal.
	assert.equal(statusTag("live", false, "fail"), "tag-muted");
	assert.equal(statusLabel("live", false, "fail"), GATE_FAILED_LABEL);
	assert.equal(GATE_FAILED_LABEL, "Reference only — gate failed");
});

test("an ungraded row says so, and never says it failed", () => {
	// MUTATION: widen the demotion guard to `passesRigor !== true`. Both asserts
	// redden — and that widening is the #1358 defect in mirror image: it asserts
	// a failure for something no gate ever ran on, contradicting the clock icon
	// the very same row renders.
	for (const [passes, gate] of [
		[null, "pending"],
		[null, null],
		[undefined, undefined],
	]) {
		assert.equal(statusTag("live", passes, gate), "tag-muted");
		assert.equal(statusLabel("live", passes, gate), NOT_GRADED_LABEL);
		assert.notEqual(statusLabel("live", passes, gate), GATE_FAILED_LABEL);
	}
	assert.equal(NOT_GRADED_LABEL, "Not yet graded");
});

test("a degenerate row gets its own words, not pending's and not fail's", () => {
	// MUTATION: fold "degenerate" into either neighbouring arm. "Pending" would
	// claim nothing was evaluated (the returns exist, they are just flat);
	// "fail" would claim the strategy was graded and lost.
	assert.equal(statusTag("live", false, "degenerate"), "tag-muted");
	assert.equal(statusLabel("live", false, "degenerate"), DEGENERATE_LABEL);
	assert.notEqual(DEGENERATE_LABEL, NOT_GRADED_LABEL);
	assert.notEqual(DEGENERATE_LABEL, GATE_FAILED_LABEL);
});

test("the curated tab's existing labels are unchanged", () => {
	// CONTROL. MUTATION: reorder the arms so the four-state arms swallow these.
	// The Examples tab serves curated rows with a real verdict and a `validated`
	// / `candidate` status; this change must not touch what they say.
	assert.equal(statusTag("validated", true, "pass"), "tag-accent");
	assert.equal(statusLabel("validated", true, "pass"), "Validated");
	assert.equal(statusLabel("", true, "pass"), "Candidate");
});

// ── (b) The wiring, as source text ─────────────────────────────────────────

test("coerceGenerated no longer reads rigor_verdict for the badge or its numbers", () => {
	// MUTATION: restore any of `verdict?.dsr`, `verdict?.pbo`,
	// `verdict?.oos_sharpe`, `verdict?.dsr_p_value`, or
	// `passes_rigor_gate: verdict ? Boolean(verdict.passing) : null`.
	const coerce = strategies.slice(
		strategies.indexOf("function coerceGenerated(row)"),
	);
	const body = coerce.slice(0, coerce.indexOf("\n}\n"));

	assert.ok(
		!/verdict\?\./.test(body),
		"coerceGenerated must not read any field off the generation-time rigor_verdict blob",
	);
	assert.ok(
		!body.includes("Boolean(verdict.passing)"),
		"the badge must not be coerced from the fusion verdict's `passing`",
	);
	assert.ok(
		body.includes(
			"passes_rigor_gate: typeof row.passes_rigor_gate === 'boolean' ? row.passes_rigor_gate : null",
		),
		"passes_rigor_gate must be taken as a LITERAL boolean, with null preserved as null",
	);
	assert.ok(
		body.includes("rigor_gate_status: row.rigor_gate_status ?? null"),
		"the four-state verdict must be carried through to the pill",
	);
	for (const field of [
		"deflated_sharpe_ratio: row.deflated_sharpe_ratio",
		"pbo_score: row.pbo_score",
		"out_of_sample_sharpe: row.out_of_sample_sharpe",
		"dsr_p_value: row.dsr_p_value",
	]) {
		assert.ok(
			body.includes(field),
			`the rigor numbers must come from the served row, not the fusion blob (missing: ${field})`,
		);
	}
});

test("the pill helpers are imported, not redefined, and get the four-state", () => {
	// MUTATION: re-declare `function statusTag(...)` inside Strategies.jsx, or
	// drop the third argument at either call site.
	assert.ok(
		strategies.includes(
			"import { statusTag, statusLabel } from '../libraryStatus.js'",
		),
		"Strategies.jsx must import the shared helpers",
	);
	assert.ok(
		!/^function statusTag\(/m.test(strategies),
		"Strategies.jsx must not carry its own copy of statusTag",
	);
	assert.ok(
		!/^function statusLabel\(/m.test(strategies),
		"Strategies.jsx must not carry its own copy of statusLabel",
	);

	// BOTH renderings — the desktop table row AND the mobile lib-card. The card
	// has no rigor icon of its own, so its pill is the only verdict signal on a
	// phone; a fix that reached only the table would leave a bare green "Live"
	// there with zero counter-signal.
	const tagCalls = strategies.match(
		/statusTag\(s\.status, s\.passes_rigor_gate, s\.rigor_gate_status\)/g,
	);
	const labelCalls = strategies.match(
		/statusLabel\(s\.status, s\.passes_rigor_gate, s\.rigor_gate_status\)/g,
	);
	assert.equal(tagCalls?.length, 2, "both the table row and the lib-card must pass the four-state");
	assert.equal(labelCalls?.length, 2, "both the table row and the lib-card must pass the four-state");
	assert.ok(
		!/statusTag\(s\.status, s\.passes_rigor_gate\)/.test(strategies),
		"no two-argument call may remain — it would silently drop the four-state",
	);
});

test("the passport imports the demotion label rather than retyping it", () => {
	// MUTATION: put the literal back in StrategyPassport.jsx. The two surfaces
	// render the same words for the same row; as two literals, "byte-identical"
	// was a convention one keystroke could break with nothing to catch it.
	assert.ok(
		passport.includes(
			'import { GATE_FAILED_LABEL } from "../libraryStatus.js"',
		),
		"StrategyPassport.jsx must import the shared label",
	);
	assert.ok(
		!passport.includes('"Reference only — gate failed"'),
		"StrategyPassport.jsx must not carry its own copy of the label literal",
	);
	assert.ok(passport.includes("return GATE_FAILED_LABEL;"));
});

test("the passport's selection-bias comment no longer claims a 404 is expected", () => {
	// The comment said "404 (generated strategy not in the curated cohort) is
	// expected — we fall back to the badge boolean below". That stopped being
	// true when `_generated_strategy_rigor` landed: a generated id now gets a
	// 200 with a live ladder. A comment that describes a seam wrongly is how the
	// seam stays invisible.
	const staleClaim = "is expected — we fall back to the badge boolean below";
	const staleIdx = passport.indexOf(staleClaim);
	if (staleIdx !== -1) {
		// It may appear ONLY as a quotation of what the comment used to say,
		// introduced as such — never as a live claim.
		const quoteIntro = passport.indexOf("The old comment here said");
		assert.ok(
			quoteIntro !== -1 && quoteIntro < staleIdx,
			"the stale claim may only appear quoted as history, never asserted",
		);
		assert.ok(passport.includes("stopped being true"));
	}
	assert.ok(
		passport.includes("the DEPLOY ladder"),
		"the comment must say what the call actually is",
	);
	assert.ok(
		passport.includes("badge is the verdict, the ladder is the deploy check"),
		"the comment must name the seam between the stored badge and the live ladder",
	);
});
