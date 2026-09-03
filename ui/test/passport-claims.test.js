// #1769 — the two claims the passport was making that its own data did not
// support.
//
// 1. "FUSED FROM 5 PAPERS" over five paper refs with ZERO attributed
//    mechanisms. #1739 landed the `contribution` column; the model emitted no
//    usable `paper_mechanisms`, so every cell is an em-dash — and the header
//    still told the reader those five papers had been fused into the
//    methodology. A citation count is not fusion depth.
// 2. A Backtest card printing Sharpe 0.70 and a Rigor card printing Deflated
//    Sharpe 0.08 beside a verdict pill, with no sentence anywhere saying they
//    are the same edge measured twice.
//
// The helpers live in plain .js modules so `node --test` executes them for
// real; the .jsx that consumes them is pinned by source-structure assertions,
// the convention the rest of this suite uses (see the note at the top of
// ui/test/passport-dsl.test.js).

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { paperAttributionHeader } from "../src/paperAttribution.js";
import {
	hasSharpeReconciliation,
	rigorBarClause,
} from "../src/sharpeReconciliation.js";

const passport = readFileSync(
	new URL("../src/components/StrategyPassport.jsx", import.meta.url),
	"utf8",
);

const papers = (n, attributed = 0) =>
	Array.from({ length: n }, (_, i) => ({
		arxiv_id: `2401.0000${i}`,
		title: `Paper ${i}`,
		contribution: i < attributed ? "supplies the 200-day trend filter" : null,
	}));

// ── The paper header ──────────────────────────────────────────────────────

test("the header counts citations and attributions separately", () => {
	// The exact strategy from the issue: 5 refs, 0 contributions.
	const h = paperAttributionHeader(papers(5, 0));
	assert.equal(
		h.heading,
		"5 papers cited · 0 name a mechanism this strategy trades",
	);
	assert.equal(h.cited, 5);
	assert.equal(h.attributed, 0);
});

test("zero attributions gets its own sentence, not silence", () => {
	// A bare "· 0" with nothing after it reads as a rendering bug. The zero
	// case is the one a reader most needs told, and it is the honest-shortfall
	// rule (#1636): label it, never hide it and never gate on it.
	const h = paperAttributionHeader(papers(5, 0));
	assert.match(h.note, /No per-paper mechanism attribution is recorded/);
	assert.match(h.note, /does not tie any of them to an element this strategy trades/);
});

test("the zero sentence claims nothing was RECORDED, never that nothing was found", () => {
	// MUTATION: restore "None of the cited papers was attributed to any element
	// of this strategy's spec". That asserts an attribution step ran and came
	// back empty. Curated rows have no spec at all, and every generated row
	// written before #1739's `contribution` writer simply has an empty column —
	// so the completed negative is false on both, and it contradicts the
	// blank-cell footnote ("unrecorded, not zero") in the same panel.
	for (const n of [1, 2, 5]) {
		const note = paperAttributionHeader(papers(n, 0)).note;
		assert.doesNotMatch(note, /was attributed to/);
		assert.doesNotMatch(note, /None of the cited papers/);
		assert.match(note, /recorded/);
	}
	// The partial branch is the same claim with a smaller subject and must use
	// the same framing.
	const partial = paperAttributionHeader(papers(3, 2)).note;
	assert.doesNotMatch(partial, /without an attributed mechanism/);
	assert.match(partial, /no recorded mechanism attribution/);
});

test("a partial attribution says how many are left over", () => {
	const h = paperAttributionHeader(papers(3, 2));
	assert.equal(
		h.heading,
		"3 papers cited · 2 name a mechanism this strategy trades",
	);
	assert.match(h.note, /The remaining 1 paper has no recorded mechanism attribution/);
	assert.match(
		paperAttributionHeader(papers(5, 2)).note,
		/The remaining 3 papers have no recorded mechanism attribution/,
	);
});

