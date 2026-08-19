import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
	PAYMENT_STATUS,
	attachQuoteId,
	deriveQuoteView,
	derivePaymentState,
	isPaywallError,
	isQuoteExpired,
} from "../src/generateQuote.js";

// ── deriveQuoteView: shapes the raw GET /api/generate/quote response ──────

test("quote renders: deriveQuoteView shapes price, testnet label, and breakdown", () => {
	const view = deriveQuoteView({
		quote_id: "qt_abc123",
		price_usdc: "0.42",
		currency: "USDC-testnet",
		breakdown: [{ label: "LLM inference (est.)", amount_usdc: "0.30" }],
		expires_at: "2026-08-19T18:05:00Z",
	});
	assert.equal(view.priceLabel, "0.42 USDC-testnet");
	assert.match(view.priceLabel, /USDC-testnet/);
	assert.equal(view.quoteId, "qt_abc123");
	assert.equal(view.breakdown.length, 1);
	assert.equal(view.breakdown[0].label, "LLM inference (est.)");
	assert.equal(view.expiresAt, "2026-08-19T18:05:00Z");
});

test("quote renders: absent breakdown/quote degrade to empty/null, not a crash", () => {
	assert.equal(deriveQuoteView(null), null);
	const view = deriveQuoteView({
		quote_id: "qt_x",
		price_usdc: "1.00",
		currency: "USDC-testnet",
	});
	assert.deepEqual(view.breakdown, []);
	assert.equal(view.expiresAt, null);
});

// ── isQuoteExpired ─────────────────────────────────────────────────────────

test("a quote past its expires_at is expired; one still ahead is not", () => {
	const now = new Date("2026-08-19T12:00:00Z");
	assert.equal(
		isQuoteExpired({ expires_at: "2026-08-19T11:59:59Z" }, now),
		true,
	);
	assert.equal(
		isQuoteExpired({ expires_at: "2026-08-19T12:00:00Z" }, now),
		true,
		"exact boundary counts as expired, not a race the caller can win",
	);
	assert.equal(
		isQuoteExpired({ expires_at: "2026-08-19T12:00:01Z" }, now),
		false,
	);
});

test("a quote with no/malformed expires_at is never treated as expired", () => {
	assert.equal(isQuoteExpired(null), false);
	assert.equal(isQuoteExpired({}), false);
	assert.equal(isQuoteExpired({ expires_at: "not-a-date" }), false);
});

// ── attachQuoteId: "approve carries quote_id" ──────────────────────────────

test("approve carries quote_id: attached only when the flag is on and a quote was fetched", () => {
	const payload = { brief: { intent: "momentum" } };
	const withId = attachQuoteId(payload, {
		quoteEnabled: true,
		approvedQuoteId: "qt_777",
	});
	assert.equal(withId.quote_id, "qt_777");
	assert.equal(withId.brief.intent, "momentum", "original payload fields untouched");
	assert.notEqual(withId, payload, "does not mutate the input payload");
});

test("approve carries quote_id: fails closed when flag is off, even if a quote id is present", () => {
	const payload = { brief: { intent: "momentum" } };
	const result = attachQuoteId(payload, {
		quoteEnabled: false,
		approvedQuoteId: "qt_777",
	});
	assert.equal(result, payload);
	assert.equal("quote_id" in result, false);
});

test("approve carries quote_id: fails closed when the flag is on but no quote was approved", () => {
	const payload = { brief: { intent: "momentum" } };
	const result = attachQuoteId(payload, {
		quoteEnabled: true,
		approvedQuoteId: null,
	});
	assert.equal(result, payload);
	assert.equal("quote_id" in result, false);
});

// ── 402 state: isPaywallError + derivePaymentState ─────────────────────────

test("402 state renders: isPaywallError recognizes only a genuine 402", () => {
	assert.equal(isPaywallError({ status: 402 }), true);
	assert.equal(isPaywallError({ status: 500 }), false);
	assert.equal(isPaywallError({ status: 404 }), false);
	assert.equal(isPaywallError(null), false);
	assert.equal(isPaywallError(undefined), false);
	assert.equal(isPaywallError({}), false);
});

test("402 state renders: no wallet -> wallet-required, connected wallet -> payment preview", () => {
	assert.equal(derivePaymentState(null), PAYMENT_STATUS.WALLET_REQUIRED);
	assert.equal(derivePaymentState(""), PAYMENT_STATUS.WALLET_REQUIRED);
	assert.equal(derivePaymentState("   "), PAYMENT_STATUS.WALLET_REQUIRED);
	assert.equal(
		derivePaymentState("0xabc123"),
		PAYMENT_STATUS.PAYMENT_PREVIEW,
	);
});

// ── Wiring: Generate.jsx actually uses the flag + helpers, not a fork of the
// logic re-implemented inline. Static-source checks, matching the pattern
// already established in ui/test/app-visuals.test.js for this file. ──────

const generate = readFileSync(
	new URL("../src/components/Generate.jsx", import.meta.url),
	"utf8",
);

test("Generate.jsx gates the quote card and payment step behind GENERATION_QUOTE_ENABLED", () => {
	assert.match(generate, /from ["']\.\.\/featureFlags["']/);
	assert.match(generate, /GENERATION_QUOTE_ENABLED\s*&&/);
	assert.match(generate, /testnet USDC/);
	assert.match(generate, /Paper trading after generation costs nothing/);
});

test("Generate.jsx builds the /start payload through attachQuoteId, not an inline fork", () => {
	assert.match(generate, /attachQuoteId\(/);
	assert.match(generate, /apiPost\(["']\/api\/generate\/start["']/);
});

test("Generate.jsx routes a 402 through isPaywallError into the payment-step states", () => {
	assert.match(generate, /isPaywallError\(e\)/);
	assert.match(generate, /PAYMENT_STATUS\.WALLET_REQUIRED/);
	assert.match(generate, /PAYMENT_STATUS\.PAYMENT_PREVIEW/);
	assert.match(generate, /open-wallet-modal/);
});
