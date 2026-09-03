// #1764 — deploy at will, with the verdict of record beside the forward record.
//
// Owner decision (Dan, 2026-09-01): any strategy can be paper-deployed, gate
// pass / fail / pending / degenerate alike, because a rejected strategy that
// performs poorly forward is evidence about the gate's call. The API has no
// rigor precondition on the deploy route and never had one
// (docs/claims-ledger.md: "A failing strategy stays a failing strategy.
// Paper-trading one is allowed. Relabelling one is not.").
//
// What was missing is the other half. /app/paper rendered "+2.10% · total
// return" with nothing on the card saying the gate had rejected the strategy —
// a performance figure standing alone reads as an endorsement. So the law this
// file pins is a single sentence:
//
//   THE PAPER CARD NEVER SHOWS A PERFORMANCE NUMBER WITHOUT THE GATE VERDICT
//   BESIDE IT — INCLUDING WHEN THE PAYLOAD CARRIES NO VERDICT AT ALL.
//
// Two halves, both required:
//   1. the helper has no silent arm (every input yields a rendered verdict);
//   2. the component has no route to the number that bypasses it (one call
//      site, unconditional chip, and one accessible name carrying both).

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
	DEGENERATE_TITLE,
	NOT_GRADED_TITLE,
} from "../src/libraryStatus.js";
import {
	DEPLOY_AT_WILL_NOTE,
	FORWARD_EVIDENCE_NOTE,
	LEGACY_DERIVED_GATE_VERSION,
	LEGACY_DERIVED_NOTE,
	VERDICT_UNAVAILABLE_LABEL,
	formatGradedAt,
	gateVerdict,
	gateVerdictText,
	paperReturnAnnouncement,
} from "../src/paperCopy.js";
import { RIGOR_GATE_STATES } from "../src/rigorGateStatus.js";

const FAILED = {
	total_return: 0.021,
	days: 12,
	rigor_gate_status: "fail",
	graded_at: "2026-08-30T11:22:33",
	gate_version: "gate-v1-deadbeefdeadbeef",
};

// ── 1. The helper has no silent arm ─────────────────────────────────────────

test("gateVerdict returns a rendered verdict for every four-state value", () => {
	for (const status of RIGOR_GATE_STATES) {
		const v = gateVerdict({ ...FAILED, rigor_gate_status: status });
		assert.equal(v.status, status);
		assert.equal(typeof v.label, "string");
		assert.ok(v.label.length > 0, `${status} rendered an empty label`);
		assert.match(v.label, /^Gate: /, `${status} must name WHAT was graded`);
	}
});

test("gateVerdict never returns null or an empty label, for any input", () => {
	for (const dep of [
		undefined,
		null,
		{},
		{ total_return: 0.5, days: 30 },
		{ rigor_gate_status: null },
		{ rigor_gate_status: "" },
		{ rigor_gate_status: "quarantined" },
	]) {
		const v = gateVerdict(dep);
		assert.ok(v && typeof v.label === "string" && v.label.length > 0);
	}
});

test("the exact chip the issue asked for: 'Gate: failed (graded …)' beside the number", () => {
	assert.equal(gateVerdictText(FAILED), "Gate: failed (graded Aug 30, 2026)");
});

test("only a literal pass is ever the positive tone", () => {
	assert.equal(gateVerdict({ rigor_gate_status: "pass" }).tone, "positive");
	for (const status of ["fail", "pending", "degenerate", "quarantined"]) {
		assert.notEqual(
			gateVerdict({ rigor_gate_status: status }).tone,
			"positive",
			`${status} must not be rendered as a pass`,
		);
	}
	assert.notEqual(gateVerdict({}).tone, "positive");
});