test("the header reads as English at every count", () => {
	// Singular/plural on BOTH halves, including the "1 names" agreement that a
	// naive `${n} name` gets wrong.
	assert.equal(
		paperAttributionHeader(papers(1, 1)).heading,
		"1 paper cited · 1 names a mechanism this strategy trades",
	);
	assert.equal(
		paperAttributionHeader(papers(1, 0)).heading,
		"1 paper cited · 0 name a mechanism this strategy trades",
	);
	assert.equal(
		paperAttributionHeader(papers(2, 2)).heading,
		"2 papers cited · 2 name a mechanism this strategy trades",
	);
	assert.match(
		paperAttributionHeader(papers(2, 2)).note,
		/Every cited paper is tied to a named element of the spec\./,
	);
});

test("the pipeline's own count can only ever LOWER the claim", () => {
	// `distinct_mechanism_papers` and the rendered contribution cells are two
	// counts of one fact. The failure that matters is overclaiming, so the
	// smaller wins in both directions: the header must never assert more
	// attribution than the table under it can show, nor more than the pipeline
	// recorded.
	assert.equal(paperAttributionHeader(papers(5, 4), 1).attributed, 1);
	assert.equal(paperAttributionHeader(papers(5, 1), 4).attributed, 1);
	// Absent / malformed leaves the rendered count alone rather than zeroing it.
	assert.equal(paperAttributionHeader(papers(5, 3), undefined).attributed, 3);
	assert.equal(paperAttributionHeader(papers(5, 3), null).attributed, 3);
	assert.equal(paperAttributionHeader(papers(5, 3), "2").attributed, 3);
});

test("whitespace is not an attribution", () => {
	// `contribution` is model-authored text through a nullable column; a "  "
	// would otherwise count as a named mechanism.
	const rows = papers(2, 0);
	rows[0].contribution = "   ";
	rows[1].contribution = "";
	assert.equal(paperAttributionHeader(rows).attributed, 0);
});

test("no papers means no header at all", () => {
	assert.equal(paperAttributionHeader([]), null);
	assert.equal(paperAttributionHeader(null), null);
	assert.equal(paperAttributionHeader(undefined), null);
});

test("the passport renders the honest header and not the old one", () => {
	// MUTATION: restore the old header — put
	// `` {rows.length === 1 ? "Source paper" : `Fused from ${rows.length} papers`} ``
	// back in PapersTable — and the first two assertions fail.
	//
	// Matched on the RENDER forms (an interpolated template, and the chip's
	// `{expr} fused papers`) rather than on the bare phrase, because the module
	// comment above `PapersTable` quotes the retired header to explain why it
	// went. A test that forbade the words would forbid saying what was fixed.
	assert.doesNotMatch(passport, /Fused from \$\{/);
	assert.doesNotMatch(passport, /\}\s*fused papers/);
	assert.match(passport, /paperAttributionHeader\(rows, distinctMechanismPapers\)/);
	assert.match(passport, /\{attribution\.heading\}/);
	assert.match(passport, /\{attribution\.note\}/);
	// The sub-line is no longer conditional on a multi-paper row: it used to be
	// "One methodology synthesized from every row below", which is the same
	// overclaim written as a sentence.
	assert.doesNotMatch(
		passport,
		/One methodology synthesized from every row below/,
	);
	// The header chip beside the title carried the identical claim.
	assert.match(passport, /\{s\.papers\.length\} papers cited/);
	// The pipeline's own count is threaded from the payload, so the day the API
	// serves it the header uses it without a second change.
	assert.match(passport, /distinctMechanismPapers=\{s\.distinct_mechanism_papers\}/);
});

// ── The raw-vs-deflated sentence ──────────────────────────────────────────

test("the sentence needs BOTH numbers or it does not render", () => {
	// Its whole subject is the relationship between them; with one in hand
	// there is nothing to reconcile and half a sentence would have to invent
	// the missing side.
	assert.equal(
		hasSharpeReconciliation({ sharpe_ratio: 0.7, deflated_sharpe_ratio: 0.08 }),
		true,
	);
	assert.equal(
		hasSharpeReconciliation({ sharpe_ratio: 0.7, deflated_sharpe_ratio: null }),
		false,
	);
	assert.equal(
		hasSharpeReconciliation({ sharpe_ratio: null, deflated_sharpe_ratio: 0.08 }),
		false,
	);
	assert.equal(hasSharpeReconciliation({}), false);
	assert.equal(hasSharpeReconciliation(null), false);
	// A zero is a measurement, not an absence.
	assert.equal(
		hasSharpeReconciliation({ sharpe_ratio: 0, deflated_sharpe_ratio: 0 }),
		true,
	);
	// NaN/Infinity reach JSON as null, but a coerced row could carry them.
	assert.equal(
		hasSharpeReconciliation({ sharpe_ratio: NaN, deflated_sharpe_ratio: 0.08 }),
		false,
	);
	assert.equal(
		hasSharpeReconciliation({ sharpe_ratio: 0.7, deflated_sharpe_ratio: Infinity }),
		false,
	);
});

