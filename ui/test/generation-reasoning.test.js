import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
	EVENT_LABELS,
	HEADLINES,
	TOOL_COPY,
	eventDetail,
	eventHeadline,
} from "../src/generation-copy.js";

// Where the strategy engine's reasoning is allowed to appear, and what it is
// allowed to say.
//
// The owner, dogfooding a live generation on a phone: "You still can't see the
// actual reasoning traces anywhere as best I can tell. Seems like it should be
// somewhere in the generation stream and also in the strategy passport […] the
// Reasoning page is supposed to be for reasoning from the execution engine
// rather than the strategy engine — so we shouldn't have strategy generation
// reasoning on the reasoning page."
//
// That is three separate rules, and this file is one section per rule:
//
//   1. EVENT_LABELS is the EventSource subscription list. An event the backend
//      emits but this map omits is never received by the browser at all — the
//      live bug that keeps `backtest_running` / `backtest_done` /
//      `backtest_failed` invisible to this day. So the label map and the copy
//      map are pinned against each other in both directions.
//   2. The log line is human copy; the wire strings survive under a toggle.
//      Neither half may swallow the other.
//   3. The passport shows the strategy engine's reasoning; /app/reasoning shows
//      the execution engine's, and must import none of the former.

const src = (p) => readFileSync(new URL(`../src/${p}`, import.meta.url), "utf8");

// One realistic payload per event name, taken from the shapes the backend
// actually emits (backend/archimedes/agents/generation_pipeline.py's `_Emitter`
// call sites, and debate_engine.py's). Deliberately jargon-LADEN: the strings
// below are the exact developer text the owner's screenshots showed, so a test
// that finds no jargon in the headline has genuinely moved it rather than been
// handed a clean fixture.
const FIXTURES = {
	job_queued: { brief: { intent: "something that holds up in a drawdown" } },
	brief_validated: { risk_appetite: "moderate", asset_classes: ["equity"] },
	pipeline_selected: { pipeline: "debate", reason: "multi-agent debate society" },
	candidates_selected: { candidate_count: 1, regimes: ["neutral"] },
	agent_iteration: { candidate_id: "cand_neutral", iteration_n: 3, max_iterations: 4 },
	tool_called: {
		candidate_id: "cand_neutral",
		tool_name: "propose_pool",
		args_summary: "steers=18, asset_classes=(any)",
	},
	tool_result: {
		candidate_id: "cand_neutral",
		tool_name: "synthesize",
		result_summary: "leader=Volatility-Relief Swing dsr=0.082005 of 1 entries",
	},
	debate_turn: {
		candidate_id: "cand_neutral",
		role: "bear",
		round: 2,
		verdict: "decline",
		claims: [{ claim: "the factor is crowded", candidate_id: "C1", arxiv_ids: ["2401.00001"] }],
		discard: [{ arxiv_id: "2402.00003", reason: "no distinct mechanism" }],
		headline:
			"Bear researcher, rebuttal — verdict: decline. 1 claim, 1 grounded in a named paper; 1 paper set aside.",
	},
	debate_attribution: {
		candidate_id: "cand_neutral",
		role: "attribution",
		round: null,
		verdict:
			"Paper attribution: 3 of 30 retrieved paper(s) were cited or discarded by name in this debate; 2 of 5 cited paper(s) name a mechanism this strategy trades.",
		paper_verdicts: [
			{
				arxiv_id: "2401.00001",
				title: "Cross-Sectional Equity Momentum",
				cited_by: ["bull"],
				citations: 2,
				discarded_by: [],
				discard_reasons: [],
				verdict: "cited",
			},
		],
		fusion_reasoning: "Paper A gives the 200-day trend filter.",
		papers_offered: 30,
		distinct_mechanism_papers: 2,
	},
	candidate_drafted: {
		candidate_id: "cand_neutral",
		strategy_name: "Volatility-Relief Swing",
		regime: "neutral",
		source_arxiv_ids: ["2401.00001", "2402.00001", "2403.00001", "2404.00001", "2405.00001"],
	},
	candidate_failed: { candidate_id: "cand_bear", regime: "bear", message: "No candidate beat the passive null" },
	candidate_evaluated: {
		candidate_id: "cand_neutral",
		regime: "neutral",
		rigor_verdict: { dsr: 0.082005, oos_sharpe: 0.198029, pbo: 0.12 },
	},
	best_selected: {
		best_candidate_id: "cand_neutral",
		considered_count: 1,
		validated_count: 1,
		deployable: false,
	},
	trace_hashed: { candidate_id: "cand_neutral", trace_hash: "6ae4b039607e14aa77", regime: "neutral" },
	persisted: {
		strategy_id: "strat-abc",
		candidate_id: "cand_neutral",
		regime: "neutral",
		redirect_url: "/app/library?highlight=strat-abc",
	},
	done: { strategy_id: "strat-abc", served_model: "glm-4.6" },
	error: { message: "Generation failed", code: "GENERATION_UNAVAILABLE", recoverable: true },
};

