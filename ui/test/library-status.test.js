// #1747: the Library's Generated tab rendered "Live ✓" on strategies whose own
// passport says "Reference only — gate failed".
//
// The cause was that both halves of the badge came from the same generation-time
// blob: the backend wrote StrategyRecord.status="live" and
// rigor_verdict.passing=true on one write from the FUSION verdict, and
// coerceGenerated read `passes_rigor_gate` back out of that same blob. The pill's
// demotion arm (status "live" AND the gate failed) could therefore never fire —
// the two inputs were one fact.
//
// These tests are of two kinds, on purpose:
//   * EXECUTED — statusTag/statusLabel now live in a plain module
//     (ui/src/libraryStatus.js) precisely so the rule can be RUN. It is a claim
//     about a strategy; reading the source and hoping is not a check.
//   * SOURCE-TEXT — for the wiring inside Strategies.jsx, which `node --test`
//     cannot import (`.jsx` is not importable here; see
//     ui/test/account-management.test.js). Same shape as
//     ui/test/rigor-tristate.test.js.
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

// ── The four states ────────────────────────────────────────────────────────

test("pass: a gate that ran and passed is the only green pill", () => {
	assert.equal(statusTag("live", true, "pass"), "tag-positive");
	assert.equal(statusLabel("live", true, "pass"), "Live");
});

test("fail: a gate that ran and failed is muted, and says so", () => {
	assert.equal(statusTag("live", false, "fail"), "tag-muted");
	assert.equal(statusLabel("live", false, "fail"), GATE_FAILED_LABEL);
});

test("pending: nothing graded this row — not green, and NOT accused of failing", () => {
	assert.equal(statusTag("live", null, "pending"), "tag-muted");
	assert.equal(statusLabel("live", null, "pending"), NOT_GRADED_LABEL);
	assert.notEqual(
		statusLabel("live", null, "pending"),
		GATE_FAILED_LABEL,
		"a row the gate never ran on must not be labelled as having failed it — " +
			"Strategies.jsx renders the clock 'Not yet evaluated' on this same row",
	);
});

test("degenerate: evaluated, and unevaluable — its own sentence", () => {
	assert.equal(statusTag("live", false, "degenerate"), "tag-muted");
	assert.equal(statusLabel("live", false, "degenerate"), DEGENERATE_LABEL);
});

test("a row with no verdict at all is not green", () => {
	// The gate-free seed shape: StrategyRecord rows written with status "live"
	// and no rigor verdict of any kind (backend/archimedes/main.py seeds curated
	// examples this way). Before #1747 this rendered a green "Live".
	assert.equal(statusTag("live", null, null), "tag-muted");
	assert.equal(statusLabel("live", null, null), NOT_GRADED_LABEL);
});

test("the four-state verdict outranks a disagreeing boolean", () => {
	// Fail-closed: if the two ever disagree on the wire, the pill follows the
	// verdict, never the boolean.
	assert.equal(statusTag("live", true, "fail"), "tag-muted");
	assert.equal(statusLabel("live", true, "fail"), GATE_FAILED_LABEL);
});

test("non-'live' statuses are untouched by this fix", () => {
	// The #1747 change is scoped to the arm that produces a green claim. A
	// curated candidate that fails the gate still reads "Candidate" — relabelling
	// it would be a different product decision on a different tab.
	assert.equal(statusTag("candidate", false, "fail"), "tag-muted");
	assert.equal(statusLabel("candidate", false, "fail"), "Candidate");
	assert.equal(statusTag("validated", true, "pass"), "tag-accent");
	assert.equal(statusLabel("validated", true, "pass"), "Validated");
	assert.equal(statusTag("pending_backtest", null, "pending"), "tag-warning");
	assert.equal(statusLabel("pending_backtest", null, "pending"), "Pending Backtest");
	assert.equal(statusLabel(null, null, null), "Candidate");
});

// ── One sentence, two surfaces ─────────────────────────────────────────────

test("the demotion label is byte-identical to StrategyPassport.jsx's literal", () => {
	// The two surfaces keep SEPARATE statusTag/statusLabel implementations
	// (the passport maps `validated` to green and has no pending_backtest arm —
	// they are near-duplicates, not duplicates, and merging them would change
	// the passport's rendering as a side effect of a Library fix). What must not
	// diverge is the sentence a user reads about the same strategy in two
	// places, which is what #1747 was: "Live ✓" here, this string there.
	assert.ok(
		passport.includes(`"${GATE_FAILED_LABEL}"`),
		`StrategyPassport.jsx no longer contains the exact literal "${GATE_FAILED_LABEL}" — ` +
			"the Library and the passport have started describing the same verdict differently",
	);
});

// ── Wiring inside Strategies.jsx ───────────────────────────────────────────

test("coerceGenerated no longer sources the badge from rigor_verdict", () => {
	assert.equal(
		strategies.indexOf("verdict ? Boolean(verdict.passing)"),
		-1,
		"passes_rigor_gate is being read back out of the generation-time fusion verdict again — " +
			"that blob is a different gate's output and is never re-derived after a backtest",
	);
	assert.ok(
		strategies.includes(
			"passes_rigor_gate: typeof row.passes_rigor_gate === 'boolean' ? row.passes_rigor_gate : null",
		),
		"the badge must be a LITERAL server boolean or null — a coercion manufactures a verdict",
	);
	assert.ok(
		strategies.includes("rigor_gate_status: row.rigor_gate_status ?? null"),
		"coerceGenerated must carry the four-state verdict through, or the pill has nothing to read",
	);
});

test("the rigor numbers no longer come from rigor_verdict either", () => {
	for (const dead of [
		"verdict?.dsr",
		"verdict?.pbo",
		"verdict?.oos_sharpe",
		"verdict?.dsr_p_value",
	]) {
		// `verdict?.dsr != null` (the hasRealMetrics probe, which decides a label
		// relabel and never a metric) is the one permitted read.
		const idx = strategies.indexOf(dead);
		const permitted = idx !== -1 && strategies.slice(idx).startsWith("verdict?.dsr != null");
		assert.ok(
			idx === -1 || permitted,
			`Strategies.jsx renders ${dead} — the fusion verdict's numbers beside a live-gate pill`,
		);
	}
});

test("both the desktop pill and the mobile lib-card call the shared helpers", () => {
	const calls = strategies.match(
		/statusTag\(s\.status, s\.passes_rigor_gate, s\.rigor_gate_status\)/g,
	);
	assert.equal(
		calls?.length,
		2,
		"expected exactly two statusTag call sites (the table pill and the lib-card) " +
			"passing the four-state verdict — a card that drops it renders a bare green Live on mobile",
	);
	const labels = strategies.match(
		/statusLabel\(s\.status, s\.passes_rigor_gate, s\.rigor_gate_status\)/g,
	);
	assert.equal(labels?.length, 2);
	assert.equal(
		strategies.indexOf("statusTag(s.status, s.passes_rigor_gate)"),
		-1,
		"a call site is still on the two-argument form and cannot see the gate verdict",
	);
	assert.ok(
		strategies.includes("from '../libraryStatus.js'"),
		"Strategies.jsx must import the helpers rather than re-declaring them privately — " +
			"module-private is how they went untested through #1747",
	);
});
