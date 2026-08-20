import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const css = readFileSync(new URL("../src/App.css", import.meta.url), "utf8");
const authenticatedApp = readFileSync(
	new URL("../src/AuthenticatedApp.jsx", import.meta.url),
	"utf8",
);
const generate = readFileSync(
	new URL("../src/components/Generate.jsx", import.meta.url),
	"utf8",
);
const layout = readFileSync(
	new URL("../src/components/Layout.jsx", import.meta.url),
	"utf8",
);
// PROOF_STAGES itself moved out of Layout.jsx into proofStages.js (#1354) so
// the roadmap-copy guard can call getProofStages() under plain node — see
// that file and ui/test/roadmap-copy.test.js for the 3-vs-5-stage guard.
const proofStages = readFileSync(
	new URL("../src/proofStages.js", import.meta.url),
	"utf8",
);
const passport = readFileSync(
	new URL("../src/components/StrategyPassport.jsx", import.meta.url),
	"utf8",
);
const portfolio = readFileSync(
	new URL("../src/components/Portfolio.jsx", import.meta.url),
	"utf8",
);
const insights = readFileSync(
	new URL("../src/components/Insights.jsx", import.meta.url),
	"utf8",
);

test("authenticated shell has isolated operational tokens and journey rail", () => {
	assert.match(layout, /shell app-site/);
	assert.match(layout, /PROOF_STAGES/);
	assert.match(layout, /className="app-proof-rail"/);
	assert.match(proofStages, /Brief/);
	assert.match(proofStages, /Debate/);
	assert.match(proofStages, /Gate/);
	assert.match(proofStages, /Vault/);
	assert.match(proofStages, /Monitor/);
	assert.match(layout, /aria-current=\{isCurrent \? "step" : undefined\}/);
	assert.match(
		layout,
		/const proofStage\s*=\s*\(page === ["']generate["'] \? journeyStage : null\) \?\?\s*CORE_PAGE_STAGE\[page\]/,
	);
	assert.match(layout, /\{proofStage && \(/);
	assert.match(authenticatedApp, /const \[journeyStage, setJourneyStage\]/);
	assert.match(authenticatedApp, /onStageChange=\{setJourneyStage\}/);
	assert.match(
		generate,
		/onStageChange\?\.\(drillInJobId \? ["']debate["'] : ["']brief["']\)/,
	);
	assert.match(css, /\.app-site\s*\{[^}]*--app-canvas:\s*#081218;/s);
	assert.match(
		css,
		/:root\[data-theme="light"\] \.app-site\s*\{[^}]*--app-canvas:\s*#f2f5f1;/s,
	);
	assert.match(css, /\.app-site :focus-visible\s*\{/);
	assert.match(css, /\.app-site\s*\{[^}]*--text-4:\s*#71867e;/s);
	assert.match(
		css,
		/:root\[data-theme="light"\] \.app-site\s*\{[^}]*--text-4:\s*#60746b;/s,
	);
});

test("Generate uses brief-first workbench with context rail", () => {
	assert.match(generate, /className="generate-page"/);
	assert.match(generate, /className="app-page-heading generate-page__heading"/);
	assert.match(generate, /className="generate-workbench"/);
	assert.match(generate, /className="card generate-brief"/);
	assert.match(generate, /className="generate-context-rail"/);
	assert.match(generate, /className="generate-register"/);
	assert.match(
		css,
		/\.generate-workbench\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1\.35fr\) minmax\(280px,\s*0\.65fr\);/s,
	);
});

test("Strategy Passport separates evidence from user authority", () => {
	assert.match(passport, /className="passport-page"/);
	assert.match(passport, /className="app-page-heading passport-heading/);
	assert.match(passport, /className="passport-workspace"/);
	assert.match(passport, /className="passport-authority"/);
	assert.match(passport, /className="passport-evidence"/);
	assert.match(passport, /passport-deploy/);
	assert.match(
		css,
		/\.passport-workspace\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\) minmax\(280px,\s*340px\);/s,
	);
	assert.match(css, /\.passport-authority\s*\{[^}]*position:\s*sticky;/s);
	assert.match(css, /\.passport-rigor\s*\{[^}]*--text-4:\s*#566a61;/s);
});

test("Portfolio uses ledger metrics and split audit workspace", () => {
	assert.match(portfolio, /className="portfolio-page"/);
	assert.match(portfolio, /className="app-page-heading portfolio-heading/);
	assert.match(portfolio, /className="portfolio-ledger"/);
	assert.match(portfolio, /className="portfolio-workspace"/);
	assert.match(portfolio, /className="portfolio-positions"/);
	assert.match(portfolio, /className="portfolio-activity"/);
	assert.match(
		css,
		/\.portfolio-workspace\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1\.25fr\) minmax\(300px,\s*0\.75fr\);/s,
	);
});

test("app semantics reserve bronze for action and verdigris for verification", () => {
	assert.match(css, /--app-action:\s*#d9a85c;/);
	assert.match(css, /--app-verify:\s*#79c9b7;/);
	assert.match(
		css,
		/\.app-site \.btn-primary\s*\{[^}]*background:\s*var\(--app-action\);/s,
	);
	assert.match(
		css,
		/\.app-site \.tag-positive\s*\{[^}]*color:\s*var\(--app-verify\);/s,
	);
});

test("app motion and core workspaces respect narrow or reduced-motion contexts", () => {
	assert.match(
		css,
		/@media \(prefers-reduced-motion: reduce\)[^{]*\{[\s\S]*?\.app-site \.fade-up,[\s\S]*?animation:\s*none !important;/s,
	);
	assert.match(
		css,
		/@media \(max-width: 900px\)[^{]*\{[\s\S]*?\.generate-workbench,[\s\S]*?\.passport-workspace,[\s\S]*?\.portfolio-workspace\s*\{[^}]*grid-template-columns:\s*1fr;/s,
	);
});

const generationStream = readFileSync(
	new URL("../src/components/GenerationStream.jsx", import.meta.url),
	"utf8",
);

test("generation stream claims papers only from real per-candidate citations (task #54)", () => {
	// The old candidates_selected line rendered a papers COUNT sliced from the
	// curated library (a constant from the wrong population) — it must not return.
	assert.doesNotMatch(generationStream, /candidates;.*papers/);
	// The honest claim: candidate_drafted's own provenance-checked citations,
	// omitted when absent.
	assert.match(generationStream, /case 'candidate_drafted'[\s\S]{0,400}source_arxiv_ids/);
	assert.match(generationStream, /grounded in \$\{nPapers\}/);
});

const leaderboard = readFileSync(
	new URL("../src/components/Leaderboard.jsx", import.meta.url),
	"utf8",
);

test("leaderboard caveat banner is own-scope-gated with only the refresh residual (#1306, F2/F3 landed)", () => {
	// The broad unconditional banner ("known to be incorrect") is retired for
	// the curated view, whose freshness was verified against prod.
	assert.doesNotMatch(leaderboard, /known to be incorrect/);
	// The remaining banner renders ONLY under isOwn — the gate expression must
	// immediately precede the banner's status div.
	assert.match(leaderboard, /\{isOwn && \(\s*<div\s*\n?\s*role="status"/);
	// The one remaining residual: pre-correction rows that refresh later.
	assert.match(leaderboard, /before the August engine corrections/);
	// The interpreter-divergence clause is RETIRED (F2/F3 landed via #1320,
	// parity-pinned, verified on the redeployed runner) — its claim would now
	// be false, so it must not reappear in the rendered banner text. (The
	// comment block documenting the retirement legitimately mentions the
	// fixes, so pin the banner's own load-bearing phrases, not the comment.)
	assert.doesNotMatch(leaderboard, /live execution currently interprets/);
	assert.doesNotMatch(leaderboard, /awaiting\s*\n?\s*live-trading sign-off/);
});

test("Insights headline claim matches the write path — no bot-exclusion claim, per-stage split shown instead (#1366 AC3)", () => {
	// record_funnel writes the landed HLL for every beacon regardless of the
	// classifier's verdict (metrics_routes.py:278 runs before is_agent is even
	// read at :285) — the funnel's landed count does NOT drop crawlers/bots.
	// The old copy claimed otherwise; it must not come back.
	assert.doesNotMatch(insights, /crawlers and bots drop out/);
	// The fix: render the by_agent_type per-stage split the API already
	// returns (issue #788) instead of describing a filter the code doesn't
	// perform.
	assert.match(insights, /by_agent_type/);
});

test("Insights country names use Intl.DisplayNames, not a hand-maintained map, and the epoch date is real (#1366 AC4)", () => {
	assert.doesNotMatch(insights, /const COUNTRY_NAMES/);
	assert.match(insights, /Intl\.DisplayNames/);
	// ZZ must stay an explicit case — Intl renders it "Unknown Region", which
	// would contradict the "unknown / not provided" copy this page uses for ZZ.
	assert.match(insights, /ZZ.*Unknown \/ not provided/);
	assert.doesNotMatch(insights, /deployed today/);
	assert.match(insights, /epoch_started_at/);
});

test("Insights drops the first-person operator copy (#1366 AC5)", () => {
	assert.doesNotMatch(insights, /Live conversion instruments for our/);
});