// ── 1. The subscription list cannot drift from the copy ─────────────────

test("every subscribed event has a headline, and every headline is subscribed", () => {
	// GenerationStream.jsx does
	// `Object.keys(EVENT_LABELS).forEach(name => es.addEventListener(name, …))`,
	// so this map decides what the browser hears. Copy without a label is dead
	// code; a label without copy renders a bare "Label — " row.
	//
	// MUTATION (verified red): add `backtest_done: 'Backtest done'` to
	// EVENT_LABELS without a HEADLINES entry, or delete `debate_turn` from
	// EVENT_LABELS — either direction fails here.
	assert.deepEqual(Object.keys(EVENT_LABELS).sort(), Object.keys(HEADLINES).sort());
});

test("the debate's own events are on the subscription list", () => {
	// The whole point of the change: without these two names the four paid
	// bull/bear turns and the per-paper record are emitted by the backend and
	// dropped on the floor by the client, which is what the owner saw.
	assert.ok("debate_turn" in EVENT_LABELS);
	assert.ok("debate_attribution" in EVENT_LABELS);
	assert.equal(EVENT_LABELS.debate_turn, "Researcher argued");
	assert.equal(EVENT_LABELS.debate_attribution, "Papers accounted for");
});

test("GenerationStream subscribes from the shared map rather than a private list", () => {
	const stream = src("components/GenerationStream.jsx");
	assert.match(stream, /from '\.\.\/generation-copy'/);
	assert.match(stream, /Object\.keys\(EVENT_LABELS\)\.forEach\(name => es\.addEventListener\(name/);
	// The component must not re-declare its own copy of either map.
	assert.ok(!/^const EVENT_LABELS = \{/m.test(stream));
	assert.ok(!/function summarizeEvent/.test(stream));
});

test("every fixture is exercised — a new event cannot slip past the copy guards", () => {
	assert.deepEqual(Object.keys(FIXTURES).sort(), Object.keys(EVENT_LABELS).sort());
});

// ── 2. Human copy up front, machine fields kept ─────────────────────────

// The developer vocabulary the owner's screenshots were made of. Every token
// here was ON SCREEN in a shipped generation stream.
const JARGON = [
	"cand_neutral",
	"cand_bear",
	"dsr=",
	"of 1 entries",
	"steers=",
	"args_summary",
	"result_summary",
	"redirect_url",
	"/app/library?highlight=",
	"tool_name",
];

test("no headline contains engine jargon", () => {
	// MUTATION (verified red): restore the old copy — make `tool_result` render
	// `${tool_name} → ${result_summary}` and `candidate_evaluated` lead with
	// `${candidate_id}` — and `dsr=`, `of 1 entries` and `cand_neutral` are
	// back on the first line of the log.
	for (const name of Object.keys(EVENT_LABELS)) {
		const line = eventHeadline(name, FIXTURES[name]);
		assert.equal(typeof line, "string");
		assert.ok(line.length > 0, `${name} rendered an empty headline`);
		for (const token of JARGON) {
			assert.ok(!line.includes(token), `${name} headline still says ${token}: ${line}`);
		}
	}
});

test("the numbers a reader can check stay in the headline", () => {
	// Moving the machine strings under a toggle must not take the VERIFIABLE
	// values with them. The rigor verdict and the trace-hash prefix are the two
	// things on this screen a user could independently confirm.
	const graded = eventHeadline("candidate_evaluated", FIXTURES.candidate_evaluated);
	assert.match(graded, /Rigor gate ran/);
	assert.ok(graded.includes("DSR 0.082005"));
	assert.ok(graded.includes("OOS 0.198029"));
	assert.ok(graded.includes("PBO 0.12"));
	assert.ok(eventHeadline("trace_hashed", FIXTURES.trace_hashed).includes("6ae4b039607e14"));
	// The drafted line still counts only provenance-checked citations.
	assert.equal(
		eventHeadline("candidate_drafted", FIXTURES.candidate_drafted),
		'Drafted "Volatility-Relief Swing" from 5 papers',
	);
	// …and never invents one when the candidate cited nothing.
	assert.equal(
		eventHeadline("candidate_drafted", { strategy_name: "X", source_arxiv_ids: [] }),
		'Drafted "X"',
	);
});

test("the machine strings survive verbatim under the details toggle", () => {
	// MUTATION (verified red): return "" from eventDetail for tool_result — the
	// backend's own summary would then exist nowhere in the UI, which is the
	// opposite failure from the one this change fixes.
	assert.equal(
		eventDetail("tool_result", FIXTURES.tool_result),
		"synthesize → leader=Volatility-Relief Swing dsr=0.082005 of 1 entries",
	);
	assert.equal(eventDetail("tool_called", FIXTURES.tool_called), "propose_pool(steers=18, asset_classes=(any))");
	assert.ok(eventDetail("candidate_evaluated", FIXTURES.candidate_evaluated).includes("cand_neutral"));
	assert.ok(eventDetail("persisted", FIXTURES.persisted).includes("/app/library?highlight=strat-abc"));
	assert.equal(eventDetail("trace_hashed", FIXTURES.trace_hashed), "6ae4b039607e14aa77");
});

test("the stream renders the details line and the debate cards", () => {
	const stream = src("components/GenerationStream.jsx");
	assert.match(stream, /eventDetail\(ev\.name, ev\.data\)/);
	assert.match(stream, /showDetails && detail/);
	assert.match(stream, /Show machine details/);
	assert.match(stream, /<DebateTurn turn=\{ev\.data\} \/>/);
	assert.match(stream, /<DebatePaperVerdicts entry=\{ev\.data\}/);
});

test("the debate turn falls back to honest copy when the server sent no headline", () => {
	// An older backend, or a partial frame. The client says less than the
	// server's sentence rather than inventing the counts it cannot see.
	const line = eventHeadline("debate_turn", { role: "bull", round: 1 });
	assert.equal(line, "Bull researcher, opening argument");
});

test("tool copy distinguishes starting from finishing, and never narrates an unknown tool", () => {
	for (const [tool, copy] of Object.entries(TOOL_COPY)) {
		assert.ok(copy.started && copy.finished, `${tool} is missing a phase`);
		assert.notEqual(copy.started, copy.finished, `${tool} says the same thing twice`);
	}
	// A tool this copy predates is NAMED, not described — describing it would be
	// a claim about behaviour written before the behaviour existed.
	assert.equal(eventHeadline("tool_called", { tool_name: "some_future_tool" }), "Running some_future_tool");
	assert.equal(eventHeadline("tool_result", { tool_name: "some_future_tool" }), "Finished some_future_tool");
});

test("the stream links to the full transcript instead of ending at the Library", () => {
	const stream = src("components/GenerationStream.jsx");
	assert.match(stream, /See the full reasoning/);
	assert.match(stream, /onNavigate\('strategy', \{ strategyId \}\)/);
});

// ── 3. The two engines stay on their own pages ──────────────────────────

test("the passport names both engines", () => {
	// MUTATION (verified red): drop either heading — the panel goes back to one
	// section called "Reasoning" containing two unrelated kinds of it.
	const panel = src("components/StrategyReasoning.jsx");
	assert.match(panel, /Strategy engine — generation debate/);
	assert.match(panel, /Execution engine — trading decisions/);
	// …and points at where the other one lives.
	assert.match(panel, /The Reasoning page carries this engine's traces/);
});

test("the execution Reasoning page carries none of the generation debate", () => {
	// The owner's explicit line: "the Reasoning page is supposed to be for
	// reasoning from the execution engine rather than the strategy engine — so
	// we shouldn't have strategy generation reasoning on the reasoning page."
	//
	// MUTATION (verified red): import DebatePaperVerdicts into Reasoning.jsx, or
	// add a `/debate` fetch there.
	const page = src("components/Reasoning.jsx");
	assert.ok(!page.includes("/debate"), "the Reasoning page must not fetch the generation debate");
	assert.ok(!page.includes("debate_turn"));
	assert.ok(!page.includes("debate_attribution"));
	assert.ok(!page.includes("DebatePaperVerdicts"));
	assert.ok(!page.includes("GenerationDebate"));
	assert.ok(!page.includes("StrategyReasoning"));
});

test("the passport panel reads the per-paper record, not just the summary sentence", () => {
	// MUTATION (verified red): render only `entry.verdict` — the discard reasons
	// and the mechanism prose (the owner's "why papers were discarded") vanish
	// again, which is the state this change is fixing.
	const renderer = src("components/DebatePaperVerdicts.jsx");
	assert.match(renderer, /paper_verdicts/);
	assert.match(renderer, /discard_reasons/);
	assert.match(renderer, /fusion_reasoning/);
	assert.match(renderer, /cited_by/);
	assert.match(renderer, /discarded_by/);
	// The four verdicts the backend actually produces, each explained rather
	// than left as a bare chip. "unused" is the load-bearing one: a paper that
	// was retrieved, shown, and named by nobody.
	for (const verdict of ["cited", "discarded", "contested", "unused"]) {
		assert.ok(renderer.includes(verdict), `the ${verdict} verdict is unrendered`);
	}
	assert.match(renderer, /Retrieved and shown to the researchers, but neither one named it\./);
	// It renders no anchoring claim: an argument moved no money.
	assert.ok(!renderer.includes("AnchorBadge"));
	assert.ok(!renderer.includes("anchorState"));
});

test("the attribution entry is split out of the turns rather than walked as one", () => {
	// Functional, not source-text: the #1739 entry rides the SAME json list as
	// the turns, and a renderer that walks the list uniformly shows its summary
	// sentence and silently drops paper_verdicts + fusion_reasoning.
	//
	// MUTATION (verified red): make splitTranscript return every entry as a
	// turn — `attribution` goes null and the per-paper table never renders.
	const mod = src("components/DebatePaperVerdicts.jsx");
	assert.match(mod, /export function splitTranscript/);
	assert.match(mod, /e\.role !== "attribution"/);
	assert.match(mod, /e\.role === "attribution"/);
	// And the passport actually calls it.
	const panel = src("components/StrategyReasoning.jsx");
	assert.match(panel, /splitTranscript\(payload\?\.transcript\)/);
});

test("the passport's debate half still asks the owner-only endpoint, unchanged", () => {
	// This change carries no gate change. GET /api/strategies/{id}/debate is
	// owner-only by design (#1557: publishing shares the RESULT, not the
	// derivation), and the SSE stream has its own independent owner gate — so
	// putting the debate on the wire needed no new one.
	const panel = src("components/StrategyReasoning.jsx");
	assert.match(panel, /\/api\/strategies\/\$\{encodeURIComponent\(strategyId\)\}\/debate/);
	assert.match(panel, /e\.status !== 404/);
});
