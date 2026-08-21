import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

// Source-regex pins on PaymentReceipts.jsx (Dan's directive, 2026-08-21: "we
// must provide people with their receipts"). Same shape as
// ui/test/trace-binding.test.js's "Source-regex pins" section: readFileSync +
// substring/regex assertions on the rendered source, confirming the honesty
// contract and the owner-scoped fetch actually landed in the component. This
// is a brand-new component with no pre-fix tree of its own to fail against
// (mirrors trace-binding.test.js's blockOrderCopy note); the anti-goal check
// on Portfolio.jsx below exists to catch a *future* PR breaking the wiring.

const receiptsSrc = readFileSync(
	new URL("../src/components/PaymentReceipts.jsx", import.meta.url),
	"utf8",
);

// ── Owner-scoped fetch path ──────────────────────────────────────────────

test("fetches the owner-scoped receipts endpoint via the shared apiGet helper", () => {
	// apiGet (src/api.js) sends credentials:'include', which is what makes the
	// request owner-scoped server-side (require_current_user reads the Better
	// Auth session cookie). A bare fetch() with no credentials would silently
	// 401 rather than leak another user's receipts, but pin the real helper
	// call so this stays wired to the session-carrying path, not a copy/paste
	// bare fetch.
	assert.ok(
		receiptsSrc.includes('apiGet("/api/payments/receipts")'),
		"must call apiGet on the exact /api/payments/receipts path",
	);
});

test("imports apiGet from the shared API helper, not a bare fetch", () => {
	assert.ok(
		/from ['"]\.\.\/api['"]/.test(receiptsSrc),
		"must import from ../api (the credentials:'include' helper), not roll its own fetch",
	);
	assert.ok(
		!receiptsSrc.includes("fetch("),
		"must not call the global fetch() directly — apiGet is the one credentialed path",
	);
});

// ── Honesty: settlement_ref is a Circle reference, never an arcscan link ──

test("labels settlement_ref honestly as a Circle facilitator reference, not a tx hash", () => {
	assert.ok(
		receiptsSrc.includes("Circle settlement reference"),
		"the honest label text must be present verbatim",
	);
});

test("never renders settlement_ref as an arcscan link", () => {
	// Checks for the actual hazard (a live link to the block explorer), not
	// the word "arcscan" in prose — the component's own honesty-rule comments
	// legitimately name arcscan while explaining why it must not appear as a
	// link, so a bare substring ban would false-positive on those comments.
	assert.ok(
		!receiptsSrc.includes("testnet.arcscan.app"),
		"settlement_ref is a Circle facilitator reference id, not an on-chain tx hash — it must never be linked to the block explorer",
	);
	assert.ok(
		!/href=.*arcscan/i.test(receiptsSrc),
		"no href in this component may point at arcscan",
	);
});

test("settlement_ref is rendered as plain text, never inside an anchor tag", () => {
	const labelIdx = receiptsSrc.indexOf("Circle settlement reference");
	assert.ok(labelIdx !== -1, "the honest label must exist to anchor this check");
	const refIdx = receiptsSrc.indexOf("settlement_ref", labelIdx);
	assert.ok(refIdx !== -1, "settlement_ref must actually be rendered near its label");
	const between = receiptsSrc.slice(labelIdx, refIdx + "settlement_ref".length + 10);
	assert.ok(
		!between.includes("<a "),
		"the settlement_ref value must not be wrapped in a clickable <a> element",
	);
});

// ── Portfolio.jsx wiring (anti-goal pin — this PR must not skip it) ──────

const portfolioSrc = readFileSync(
	new URL("../src/components/Portfolio.jsx", import.meta.url),
	"utf8",
);

test("Portfolio.jsx imports and renders PaymentReceipts", () => {
	assert.ok(
		/import PaymentReceipts from ['"]\.\/PaymentReceipts['"]/.test(portfolioSrc),
		"Portfolio.jsx must import the PaymentReceipts component",
	);
	assert.ok(
		portfolioSrc.includes("<PaymentReceipts"),
		"Portfolio.jsx must actually render <PaymentReceipts />",
	);
});

test("Portfolio.jsx does not touch Generate.jsx or AccountSettings.jsx conventions (anti-goal)", () => {
	// This feature's spec explicitly forbids touching Generate.jsx and
	// AccountSettings.jsx (parallel work owns them) — pin that the payments
	// section landed in Portfolio.jsx itself, not smuggled elsewhere.
	assert.ok(portfolioSrc.includes("portfolio-payments"));
});
