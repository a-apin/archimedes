import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

// Quant Lab prod-readiness residuals (#1369).
//
// Same shape as a11y.test.js / app-visuals.test.js: readFileSync + regex pins
// on the source, no DOM renderer (ui/package.json runs plain `node --test`;
// see CLAUDE.md anti-goals — no jsdom/testing-library/vitest). Every
// assertion here was confirmed to FAIL against the pre-fix tree (see the PR
// body for the transcript: each fix reverted in turn, this suite re-run).

const riskAnalysis = readFileSync(new URL('../src/components/RiskAnalysis.jsx', import.meta.url), 'utf8')
const quantLab = readFileSync(new URL('../src/components/QuantLab.jsx', import.meta.url), 'utf8')
const backtestVisualizer = readFileSync(new URL('../src/components/BacktestVisualizer.jsx', import.meta.url), 'utf8')
const portfolioAdvisorPanels = readFileSync(
  new URL('../src/components/PortfolioAdvisorPanels.jsx', import.meta.url),
  'utf8',
)

// ── 1. Both risk calls route through apiGet (throws on non-2xx) ───────────
//
// A raw fetch() only rejects on network-level failure; `.ok` was never
// checked, so a 404 (FEATURE_QUANT off), a 401, or a 5xx all read as success
// and `backendError` never flipped. apiGet (ui/src/api.js) throws on any
// non-2xx status, making the existing catch block correct.
test('RiskAnalysis fetches /api/risk/cvar and /api/risk/greeks through apiGet, not a raw fetch', () => {
  assert.match(riskAnalysis, /import\s*\{\s*apiGet\s*\}\s*from\s*['"]\.\.\/api['"]/)
  assert.match(riskAnalysis, /apiGet\(['"]\/api\/risk\/cvar['"]\)/)
  assert.match(riskAnalysis, /apiGet\(['"]\/api\/risk\/greeks['"]\)/)
  assert.equal(
    (riskAnalysis.match(/fetch\(`\$\{API_BASE\}/g) || []).length,
    0,
    'no raw fetch(`${API_BASE}...`) call should remain for the risk endpoints',
  )
})

// ── 2. Zero strategies -> a named empty option, never a blank <select>, ───
//    and the placeholder must not claim "empty" while still loading or on
//    a fetch failure (adversarial-review finding on the first cut of this
//    fix: it used one blanket "No strategies in library" string for all
//    three states).
test('QuantLab select shows distinct copy while loading, on library-fetch failure, and when truly empty', () => {
  assert.match(quantLab, /strategies\.length === 0/)
  // The select must actually be disabled in that state, not just decorated.
  assert.match(quantLab, /disabled=\{strategies\.length === 0\}/)

  const optionBlock = quantLab.match(/<option value="">[\s\S]*?<\/option>/)
  assert.ok(optionBlock, 'expected a named <option value=""> placeholder')
  const text = optionBlock[0]
  assert.match(text, /loading/)
  assert.match(text, /libraryError/)
  const strings = [...text.matchAll(/'([^']*)'/g)].map((m) => m[1])
  assert.equal(strings.length, 3, 'expected three literal copy strings (loading / error / empty)')
  assert.equal(new Set(strings).size, 3, 'loading/error/empty copy must be pairwise distinct')
})

// ── 3. Falsy strategyId -> the Equity Curve honest-empty-state card, ──────
//    never a silently absent card — but NOT while the parent's own
//    strategy-list fetch is still in flight. Adversarial review caught the
//    first cut of this fix: it rendered "the library has nothing to chart
//    yet" on every normal page load (selectedId starts '' in QuantLab and
//    only resolves after an async round trip), not just on a genuinely
//    empty library.
test('BacktestVisualizer does not assert an empty library while the parent library fetch is still loading', () => {
  assert.match(
    backtestVisualizer,
    /if \(!strategyId\) \{[\s\S]*?if \(libraryLoading\) \{[\s\S]*?setReturnsLoading\(true\)[\s\S]*?return[\s\S]*?\}[\s\S]*?setReturnsNoData\(true\)[\s\S]*?setReturnsLoading\(false\)[\s\S]*?return[\s\S]*?\}/,
  )
  // The old blanket claim must be gone, replaced by copy that distinguishes
  // a genuinely empty library from a library the frontend couldn't fetch.
  assert.equal(
    (backtestVisualizer.match(/No strategy selected — the library has nothing to chart yet\./g) || []).length,
    0,
  )
  assert.match(backtestVisualizer, /'Strategy library unavailable — could not load strategies to chart\.'/)
  assert.match(backtestVisualizer, /'No strategies in the library yet — nothing to chart\.'/)
})

// ── Copy-honesty residuals from the same issue, pinned so they can't ──────
//    silently regress back once fixed.
test('RiskAnalysis intro no longer cites the non-existent "Advisor" surface', () => {
  assert.equal((riskAnalysis.match(/the Advisor/g) || []).length, 0)
  assert.match(riskAnalysis, /Optimizer &amp; Sizing/)
})

test('no developer-facing copy or raw routes leak onto the risk / optimizer surfaces', () => {
  assert.equal((riskAnalysis.match(/connect backend for live data/g) || []).length, 0)
  assert.equal((portfolioAdvisorPanels.match(/POST \{OPTIMIZE_ENDPOINT\}/g) || []).length, 0)
})

test('QuantLab intro no longer promises a live source the vault/trace panels can never reach today', () => {
  assert.equal((quantLab.match(/live source yet renders a synthetic sample/g) || []).length, 0)
})

// ── Adversarial-review residuals on the first cut of this PR ──────────────
//    (findings verified against the code, fixed in a follow-up commit).

// QuantLab.jsx never imports ROADMAP_SURFACES_ENABLED, so the original copy
// ("vault deployment isn't available in this build") was a hard-coded claim
// about a build flag the component never reads — false under a
// VITE_ROADMAP_SURFACES=true preview build, where vault deployment is live.
test('QuantLab intro does not assert vault deployment is unavailable in this build without reading the flag that decides it', () => {
  assert.equal((quantLab.match(/vault deployment isn't available in this build/g) || []).length, 0)
  assert.match(quantLab, /once a vault is deployed; until then/)
})

// The "figures below are a synthetic sample, marked on each section's
// badge" notice is only true when returnsProp is null. With a real
// strategy selected but FEATURE_QUANT off, VaRPanel/DrawdownPlot/
// RollingSharpePlot compute from the strategy's real persisted returns
// (sample={returnsProp == null}) and carry no badge — the notice must not
// claim otherwise.
test('RiskAnalysis backend-unavailable notice does not claim a synthetic sample when real persisted returns are selected', () => {
  assert.match(riskAnalysis, /returnsProp == null \? \(/)
  assert.match(riskAnalysis, /computed in-browser from this strategy's persisted returns, not a sample/)
})

// risk_routes.py's get_portfolio_cvar returns 200 with lookback_days=0 and
// every level zeroed when no strategy has a persisted equity curve at all —
// a real success, not an error, so `backendError` never flips. Without the
// lookback_days>0 requirement, `useBackend` read that response as measured
// data: it rendered "-0.00%" as a real figure and suppressed the sample
// badge (the plausible-substitute failure this issue exists to catch).
test('RiskAnalysis VaRPanel does not treat the zero-lookback CVaR response as live backend data', () => {
  assert.match(riskAnalysis, /cvarData\.levels\.length > 0 && cvarData\.lookback_days > 0/)
})
