import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
	NOT_MEASURED,
	NOT_MEASURED_HINT,
	UMBRELLA_STAGES,
	compactCostCell,
	deriveGenerationCostView,
	deriveRecordedQuote,
	dominantStage,
	formatDuration,
	formatTokenCount,
	quoteLabel,
	quoteNote,
	quotePriceLabel,
	stageLabel,
	tokensLabel,
	usageNote,
} from "../src/generationCost.js";

// A representative durable record, matching what the backend serves on
// `strategy.generation_cost` (#1326): the cost_v1 measurement in one field, the
// literal generation_payment.quote() payload in another.
const RECORD = {
	schema: "cost_v1",
	job_id: "9f2c8a1b4d5e6f70",
	recorded_at: "2026-08-20T09:16:04.228106+00:00",
	measurement: {
		schema: "cost_v1",
		job_id: "9f2c8a1b4d5e6f70",
		wall_seconds: 47.9312,
		cpu_seconds: 31.4407,
		llm: {
			calls: 17,
			calls_missing_usage: 0,
			usage_complete: true,
			input_tokens: 41234,
			output_tokens: 5120,
			total_tokens: 46354,
		},
		stages: {
			candidate_generation: { wall_seconds: 43.1, cpu_seconds: 24.9, runs: 1 },
			debate_backtest: { wall_seconds: 21.55, cpu_seconds: 20.9, runs: 1 },
			debate_propose: { wall_seconds: 12.8, cpu_seconds: 1.1, runs: 1 },
			rigor_gate: { wall_seconds: 0.9, cpu_seconds: 0.88, runs: 1 },
		},
		writes: { strategy_store: 1, strategy_passports: 2 },
		meta: { pipeline: "debate", outcome: "done", candidates_passing_rigor: 0 },
	},
	quote: {
		payment_required: true,
		pricing_model: "flat_v1",
		price: "$0.150000",
		asset: "USDC",
		chain: "eip155:5042002",
		recipient: "0xRecipient",
		dry_run: true,
		how: "POST /api/generate/start ...",
	},
};

// ── The record shapes into a view ─────────────────────────────────────────

test("deriveGenerationCostView shapes the measurement and the quote as two separate facts", () => {
	const view = deriveGenerationCostView(RECORD);
	assert.equal(view.schema, "cost_v1");
	assert.equal(view.jobId, "9f2c8a1b4d5e6f70");
	assert.equal(view.wallSeconds, 47.9312);
	assert.equal(view.cpuSeconds, 31.4407);
	assert.equal(view.tokens.input, 41234);
	assert.equal(view.tokens.output, 5120);
	assert.equal(view.tokens.total, 46354);
	assert.equal(view.tokens.calls, 17);
	assert.equal(view.usageComplete, true);
	assert.equal(view.quote.price, "$0.150000");
	assert.equal(view.quote.pricingModel, "flat_v1");
	// No $-figure is ever derived FROM the tokens — the only money on the view
	// is the string the server recorded from the quote seam.
	assert.equal("usd" in view, false);
	assert.equal("priceFromTokens" in view, false);
});

test("deriveGenerationCostView returns null when there is no record and when the record carries no measurement", () => {
	assert.equal(deriveGenerationCostView(null), null);
	assert.equal(deriveGenerationCostView(undefined), null);
	assert.equal(deriveGenerationCostView({}), null);
	assert.equal(deriveGenerationCostView({ measurement: null, quote: RECORD.quote }), null);
	// A record carrying only a QUOTE is not a measurement. Rendering it as a
	// cost card would put a price where a measurement belongs.
	assert.equal(deriveGenerationCostView({ schema: "cost_v1", quote: RECORD.quote }), null);
});

// ── GUARD: unknown is never zero ──────────────────────────────────────────

