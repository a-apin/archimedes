// Pure, framework-free helpers for the Generate page's upfront cost-quote
// step (docs/specs/generation-quote-contract.md — PROPOSED, feature-flagged
// via GENERATION_QUOTE_ENABLED in featureFlags.js).
//
// Kept out of Generate.jsx so the state-transition logic — what payload
// goes to /start, what the payment step shows after a 402 — is unit
// testable without a DOM. This repo's `ui/test` has no jsdom /
// testing-library; see ui/test/routes.test.js for the established pattern
// of testing pure logic directly rather than rendered markup.

/** Lifecycle of the GET /api/generate/quote fetch. */
export const QUOTE_STATUS = {
	IDLE: "idle",
	LOADING: "loading",
	READY: "ready",
	ERROR: "error",
};

/** What the payment step shows once /start responds 402 (paywall active). */
export const PAYMENT_STATUS = {
	NONE: "none",
	WALLET_REQUIRED: "wallet-required",
	// Wallet is connected and the quote is real, but this build doesn't wire
	// actual x402 signing yet — render the state honestly instead of a pay
	// button that doesn't work. See PR notes / contract doc "out of scope".
	PAYMENT_PREVIEW: "payment-preview",
};

/**
 * Shape a raw GET /api/generate/quote response into what the quote card
 * needs to render. Returns null for a missing/falsy quote (nothing to
 * show yet).
 */
export function deriveQuoteView(quote) {
	if (!quote) return null;
	return {
		priceLabel: `${quote.price_usdc} ${quote.currency}`,
		quoteId: quote.quote_id,
		breakdown: Array.isArray(quote.breakdown) ? quote.breakdown : [],
		expiresAt: quote.expires_at ?? null,
	};
}

/**
 * True once a quote's `expires_at` (ISO-8601) is at/past `now`. A quote
 * with no `expires_at` never expires (backend opted out of the check).
 */
export function isQuoteExpired(quote, now = new Date()) {
	if (!quote?.expires_at) return false;
	const exp = new Date(quote.expires_at);
	if (Number.isNaN(exp.getTime())) return false;
	return exp.getTime() <= now.getTime();
}

/**
 * Build the /api/generate/start payload. quote_id is attached only when
 * the quote flow is enabled AND a quote was actually approved — never
 * fabricated, never carried over stale. When the flag is off, or nothing
 * has been approved yet, the payload passes through unchanged so a
 * flag-off build behaves exactly like before this feature existed.
 */
export function attachQuoteId(payload, { quoteEnabled, approvedQuoteId }) {
	if (!quoteEnabled || !approvedQuoteId) return payload;
	return { ...payload, quote_id: approvedQuoteId };
}

/**
 * Decide the payment-step state from the currently connected wallet
 * address. Fails closed to WALLET_REQUIRED for any falsy/blank address —
 * a payment can't be previewed for a wallet that isn't there.
 */
export function derivePaymentState(walletAddress) {
	return walletAddress && walletAddress.trim()
		? PAYMENT_STATUS.PAYMENT_PREVIEW
		: PAYMENT_STATUS.WALLET_REQUIRED;
}

/**
 * True only for a genuine HTTP 402 as thrown by api.js's apiGet/apiPost
 * (they set `err.status` from the response). Guards against treating a
 * generic network failure or a 5xx as "payment required".
 */
export function isPaywallError(err) {
	return Boolean(err) && err.status === 402;
}
