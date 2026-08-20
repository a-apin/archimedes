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
	assert.match(landing, /Vault/);
	assert.match(css, /@keyframes proof-pulse/);
	assert.match(
		css,
		/@media \(prefers-reduced-motion: reduce\)[^{]*\{[\s\S]*?\.proof-spiral__pulse/s,
	);
});

test("landing presents evidence as criteria, not decorative steps", () => {
	assert.match(landing, /EvidenceLedger/);
	assert.match(landing, /RigorMatrix/);
	assert.match(landing, /AuthorityBoundary/);
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
	assert.doesNotMatch(
		architecture,
		/V_check fails any rebalance where they disagree/,
	);
	assert.match(architecture, /Chain state outranks the LLM's narrative/);
	assert.match(architecture, /concentration/);
});

test("honesty ledger gives every row an explicit LedgerStatus verdict", () => {
	// A status cell with no <LedgerStatus> verdict reads as an implicit
	// "Live" next to coloured verdicts on neighbouring rows — on the
	// ledger's highest-stakes row (the autonomous rebalance loop), that is
	// exactly backwards: liveness there is unverified.
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
	// The rebalance row specifically must not be marked live — runner
	// liveness is unresolved (agent_runner is stranded post-Fargate-cutover).
	const rebalanceRow = rows.find((row) => row.includes("Autonomous rebalance loop"));
	assert.match(rebalanceRow, /tone="pending"/);
});
