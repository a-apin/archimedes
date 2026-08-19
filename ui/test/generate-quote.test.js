import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
	PAYMENT_STATUS,
	buildDryRunPaymentHeader,
	deriveQuoteView,
	derivePaymentState,
	describePayerMismatch,
	extractReceipt,
	isPaywallError,
	isWalletLinkRequiredError,
	paymentErrorMessage,
	primaryLinkedWallet,
} from "../src/generateQuote.js";

// ── deriveQuoteView: shapes the ratified GET /api/generate/quote response
// (#1296) — payment_required, pricing_model, price, asset, chain,
// recipient, dry_run, how. NO quote_id, NO expires_at, NO breakdown: the
// PROPOSED contract this replaced had all three; the ratified shape has
// none of them. ─────────────────────────────────────────────────────────

test("quote renders: deriveQuoteView shapes the ratified fields", () => {
	const view = deriveQuoteView({
		payment_required: true,
		pricing_model: "flat_v1",
		price: "$0.150000",
		asset: "USDC",
		chain: "eip155:5042002",
		recipient: "0xRecipient",
		dry_run: true,
		how: "POST /api/generate/start without a Payment-Signature header returns 402...",
	});
	assert.equal(view.paymentRequired, true);
	assert.equal(view.pricingModel, "flat_v1");
	assert.equal(view.price, "$0.150000");
	assert.equal(view.asset, "USDC");
	assert.equal(view.chain, "eip155:5042002");
	assert.equal(view.recipient, "0xRecipient");
	assert.equal(view.dryRun, true);
	assert.match(view.how, /Payment-Signature/);
});

test("quote renders: null quote and a null recipient degrade cleanly, not a crash", () => {
	assert.equal(deriveQuoteView(null), null);
	const view = deriveQuoteView({
		payment_required: false,
		pricing_model: "flat_v1",
		price: "$0.150000",
		asset: "USDC",
		chain: "eip155:5042002",
		recipient: null,
		dry_run: true,
		how: "...",
	});
	assert.equal(view.recipient, null);
	assert.equal(view.paymentRequired, false);
});

test("quote renders: the ratified shape carries no quote_id, expires_at, or breakdown", () => {
	// The PROPOSED contract's dead fields must not leak through even if a
	// stale/misbehaving backend sent them — deriveQuoteView only reads the
	// ratified field names.
	const view = deriveQuoteView({
		payment_required: true,
		pricing_model: "flat_v1",
		price: "$0.150000",
		asset: "USDC",
		chain: "eip155:5042002",
		recipient: null,
		dry_run: false,
		how: "...",
		quote_id: "qt_shouldnt_survive",
		expires_at: "2026-08-19T18:05:00Z",
		breakdown: [{ label: "should not survive" }],
	});
	assert.equal("quoteId" in view, false);
	assert.equal("expiresAt" in view, false);
	assert.equal("breakdown" in view, false);
});

// ── isPaywallError / isWalletLinkRequiredError: the two gate guards ───────

test("402 state: isPaywallError recognizes only a genuine 402", () => {
	assert.equal(isPaywallError({ status: 402 }), true);
	assert.equal(isPaywallError({ status: 500 }), false);
	assert.equal(isPaywallError({ status: 409 }), false);
	assert.equal(isPaywallError(null), false);
	assert.equal(isPaywallError(undefined), false);
	assert.equal(isPaywallError({}), false);
});

test("409 state: isWalletLinkRequiredError recognizes only the wallet_link_required reason, not any 409", () => {
	assert.equal(
		isWalletLinkRequiredError({ status: 409, detail: { reason: "wallet_link_required" } }),
		true,
	);
	// A 409 for some other reason must NOT be mistaken for this precondition.
	assert.equal(
		isWalletLinkRequiredError({ status: 409, detail: { reason: "some_other_conflict" } }),
		false,
	);
	assert.equal(isWalletLinkRequiredError({ status: 409 }), false);
	assert.equal(isWalletLinkRequiredError({ status: 402, detail: { reason: "wallet_link_required" } }), false);
	assert.equal(isWalletLinkRequiredError(null), false);
});

// ── derivePaymentState: routes an error + the quote's dry_run flag into a
// PAYMENT_STATUS ────────────────────────────────────────────────────────

