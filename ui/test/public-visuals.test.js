import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";

const app = readFileSync(new URL("../src/App.jsx", import.meta.url), "utf8");
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
const securityUrl = new URL("../src/components/Security.jsx", import.meta.url);
const flowDiagram = readFileSync(
	new URL("../src/assets/flow-diagram.svg", import.meta.url),
	"utf8",
);
const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const sitemap = readFileSync(
	new URL("../public/sitemap.xml", import.meta.url),
	"utf8",
);
const nginx = readFileSync(
	new URL("../../nginx/nginx.conf", import.meta.url),
	"utf8",
);

test("public shell owns calm-precision tokens and accessible navigation", () => {
	assert.match(publicLayout, /className="public-site"/);
	assert.match(publicLayout, /BrandMark/);
	assert.match(publicLayout, /className="public-announcement"/);
	assert.match(publicLayout, /aria-label="Public navigation"/);
	assert.match(publicLayout, /aria-label=.*theme/i);
	assert.doesNotMatch(
		css,
		/\.public-brand__copy small,\s*\.public-theme-toggle\s*\{\s*display:\s*none;/s,
	);
	assert.match(publicLayout, /href="\/app\/generate"/);
	assert.match(css, /--brand-canvas:\s*#f4f1e9;/i);
	assert.match(css, /--brand-ink:\s*#0d1218;/i);
	assert.match(css, /--brand-cobalt:\s*#4658e8;/i);
	assert.match(css, /--brand-verdigris:\s*#147a69;/i);
	assert.match(css, /--brand-muted:\s*#596570;/i);
	assert.match(css, /\.public-site :focus-visible\s*\{/);
	assert.match(
		css,
		/\.public-skip-link\s*\{[^}]*color:\s*var\(--accent-on\);/s,
	);
	assert.doesNotMatch(css, /gradient\(/i);
	assert.doesNotMatch(architecture, /gradient\(/i);
});

test("security page separates verified controls from known limits", () => {
	assert.equal(existsSync(securityUrl), true, "Security.jsx must exist");
	const security = readFileSync(securityUrl, "utf8");
	assert.match(security, /Security is enforced boundaries, not a guarantee/i);
	assert.match(security, /Better Auth/);
	assert.match(security, /five-minute/i);
	// Post-scrub (2026-08-30): the known-limits list must still lead with the
	// two limits that matter most on a page with no execution behind it —
	// that Archimedes does not trade, and that a passed gate is not a
	// judgement guarantee. These replace the old "Agent may mis-rebalance …
	// cannot withdraw" pair, which pinned a custody claim the page no longer
	// makes.
	assert.match(security, /does not trade with\s+capital today/);
	assert.match(security, /Model risk:/);
	assert.match(security, /No independent security audit/i);
	assert.match(security, /Arc public testnet/i);
	assert.match(security, /<main className="security-page">/);
	assert.match(security, /<h1 id="security-title">/);
	assert.match(security, /id="known-limits"/);
});

test("security controls reserve enough width for their display heading", () => {
	assert.match(
		css,
		/\.security-controls__layout\s*\{[^}]*grid-template-columns:\s*minmax\(360px,\s*0\.82fr\)\s*minmax\(0,\s*1\.18fr\);[^}]*gap:\s*clamp\(56px,\s*6vw,\s*80px\);/s,
	);
});

test("landing has complete evidence-led conversion narrative", () => {
	for (const id of [
		"problem",
		"product",
		"capabilities",
		"workflow",
		"use-cases",
		"integrations",
		"security",
		"faq",
	]) {
		assert.match(landing, new RegExp(`id=["']${id}["']`));
	}
	assert.match(landing, /EvidenceLedger/);
	assert.match(landing, /RigorMatrix/);
	assert.match(landing, /AuthorityBoundary/);
	// Post-scrub (2026-08-30): AuthorityBoundary no longer describes an
	// execution-authority split, so it is no longer flag-gated — it renders
	// unconditionally and describes the admission boundary that is live. The
	// WORKFLOW roadmap tail is gone with it, so there is nothing left to
	// slice: every step in WORKFLOW describes a path that runs today.
	assert.match(landing, /^\t\t\t<AuthorityBoundary \/>$/m);
	assert.doesNotMatch(landing, /ROADMAP_SURFACES_ENABLED/);
	assert.doesNotMatch(landing, /WORKFLOW\.slice\(/);
	assert.match(landing, /Deflated Sharpe Ratio/);
	assert.match(landing, /Probability of Backtest Overfitting/);
	assert.match(landing, /Walk-forward out-of-sample/);
	assert.match(landing, /Look-ahead audit/);
	assert.doesNotMatch(landing, /testimonial|trusted by|customer logos/i);
});

test("landing uses a bespoke product theatre instead of register-template motifs", () => {
	assert.match(landing, /className="public-hero__stage"/);
	assert.match(landing, /className="public-proof-strip"/);
	assert.match(landing, /className="public-proof-deck"/);
	assert.match(landing, /className="public-use-case-scenes"/);
	assert.doesNotMatch(landing, /capability-register__index/);
	assert.doesNotMatch(
		landing,
		/Inspection register|Admission register|Product anatomy/,
	);
	assert.match(css, /--public-haze:\s*#efedff;/i);
	assert.match(css, /--public-stage:\s*#0c0c11;/i);
	assert.match(
		css,
		/@font-face\s*\{[^}]*font-family:\s*"Gabarito";[^}]*gabarito-latin\.woff2/s,
	);
	assert.match(
		css,
		/@font-face\s*\{[^}]*font-family:\s*"IBM Plex Mono";[^}]*ibm-plex-mono-latin-400\.woff2/s,
	);
	assert.match(
		css,
		/\.public-proof-deck article\s*\{[^}]*position:\s*sticky;/s,
	);
	assert.match(
		css,
		/\.public-landing\s*\{[^}]*overflow:\s*visible;[^}]*background:\s*var\(--canvas\);/s,
	);
	assert.match(
		css,
		/html:has\(\.public-site\),\s*body:has\(\.public-site\)\s*\{[^}]*overflow:\s*visible;/s,
	);
	assert.equal(
		existsSync(
			new URL("../public/fonts/gabarito-latin.woff2", import.meta.url),
		),
		true,
	);
	assert.equal(
		existsSync(
			new URL("../public/fonts/ibm-plex-mono-latin-400.woff2", import.meta.url),
		),
		true,
	);
});

test("proof deck exits its sticky phase as one complete card", () => {
	assert.match(
		css,
		/\.public-proof-deck article\s*\{[^}]*position:\s*sticky;[^}]*height:\s*340px;/s,
	);
	assert.match(
		css,
		/@media \(max-width: 760px\)[^{]*\{[\s\S]*?\.public-proof-deck article,[\s\S]*?\.public-proof-deck article:nth-child\(4\)\s*\{[^}]*position:\s*static;[^}]*height:\s*auto;/s,
	);
});

test("landing consolidates proof into connected instrument sections", () => {
	assert.match(landing, /className="public-section public-rigor-story"/);
	assert.match(landing, /className="public-proof-deck"/);
	assert.match(landing, /className="public-section public-path"/);
	assert.match(landing, /id="workflow"\s+className="public-path__sequence"/);
	assert.match(landing, /className="public-use-case-scenes"/);
	assert.match(landing, /id="integrations"\s+className="public-rail-stack"/);
	assert.match(landing, /className="authority-boundary__verdict"/);
	assert.match(
		css,
		/\.public-path__sequence ol\s*\{[^}]*grid-template-columns:\s*repeat\(auto-fit,/s,
	);
	assert.match(
		css,
		/\.public-use-case-scenes \.is-research\s*\{[^}]*margin-left:\s*auto;/s,
	);
});

test("landing uses real product capture and one primary CTA label", () => {
	assert.match(landing, /src="\/product-workspace\.png"/);
	assert.match(landing, /width=\{1600\}/);
	assert.match(landing, /height=\{1000\}/);
	assert.match(landing, /Generate a strategy/);
	assert.doesNotMatch(landing, /Get started|Try free|Start now/);
	assert.match(landing, /apiGet\("\/api\/config\/contracts"\)/);
	assert.match(landing, /Live census unavailable/);
	assert.match(landing, /poolsUnread/);
});

test("public architecture page uses one skip target and main landmark", () => {
	assert.match(publicLayout, /<div id="public-content" tabIndex="-1">/);
	assert.match(architecture, /return \(\s*<main className="page-content">/);
	assert.doesNotMatch(architecture, /id="public-content"/);
});

test("architecture diagram uses account-first identity and current brand semantics", () => {
	assert.match(flowDiagram, /Account owns research and settings/);
	assert.match(flowDiagram, /Wallet proof only for on-chain actions/);
	assert.match(flowDiagram, /Arc public testnet/);
	assert.doesNotMatch(
		flowDiagram,
		/#D4A853|Georgia|SIWE|289 contracts|verified against main/i,
	);
	assert.doesNotMatch(architecture, /sign in with any wallet/i);
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

test("public product theatre stays structural across desktop and mobile", () => {
	assert.match(
		css,
		/\.public-hero__stage\s*\{[^}]*display:\s*grid;[^}]*border-radius:\s*10px;[^}]*background:\s*var\(--public-theatre-bg\);/s,
	);
	assert.match(
		css,
		/\.public-product-frame\s*\{[^}]*width:\s*min\(100%,\s*900px\);/s,
	);
	assert.match(
		css,
		/@media \(max-width: 760px\)[^{]*\{[\s\S]*?\.public-hero__stage\s*\{[^}]*padding:\s*44px 20px 0;/s,
	);
});

test("light landing replaces every dark theatre surface", () => {
	assert.match(
		css,
		/:root\[data-theme="light"\] \.public-site\s*\{[^}]*--public-theatre-bg:\s*var\(--surface-2\);[^}]*--public-theatre-text:\s*var\(--public-ink\);/s,
	);
	for (const selector of [
		"public-hero__stage",
		"public-path__sequence",
		"authority-boundary",
	]) {
		assert.match(
			css,
			new RegExp(
				`\\.${selector}\\s*\\{[^}]*background:\\s*var\\(--public-theatre-bg\\);`,
				"s",
			),
		);
	}
	assert.match(
		css,
		/\.public-proof-deck article:nth-child\(3\)\s*\{[^}]*background:\s*var\(--public-theatre-bg\);/s,
	);
	assert.match(
		css,
		/\.public-use-case-scenes \.is-research\s*\{[^}]*background:\s*var\(--public-theatre-bg\);/s,
	);
	assert.match(
		css,
		/:root\[data-theme="light"\] \.public-site\s*\{[^}]*--public-theatre-positive:\s*#116c5e;/s,
	);
	assert.match(
		css,
		/\.authority-boundary__side--agent \.authority-boundary__owner\s*\{[^}]*color:\s*var\(--public-theatre-positive\);/s,
	);
});

test("dark landing preserves atmospheric contrast and semantic accents", () => {
	assert.match(
		css,
		/\.public-site\s*\{[^}]*--canvas:\s*#15131d;[^}]*--glass-border:\s*#484155;[^}]*--accent-on:\s*#17151f;/s,
	);
	assert.match(css, /--accent-on-muted:\s*rgba\(23, 21, 31, 0\.82\);/);
	assert.match(
		css,
		/:root\[data-theme="light"\] \.public-site\s*\{[^}]*--accent-on-muted:\s*rgba\(255, 255, 255, 0\.9\);/s,
	);
	assert.match(
		css,
		/\.public-site\s*\{[^}]*--public-theatre-bg:\s*var\(--public-stage\);/s,
	);
	assert.match(
		css,
		/\.authority-boundary\s*\{[^}]*background:\s*var\(--public-theatre-bg\);/s,
	);
	assert.match(
		css,
		/\.public-proof-deck article:nth-child\(2\)\s*\{[^}]*background:\s*var\(--accent\);[^}]*color:\s*var\(--accent-on\);/s,
	);
});

test("ownership verdict follows the active public theme", () => {
	assert.match(
		css,
		/\.public-site\s*\{[^}]*--public-theatre-contrast:\s*var\(--surface-1\);[^}]*--public-theatre-contrast-text:\s*var\(--text-1\);[^}]*--public-theatre-contrast-muted:\s*var\(--text-3\);/s,
	);
	assert.match(
		css,
		/:root\[data-theme="light"\] \.public-site\s*\{[^}]*--public-theatre-contrast-muted:\s*#625d6a;/s,
	);
	assert.match(
		css,
		/\.authority-boundary__verdict span\s*\{[^}]*color:\s*var\(--public-theatre-contrast-muted\);/s,
	);
});

test("metadata and sitemap describe canonical anonymous public routes", () => {
	assert.doesNotMatch(html, /rel="canonical"/);
	assert.match(app, /document\.querySelector\(['"]link\[rel=[^\]]*canonical/);
	assert.match(app, /architecture:\s*["']\/architecture["']/);
	assert.match(app, /canonicalPaths\[route\.page\]\s*\?\?\s*["']\/["']/);
	assert.match(
		html,
		/property="og:image"\s+content="https:\/\/archimedes-arc\.com\/og-image\.png"/,
	);
	assert.match(html, /name="twitter:card" content="summary_large_image"/);
	assert.match(
		html,
		/"target":\s*"https:\/\/archimedes-arc\.com\/app\/generate"/,
	);
	assert.match(sitemap, /<loc>https:\/\/archimedes-arc\.com\/<\/loc>/);
	assert.match(
		sitemap,
		/<loc>https:\/\/archimedes-arc\.com\/architecture<\/loc>/,
	);
	for (const route of ["explore", "leaderboard", "corpus", "insights"]) {
		assert.match(
			sitemap,
			new RegExp(`<loc>https://archimedes-arc\\.com/${route}</loc>`),
		);
	}
	assert.doesNotMatch(
		sitemap,
		/<loc>https:\/\/archimedes-arc\.com\/(marketplace|portfolio|publish|subscriptions)<\/loc>/,
	);
});

test("security page is a canonical public destination", () => {
	assert.match(app, /import Security from ["']\.\/components\/Security["']/);
	assert.match(app, /security:\s*["']Security · Archimedes["']/);
	assert.match(app, /security:\s*["']\/security["']/);
	assert.match(app, /route\.page === ["']security["'][\s\S]*<Security \/>/);
	assert.match(publicLayout, /href="\/security"[\s\S]*Security/);
	assert.ok((landing.match(/href="\/security"/g) ?? []).length >= 2);
	assert.match(sitemap, /<loc>https:\/\/archimedes-arc\.com\/security<\/loc>/);
});

test("production CSP permits only the hashed theme bootstrap", () => {
	// Case-insensitive: this extracts our own build's inline bootstrap for CSP
	// hashing (not a security filter on untrusted input), but CodeQL js/bad-tag-filter
	// flags a case-sensitive <script> match — and it costs nothing to be exact.
	const themeBootstrap = html.match(/<script>([\s\S]*?)<\/script>/i)?.[1];
	assert.ok(themeBootstrap);
	const themeHash = createHash("sha256")
		.update(themeBootstrap)
		.digest("base64");
	assert.ok(nginx.includes(`script-src 'self' 'sha256-${themeHash}'`));
	// Scope the unsafe-inline ban to the app's DEFAULT CSP entry: the internal
	// ~^/docs location (FastAPI docs UI, main-side, pre-existing) legitimately
	// carries 'unsafe-inline' for swagger assets and is not part of the public
	// app surface this test guards.
	const defaultCsp = nginx
		.split("\n")
		.find((line) => line.includes('default "default-src'));
	assert.ok(defaultCsp, "default CSP map entry must exist in nginx.conf");
	assert.doesNotMatch(defaultCsp, /script-src[^;]*'unsafe-inline'/);
});

test("generated public product and social images exist", () => {
	assert.equal(
		existsSync(new URL("../public/product-workspace.png", import.meta.url)),
		true,
	);
	assert.equal(
		existsSync(new URL("../public/og-image.png", import.meta.url)),
		true,
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