test("pending says no gate has graded it — never that it failed", () => {
	const v = gateVerdict({ rigor_gate_status: "pending" });
	assert.match(v.label, /not yet graded/i);
	assert.doesNotMatch(v.label, /fail/i);
	// The tooltip is the Library's sentence, imported rather than re-typed, so
	// the two surfaces cannot start explaining an ungraded row differently.
	assert.ok(v.title.startsWith(NOT_GRADED_TITLE), "the ungraded tooltip is the Library's sentence, not a second one");
});

test("degenerate says unevaluable — neither a failure nor a pending grade", () => {
	const v = gateVerdict({ rigor_gate_status: "degenerate" });
	assert.match(v.label, /unevaluable/i);
	assert.doesNotMatch(v.label, /fail|not yet graded/i);
	assert.ok(v.title.startsWith(DEGENERATE_TITLE), "the degenerate tooltip is the Library's sentence, not a second one");
});

test("the forward-evidence sentence lands on every graded state, exactly once", () => {
	// It is composed in gateVerdict rather than baked into the four constants,
	// so a render site that appended it again would double it — which is how a
	// tooltip becomes something nobody reads.
	for (const status of RIGOR_GATE_STATES) {
		const { title } = gateVerdict({ rigor_gate_status: status });
		assert.equal(title.split(FORWARD_EVIDENCE_NOTE).length - 1, 1, status);
	}
	// Not on the two states that have no verdict to qualify.
	assert.doesNotMatch(gateVerdict({}).title, /Neither re-labels the other/);
});

test("an unrecognised state renders the shared em-dash, never a guessed verdict", () => {
	// The API growing a fifth state must not be silently mapped onto "failed"
	// (or, far worse, onto "passed") by a stale bundle — the #1358 shape.
	const v = gateVerdict({ rigor_gate_status: "quarantined" });
	assert.match(v.label, /—/);
	assert.doesNotMatch(v.label, /passed|failed|not yet graded|unevaluable/i);
	assert.equal(v.tone, "unknown");
});

// ── The load-bearing arm: a payload with NO verdict ──────────────────────────

test("a payload carrying no verdict renders 'verdict unavailable', not silence", () => {
	// The mutation the issue names: drop the verdict from the payload. The card
	// must still say something — an absence rendered as an absence, the same
	// rule formatTotalReturn's day-0 em-dash follows.
	const noVerdict = { ...FAILED };
	delete noVerdict.rigor_gate_status;
	assert.equal(gateVerdictText(noVerdict), VERDICT_UNAVAILABLE_LABEL);
	assert.equal(gateVerdict(noVerdict).tone, "unknown");
});

test("'verdict unavailable' makes no claim about the strategy", () => {
	// It is a statement about the PAYLOAD. Saying "not yet graded" here would
	// assert that no gate ran, which this state cannot support — the gate may
	// well have run and the answer simply did not arrive.
	const v = gateVerdict({ total_return: 0.5, days: 30 });
	assert.doesNotMatch(v.label, /passed|failed|not yet graded|unevaluable/i);
	assert.match(v.title, /payload/i);
	assert.match(
		v.title,
		/not a statement that the strategy passed, failed, or was never graded/i,
	);
});

// ── Provenance: a legacy-derived verdict is not a grade ──────────────────────

test("a legacy-derived verdict says a gate did not produce it", () => {
	const v = gateVerdict({ ...FAILED, gate_version: LEGACY_DERIVED_GATE_VERSION });
	assert.ok(v.title.includes(LEGACY_DERIVED_NOTE));
	// The label is unchanged — the caveat belongs in the explanation, not in a
	// second, competing verdict word.
	assert.equal(v.label, "Gate: failed");
});

test("a real gate_version carries no legacy caveat", () => {
	assert.ok(!gateVerdict(FAILED).title.includes(LEGACY_DERIVED_NOTE));
});

// ── The graded date is never fabricated, and never moves with the reader ─────

test("no graded_at means no parenthetical — never '(graded —)'", () => {
	const v = gateVerdict({ rigor_gate_status: "fail" });
	assert.equal(v.gradedLabel, null);
	assert.equal(gateVerdictText({ rigor_gate_status: "fail" }), "Gate: failed");
});

