// Generation cost (#1326) — reading the durable `cost_v1` record honestly.
//
// The backend serves `generation_cost` on a strategy as
//   { schema, job_id, recorded_at, measurement: {…cost_v1…}, quote: {…} | null }
// where `measurement` is raw counts and seconds, and `quote` is the literal
// `generation_payment.quote()` payload that was in force when the run started.
// The two are separate columns in the database and stay separate here: nothing
// in this file converts a token count into money. Quote-vs-measured is two
// recorded facts side by side, never a derivation.
//
// The rule this file exists to enforce, from #1314 and restated by #1326:
// **a missing measurement is never a zero.** Every reader below returns `null`
// for "not measured" and the formatters render `null` as an em-dash. A genuine
// measured zero — the fixture path makes no LLM calls and honestly reports
// `total_tokens: 0` — still renders as `0`, and the two must never collapse
// into each other.

/** What every formatter renders for a value nothing measured. */
export const NOT_MEASURED = "—";

export const NOT_MEASURED_HINT =
	"Not measured — this strategy predates generation-cost instrumentation, or its run recorded no measurement.";

// `candidate_generation` is the OUTER stage; `corpus_load` / `debate_propose` /
// `debate_transcript` / `debate_backtest` run inside it and overlap it by
// construction (docs/generation-cost-instrumentation.md § "Reading it
// honestly"). Picking the plain maximum would therefore name the umbrella every
// single time and tell the reader nothing about where the run's time actually
// went, so umbrellas are excluded from the "dominant stage" answer.
export const UMBRELLA_STAGES = new Set(["candidate_generation"]);

const STAGE_LABELS = {
	brief_validation: "Brief validation",
	pipeline_select: "Pipeline select",
	corpus_load: "Corpus retrieval",
	debate_propose: "Debate — propose",
	debate_transcript: "Debate — transcript",
	debate_backtest: "Debate — backtest",
	candidate_generation: "Candidate generation",
	rigor_gate: "Rigor gate",
	persist_winner: "Persist winner",
	backtest_persist: "Backtest persist",
};

/** A human label for a stage name; unknown names pass through verbatim. */
export function stageLabel(name) {
	if (!name) return NOT_MEASURED;
	return STAGE_LABELS[name] || name;
}

// A count is a measurement only when it arrived as a finite, non-negative
// number. A string ("1234"), a boolean, null, undefined, NaN and Infinity are
// all "not measured" — mirroring the server-side `_coerce_count` in
// cost_meter.py, which refuses the same shapes rather than banking them.
function countOrNull(value) {
	if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return null;
	return value;
}

function secondsOrNull(value) {
	return countOrNull(value);
}

/**
 * The stage that consumed the most wall time, excluding umbrella stages.
 * `null` when no non-umbrella stage carries a readable wall time.
 */
export function dominantStage(measurement) {
	const stages = measurement?.stages;
	if (!stages || typeof stages !== "object") return null;
	let best = null;
	for (const [name, bucket] of Object.entries(stages)) {
		if (UMBRELLA_STAGES.has(name)) continue;
		const wallSeconds = secondsOrNull(bucket?.wall_seconds);
		if (wallSeconds == null) continue;
		if (best == null || wallSeconds > best.wallSeconds) best = { name, wallSeconds };
	}
	return best;
}

/**
 * Shape the raw `generation_cost` payload for display.
 * Returns `null` when there is no record at all — the caller renders the
 * "not measured" state rather than an empty card full of em-dashes.
 */
export function deriveGenerationCostView(record) {
	if (!record || typeof record !== "object") return null;
	const measurement = record.measurement;
	if (!measurement || typeof measurement !== "object") return null;

	const llm = measurement.llm && typeof measurement.llm === "object" ? measurement.llm : {};
	// Fail closed: completeness is claimed only when the record literally says
	// `true`. A missing or non-boolean flag means we do not know whether every
	// call reported usage, and "we do not know" may not be shown as "complete".
	const usageKnown = typeof llm.usage_complete === "boolean";
	const usageComplete = llm.usage_complete === true;

	return {
		schema: typeof record.schema === "string" ? record.schema : null,
		jobId: typeof record.job_id === "string" ? record.job_id : null,
		recordedAt: typeof record.recorded_at === "string" ? record.recorded_at : null,
		wallSeconds: secondsOrNull(measurement.wall_seconds),
		cpuSeconds: secondsOrNull(measurement.cpu_seconds),
		tokens: {
			input: countOrNull(llm.input_tokens),
			output: countOrNull(llm.output_tokens),
			total: countOrNull(llm.total_tokens),
			calls: countOrNull(llm.calls),
			callsMissingUsage: countOrNull(llm.calls_missing_usage),
		},
		usageComplete,
		usageKnown,
		dominantStage: dominantStage(measurement),
		quote: deriveRecordedQuote(record.quote),
	};
}

/**
 * The recorded quote, verbatim. `null` when the run recorded no quote — which
 * means "we did not write down what was quoted", never "it was free".
 */
