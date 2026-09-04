import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { sourcePapersCopy } from "../src/trace-binding.js";

// `GET /api/traces/{id}/verify` runs two independent checks (#1637): the
// on-chain half ("were these bytes anchored") and the source-paper half
// ("does the corpus have the papers this decision cites"). This file pins the
// copy for the second, and the two things the owner's Q8 call on #1688 asked
// for by name:
//
//   1. the button must NOT say "verified" unqualified — corpus content_hash /
//      pdf_sha256 are NULL until #1091, so the backend checks EXISTENCE and
//      compares no hashes. The word it uses is "exists in the corpus".
//   2. the tri-state `mode` must be SURFACED, not merely returned.
//
// Each guard here is run against the input it must reject as well as the one
// it accepts — a guard nobody has watched reject something is not known to
// guard anything.

const src = (p) => readFileSync(new URL(`../src/${p}`, import.meta.url), "utf8");

const checked = (overrides = {}) => ({
	papers_verified: true,
	source_paper_verification: {
		mode: "checked",
		checked: 2,
		verified: true,
		missing: [],
		hash_mismatch: [],
		...overrides,
	},
});

// ── 1. The pass case does not overclaim ─────────────────────────────────

test("a passing check says the papers EXIST — never 'verified'", () => {
	const c = sourcePapersCopy(checked());
	assert.equal(c.tone, "verified");
	// The claim word is the whole point of Q8 — asserted BEFORE the exact
	// string, so a regression reports "overclaims" rather than a diff.
	assert.ok(
		!/verified/i.test(c.label),
		`label overclaims a hash comparison that did not happen: ${c.label}`,
	);
	assert.equal(c.label, "2 cited papers exist in the corpus");
	// …and the limit is stated, not left to the reader to infer.
	assert.match(c.detail, /no hash was compared/i);
	assert.match(c.detail, /#1091/);
});

test("one paper is not pluralised into two", () => {
	const c = sourcePapersCopy(checked({ checked: 1 }));
	assert.equal(c.label, "1 cited paper exists in the corpus");
});

// ── 2. The two null modes are neither a pass nor a failure ──────────────

test("a corpus outage is not a provenance failure and not a pass", () => {
	const c = sourcePapersCopy({
		papers_verified: null,
		source_paper_verification: { mode: "corpus_unavailable", checked: 0, missing: [], hash_mismatch: [] },
	});
	assert.notEqual(c.tone, "failed");
	assert.notEqual(c.tone, "verified");
	assert.match(c.label, /not checked/);
	// The mode is SURFACED, not merely returned (Q8).
	assert.match(c.detail, /corpus_unavailable/);
	assert.match(c.detail, /not a failure and not a pass/i);
});

test("a trace that cites no papers reports an absence, not a verdict", () => {
	const c = sourcePapersCopy({
		papers_verified: null,
		source_paper_verification: { mode: "no_papers_claimed", checked: 0, missing: [], hash_mismatch: [] },
	});
	assert.equal(c.tone, "absent");
	assert.equal(c.label, "no papers cited");
	assert.match(c.detail, /no_papers_claimed/);
	assert.ok(!/verified/i.test(c.label));
});

// ── 3. The failure case names the papers ────────────────────────────────

test("a missing paper is a failure that names the id", () => {
	const c = sourcePapersCopy({
		papers_verified: false,
		source_paper_verification: {
			mode: "checked",
			checked: 2,
			verified: false,
			missing: ["2999.99999"],
			hash_mismatch: [],
		},
	});
	assert.equal(c.tone, "failed");
	assert.match(c.detail, /2999\.99999/);
});

test("a hash disagreement is reported as a disagreement, not as absence", () => {
	const c = sourcePapersCopy({
		papers_verified: false,
		source_paper_verification: {
			mode: "checked",
			checked: 1,
			verified: false,
			missing: [],
			hash_mismatch: ["2301.00001"],
		},
	});
	assert.equal(c.tone, "failed");
	assert.match(c.detail, /content hash disagrees: 2301\.00001/);
	assert.ok(!/not in the corpus/.test(c.detail));
});

// ── 4. Unknown / absent input never defaults to a verdict ───────────────

test("no result at all is 'not checked', never a pass and never a failure", () => {
	for (const input of [undefined, null, {}, { papers_verified: undefined }]) {
		const c = sourcePapersCopy(input);
		assert.notEqual(c.tone, "verified", `${JSON.stringify(input)} rendered as a pass`);
		assert.notEqual(c.tone, "failed", `${JSON.stringify(input)} rendered as a failure`);
		assert.match(c.label, /not checked/);
	}
});

// ── 5. The component actually calls it ──────────────────────────────────
//
// The copy being right in this module buys nothing if Reasoning.jsx re-derives
// its own line from `papers_verified`. Same rule anchor-state.test.js applies
// to `anchorState`.

test("Reasoning.jsx renders the shared helper, not its own ternary", () => {
	const jsx = src("components/Reasoning.jsx");
	assert.match(jsx, /sourcePapersCopy/, "Reasoning.jsx does not call sourcePapersCopy");
	assert.match(
		jsx,
		/import \{[^}]*sourcePapersCopy[^}]*\} from '\.\.\/trace-binding'/,
		"sourcePapersCopy is not imported from the shared module",
	);
	// The adversarial half: the component must not spell its own claim.
	const inlineClaim = /papers_verified\s*\?\s*['"`][^'"`]*[Vv]erified/;
	assert.ok(
		!inlineClaim.test(jsx),
		"Reasoning.jsx spells its own 'verified' claim off papers_verified",
	);
	assert.ok(inlineClaim.test("{t.papers_verified ? 'Papers verified' : 'no'}"), "the guard cannot fire");
});

// ── 6. The card that printed empty quotes ───────────────────────────────
//
// `paper_title` is now null, not "", when no title resolves (#1637, owner Q3).
// The single-paper card printed it inside literal quotation marks, so a null
// rendered as `""` — the exact defect the issue names. This pins the guard
// around it, and shows the guard can fail.

test("the single-paper card never prints an empty pair of quotes", () => {
	const jsx = src("components/Strategies.jsx");
	assert.match(
		jsx,
		/\{s\.paper_title \? \(/,
		"Strategies.jsx renders paper_title unguarded — a null title prints as empty quotes",
	);
	assert.match(jsx, /title unavailable — arXiv:\$\{s\.paper_arxiv_id\}/);

	// Adversarial companion: the predicate does NOT match the unguarded form.
	const unguarded = '<div className="body">"{s.paper_title}"</div>';
	assert.ok(!/\{s\.paper_title \? \(/.test(unguarded), "the guard cannot fail");
});