test("a malformed graded_at degrades to no date, never 'Invalid Date'", () => {
	for (const bad of ["", "not-a-date", "30/08/2026", 20260830, null, undefined]) {
		assert.equal(formatGradedAt(bad), null);
	}
	assert.doesNotMatch(gateVerdictText({ rigor_gate_status: "fail", graded_at: "nope" }), /Invalid Date/);
});

test("graded_at is read as the calendar day it names, in every reader's timezone", () => {
	// `graded_at` is `datetime.isoformat()` of a NAIVE column, so it arrives
	// with no offset. Handing "2026-08-30T00:30:00" to `new Date` reads it as
	// LOCAL time, and rendering that in UTC prints Aug 30 in London and Aug 30
	// in Chicago — but "2026-08-30T23:30:00" prints Aug 31 in Chicago. A grade
	// date that moves with the reader is a fabricated date, so the parser takes
	// the leading YYYY-MM-DD and never constructs a local Date at all.
	const tz = process.env.TZ;
	try {
		for (const zone of ["UTC", "America/Chicago", "Asia/Tokyo"]) {
			process.env.TZ = zone;
			assert.equal(formatGradedAt("2026-08-30T23:30:00"), "Aug 30, 2026", zone);
			assert.equal(formatGradedAt("2026-08-30T00:30:00"), "Aug 30, 2026", zone);
		}
	} finally {
		if (tz === undefined) delete process.env.TZ;
		else process.env.TZ = tz;
	}
});

// ── The number and the verdict reach a screen reader as one utterance ───────

test("paperReturnAnnouncement always carries a gate clause, whatever the payload", () => {
	for (const dep of [
		FAILED,
		{ ...FAILED, rigor_gate_status: "pass" },
		{ total_return: 0.0, days: 0, rigor_gate_status: "pending" },
		{ total_return: 0.5, days: 30 }, // the verdict dropped from the payload
		{},
	]) {
		assert.match(paperReturnAnnouncement(dep), /Gate: /);
	}
});

test("the announcement states the verdict the chip states — one call, one answer", () => {
	assert.ok(paperReturnAnnouncement(FAILED).includes(gateVerdictText(FAILED)));
});

test("the announcement never announces a day-0 ledger as a measured return", () => {
	// formatTotalReturn's discriminator, carried into the accessible name: day 0
	// is the normal state right after deploy, not a measurement of zero.
	const day0 = paperReturnAnnouncement({ total_return: 0.0, days: 0, rigor_gate_status: "fail" });
	assert.match(day0, /No settled paper return yet/);
	assert.doesNotMatch(day0, /0\.00%/);
	assert.match(day0, /Gate: failed/);
});

test("a measured return is announced with its horizon, singular and plural", () => {
	assert.match(paperReturnAnnouncement(FAILED), /\+2\.10% over 12 trading days/);
	assert.match(
		paperReturnAnnouncement({ ...FAILED, days: 1, total_return: -0.004 }),
		/-0\.40% over 1 trading day\./,
	);
});

// ── Copy, against docs/claims-ledger.md ─────────────────────────────────────

test("no copy on this surface promotes a paper return into a verdict", () => {
	const everything = [
		DEPLOY_AT_WILL_NOTE,
		FORWARD_EVIDENCE_NOTE,
		VERDICT_UNAVAILABLE_LABEL,
		LEGACY_DERIVED_NOTE,
		...RIGOR_GATE_STATES.flatMap((status) => {
			const v = gateVerdict({ rigor_gate_status: status, graded_at: "2026-08-30T11:22:33" });
			return [v.label, v.title];
		}),
	].join(" \n ");
	// The banned shapes, from the claims ledger and the issue's own "what does
	// not change" list.
	assert.doesNotMatch(everything, /validat\w*\s+(the\s+)?(vault|strategy)/i);
	assert.doesNotMatch(everything, /validated by paper/i);
	assert.doesNotMatch(everything, /prove[sd]?\s+(the\s+)?strategy/i);
	assert.doesNotMatch(everything, /guarantee/i);
	// And the positive obligation: the surface says the forward record does not
	// re-label the gate.
	assert.match(FORWARD_EVIDENCE_NOTE, /Neither re-labels the other/);
});