test("the bar clause reads the four-state verdict, never the boolean alone", () => {
	// #1358's bug class: `passes_rigor_gate` is false for "pending" and
	// "degenerate" too, so keying the sentence on the boolean would tell a user
	// their ungraded strategy had lost.
	assert.match(rigorBarClause({ rigor_gate_status: "pass" }), /clears the Archimedes Verified bar/);
	assert.match(rigorBarClause({ rigor_gate_status: "fail" }), /sits below the Archimedes Verified bar/);
	assert.match(
		rigorBarClause({ rigor_gate_status: "pending", passes_rigor_gate: false }),
		/has not graded this strategy yet/,
	);
	assert.doesNotMatch(
		rigorBarClause({ rigor_gate_status: "pending", passes_rigor_gate: false }),
		/below/,
	);
	assert.match(
		rigorBarClause({ rigor_gate_status: "degenerate", passes_rigor_gate: false }),
		/nothing to grade/,
	);
	assert.doesNotMatch(
		rigorBarClause({ rigor_gate_status: "degenerate", passes_rigor_gate: false }),
		/below/,
	);
});

test("an unreadable verdict produces no clause at all", () => {
	// The em-dash rule (#1326) applied to prose: for a status this build does
	// not know, no verdict beats a guessed one. The sentence still renders —
	// the raw-vs-deflated half is true regardless — it just stops there.
	assert.equal(rigorBarClause({ rigor_gate_status: "probationary" }), null);
	assert.equal(rigorBarClause({}), null);
	assert.equal(rigorBarClause(null), null);
	// Rows coerced from the generated feed carry no status; the boolean is the
	// documented fallback for exactly those.
	assert.match(rigorBarClause({ passes_rigor_gate: true }), /clears/);
	assert.match(rigorBarClause({ passes_rigor_gate: false }), /sits below/);
});

test("the passport renders the sentence under the backtest block", () => {
	// MUTATION: delete the `hasSharpeReconciliation(s) && (...)` block — the
	// two Sharpes go back to sitting in separate cards with nothing connecting
	// them, and every assertion here fails.
	assert.match(passport, /hasSharpeReconciliation\(s\) && \(/);
	assert.match(passport, /is the raw backtest figure; deflated for selection bias it is/);
	// The four signals named here are the four `run_rigor_gate` actually ORs
	// into `passes_all` (rigor_evaluator.gate_details) and the same four the
	// Rigor card's own explainer lists. "grades the deflated Sharpe" alone
	// would have been a narrower claim than the gate makes.
	assert.match(
		passport,
		/reads that deflation — not the raw\s*\n?\s*Sharpe — alongside PBO, out-of-sample Sharpe and the\s*\n?\s*look-ahead audit/,
	);
	assert.match(passport, /rigorBarClause\(s\)/);
});

test("the sentence introduces no number of its own", () => {
	// The rule for this sentence: every figure in it is one the payload already
	// carries and the page already prints. A hardcoded threshold (the 0.90
	// level-1 `dsr_p_min`, say) would be a fourth number no API field backs —
	// and it would be a claim about a p-value pinned next to two Sharpes, which
	// is the confusion this sentence exists to remove.
	const block = passport.slice(
		passport.indexOf("hasSharpeReconciliation(s) && ("),
		passport.indexOf("{(s.backtest_start || s.backtest_end) && ("),
	);
	assert.ok(block.length > 200, "the reconciliation block was not located");
	assert.doesNotMatch(block, /\d+\.\d+/);
	// Both figures go through the shared renderer like every other metric on
	// this page (#1651) — no local formatting, in-domain or not.
	assert.match(block, /metric="sharpe_ratio"/);
	assert.match(block, /metric="deflated_sharpe_ratio"/);
});