test("GUARD: a missing token total renders as unknown, never as 0", () => {
	const record = {
		...RECORD,
		measurement: {
			...RECORD.measurement,
			llm: { calls: 4, calls_missing_usage: 4, usage_complete: false },
		},
	};
	const view = deriveGenerationCostView(record);
	assert.equal(view.tokens.total, null);
	assert.equal(view.tokens.input, null);
	assert.equal(view.tokens.output, null);
	assert.equal(tokensLabel(view), NOT_MEASURED);
	assert.notEqual(tokensLabel(view), "0");
	assert.equal(formatTokenCount(undefined), NOT_MEASURED);
	assert.equal(formatTokenCount(null), NOT_MEASURED);
});

test("GUARD: an unusable token count is unknown, not banked — strings, booleans, NaN, Infinity, negatives", () => {
	// Mirrors cost_meter._coerce_count server-side: a stringly-typed count is a
	// parse we did not do, so it is not a measurement we may report.
	for (const bad of ["46354", true, false, NaN, Infinity, -1, {}, []]) {
		const view = deriveGenerationCostView({
			...RECORD,
			measurement: { ...RECORD.measurement, llm: { ...RECORD.measurement.llm, total_tokens: bad } },
		});
		assert.equal(view.tokens.total, null, `expected ${String(bad)} to read as unknown`);
		assert.equal(tokensLabel(view), NOT_MEASURED);
	}
});

test("GUARD: a genuinely measured zero still renders as 0 — the fixture path makes no LLM calls", () => {
	// The other half of the same rule. If unknown collapsed onto zero, this case
	// would be indistinguishable from the one above; it must not be.
	const view = deriveGenerationCostView({
		...RECORD,
		measurement: {
			...RECORD.measurement,
			llm: {
				calls: 0,
				calls_missing_usage: 0,
				usage_complete: true,
				input_tokens: 0,
				output_tokens: 0,
				total_tokens: 0,
			},
		},
	});
	assert.equal(view.tokens.total, 0);
	assert.equal(tokensLabel(view), "0");
	assert.equal(formatTokenCount(0), "0");
});

// ── GUARD: a floor is never presented as a total ──────────────────────────

test("GUARD: usage_complete=false renders the totals as a floor (≥) and says how many calls were unreadable", () => {
	const view = deriveGenerationCostView({
		...RECORD,
		measurement: {
			...RECORD.measurement,
			llm: {
				calls: 17,
				calls_missing_usage: 3,
				usage_complete: false,
				input_tokens: 30000,
				output_tokens: 4000,
				total_tokens: 34000,
			},
		},
	});
	assert.equal(view.usageComplete, false);
	assert.equal(tokensLabel(view), "≥ 34,000");
	assert.match(usageNote(view), /3 of 17/);
	assert.match(usageNote(view), /floor, not the total/);
});

test("GUARD: an absent usage_complete flag fails CLOSED — never claimed complete", () => {
	const view = deriveGenerationCostView({
		...RECORD,
		measurement: {
			...RECORD.measurement,
			llm: { calls: 5, input_tokens: 10, output_tokens: 5, total_tokens: 15 },
		},
	});
	assert.equal(view.usageKnown, false);
	assert.equal(view.usageComplete, false);
	assert.equal(tokensLabel(view), "≥ 15");
	assert.match(usageNote(view), /did not record whether/);
});

test("a complete measurement says so plainly", () => {
	const view = deriveGenerationCostView(RECORD);
	assert.equal(tokensLabel(view), "46,354");
	assert.match(usageNote(view), /^Complete/);
});

// ── GUARD: the dominant stage is not the umbrella ─────────────────────────

test("GUARD: dominantStage excludes candidate_generation, the umbrella that contains the sub-phases", () => {
	// candidate_generation has the LARGEST wall_seconds in the fixture (43.1 vs
	// 21.55) because the sub-phases run inside it. A plain max would name it
	// every time and say nothing about where the run's time went.
	assert.ok(UMBRELLA_STAGES.has("candidate_generation"));
	const best = dominantStage(RECORD.measurement);
	assert.equal(best.name, "debate_backtest");
	assert.equal(best.wallSeconds, 21.55);
	assert.notEqual(best.name, "candidate_generation");
});

