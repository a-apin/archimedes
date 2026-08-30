import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { ANCHOR_STATES, anchorState } from "../src/trace-binding.js";

// The per-row anchoring claim, and the three surfaces that make it.
//
// Reasoning, the Portfolio activity feed, and the new strategy-passport panel
// each have to answer "is this decision anchored?" for every row. Two of them
// answered it with their own inline ternary on `is_verified`, and both were
// wrong in the same two ways:
//
//   * a SKIP trace was labelled "anchor pending", promising a registry write
//     that by design never arrives — a skip has no trade for commit() to bind
//     (#714), so no anchor is ever attempted;
//   * the `anchored_only` projection (an anchor exists, no off-chain body was
//     there to compare against — #1407) rendered as a plain green check, i.e.
//     as a hash comparison that never happened.
//
// These pin the honest four-state derivation and then pin, per component,
// that the component actually calls it instead of re-deriving it.

const src = (p) => readFileSync(new URL(`../src/${p}`, import.meta.url), "utf8");

// ── The derivation ──────────────────────────────────────────────────────

test("anchored_only wins over is_verified — the anchor is real, the comparison is not", () => {
	// That path sets is_verified: true ON PURPOSE (the anchor genuinely is
	// confirmed). An is_verified check placed first would swallow it and
	// re-render it as a hash match.
	const a = anchorState({
		verification_mode: "anchored_only",
		is_verified: true,
		arc_tx_hash: null,
	});
	assert.equal(a.state, "anchored_unverified");
	assert.notEqual(a.tone, "verified");
	assert.match(a.label, /not re-hashed/);
	assert.match(a.title, /Zero hashes were compared/);
});

test("an anchored trace is labelled anchored, and claims no hash comparison", () => {
	const a = anchorState({ arc_tx_hash: "0xabc", is_verified: true });
	assert.equal(a.state, "anchored");
	assert.equal(a.tone, "verified");
	assert.equal(a.label, "anchored on Arc");
	// The label must not say "verified": that word belongs to
	// /api/traces/{id}/verify, and only after a real re-fetch and compare.
	assert.ok(!/verified/i.test(a.label), `label overclaims: ${a.label}`);
	assert.match(a.title, /not that the stored body still hashes to it/);
});

test("a skip with no anchor is a permanent explained absence, never 'pending'", () => {
	const a = anchorState({ decision_type: "skip", arc_tx_hash: null, is_verified: false });
	assert.equal(a.state, "not_anchored_no_trade");
	assert.equal(a.label, "not anchored (no trade to bind)");
	assert.ok(
		!/pending/i.test(a.label),
		"a skip must never be labelled pending — no write is coming",
	);
	// The title may only mention pending to DENY it. Pin the denial rather
	// than the substring, so a future edit cannot reintroduce the promise
	// while still matching a naive `!includes("pending")` check.
	assert.match(a.title, /no anchor is attempted or pending/);
	assert.notEqual(a.state, ANCHOR_STATES.anchor_pending.state);
});

test("a non-skip trace with no anchor is still genuinely pending", () => {
	const a = anchorState({ decision_type: "rebalance", arc_tx_hash: null, is_verified: false });
	assert.equal(a.state, "anchor_pending");
	assert.match(a.label, /pending/);
});

test("a skip that DID anchor keeps its anchor — 'no trade to bind' explains an absence, not a presence", () => {
	// The legacy publishTrace fallback could anchor a skip. Ordering the skip
	// check before the anchored checks would erase a real on-chain reference.
	const a = anchorState({ decision_type: "skip", arc_tx_hash: "0xabc" });
	assert.equal(a.state, "anchored");
});

test("the four states are mutually distinct in state, label, and icon", () => {
	const all = Object.values(ANCHOR_STATES);
	assert.equal(all.length, 4);
	for (const key of ["state", "label", "icon"]) {
		const seen = new Set(all.map((s) => s[key]));
		assert.equal(seen.size, 4, `two states share a ${key} — they would be indistinguishable`);
	}
	// Exactly one state may carry the positive/verified affordance.
	assert.equal(all.filter((s) => s.tone === "verified").length, 1);
});

test("a missing or empty trace degrades to pending, never to anchored", () => {
	for (const t of [undefined, null, {}]) {
		assert.equal(anchorState(t).state, "anchor_pending");
	}
});

// ── The components actually use it ───────────────────────────────────────
//
// Confirmed to FAIL against the pre-fix tree: Portfolio.jsx and Reasoning.jsx
// both carried their own inline `is_verified` ternary and neither imported
// anchorState.

test("Portfolio.jsx derives the badge from anchorState, not its own ternary", () => {
	const portfolio = src("components/Portfolio.jsx");
	assert.ok(portfolio.includes('import { anchorState } from "../trace-binding"'));
	assert.ok(portfolio.includes("anchorState(t)"));
	// The old two-state ternary and its hardcoded copy must be gone.
	assert.ok(
		!portfolio.includes("{t.is_verified ? ("),
		"the inline is_verified ternary is still deciding the anchoring claim",
	);
	assert.ok(
		!/title="Trace hashed \+ persisted off-chain; on-chain anchor pending/.test(portfolio),
		"the hardcoded 'anchor pending' copy is still inline — a skip would still get it",
	);
});

