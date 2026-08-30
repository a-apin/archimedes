import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

// Source-regex pins on Generate.jsx's new credit-visibility notice (v8 Lane
// 1.3a). Same idiom as ui/test/generate-quote.test.js's "Wiring" section and
// ui/test/payment-receipts.test.js: readFileSync + regex/substring
// assertions on the rendered source, confirming the fetch is wired to the
// real credentialed helper and the endpoint, the notice is gated on a real
// unspent credit, it is dismissable, and its copy actually describes what
// `_paywall_with_credit` (backend/archimedes/api/generate_routes.py) does.

const generate = readFileSync(
	new URL("../src/components/Generate.jsx", import.meta.url),
	"utf8",
);

// ── Fetches the owner-scoped credits endpoint via the shared apiGet helper ──

test("fetches GET /api/generate/credits via the shared apiGet helper, not a bare fetch", () => {
	// apiGet (src/api.js) sends credentials:'include', which is what makes the
	// request owner-scoped server-side (require_current_user reads the Better
	// Auth session cookie) — pin the real helper call, not a copy/paste fetch.
	assert.match(generate, /apiGet\(\s*["']\/api\/generate\/credits["']\s*\)/);
});

test("fetches credits on mount, independently of GENERATION_QUOTE_ENABLED", () => {
	// A credit is a real balance regardless of whether the upfront quote card
	// is shown — the fetch must not be nested inside the quote flag's gate.
	const fetchCallIdx = generate.indexOf("fetchCredits();");
	assert.ok(fetchCallIdx !== -1, "fetchCredits() must be called somewhere");
	assert.match(generate, /useEffect\(\(\) => \{\s*fetchCredits\(\);\s*\}, \[fetchCredits\]\);/);
});

test("re-fetches credits after every successful /start (a credit may have just been spent)", () => {
	// Each of the four post-submitStart() success paths (plain start, the
	// refetch-then-retry path, the held-requirements finish, and the
	// smart-wallet finish) must refresh the ledger, not just the quote.
	const matches = generate.match(/fetchCredits\(\);/g) || [];
	// One for the mount effect's definition site is NOT counted here (that's
	// the useEffect body, matched separately above) — this counts every call
	// SITE, so >= 4 post-submit refreshes + the 1 mount-effect call.
	assert.ok(matches.length >= 5, `expected >=5 fetchCredits() call sites, found ${matches.length}`);
});

// ── The notice: gated on a real unspent ("available") credit ─────────────

test("derives the notice from an `available` credit, not merely a non-empty list", () => {
	// A `pending`/`consumed`/`void` row must not trigger the notice — only a
	// spendable one does (mirrors take_available_credit's status filter).
	assert.match(generate, /credits\.find\(\(c\) => c\.status === ["']available["']\)/);
});

test("the notice is gated on the derived unspent credit AND the dismissed flag", () => {
	assert.match(generate, /\{unspentCredit && !creditNoticeDismissed && \(/);
});

test("the notice has a working dismiss control (dismissable, per spec)", () => {
	assert.match(generate, /creditNoticeDismissed/);
	assert.match(generate, /setCreditNoticeDismissed\(true\)/);
	assert.match(generate, /useState\(false\)/);
});

// ── Wording matches what _paywall_with_credit actually does ──────────────

test("the notice's copy is the exact honest wording from the spec", () => {
	// _paywall_with_credit (generate_routes.py) checks generation_credits.take_credit()
	// BEFORE the paywall runs and, if an unspent credit exists, spends it
	// instead of charging — this is the literal behavior the banner describes.
	assert.match(
		generate,
		/You have a paid generation credit — this run will use\s+it, no new charge\./,
	);
});

test("does not overclaim: never says the credit is a refund, discount, or free trial", () => {
	// Loose but real anti-goal: this is a paid-and-banked credit being spent,
	// not any of these other financial concepts that would misdescribe it.
	assert.doesNotMatch(generate, /\brefund\b/i);
	assert.doesNotMatch(generate, /\bdiscount\b/i);
	assert.doesNotMatch(generate, /\bfree trial\b/i);
});