test("derivePaymentState: 409 wallet_link_required routes to WALLET_LINK_REQUIRED regardless of dry_run", () => {
	const err = { status: 409, detail: { reason: "wallet_link_required" } };
	assert.equal(derivePaymentState(err, true), PAYMENT_STATUS.WALLET_LINK_REQUIRED);
	assert.equal(derivePaymentState(err, false), PAYMENT_STATUS.WALLET_LINK_REQUIRED);
});

test("derivePaymentState: a 402 routes to DRY_RUN or LIVE_UNAVAILABLE by the quote's dry_run flag", () => {
	const err = { status: 402, detail: { reason: "payment_required" } };
	assert.equal(derivePaymentState(err, true), PAYMENT_STATUS.DRY_RUN);
	assert.equal(derivePaymentState(err, false), PAYMENT_STATUS.LIVE_UNAVAILABLE);
});

test("derivePaymentState: anything else fails closed to NONE, never a payment-specific state", () => {
	assert.equal(derivePaymentState({ status: 500 }, true), PAYMENT_STATUS.NONE);
	assert.equal(derivePaymentState({ status: 404 }, true), PAYMENT_STATUS.NONE);
	assert.equal(derivePaymentState(null, true), PAYMENT_STATUS.NONE);
});

// ── paymentErrorMessage: renders the backend's message verbatim ───────────

test("paymentErrorMessage: renders detail.message verbatim, falls back only when absent", () => {
	assert.equal(
		paymentErrorMessage({ detail: { message: "fund it with testnet USDC (the faucet currently requires a human)" } }),
		"fund it with testnet USDC (the faucet currently requires a human)",
	);
	assert.equal(paymentErrorMessage({}, "fallback"), "fallback");
	assert.equal(paymentErrorMessage(null), "Payment step failed.");
});

// ── primaryLinkedWallet / describePayerMismatch: payer binding ────────────

test("primaryLinkedWallet: picks the wallet flagged primary, or the first if none is", () => {
	assert.equal(primaryLinkedWallet([]), null);
	assert.equal(primaryLinkedWallet(null), null);
	const wallets = [
		{ address: "0xAAA", is_primary: false },
		{ address: "0xBBB", is_primary: true },
	];
	assert.equal(primaryLinkedWallet(wallets).address, "0xBBB");
	assert.equal(
		primaryLinkedWallet([{ address: "0xCCC", is_primary: false }]).address,
		"0xCCC",
	);
});

test("describePayerMismatch: null when the active wallet IS one of the linked wallets (case-insensitive, either side mixed-case)", () => {
	const wallets = [{ address: "0xabcdef0000000000000000000000000000001", is_primary: true }];
	// The ACTIVE address (not just the linked one) is mixed-case here — both
	// sides must be normalized, not just the linked-wallet side.
	assert.equal(describePayerMismatch("0xAbCdEf0000000000000000000000000000001", wallets), null);
});

test("describePayerMismatch: null when there's nothing to compare (no active address, or no linked wallets)", () => {
	assert.equal(describePayerMismatch(null, [{ address: "0xAAA", is_primary: true }]), null);
	assert.equal(describePayerMismatch("0xAAA", []), null);
	assert.equal(describePayerMismatch("0xAAA", null), null);
});

test("describePayerMismatch: flags the primary linked wallet when the active address isn't any linked one", () => {
	const wallets = [
		{ address: "0xAAA", is_primary: false },
		{ address: "0xBBB", is_primary: true },
	];
	const result = describePayerMismatch("0xCCC", wallets);
	assert.deepEqual(result, { active: "0xCCC", linked: "0xBBB" });
});

// ── buildDryRunPaymentHeader: the honest test-mode stand-in ────────────────

test("buildDryRunPaymentHeader: base64-JSON carrying the REAL payer, decodable the same way the backend's own test fixture is built", () => {
	const header = buildDryRunPaymentHeader("0xPayerAddress");
	const decoded = JSON.parse(globalThis.Buffer.from(header, "base64").toString("utf8"));
	assert.equal(decoded.payload.authorization.from, "0xPayerAddress");
});