test("the deploy-at-will note states the permission AND that no verdict moves", () => {
	assert.match(DEPLOY_AT_WILL_NOTE, /passed, failed, never ran/);
	assert.match(DEPLOY_AT_WILL_NOTE, /Deploying changes no verdict/);
});

// ── 2. The component has no route to the number that bypasses the verdict ────

const paperTrading = readFileSync(
	new URL("../src/components/PaperTrading.jsx", import.meta.url),
	"utf8",
);

test("PaperTrading.jsx imports the verdict helpers from ../paperCopy", () => {
	const block = paperTrading.match(/import \{([^}]*)\} from ['"]\.\.\/paperCopy['"]/);
	assert.ok(block, "expected a named import block from ../paperCopy");
	const imported = new Set(block[1].split(",").map((s) => s.trim()).filter(Boolean));
	for (const name of [
		"DEPLOY_AT_WILL_NOTE",
		"FORWARD_EVIDENCE_NOTE",
		"gateVerdict",
		"gateVerdictText",
		"paperReturnAnnouncement",
	]) {
		assert.ok(imported.has(name), `expected ${name} to be imported from ../paperCopy`);
	}
});

test("there is exactly ONE render of the total-return figure", () => {
	// A second, unguarded call site is how a number gets back onto the page
	// without its verdict. One call site is what makes the adjacency guard below
	// a statement about the whole component rather than about one branch.
	const calls = paperTrading.match(/formatTotalReturn\(/g) || [];
	assert.equal(calls.length, 1);
});

test("the verdict chip is rendered UNCONDITIONALLY beside the figure", () => {
	// Unconditional is the whole point: `gateVerdict` has a state for every
	// payload, so there is no input for which drawing nothing is correct. A
	// `{cond && <GateVerdictChip .../>}` here would reintroduce the bare number
	// on exactly the payloads that need the chip most.
	const chip = paperTrading.match(/^.*<GateVerdictChip\b.*$/m);
	assert.ok(chip, "expected <GateVerdictChip /> to be rendered");
	assert.doesNotMatch(chip[0], /&&|\?/, "the chip must not sit behind a conditional");
});

test("the chip sits between the figure and the live value, on the same card", () => {
	// Adjacency, asserted structurally rather than trusted: the region from the
	// settled figure to the unsettled live value must contain the verdict, so a
	// reader scanning the number meets the verdict before anything else.
	const start = paperTrading.indexOf("formatTotalReturn(dep.total_return, dep.days)");
	const end = paperTrading.indexOf("<LiveValue dep={dep}");
	assert.ok(start > 0 && end > start);
	assert.match(paperTrading.slice(start, end), /<GateVerdictChip dep=\{dep\} \/>/);
});

test("the figure's accessible name is the paired announcement, not the bare percentage", () => {
	assert.match(paperTrading, /<span className="sr-only">\{paperReturnAnnouncement\(dep\)\}<\/span>/);
	assert.match(
		paperTrading,
		/<span aria-hidden="true">\{formatTotalReturn\(dep\.total_return, dep\.days\)\}<\/span>/,
	);
});

test("the deploy-at-will note is rendered as page text, not merely referenced", () => {
	// `title={DEPLOY_AT_WILL_NOTE}` would satisfy a bare name match with the
	// paragraph deleted — the same weakness ui/test/paper-marks.test.js calls
	// out for MARK_BASIS_DISCLOSURE. Assert the closing </p>.
	assert.match(paperTrading, /\{DEPLOY_AT_WILL_NOTE\} \{FORWARD_EVIDENCE_NOTE\}\s*<\/p>/);
});
