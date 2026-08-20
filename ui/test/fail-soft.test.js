import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

// #1356 — an API failure must render as a loud, visible absence, never as an
// empty state or a measured zero. Same shape as public-visuals.test.js /
// a11y.test.js: readFileSync + regex pins on the source, no DOM. Every
// assertion here was confirmed to FAIL against the pre-fix tree (see the PR
// body for the revert-and-rerun transcripts).

const src = (p) => readFileSync(new URL(`../src/${p}`, import.meta.url), "utf8");

const architecture = src("components/Architecture.jsx");
const landing = src("components/Landing.jsx");
const strategies = src("components/Strategies.jsx");
const corpusExplorer = src("components/CorpusExplorer.jsx");
const leaderboard = src("components/Leaderboard.jsx");

// ── Item 2: /api/config/contracts null-vs-loading-vs-zero ────────────────

test("Architecture.jsx treats a null vaults read as failed, not loading and not zero", () => {
	// contracts.vaults === null (the fetch resolved, but the on-chain read
	// failed) must flip the "Live user vaults" tile to failed — distinct from
	// contractsLoading (contracts itself still null, i.e. fetch pending).
	assert.match(
		architecture,
		/const vaultsUnread = contracts != null && contracts\.vaults == null;/,
	);
	assert.match(architecture, /failed=\{contractsError \|\| vaultsUnread\}/);
});

test("Landing.jsx renders the error census state for a null pool read, not the waiting state", () => {
	assert.match(
		landing,
		/const poolsUnread = contracts != null && contracts\.pools == null;/,
	);
	// The error branch's condition must include poolsUnread, so a null-because-
	// unread pool count takes the census-state--error branch rather than
	// falling through to "Waiting for live deployment data…" (totalLive==null
	// is also true in that case, so ordering/inclusion here is load-bearing).
	assert.match(landing, /\{contractsError \|\| poolsUnread \? \(/);
	assert.match(landing, /census-state census-state--error/);
	assert.match(landing, /Waiting for live deployment data…/);
});

test("Landing.jsx's live census states a denominator for the core-contract count", () => {
	assert.match(landing, /CORE_CONTRACT_FIELDS\.length/);
});

// ── Item 3: Library reports a per-feed failure ────────────────────────────

test("Strategies.jsx tracks genRes / gateRes / publishedRes failures separately", () => {
	assert.match(strategies, /const \[genError, setGenError\] = useState\(''\)/);
	assert.match(strategies, /const \[gateError, setGateError\] = useState\(''\)/);
	assert.match(strategies, /const \[publishedError, setPublishedError\] = useState\(''\)/);
	assert.match(strategies, /setGenError\(genRes\.reason\?\.message/);
	assert.match(strategies, /setGateError\(gateRes\.reason\?\.message/);
	assert.match(strategies, /setPublishedError\(publishedRes\.reason\?\.message/);
	// The old comment asserted a false equivalence (empty state == honest
	// fallback) that this issue exists to retract.
	assert.doesNotMatch(strategies, /empty state is the honest fallback/);
});

test("Strategies.jsx's Examples feed treats a fulfilled-but-degraded response as a failure, not a real empty library", () => {
	// /api/strategies/ (the seed/Examples feed) can now return HTTP 200 with
	// `{strategies: [], degraded: true}` when the provider raised — a 2xx is
	// not proof the fetch actually succeeded, so a fulfilled promise alone
	// must not be trusted to mean "the library is genuinely empty" (#1356
	// re-occurring on the very PR meant to close it — a fulfilled seedRes
	// with degraded:true used to fall straight through to setExamples([])
	// with loadError never set, painting the false "No example strategies
	// loaded." instead of the loud loadError banner).
	assert.match(strategies, /if \(seedRes\.value\.degraded\) \{/);
	assert.match(strategies, /setLoadError\(seedRes\.value\.degraded_reason/);
});

test("Strategies.jsx's generated panel has the same loading guard Examples/Published already have", () => {
	// Examples/Published: `{loading && <div className="caption mb-4">Loading…</div>}`
	// then a separate `{!loading && (...)}` block. Generated now uses a
	// ternary with the identical loading branch inside the same activeTab
	// block, so this must appear a third time (once per tab).
	const loadingGuardCount = (strategies.match(/<div className="caption mb-4">Loading…<\/div>/g) || []).length;
	assert.equal(loadingGuardCount, 3, "expected one loading guard each for generated, examples, and published");
});

test("Strategies.jsx surfaces per-feed errors near the panels they describe, with a retry", () => {
	assert.match(strategies, /Couldn't load generated strategies: \{genError\}/);
	assert.match(strategies, /Deployability status unavailable: \{gateError\}/);
	assert.match(strategies, /Couldn't load published strategies: \{publishedError\}/);
	// Every per-feed error offers a way back in, not just a dead-end message.
	const retryCount = (strategies.match(/onClick=\{load\}/g) || []).length;
	assert.ok(retryCount >= 3, `expected at least 3 retry buttons wired to load(), found ${retryCount}`);
});

// ── Item 4: Corpus gets error state + retry ───────────────────────────────

test("CorpusExplorer.jsx never swallows an overview failure into null with no error flag", () => {
	assert.doesNotMatch(corpusExplorer, /catch\(\(\) => setOverview\(null\)\)/);
	assert.match(corpusExplorer, /const \[overviewError, setOverviewError\] = useState\(false\)/);
});

test("CorpusExplorer.jsx's overview tab renders an unavailable + Retry line instead of an infinite spinner", () => {
	assert.match(
		corpusExplorer,
		/function OverviewTab\(\{ overview, overviewError, onRetry \}\)/,
	);
	assert.match(corpusExplorer, /if \(overviewError\) \{/);
	assert.match(corpusExplorer, /Overview unavailable/);
	assert.match(corpusExplorer, /onClick=\{onRetry\}/);
	// The permanent-spinner line must still exist as the genuine-loading
	// fallback, but only reached once overviewError has already been ruled out.
	assert.match(corpusExplorer, /Loading overview\.\.\./);
});

test("CorpusExplorer.jsx's catalog tab suppresses the papers-found count on a failed fetch", () => {
	assert.match(corpusExplorer, /const \[catalogError, setCatalogError\] = useState\(false\)/);
	assert.match(corpusExplorer, /setCatalogError\(true\)/);
	assert.match(corpusExplorer, /catalogError \? \(/);
	assert.match(corpusExplorer, /Catalog unavailable/);
});

test("CorpusExplorer.jsx's header stat chips stay visible on an overview failure, on every tab", () => {
	// The header (papers/categories/source chips) renders once, above the
	// per-tab content, so `tab === 'catalog'` (the default) must not hide an
	// overview outage behind a tab the visitor never opens. Must not fall
	// straight through to the bare `overview && (...)` — that silently
	// renders nothing, indistinguishable from "still loading".
	assert.match(corpusExplorer, /overviewError \? \(/);
	assert.match(corpusExplorer, /corpus stats unavailable/);
});

// ── Item 5-6: degraded leaderboard must not fall through to a false claim ─

test("Leaderboard.jsx never renders the filters-empty message while the board is degraded", () => {
	assert.match(leaderboard, /data\?\.degraded/);
	assert.match(leaderboard, /Board data is degraded\./);
	// Both honest-empty branches must be gated on !data.degraded so a
	// degraded board can't fall through to a false "nothing here" claim.
	assert.match(
		leaderboard,
		/!error && data && !data\.degraded && data\.entries\.length === 0 && isOwn/,
	);
	assert.match(
		leaderboard,
		/!error && data && !data\.degraded && data\.entries\.length === 0 && !isOwn/,
	);
});
