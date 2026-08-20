// Pure, framework-free helpers for the Generate page's upfront cost-quote +
// x402 paywall step (docs/specs/generation-quote-contract.md — RATIFIED,
// pinned by backend/tests/test_generate_payment_gate.py in #1296;
// feature-flagged via GENERATION_QUOTE_ENABLED in featureFlags.js).
//
// Kept out of Generate.jsx so the state-transition logic — what a 402/409
// from /start means, which linked wallet a payment binds to — is unit
// testable without a DOM. This repo's `ui/test` has no jsdom /
// testing-library; see ui/test/routes.test.js for the established pattern
// of testing pure logic directly rather than rendered markup.
//
// NOTE on scope: this build does not perform real x402 signing (EIP-712
// authorization + wallet signature) — that lands once the payment rail is
// wired end to end. What IS wired: the honest quote card, the 402/409
// state routing, a functional "test mode" continuation while the backend
// runs PAYMENTS_DRY_RUN (which accepts any non-empty Payment-Signature
// header without verifying it — see generation_payment.py on the backend),
// and passive receipt/mismatch surfacing for when real signing lands.

/** Lifecycle of the GET /api/generate/quote fetch. */
export const QUOTE_STATUS = {
	IDLE: "idle",
	LOADING: "loading",
	READY: "ready",
	ERROR: "error",
};

/**
 * What the payment step shows once /start responds 402 or 409 (paywall
 * gate active). WALLET_LINK_REQUIRED is the 409 precondition; DRY_RUN and
 * LIVE_UNAVAILABLE are the two possible 402s, distinguished by the quote's
 * `dry_run` flag — the backend's PAYMENTS_DRY_RUN mirror (#1296).
 */
export const PAYMENT_STATUS = {
	NONE: "none",
	WALLET_LINK_REQUIRED: "wallet-link-required",
	// PAYMENTS_DRY_RUN on the backend: any non-empty Payment-Signature header
	// is accepted WITHOUT verify/settle. Real functionality (the job actually
	// starts), honestly labeled — never dressed up as a real settlement.
	DRY_RUN: "dry-run",
	// Live paywall, but this build doesn't sign real x402 payments yet —
	// render the honest preview rather than a pay button that doesn't work.
	LIVE_UNAVAILABLE: "live-unavailable",
};

/**
 * Shape a raw GET /api/generate/quote response into what the quote card
 * needs to render. Returns null for a missing/falsy quote (nothing to
 * show yet). Ratified shape (#1296): payment_required, pricing_model,
 * price (a "$X.XXXXXX" decimal string, not a float), asset, chain,
 * recipient, dry_run, how. There is no quote_id and no expires_at — the
 * PROPOSED contract this replaced had both; the ratified one has neither
 * (the price is flat and re-quoted fresh on every /start attempt, so
 * there's nothing to echo back or time out).
 */
export function deriveQuoteView(quote) {
	if (!quote) return null;
	return {
		paymentRequired: Boolean(quote.payment_required),
		pricingModel: quote.pricing_model ?? null,
		price: quote.price,
		asset: quote.asset,
		chain: quote.chain,
		recipient: quote.recipient ?? null,
		dryRun: Boolean(quote.dry_run),
		how: quote.how ?? "",
	};
}

/**
 * True only for a genuine HTTP 402 as thrown by api.js's apiPost /
 * apiPostWithMeta (they set `err.status` from the response). Guards
 * against treating a generic network failure or a 5xx as "payment
 * required".
 */
export function isPaywallError(err) {
	return Boolean(err) && err.status === 402;
}

/**
 * True only for the wallet-link-required 409 — checked via the error
 * body's `detail.reason` (api.js attaches the parsed FastAPI error body as
 * `err.detail`), not just the status code: a bare 409 status alone isn't
 * enough — some other future 409 on this endpoint must not be mistaken
 * for this specific precondition.
 */
export function isWalletLinkRequiredError(err) {
	return Boolean(err) && err.status === 409 && err.detail?.reason === "wallet_link_required";
}