test("dominantStage is unknown when only umbrella stages, no stages, or unreadable stages are present", () => {
	assert.equal(dominantStage({ stages: { candidate_generation: { wall_seconds: 43.1 } } }), null);
	assert.equal(dominantStage({ stages: {} }), null);
	assert.equal(dominantStage({}), null);
	assert.equal(dominantStage(null), null);
	assert.equal(dominantStage({ stages: { rigor_gate: { wall_seconds: "0.9" } } }), null);
});

test("stageLabel humanizes known stages and passes unknown ones through verbatim", () => {
	assert.equal(stageLabel("debate_backtest"), "Debate — backtest");
	assert.equal(stageLabel("some_future_stage"), "some_future_stage");
	assert.equal(stageLabel(null), NOT_MEASURED);
});

// ── Duration formatting ───────────────────────────────────────────────────

test("formatDuration: unknown is an em-dash; sub-second, seconds and minutes each read naturally", () => {
	assert.equal(formatDuration(null), NOT_MEASURED);
	assert.equal(formatDuration("47.9"), NOT_MEASURED);
	assert.equal(formatDuration(NaN), NOT_MEASURED);
	assert.equal(formatDuration(0.42), "0.42 s");
	assert.equal(formatDuration(0), "0.00 s");
	assert.equal(formatDuration(47.9312), "47.9 s");
	assert.equal(formatDuration(192), "3 m 12 s");
});

// ── The quote is a recorded fact, shown as recorded ───────────────────────

test("quotePriceLabel trims trailing zeros without rounding, and passes anything unexpected through unchanged", () => {
	assert.equal(quotePriceLabel("$0.150000"), "$0.15");
	assert.equal(quotePriceLabel("$1.000000"), "$1.00");
	assert.equal(quotePriceLabel("$0.000001"), "$0.000001");
	assert.equal(quotePriceLabel("$0.123456"), "$0.123456");
	// Not a decimal price → returned exactly as recorded, never reformatted
	// into a guess.
	assert.equal(quotePriceLabel("free"), "free");
	assert.equal(quotePriceLabel(""), NOT_MEASURED);
	assert.equal(quotePriceLabel(null), NOT_MEASURED);
});

test("quoteLabel and quoteNote carry the recorded quote, and say so when none was recorded", () => {
	const view = deriveGenerationCostView(RECORD);
	assert.equal(quoteLabel(view), "$0.15 USDC");
	assert.match(quoteNote(view), /flat_v1/);
	assert.match(quoteNote(view), /dry run/);

	const noQuote = deriveGenerationCostView({ ...RECORD, quote: null });
	assert.equal(noQuote.quote, null);
	assert.equal(quoteLabel(noQuote), null);
	assert.equal(quoteNote(noQuote), "No quote was recorded for this run.");
});

test("a quote recorded while the paywall was OFF says nothing was charged, rather than implying a payment", () => {
	const view = deriveGenerationCostView({
		...RECORD,
		quote: { ...RECORD.quote, payment_required: false, dry_run: false },
	});
	assert.equal(view.quote.paymentRequired, false);
	assert.match(quoteNote(view), /paywall was off/);
});

test("deriveRecordedQuote reads only the ratified quote fields and never invents a price", () => {
	assert.equal(deriveRecordedQuote(null), null);
	assert.equal(deriveRecordedQuote("$0.15"), null);
	const q = deriveRecordedQuote({ price: 0.15, pricing_model: "flat_v1" });
	// A numeric price is not the recorded string shape — not read, not guessed.
	assert.equal(q.price, null);
	assert.equal(q.pricingModel, "flat_v1");
	assert.equal(q.paymentRequired, false);
});

// ── The library cell ──────────────────────────────────────────────────────

test("compactCostCell: no record renders the em-dash plus an explicit not-measured tooltip", () => {
	const cell = compactCostCell(null);
	assert.equal(cell.label, NOT_MEASURED);
	assert.equal(cell.measured, false);
	assert.equal(cell.title, NOT_MEASURED_HINT);
	assert.match(cell.title, /Not measured/);
	// Never a zero, and never an empty string that reads as "nothing to say".
	assert.notEqual(cell.label, "0");
	assert.notEqual(cell.label, "");
});

