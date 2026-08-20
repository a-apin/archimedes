import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const css = readFileSync(new URL("../src/App.css", import.meta.url), "utf8");
const architecture = readFileSync(
	new URL("../src/components/Architecture.jsx", import.meta.url),
	"utf8",
);
const landing = readFileSync(
	new URL("../src/components/Landing.jsx", import.meta.url),
	"utf8",
);
const publicLayout = readFileSync(
	new URL("../src/components/PublicLayout.jsx", import.meta.url),
	"utf8",
);
const roadmapCopy = readFileSync(
	new URL("../src/roadmapCopy.js", import.meta.url),
	"utf8",
);

test("public shell owns an isolated visual system and accessible navigation", () => {
	assert.match(publicLayout, /className="public-site"/);
	assert.match(publicLayout, /className="public-header"/);
	assert.match(publicLayout, /aria-label="Public navigation"/);
	assert.match(publicLayout, /className="public-auth-link"/);
	assert.match(css, /\.public-site\s*\{[^}]*--public-abyss:\s*#071319;/s);
	assert.match(css, /\.public-site :focus-visible\s*\{/);
});

test("public architecture page restores app content gutter", () => {
	assert.match(architecture, /return \(\s*<div className="page-content">/);
});

test("architecture stats keep two mobile columns and four desktop columns", () => {
	assert.match(architecture, /className="architecture-stats"/);
	assert.match(
		css,
		/\.architecture-stats\s*\{[^}]*grid-template-columns:\s*repeat\(2,/s,
	);
	assert.match(
		css,
		/@media \(min-width: 768px\)[^{]*\{[^}]*\.architecture-stats\s*\{[^}]*grid-template-columns:\s*repeat\(4,/s,
	);
});

test("landing hero makes proof flow the signature element", () => {
	assert.match(landing, /className="proof-spiral"/);
	assert.match(landing, /Brief/);
	assert.match(landing, /Debate/);
	assert.match(landing, /Rigor/);
	// Flag-off shape (#1354): the spiral's 4th stop names no bare "Vault"
	// literal in Landing.jsx — it's gated, sourced from roadmapCopy.js.
	// (backend/tests/... precedent: rewrite the assertion pinning the
	// pre-fix defect, don't just delete it — see ui/test/roadmap-copy.test.js
	// for the guard that pins this precisely.)
	assert.match(landing, /ROADMAP_SURFACES_ENABLED && \(/);
	assert.match(landing, /ROADMAP_COPY\.spiralVaultLabel/);
	// Flag-on counterpart: the full spiral still names a dedicated Vault
	// stop when the flag is on — the string lives in roadmapCopy.js, which
	// this file's own guard (roadmap-copy.test.js) keeps deletable-but-real.
	assert.match(roadmapCopy, /spiralVaultLabel:\s*"Vault"/);
	assert.match(css, /@keyframes proof-pulse/);
	assert.match(
		css,
		/@media \(prefers-reduced-motion: reduce\)[^{]*\{[\s\S]*?\.proof-spiral__pulse/s,
	);
});

test("proof-spiral legend grid tracks item count, not a fixed stage count", () => {
	// PR #1396 review (major finding): a fixed repeat(4, …) left an empty
	// bordered column when ROADMAP_SURFACES_ENABLED is off and the legend
	// renders only 3 <li>. Mirrors the .app-proof-rail auto-fit fix.
	assert.match(
		css,
		/\.proof-spiral__legend\s*\{[^}]*grid-template-columns:\s*repeat\(auto-fit,/s,
	);
	assert.doesNotMatch(
		css,
		/\.proof-spiral__legend\s*\{[^}]*grid-template-columns:\s*repeat\(\d+,/s,
	);
});

test("landing presents evidence as criteria, not decorative steps", () => {
	assert.match(landing, /EvidenceLedger/);
	assert.match(landing, /RigorMatrix/);
	// Flag-off shape (#1354): AuthorityBoundary (the custody-boundary panel,
	// which names a deposit/withdraw vault flow with no reachable mount
	// while vault surfaces are off) is gated at its mount site, not
	// rendered unconditionally.
	assert.match(
		landing,
		/\{ROADMAP_SURFACES_ENABLED && <AuthorityBoundary \/>\}/,
	);
	// Flag-on counterpart: the component itself is untouched — full custody
	// story still renders, byte-identical, when the flag is on.
	assert.match(landing, /function AuthorityBoundary\(\)/);
	assert.match(landing, /Autonomy stops at ownership\./);
	assert.match(landing, /Deflated Sharpe Ratio/);
	assert.match(landing, /Probability of Backtest Overfitting/);
	assert.match(landing, /Walk-forward out-of-sample/);
	assert.match(landing, /Look-ahead audit/);
	assert.doesNotMatch(landing, /n:\s*["']0[1-4]["']/);
});

test("landing keeps live census, honest failure copy, and both primary journeys", () => {
	assert.match(landing, /apiGet\("\/api\/config\/contracts"\)/);
	assert.match(landing, /Live census unavailable/);
	assert.match(landing, /onNavigate\("generate"\)/);
	assert.match(landing, /onNavigate\("library", \{ tab: "examples" \}\)/);
});

test("public layout is asymmetric on desktop and stacks before tablet width", () => {
	assert.match(
		css,
		/\.public-hero__grid\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1\.08fr\) minmax\(320px,\s*0\.92fr\);/s,
	);
	assert.match(
		css,
		/@media \(max-width: 980px\)[^{]*\{[\s\S]*?\.public-hero__grid\s*\{[^}]*grid-template-columns:\s*1fr;/s,
	);
});

test("landing does not claim the OOS gate rolls its window forward", () => {
	// The rigor gate is a single 70/30 chronological cut; the rolling
	// walk-forward re-estimation exists but never runs on a live path
	// (rigor_evaluator.py emits NOT_RUN for cpcv). Only the false "forward
	// through time" claim is retracted — the card name is repo-wide
	// vocabulary and must survive (see the previous test).
	assert.doesNotMatch(landing, /forward through time/);
	assert.match(landing, /Walk-forward out-of-sample/);
	assert.match(landing, /held-?out/);
});

test("protocols panel describes V_check by the checks it performs", () => {
	// v_check.py does arithmetic on a weights dict (sum == 10000 bps, max
	// concentration, and an optional cost-benefit floor no live caller
	// supplies). It never reads chain state or LLM output, so it cannot be a
	// chain-vs-narrative consistency gate. The "chain state outranks the
	// narrative" half is true (agent_runner reads vault state from chain)
	// and must survive; only the V_check attribution is retracted.
	//
	// Anchored to the Hierarchy of Truth entry specifically, not the whole
	// 1200-line file: a bare `assert.match(architecture, /concentration/)`
	// only guards anything today because the word happens to be unique in
	// the file, so a future rewrite of this exact `what:` string back to a
	// chain-vs-narrative claim would still pass as long as any other line
	// anywhere in the file mentions concentration.
	const hot = architecture.slice(
		architecture.indexOf('name: "Hierarchy of Truth"'),
	);
	const what = hot.slice(0, hot.indexOf("},"));
	assert.doesNotMatch(
		what,
		/V_check fails any rebalance where they disagree/,
	);
	assert.match(what, /Chain state outranks the LLM's narrative/);
	assert.match(what, /concentration/);
});

test("honesty ledger gives every row an explicit LedgerStatus verdict", () => {
	// A status cell with no <LedgerStatus> verdict reads as an implicit
	// "Live" next to coloured verdicts on neighbouring rows — on the
	// ledger's highest-stakes row (the autonomous rebalance loop), that is
	// exactly backwards when liveness is genuinely unverified.
	const ledger = architecture.slice(
		architecture.indexOf("function HonestyLedger"),
	);
	const tbody = ledger.slice(
		ledger.indexOf("<tbody>"),
		ledger.indexOf("</tbody>"),
	);
	const rows = tbody.split("<tr>").slice(1);
	assert.equal(rows.length, 8);
	for (const row of rows) assert.match(row, /<LedgerStatus/);
	// The rebalance row must not assert a single hardcoded verdict either
	// way — runner liveness changes over time (the runner was relocated off
	// the old detached EC2 box 2026-08-18/19, #1043/#1065, and could go
	// down again later), so the row must be driven by the live
	// /api/agent/status heartbeat (`agentStatus.alive`) and able to render
	// either a "live" or a "pending" verdict depending on what it reports —
	// never a claim asserted independent of that signal.
	const rebalanceRow = rows.find((row) =>
		row.includes("Autonomous rebalance loop"),
	);
	assert.match(rebalanceRow, /agentStatus\.alive/);
	assert.match(rebalanceRow, /tone="live"/);
	assert.match(rebalanceRow, /tone="pending"/);
});

test("honesty ledger's rebalance row does not tie the full commit/trade/reveal mechanism claim to the heartbeat-only 'Live' verdict — PR #1382 round-2 review", () => {
	// The heartbeat (`agentStatus.alive`) is written unconditionally after
	// every tick — including one that failed entirely (agent_runner.py's
	// outer try/except swallows a failed tick() and logs "will retry" — the
	// heartbeat save sits after that, unguarded) — and it is orthogonal to
	// AGENT_DRY_RUN, under which no commit/trade/reveal happens at all
	// (agent_runner.py gates each phase separately on `if not DRY_RUN`).
	// The heartbeat alone cannot back a claim that evaluate/commit/trade/
	// reveal actually ran; the live-verdict clause must say only what the
	// heartbeat proves (the loop is ticking). Scoped to the whole row (not
	// just the live branch): the pending branch is under the exact same
	// AGENT_DRY_RUN=true recommended default (infra/scripts/setup-ssm-
	// secrets.sh), so an unqualified mechanism claim there is the same
	// defect, just reached via a different verdict.
	const ledger = architecture.slice(
		architecture.indexOf("function HonestyLedger"),
	);
	const tbody = ledger.slice(
		ledger.indexOf("<tbody>"),
		ledger.indexOf("</tbody>"),
	);
	const rebalanceRow = tbody
		.split("<tr>")
		.slice(1)
		.find((row) => row.includes("Autonomous rebalance loop"));
	assert.doesNotMatch(
		rebalanceRow,
		/evaluate, commit, trade, reveal/,
		"a branch of the rebalance row claims the full commit/trade/reveal mechanism ran off a signal (heartbeat) that doesn't measure it — same defect class this PR exists to police",
	);
	const liveBranch = rebalanceRow.slice(
		rebalanceRow.indexOf("agentStatus.alive ? ("),
		rebalanceRow.indexOf(") : ("),
	);
	assert.match(liveBranch, /heartbeat/i);
});