/**
 * Classify a /start error into a payment-step state. Anything that isn't
 * the wallet-link 409 or a genuine 402 fails closed to NONE — callers
 * fall through to the generic error banner for those, never a
 * payment-specific one.
 */
export function derivePaymentState(err, quoteDryRun) {
	if (isWalletLinkRequiredError(err)) return PAYMENT_STATUS.WALLET_LINK_REQUIRED;
	if (isPaywallError(err)) return quoteDryRun ? PAYMENT_STATUS.DRY_RUN : PAYMENT_STATUS.LIVE_UNAVAILABLE;
	return PAYMENT_STATUS.NONE;
}

/**
 * The human-readable message the backend attached to a 402/409 error body
 * (`detail.message` — #1296's shape). Falls back to a generic string so a
 * malformed or absent detail never crashes the banner; never fabricates
 * backend wording otherwise — the faucet caveat on the 409 in particular
 * must render verbatim, not be re-paraphrased.
 */
export function paymentErrorMessage(err, fallback) {
	return err?.detail?.message || fallback || "Payment step failed.";
}

/**
 * The human-readable message the backend attached to a /start failure that
 * is NOT a payment-gate response (402/409 — those route through
 * paymentErrorMessage instead: quota 429s, the burst-limit 429, and auth
 * 401s land here). `err.detail` (api.js's apiPostWithMeta attaches the
 * parsed body's `detail` field, see #1296) arrives in one of two shapes on
 * this endpoint:
 *   - a dict `{message, reason, ...}` — generation_quota.py's daily-cap and
 *     quota-unavailable 429/503s.
 *   - a plain string — the slowapi burst-limit 429 and the 401's
 *     "Authentication required", both FastAPI/slowapi convention.
 * Either shape renders VERBATIM — same rule paymentErrorMessage's docstring
 * states for the faucet caveat, never re-worded. Falls back to `fallback`
 * (a short, honest sentence naming the status — never the bare `Backend
 * returned <status>` echo api.js's Error.message carries) when detail is
 * absent or malformed, so this never crashes and never regresses to the
 * status-echo issue #1363 fixed.
 */
export function startErrorMessage(err, fallback) {
	const detail = err?.detail;
	if (typeof detail === "string" && detail.trim()) return detail;
	if (detail && typeof detail === "object" && typeof detail.message === "string" && detail.message.trim()) {
		return detail.message;
	}
	return fallback || "Failed to start generation";
}

/**
 * The Depth control's offered values. MUST equal the pipeline's actually
 * enforced range — MIN_PAPERS..FUSION_MAX_PAPERS in
 * backend/archimedes/agents/strategy_fusion.py (2..6 today) — never a
 * superset. Issue #1363: the UI used to offer 8 and 10, both silently
 * clamped to 6 by the pipeline; the drift guard for the backend half of
 * this contract lives in backend/tests/test_generate_schemas_depth_drift.py.
 */
export const DEPTH_OPTIONS = [2, 3, 4, 5, 6];

/**
 * The account's PRIMARY linked wallet from a GET /api/wallets response
 * (list of LinkedWalletResponse) — or the first entry if none is flagged
 * primary. Null for an empty/missing list.
 */
export function primaryLinkedWallet(linkedWallets) {
	if (!Array.isArray(linkedWallets) || linkedWallets.length === 0) return null;
	return linkedWallets.find((w) => w?.is_primary) ?? linkedWallets[0];
}

/**
 * Detect a payer-binding mismatch worth flagging: the wallet currently
 * active in the injected provider (config.js's getAddress()) is NOT any
 * of the account's linked wallets. Returns null when there's nothing to
 * flag — no active address, no linked wallets yet (the 409 CTA already
 * covers that case), or the active address IS one of the linked wallets
 * (a payment from it resolves to itself server-side, #1296's
 * get_linked_wallet_address — no mismatch).
 *
 * This is a transparency check, not the enforcement gate: the backend is
 * the actual authority (payer_mismatch, #1296) — this only lets the UI
 * warn BEFORE a signing attempt would fail, using the LINKED wallet's
 * account rather than blindly trusting whatever's active in the provider.
 */
