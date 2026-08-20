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

// ── 2. Zero strategies -> a named empty option, never a blank <select> ────
test('QuantLab renders a named empty option when the strategy library is empty', () => {
  assert.match(quantLab, /strategies\.length === 0/)
  assert.match(quantLab, /<option value="">No strategies in library<\/option>/)
  // The select must actually be disabled in that state, not just decorated.
  assert.match(quantLab, /disabled=\{strategies\.length === 0\}/)
})

// ── 3. Falsy strategyId -> the Equity Curve honest-empty-state card, ──────
//    never a silently absent card.
test('BacktestVisualizer renders the Equity Curve empty state when strategyId is falsy', () => {
  // The !strategyId branch must set the same "no data" flag the persisted-
  // but-empty-series branch uses, not leave every flag at its default.
  assert.match(
    backtestVisualizer,
    /if \(!strategyId\) \{\s*[^}]*setReturnsNoData\(true\)/,
  )
  assert.match(backtestVisualizer, /No strategy selected — the library has nothing to chart yet\./)
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