test("Reasoning.jsx derives the badge from anchorState, not its own ternary", () => {
	const reasoning = src("components/Reasoning.jsx");
	assert.ok(reasoning.includes("anchorState(t)"));
	assert.ok(
		!reasoning.includes("t.is_verified ? ("),
		"the inline is_verified ternary is still deciding the anchoring claim",
	);
	// The old badge said the bare word "verified" for an anchor that was never
	// re-hashed. It must not come back.
	assert.ok(
		!/w-3 h-3" \/> verified</.test(reasoning),
		"the bare 'verified' badge is back on a row that compared no hashes",
	);
});

test("the skip copy matches the wording the #714 branch settled on", () => {
	// That branch made SKIP honest in the Portfolio feed with this exact
	// label + tooltip. Keeping one copy of the strings, in the shared helper,
	// is what stops the two surfaces from wording the same fact differently.
	const a = anchorState({ decision_type: "skip" });
	assert.equal(a.label, "not anchored (no trade to bind)");
	assert.equal(
		a.title,
		"No trade was made, so there is nothing for an on-chain commitment to bind. The trace is hashed and persisted off-chain; no anchor is attempted or pending.",
	);
});

test("the Reasoning page's intro copy describes buttons that exist", () => {
	const reasoning = src("components/Reasoning.jsx");
	// It used to promise "→ Strategy in Library", a button gated on a field
	// the API never sends — so the promise was unreachable on every row.
	assert.ok(!reasoning.includes("→ Strategy in Library"));
	assert.ok(reasoning.includes("→ Strategy passport"));
	// And it now explains the skip state rather than leaving the reader to
	// guess whether an unanchored row is a failure.
	assert.ok(reasoning.includes("not anchored (no trade to bind)"));
});

test("the dead t.strategy_id follow-back is gone from Reasoning.jsx", () => {
	// TraceResponse has never had a `strategy_id` field — the API emits
	// `strategies_referenced` — so the "→ Strategy" button rendered on zero
	// rows, and the page's own promise of a follow-back was unreachable.
	const reasoning = src("components/Reasoning.jsx");
	assert.ok(
		!/\{t\.strategy_id && onNavigate/.test(reasoning),
		"the button still gates on a field the API never sends",
	);
	assert.ok(reasoning.includes("t.strategies_referenced?.[0] && onNavigate"));
});

// ── The passport panel ───────────────────────────────────────────────────

test("StrategyReasoning keeps the debate and the trading decisions separate", () => {
	const panel = src("components/StrategyReasoning.jsx");
	assert.ok(panel.includes("Generation debate"));
	assert.ok(panel.includes("Trading decisions"));
	// A debate turn is anchored nowhere; it must not be rendered through the
	// anchoring badge that trace rows use.
	const debateStart = panel.indexOf("function GenerationDebate");
	const debateEnd = panel.indexOf("function TradingDecisions");
	assert.ok(debateStart !== -1 && debateEnd > debateStart);
	const debateBody = panel.slice(debateStart, debateEnd);
	assert.ok(
		!debateBody.includes("AnchorBadge"),
		"a debate turn must never render an anchoring claim",
	);
});

test("StrategyReasoning scopes its trace query to the strategy", () => {
	const panel = src("components/StrategyReasoning.jsx");
	assert.ok(panel.includes("/api/traces/?strategy_id=${encodeURIComponent(strategyId)}"));
	assert.ok(panel.includes("/api/strategies/${encodeURIComponent(strategyId)}/debate"));
});

test("empty states are honest and specific, not a generic 'nothing here'", () => {
	const panel = src("components/StrategyReasoning.jsx");
	assert.ok(panel.includes("No anchored decisions yet for this strategy."));
	assert.ok(panel.includes("No debate transcript for this strategy."));
	// Both explain WHY the absence exists rather than implying a failure.
	assert.match(panel, /curated library strategies never ran one/);
	assert.match(panel, /once the autonomous agent acts on a vault that references it/);
});

test("a 404 from either endpoint is an empty state, not an error banner", () => {
	// 404 is the honest "never persisted" answer from /debate, and the
	// visibility answer from the scoped trace listing. Rendering it as a red
	// failure would tell the user something broke when nothing did.
	const panel = src("components/StrategyReasoning.jsx");
	const guards = panel.match(/e\.status !== 404/g) || [];
	assert.equal(guards.length, 2, "both fetches must exempt 404 from the error path");
});

test("async regions announce themselves (role=status) and failures announce as alerts", () => {
	const panel = src("components/StrategyReasoning.jsx");
	assert.equal((panel.match(/role="status"/g) || []).length, 2);
	assert.equal((panel.match(/role="alert"/g) || []).length, 2);
	// The trace list on Reasoning is async too.
	assert.ok(src("components/Reasoning.jsx").includes('role="status">Loading traces…'));
});

test("the passport mounts the panel unconditionally", () => {
	// Its empty states are load-bearing product copy. A panel that vanishes
	// when there is nothing to show teaches the reader provenance is optional.
	const passport = src("components/StrategyPassport.jsx");
	assert.ok(passport.includes('import StrategyReasoning from "./StrategyReasoning"'));
	assert.match(
		passport,
		/<StrategyReasoning strategyId=\{strategyId\} onNavigate=\{onNavigate\} \/>/,
	);
});