test("compactCostCell: a measured record shows tokens, with wall time and the dominant stage in the tooltip", () => {
	const cell = compactCostCell(RECORD);
	assert.equal(cell.label, "46,354");
	assert.equal(cell.measured, true);
	assert.match(cell.title, /in 41,234 \/ out 5,120/);
	assert.match(cell.title, /wall 47\.9 s/);
	assert.match(cell.title, /dominant stage Debate — backtest/);
	assert.doesNotMatch(cell.title, /candidate generation/i);
});

test("compactCostCell: a record whose token count is unreadable says so in words, in the cell and the tooltip", () => {
	const cell = compactCostCell({
		...RECORD,
		measurement: {
			...RECORD.measurement,
			llm: { calls: 4, calls_missing_usage: 4, usage_complete: false },
		},
	});
	assert.equal(cell.label, NOT_MEASURED);
	assert.match(cell.title, /Token count not measured for this run/);
	// Never the formatting-bug rendering of the same fact.
	assert.doesNotMatch(cell.title, /— tokens \(in — \/ out —\)/);
	// The rest of the record is still readable and still shown.
	assert.match(cell.title, /wall 47\.9 s/);
});

test("compactCostCell: an incomplete measurement carries the ≥ into the library cell too", () => {
	const cell = compactCostCell({
		...RECORD,
		measurement: {
			...RECORD.measurement,
			llm: { ...RECORD.measurement.llm, usage_complete: false, calls_missing_usage: 2 },
		},
	});
	assert.equal(cell.label, "≥ 46,354");
	assert.match(cell.title, /floor, not the total/);
});

// ── Wiring: the components read the shared helpers rather than forking the
// honesty rules inline. Static-source pins, matching the pattern established
// in ui/test/generate-quote.test.js. ──────────────────────────────────────

const passport = readFileSync(
	new URL("../src/components/StrategyPassport.jsx", import.meta.url),
	"utf8",
);
const library = readFileSync(
	new URL("../src/components/Strategies.jsx", import.meta.url),
	"utf8",
);

test("StrategyPassport.jsx renders the generation-cost card from the shared helpers", () => {
	assert.match(passport, /from ["']\.\.\/generationCost\.js["']/);
	assert.match(passport, /<GenerationCostCard record=\{s\.generation_cost\}/);
	assert.match(passport, /deriveGenerationCostView\(record\)/);
	assert.match(passport, /tokensLabel\(view\)/);
	assert.match(passport, /usageNote\(view\)/);
	assert.match(passport, /quoteLabel\(view\)/);
	assert.match(passport, /NOT_MEASURED_HINT/);
});

test("StrategyPassport.jsx does no $-conversion of token counts", () => {
	// The card may only show the price the SERVER recorded from the quote seam.
	// Any arithmetic between a token count and a rate would be a pricing model
	// living in the frontend.
	assert.doesNotMatch(passport, /tokens?\s*[*/]\s*[\d.]/i);
	assert.doesNotMatch(passport, /per_?(1k|thousand|million|token)/i);
	assert.doesNotMatch(passport, /RATE_CARD|PRICE_PER/i);
});

test("Strategies.jsx renders the library cost column through compactCostCell, in both layouts", () => {
	assert.match(library, /from ["']\.\.\/generationCost\.js["']/);
	assert.match(library, /compactCostCell\(s\.generation_cost\)/);
	assert.match(library, />Gen tokens<\/th>/);
	// The table and the mobile card list must not drift apart — the card list
	// IS the ≤768px layout, so a table-only column is invisible on mobile.
	assert.match(library, /<div className="caption">Gen tokens<\/div>/);
	// The row's detail panel spans every column; adding one without widening
	// the span silently misaligns the expanded row.
	assert.match(library, /colSpan=\{9\}/);
	assert.doesNotMatch(library, /colSpan=\{8\}/);
});

test("Strategies.jsx passes the generation_cost record through the generated-row coercion", () => {
	// coerceGenerated builds the row object the table renders; a field it drops
	// can never reach the cell, however well the cell is written.
	assert.match(library, /generation_cost: row\.generation_cost \?\? null/);
});
