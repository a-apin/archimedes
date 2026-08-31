import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
	ACCOUNT_USAGE_ENDPOINT,
	deriveFreeGenerationView,
} from "../src/freeGenerations.js";

// Free-generation allowance banner (#1643).
//
// Two halves, deliberately:
//   1. REAL unit tests of ../src/freeGenerations.js — the module that decides
//      whether and what to show. Every honesty rule this feature makes on the
//      frontend is executable here, not asserted by regex.
//   2. A small set of source pins on the component + its single Generate.jsx
//      mount point (the ui/test/generation-credits.test.js idiom), because
//      `node --test` has no DOM and the wiring cannot be executed.

const banner = readFileSync(
	new URL("../src/components/FreeGenerationBanner.jsx", import.meta.url),
	"utf8",
);
const generate = readFileSync(
	new URL("../src/components/Generate.jsx", import.meta.url),
	"utf8",
);

const usage = (overrides = {}) => ({
	date: "2026-08-31",
	user_id: "u1",
	user: { used: 0, cap: 10, unlimited: false, remaining: 10, error: null },
	ip: { used: 0, cap: 20, unlimited: false, remaining: 20, error: null },
	quote: { payment_required: true },
	free_generations_allowance: 3,
	free_generations_remaining: 3,
	free_generations_error: null,
	...overrides,
});

// ── What the banner shows when the backend sent a real number ──────────────

test("a fresh account shows 3 of 3 left and says no wallet is needed yet", () => {
	const view = deriveFreeGenerationView(usage());
	assert.equal(view.remaining, 3);
	assert.equal(view.allowance, 3);
	assert.equal(view.exhausted, false);
	assert.equal(view.chipLabel, "3 free generations left");
	assert.match(view.message, /3 of 3 free generations left/);
	assert.match(view.message, /No wallet needed/i);
});

test("the last remaining generation is singular, not '1 free generations'", () => {
	const view = deriveFreeGenerationView(usage({ free_generations_remaining: 1 }));
	assert.equal(view.chipLabel, "1 free generation left");
	assert.match(view.message, /1 of 3 free generation left/);
});

test("an exhausted allowance switches to the wallet-gate message", () => {
	const view = deriveFreeGenerationView(usage({ free_generations_remaining: 0 }));
	assert.equal(view.exhausted, true);
	assert.equal(view.chipLabel, "Free generations used");
	assert.match(view.message, /used all 3 free generations/);
	assert.match(view.message, /Link a wallet/);
});

test("a non-default allowance is rendered as configured, never hard-coded to 3", () => {
	const view = deriveFreeGenerationView(
		usage({ free_generations_allowance: 5, free_generations_remaining: 4 }),
	);
	assert.equal(view.allowance, 5);
	assert.equal(view.chipLabel, "4 free generations left");
});

// ── The honesty rule: a number is shown only when the backend sent one ─────
//
// These are the adversarial cases. Each input is one a naive implementation
// renders as a confident, wrong number; every one of them must render nothing.

test("free_generations_remaining: null renders NOTHING — not 0, not the allowance", () => {
	// The backend's honest "the ledger could not be read". Rendering 0 would
	// tell a brand-new account it is locked out; rendering 3 would promise
	// free runs the gate may refuse.
	const view = deriveFreeGenerationView(
		usage({ free_generations_remaining: null, free_generations_error: "free_generation_backend_unavailable" }),
	);
	assert.equal(view, null);
});

test("a response missing the free_generations_* fields entirely renders nothing", () => {
	// An older backend, or a partial/proxied response.
	const { free_generations_allowance, free_generations_remaining, ...rest } = usage();
	void free_generations_allowance;
	void free_generations_remaining;
	assert.equal(deriveFreeGenerationView(rest), null);
});

test("allowance 0 (the free path switched off) renders nothing, not '0 left'", () => {
	// FREE_GENERATIONS_PER_ACCOUNT=0 means the policy is not running at all; a
	// "0 free generations left" chip would imply it is running and exhausted.
	assert.equal(
		deriveFreeGenerationView(usage({ free_generations_allowance: 0, free_generations_remaining: 0 })),
		null,
	);
});