test("buildDryRunPaymentHeader: REFUSES a falsy payer (loud contract for #1298)", () => {
	// A payment header with no payer is never meaningful. Strengthened from
	// the original "encode an empty from honestly" (#1295 review nit): the
	// server's payer binding (#1296) requires the linked wallet, so an
	// empty payer at this seam is a caller bug — throw, don't encode.
	assert.throws(() => buildDryRunPaymentHeader(null), /requires a payer address/);
	assert.throws(() => buildDryRunPaymentHeader(""), /requires a payer address/);
});

test("Generate.jsx resolves the test-mode payer linked-wallet-first with a no-payer guard", () => {
	// Reaching a 402 proves the SERVER-side link, not that the browser wallet
	// is connected or the linked-wallets fetch succeeded — the component must
	// resolve linked-first, fall back to the active wallet, and bail with a
	// message (no submit) when neither resolves.
	const generateSrc = readFileSync(new URL("../src/components/Generate.jsx", import.meta.url), "utf8");
	assert.match(generateSrc, /primaryLinkedWallet\(linkedWallets\) \|\| walletAddr/);
	assert.match(generateSrc, /No payment was attempted/);
	assert.match(generateSrc, /buildDryRunPaymentHeader\(payer\)/);
});

// ── extractReceipt: the PAYMENT-RESPONSE settlement receipt ───────────────

test("extractReceipt: reads PAYMENT-RESPONSE from a Headers-like object and a plain object, case-insensitively", () => {
	const fakeHeaders = { get: (name) => (name.toLowerCase() === "payment-response" ? "receipt-123" : null) };
	assert.equal(extractReceipt(fakeHeaders), "receipt-123");
	assert.equal(extractReceipt({ "payment-response": "receipt-456" }), "receipt-456");
	assert.equal(extractReceipt({ "PAYMENT-RESPONSE": "receipt-789" }), "receipt-789");
});

test("extractReceipt: null when absent, never a crash on a missing/empty headers object", () => {
	assert.equal(extractReceipt(null), null);
	assert.equal(extractReceipt({}), null);
	const fakeHeaders = { get: () => null };
	assert.equal(extractReceipt(fakeHeaders), null);
});

// ── Wiring: Generate.jsx actually uses the flag + helpers, not a fork of the
// logic re-implemented inline, and the dead PROPOSED-contract concepts
// (quote_id, expiry, attachQuoteId) are actually gone. Static-source
// checks, matching the pattern already established in
// ui/test/app-visuals.test.js for this file. ──────────────────────────────

const generate = readFileSync(
	new URL("../src/components/Generate.jsx", import.meta.url),
	"utf8",
);

test("Generate.jsx gates the quote card and payment step behind GENERATION_QUOTE_ENABLED", () => {
	assert.match(generate, /from ["']\.\.\/featureFlags["']/);
	assert.match(generate, /GENERATION_QUOTE_ENABLED\s*&&/);
	assert.match(generate, /Paper trading after generation costs nothing/);
});

test("Generate.jsx submits /start through apiPostWithMeta, not the plain apiPost / a fork of the ratified body", () => {
	assert.match(generate, /apiPostWithMeta\(\s*["']\/api\/generate\/start["']/);
	// The PROPOSED contract's quote_id/attachQuoteId must be fully gone from
	// the code (not merely from a doc comment explaining that it's dead) —
	// the ratified /start body carries no payment field at all.
	assert.doesNotMatch(generate, /attachQuoteId/);
	assert.doesNotMatch(generate, /quote_id\s*[:.]/);
});

test("Generate.jsx routes 409/402 through derivePaymentState into the ratified payment-step states", () => {
	assert.match(generate, /derivePaymentState\(/);
	assert.match(generate, /PAYMENT_STATUS\.WALLET_LINK_REQUIRED/);
	assert.match(generate, /PAYMENT_STATUS\.DRY_RUN/);
	assert.match(generate, /PAYMENT_STATUS\.LIVE_UNAVAILABLE/);
	assert.match(generate, /open-wallet-modal/);
});

test("Generate.jsx offers a functional test-mode continuation and surfaces the receipt when present", () => {
	assert.match(generate, /buildDryRunPaymentHeader\(/);
	assert.match(generate, /continueInTestMode/);
	assert.match(generate, /extractReceipt\(/);
});
