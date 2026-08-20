import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { blockOrderCopy, verificationTone } from "../src/trace-binding.js";

// ── blockOrderCopy (#1359) ──────────────────────────────────────────────
//
// The panel must key off `temporal_binding_source`, not off
// `temporal_binding_valid` alone — the backend's TraceResponse validator
// (schemas.py) coerces `temporal_binding_valid` to None whenever source
// isn't "chain", so a bare-boolean check can never distinguish "the
// contract's commit-reveal path actually ran" from "off-chain record
// only". Every assertion below was confirmed to FAIL against the pre-fix
// Reasoning.jsx tree (it always rendered the off-chain / roadmap copy
// regardless of source).

test("chain source renders the contract-enforced heading, note, and tone", () => {
	const copy = blockOrderCopy({ source: "chain", valid: true });
	assert.equal(copy.heading, "Commit → trade → reveal (contract-enforced)");
	assert.equal(
		copy.note,
		"Vault.rebalance() reverts unless this commitment existed in an earlier block — enforced by ReasoningTraceRegistry.executeTrade(), not by our code being well-behaved.",
	);
	assert.equal(copy.tone, "verified");
});

test("non-chain source renders the off-chain heading, note, and tone", () => {
	const copy = blockOrderCopy({ source: "none", valid: false });
	assert.equal(copy.heading, "Block order (off-chain record)");
	assert.equal(
		copy.note,
		"Off-chain record only — this trace was anchored without the commit-reveal path, so the ordering is not contract-proven.",
	);
	assert.equal(copy.tone, "unproven");
});

test("source is the sole discriminant — valid does not flip the copy", () => {
	// A source==="chain" trace whose valid flag is momentarily false (e.g. a
	// block field hasn't landed in Redis yet) still gets the contract-backed
	// copy: the guarantee is the contract's, not a computed boolean's.
	const chainFalse = blockOrderCopy({ source: "chain", valid: false });
	const chainTrue = blockOrderCopy({ source: "chain", valid: true });
	assert.deepEqual(chainFalse, chainTrue);

	// A non-chain source never gets the contract-enforced copy, whatever
	// valid says.
	const noneTrue = blockOrderCopy({ source: "none", valid: true });
	const noneFalse = blockOrderCopy({ source: "none", valid: false });
	assert.deepEqual(noneTrue, noneFalse);
	assert.notEqual(noneTrue.tone, "verified");
});

// ── verificationTone (#1359) ────────────────────────────────────────────

test("hash_matched maps to the verified tone", () => {
	assert.equal(verificationTone("hash_matched"), "verified");
});

test("anchored_only maps to its own tone, distinct from verified", () => {
	assert.equal(verificationTone("anchored_only"), "anchored");
	assert.notEqual(verificationTone("anchored_only"), verificationTone("hash_matched"));
});

test("failed, unrecognised, and missing modes all degrade to failed", () => {
	for (const mode of ["failed", undefined, null, "", "bogus_mode"]) {
		assert.equal(verificationTone(mode), "failed");
	}
});

// ── Source-regex pins on Reasoning.jsx ──────────────────────────────────
//
// Same shape as ui/test/a11y.test.js: readFileSync + regex/substring pins
// on the rendered source, confirming the claim-integrity fixes actually
// landed in the component (not just in the pure helper). Every assertion
// in this section was confirmed to FAIL against the pre-fix tree.

const reasoningSrc = readFileSync(
	new URL("../src/components/Reasoning.jsx", import.meta.url),
	"utf8",
);

test("the stale off-chain-forever claim is gone from Reasoning.jsx", () => {
	assert.ok(
		!reasoningSrc.includes("commit-reveal wiring is on the roadmap"),
		"the roadmap disclaimer must not still be quoted verbatim as live UI copy",
	);
	assert.ok(
		!reasoningSrc.includes("not yet wired into the"),
		"the stale not-yet-wired comment must be gone",
	);
});

test("Reasoning.jsx reads temporal_binding_source, not just temporal_binding_valid", () => {
	assert.ok(reasoningSrc.includes("temporal_binding_source"));
});

test("the verify button's details render unconditionally, not gated on failure", () => {
	// The old gate hid the one honest caveat (anchored_only's "zero hashes
	// compared") in exactly the case it existed to qualify — a Redis outage
	// or an anchored-only response painted the same silence as success.
	assert.ok(!reasoningSrc.includes("vResult && !vResult.is_verified"));
});

test("Reasoning.jsx names the anchored_only mode explicitly", () => {
	assert.ok(reasoningSrc.includes("anchored_only"));
});

test("Reasoning.jsx no longer claims the button recomputes the hash", () => {
	assert.ok(!reasoningSrc.includes("recompute"));
});

test("the prose names the button by its real label at least twice", () => {
	const count = (reasoningSrc.match(/Verify hash on-chain/g) || []).length;
	assert.ok(count >= 2, `expected >= 2 occurrences, found ${count}`);
});

test("the anchored tone's button branch never reuses the verified check icon or class", () => {
	// The button label ternary is: verifying? -> 'verified'? -> 'anchored'?
	// -> default. Skip past the 'Hash verified' (tone === 'verified') branch
	// first, so the 'anchored' match found is the label ternary's own branch
	// rather than the earlier `title={vTone === 'anchored' ? ... }` check —
	// then extract up to the next ('verify hash on-chain' / default)
	// branch's icon. Avoids a full-JSX parse while still pinning the actual
	// rendered branch, not just a substring anywhere in the file.
	const verifiedLabelIdx = reasoningSrc.indexOf("Hash verified");
	assert.ok(verifiedLabelIdx !== -1, "no verified-tone label branch found in Reasoning.jsx");

	const anchoredIdx = reasoningSrc.indexOf("vTone === 'anchored'", verifiedLabelIdx);
	assert.ok(anchoredIdx !== -1, "no anchored-tone label branch found in Reasoning.jsx");

	const branchEnd = reasoningSrc.indexOf("i-lucide-search", anchoredIdx);
	assert.ok(branchEnd !== -1, "could not find the end of the anchored branch");

	const anchoredBranch = reasoningSrc.slice(anchoredIdx, branchEnd);
	assert.ok(
		!anchoredBranch.includes("i-lucide-check"),
		"the anchored_only branch must not reuse the verified check icon",
	);
	assert.ok(
		!anchoredBranch.includes("positive"),
		"the anchored_only branch must not reuse the verified/positive styling class",
	);
});

test("Architecture.jsx's contract-enforced claims are untouched (anti-goal)", () => {
	const architectureSrc = readFileSync(
		new URL("../src/components/Architecture.jsx", import.meta.url),
		"utf8",
	);
	assert.ok(architectureSrc.includes("the ordering is enforced by the contract"));
	assert.ok(architectureSrc.includes("contract-enforced ordering"));
});