test("no response at all (signed out / failed fetch) renders nothing", () => {
	assert.equal(deriveFreeGenerationView(null), null);
	assert.equal(deriveFreeGenerationView(undefined), null);
	assert.equal(deriveFreeGenerationView("not an object"), null);
});

test("a malformed count renders nothing rather than a nonsense chip", () => {
	for (const bad of [-1, 1.5, "3", Number.NaN, {}]) {
		assert.equal(
			deriveFreeGenerationView(usage({ free_generations_remaining: bad })),
			null,
			`remaining=${String(bad)} must render nothing`,
		);
	}
});

test("remaining above the allowance is clamped, never shown as '9 of 3'", () => {
	const view = deriveFreeGenerationView(
		usage({ free_generations_allowance: 3, free_generations_remaining: 9 }),
	);
	assert.equal(view.remaining, 3);
	assert.equal(view.chipLabel, "3 free generations left");
});

// ── Wiring pins (no DOM available under `node --test`) ─────────────────────

test("the component reads the count from the backend, via the credentialed helper", () => {
	// apiGet (src/api.js) sends credentials:'include' — that cookie is what
	// makes /api/account/usage answer for THIS account. A bare fetch would
	// silently 401 for every signed-in user.
	assert.equal(ACCOUNT_USAGE_ENDPOINT, "/api/account/usage");
	assert.match(banner, /import \{ apiGet \} from "\.\.\/api"/);
	assert.match(banner, /apiGet\(ACCOUNT_USAGE_ENDPOINT\)/);
});

test("the component never counts generations itself — it only renders the backend's number", () => {
	// The gate is server-side; a client-side tally would drift from it and
	// would be trivially resettable by reloading the page.
	assert.doesNotMatch(banner, /localStorage|sessionStorage/);
	assert.doesNotMatch(banner, /\b(?:count|remaining)\s*(?:\+\+|--|\+=|-=)/);
	assert.match(banner, /deriveFreeGenerationView\(usage\)/);
});

test("a failed request sets no view — the banner never invents a fallback count", () => {
	const catchIdx = banner.indexOf(".catch(");
	assert.ok(catchIdx !== -1, "the fetch must have a catch handler");
	const catchBlock = banner.slice(catchIdx, catchIdx + 400);
	assert.match(catchBlock, /setView\(null\)/);
	assert.doesNotMatch(catchBlock, /setView\(\{/);
});

test("Generate.jsx mounts the banner exactly once, and imports it as its own component", () => {
	// The #1642 redesign is rewriting Generate.jsx concurrently, so this
	// feature's footprint there is one import + one element, and this test is
	// what keeps it that way.
	assert.match(generate, /import FreeGenerationBanner from "\.\/FreeGenerationBanner"/);
	const mounts = generate.match(/<FreeGenerationBanner\s*\/>/g) || [];
	assert.equal(mounts.length, 1, `expected exactly one mount, found ${mounts.length}`);
});

test("Insights.jsx labels the two new funnel stages instead of showing raw keys", () => {
	const insights = readFileSync(
		new URL("../src/components/Insights.jsx", import.meta.url),
		"utf8",
	);
	assert.match(insights, /free_generation_used:\s*'[^']+'/);
	assert.match(insights, /wallet_gate_shown:\s*'[^']+'/);
	// And in the backend's STAGES order — the funnel's step_conversion is
	// computed against the immediately preceding stage, so a label map that
	// implied a different journey would mislabel a real number.
	const order = ["generation_started", "free_generation_used", "wallet_gate_shown", "wallet_connected"];
	const positions = order.map((k) => insights.indexOf(`${k}:`));
	assert.deepEqual(
		positions,
		[...positions].sort((a, b) => a - b),
		"CORE_FUNNEL_LABELS keys must follow the backend STAGES order",
	);
});