export function describePayerMismatch(activeAddress, linkedWallets) {
	const active = (activeAddress || "").trim().toLowerCase();
	const wallets = Array.isArray(linkedWallets) ? linkedWallets : [];
	if (!active || wallets.length === 0) return null;
	const activeIsLinked = wallets.some((w) => (w?.address || "").toLowerCase() === active);
	if (activeIsLinked) return null;
	const primary = primaryLinkedWallet(wallets);
	return { active: activeAddress, linked: primary.address };
}

/**
 * Base64-JSON, matching circlekit's Payment-Signature wire encoding (see
 * backend/tests/test_generate_payment_gate.py's `_payment_header`) —
 * portable across the browser (btoa) and the node:test runner (Buffer).
 */
function toBase64(str) {
	if (typeof btoa === "function") return btoa(str);
	// globalThis.Buffer (not a bare `Buffer` reference) so this line is
	// statically valid under the browser-only eslint globals this repo lints
	// with — the branch itself only ever runs under node:test, where
	// globalThis.Buffer is the real Node Buffer.
	return globalThis.Buffer.from(str, "utf-8").toString("base64");
}

/**
 * Build the stand-in Payment-Signature header used ONLY to continue in
 * PAYMENTS_DRY_RUN test mode (PAYMENT_STATUS.DRY_RUN) — NOT a real
 * signature. The backend's dry-run path accepts any non-empty header
 * without decoding or verifying it, so this exists purely so the "from"
 * inside it is honest rather than absent: the payer named is the caller's
 * OWN linked wallet (the same address that already cleared the 409
 * wallet-link precondition to reach this state), never fabricated.
 */
export function buildDryRunPaymentHeader(payerAddress) {
	// Loud contract for #1298's implementer: a payment header with no payer is
	// never meaningful — dry-run happens not to read it today, but real x402
	// signing will, and the server's payer binding (#1296) requires the LINKED
	// wallet. Callers must resolve a payer first (linked wallet preferred,
	// active browser wallet as fallback) and handle the none-available case in
	// the UI rather than shipping an empty `from` here.
	if (!payerAddress) {
		throw new Error("buildDryRunPaymentHeader requires a payer address (the caller's linked wallet)");
	}
	return toBase64(JSON.stringify({ payload: { authorization: { from: payerAddress } } }));
}

/**
 * Resolve the dry-run payer ADDRESS: the account's primary linked wallet's
 * address first (the server-side truth that already cleared the 409 wallet
 * precondition), the browser-connected wallet second, null when neither
 * exists. Always a string or null — NEVER the LinkedWalletResponse object
 * (#1299 review: the call site passed `primaryLinkedWallet(...)` — the whole
 * object — into buildDryRunPaymentHeader, so the header's `from` carried an
 * object whenever a linked wallet existed; the object is truthy, so neither
 * the caller's no-payer bail nor the header's falsy-payer throw caught it).
 */
export function resolveDryRunPayer(linkedWallets, walletAddr) {
	return primaryLinkedWallet(linkedWallets)?.address || walletAddr || null;
}

/**
 * The settlement receipt (PAYMENT-RESPONSE header, #1296) from a
 * successful /start response, if present — surfaced subtly, never
 * fabricated. Accepts either a Fetch `Headers` object or a plain object
 * (tests use the latter); header names are matched case-insensitively
 * either way, since a plain object isn't guaranteed to normalize casing
 * the way `Headers` does.
 */
export function extractReceipt(headers) {
	if (!headers) return null;
	if (typeof headers.get === "function") {
		return headers.get("PAYMENT-RESPONSE") || headers.get("payment-response") || null;
	}
	const key = Object.keys(headers).find((k) => k.toLowerCase() === "payment-response");
	return key ? headers[key] : null;
}