export function deriveRecordedQuote(quote) {
	if (!quote || typeof quote !== "object") return null;
	return {
		price: typeof quote.price === "string" ? quote.price : null,
		pricingModel: typeof quote.pricing_model === "string" ? quote.pricing_model : null,
		asset: typeof quote.asset === "string" ? quote.asset : null,
		chain: typeof quote.chain === "string" ? quote.chain : null,
		paymentRequired: quote.payment_required === true,
		dryRun: quote.dry_run === true,
	};
}

/** Group digits for display. `null` → em-dash; a measured `0` stays `0`. */
export function formatTokenCount(value) {
	const n = countOrNull(value);
	if (n == null) return NOT_MEASURED;
	return n.toLocaleString("en-US");
}

/** Seconds → a readable duration. `null` → em-dash. */
export function formatDuration(value) {
	const s = secondsOrNull(value);
	if (s == null) return NOT_MEASURED;
	if (s < 1) return `${s.toFixed(2)} s`;
	if (s < 60) return `${s.toFixed(1)} s`;
	const minutes = Math.floor(s / 60);
	const rest = Math.round(s - minutes * 60);
	return `${minutes} m ${rest} s`;
}

/**
 * The headline token figure. When the run could not read every call's usage,
 * the totals are a FLOOR — rendered with a `≥` so nobody reads a partial tally
 * as the run's full consumption.
 */
export function tokensLabel(view) {
	const total = view?.tokens?.total;
	if (total == null) return NOT_MEASURED;
	const formatted = formatTokenCount(total);
	return view.usageComplete ? formatted : `≥ ${formatted}`;
}

/** One sentence on how much of the token measurement is trustworthy. */
export function usageNote(view) {
	if (!view) return NOT_MEASURED_HINT;
	if (view.usageComplete) return "Complete — every LLM call reported its token usage.";
	const { callsMissingUsage, calls } = view.tokens;
	if (!view.usageKnown) {
		return "Incomplete — this run did not record whether every LLM call reported its token usage, so the totals are a floor.";
	}
	if (callsMissingUsage != null && calls != null) {
		return `Incomplete — ${callsMissingUsage} of ${calls} LLM calls reported no usable token usage, so the totals are a floor, not the total.`;
	}
	return "Incomplete — at least one LLM call reported no usable token usage, so the totals are a floor, not the total.";
}

/**
 * Trim a recorded price string's trailing zeros for display ("$0.150000" →
 * "$0.15"). A pure display trim of a recorded string: it never rounds, never
 * drops a significant digit, and anything not shaped like a decimal price is
 * returned exactly as recorded rather than reformatted into a guess.
 */
export function quotePriceLabel(price) {
	if (typeof price !== "string" || !price) return NOT_MEASURED;
	const match = price.match(/^(\D*)(\d+)\.(\d+)$/);
	if (!match) return price;
	const [, prefix, whole, fraction] = match;
	// Trailing zeros only, and never below two decimals — a price reads as a
	// price. "$1.000000" → "$1.00", "$0.150000" → "$0.15", "$0.000001" stays.
	const trimmed = fraction.replace(/0+$/, "");
	return `${prefix}${whole}.${trimmed.length >= 2 ? trimmed : trimmed.padEnd(2, "0")}`;
}

/**
 * The quote line: what we charged, as recorded. Never derived from the tokens.
 * `null` when no quote was recorded, so the card can say so explicitly.
 */
export function quoteLabel(view) {
	const quote = view?.quote;
	if (!quote || !quote.price) return null;
	const parts = [quotePriceLabel(quote.price)];
	if (quote.asset) parts.push(quote.asset);
	return parts.join(" ");
}

/** The caveats that belong next to a recorded quote, as recorded facts. */
export function quoteNote(view) {
	const quote = view?.quote;
	if (!quote) return "No quote was recorded for this run.";
	const notes = [];
	if (quote.pricingModel) notes.push(`pricing model ${quote.pricingModel}`);
	if (!quote.paymentRequired) notes.push("the paywall was off, so nothing was charged");
	else if (quote.dryRun) notes.push("dry run — no value moved");
	return notes.length ? `Quoted at generation time (${notes.join("; ")}).` : "Quoted at generation time.";
}

/**
 * The library table's compact cell. Total tokens is the design call: it is the
 * term that scales with the model and the one #1217 exists to pin down, whereas
 * wall time is dominated by backtests and moves with whatever else the worker
 * is doing. Wall time and the dominant stage ride along in the tooltip.
 */
export function compactCostCell(record) {
	const view = deriveGenerationCostView(record);
	if (!view) return { label: NOT_MEASURED, title: NOT_MEASURED_HINT, measured: false };
	// A record can exist while its token count is unreadable. Say that in words
	// rather than rendering "— tokens (in — / out —)", which reads like a
	// formatting bug instead of the honest statement it is.
	const tokensPhrase =
		view.tokens.total == null
			? "Token count not measured for this run"
			: `${tokensLabel(view)} tokens (in ${formatTokenCount(view.tokens.input)} / out ${formatTokenCount(view.tokens.output)})`;
	const title = [
		tokensPhrase,
		`wall ${formatDuration(view.wallSeconds)}`,
		view.dominantStage
			? `dominant stage ${stageLabel(view.dominantStage.name)} (${formatDuration(view.dominantStage.wallSeconds)})`
			: "dominant stage not measured",
		usageNote(view),
	].join(" · ");
	return { label: tokensLabel(view), title, measured: true };
}
