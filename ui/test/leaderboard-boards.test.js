// Claim-integrity guards for the leaderboard's two-board split (Lane 3.4).
//
// The page now shows two surfaces whose numbers mean entirely different
// things: a RESEARCH tab whose every figure is backtest-era, and a LIVE PAPER
// tab whose every figure is compounded from the append-only forward ledger.
// The whole value of the split is destroyed by exactly two regressions, and
// both are source-checkable:
//
//   1. A LIVE ROW RENDERED WITHOUT LEDGER DATA. The backend already withholds
//      deployments with an empty ledger, so the only way one can appear on
//      screen is if this component invents it — most plausibly by falling
//      back to the research payload (`data.entries`) when `liveData` is empty
//      or absent. That is the "fabricated statistics on the flagship public
//      page" defect named in docs/architectural-principles.md § fail-soft,
//      and the test below slices the live block out by sentinel comments and
//      proves it contains no reference to the research payload at all.
//
//   2. A NUMBER RENDERED WITHOUT ITS BASIS. A Sharpe with no "measured over
//      2015–2024" beside it reads as a claim about now.
//
// Same shape as leaderboard-selectivity.test.js / roadmap-copy.test.js: a raw
// source-text scan (readFileSync, no JSX parsing), every pattern proved
// non-vacuous against its own canonical bad example, and a positive assertion
// that the honest code path exists — so no guard here can pass merely because
// the feature was deleted.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test } from 'node:test'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')
const LEADERBOARD_FILE = 'components/Leaderboard.jsx'

const source = readFileSync(join(SRC, LEADERBOARD_FILE), 'utf8')

// The sentinels the component brackets each board's JSX with. They are the
// contract this file slices on: renaming or dropping one must fail loudly
// here rather than silently widen what the live-block scan looks at.
const LIVE_BEGIN = 'LIVE-BOARD:BEGIN'
const LIVE_END = 'LIVE-BOARD:END'
const RESEARCH_BEGIN = 'RESEARCH-BOARD:BEGIN'
const RESEARCH_END = 'RESEARCH-BOARD:END'

// The research payload's row array. If this identifier appears inside the
// live block, a research entry can reach the live table.
const RESEARCH_PAYLOAD_ROWS = /\bdata\.entries\b/
// The exact empty-state sentence the product owes a user with a thin ledger.
const HONEST_EMPTY_STATE = 'No strategies are live paper trading yet'

function sliceBetween(text, begin, end) {
  const from = text.indexOf(begin)
  const to = text.indexOf(end)
  assert.ok(from !== -1, `sentinel "${begin}" is missing from ${LEADERBOARD_FILE} — this guard cannot slice`)
  assert.ok(to !== -1, `sentinel "${end}" is missing from ${LEADERBOARD_FILE} — this guard cannot slice`)
  assert.ok(to > from, `sentinel "${end}" precedes "${begin}" in ${LEADERBOARD_FILE}`)
  return text.slice(from, to)
}

// ── the scan cannot silently shrink ─────────────────────────────────────────

test('both board sentinels exist and bracket non-trivial blocks', () => {
  const live = sliceBetween(source, LIVE_BEGIN, LIVE_END)
  const research = sliceBetween(source, RESEARCH_BEGIN, RESEARCH_END)
  // A degenerate (near-empty) slice would make every scan below vacuously
  // pass. Both blocks render a whole table; a few hundred characters is a
  // floor no real implementation can fall under.
  assert.ok(live.length > 500, 'the live-board block is suspiciously small — did the tab get gutted?')
  assert.ok(research.length > 500, 'the research-board block is suspiciously small — did the tab get gutted?')
})

test('the two tabs are labelled for what they actually measure', () => {
  assert.match(source, /Research \(backtest conviction\)/, 'expected the research tab label from the spec')
  assert.match(source, /Live paper trading/, 'expected the live paper tab label from the spec')
})

// ── 1. the live tab may never render a row without ledger data ──────────────

test('RESEARCH_PAYLOAD_ROWS rejects its own canonical bad example (guard is not vacuous)', () => {
  assert.match(
    '{data.entries.map(e => (',
    RESEARCH_PAYLOAD_ROWS,
    'RESEARCH_PAYLOAD_ROWS no longer matches a data.entries render — it is guarding nothing',
  )
  assert.match(
    'const rows = liveData?.entries ?? data.entries',
    RESEARCH_PAYLOAD_ROWS,
    'RESEARCH_PAYLOAD_ROWS must catch a fallback from the live payload to the research one',
  )
})

test('the live board never reads the research payload — no fallback row source exists', () => {
  const live = sliceBetween(source, LIVE_BEGIN, LIVE_END)
  assert.doesNotMatch(
    live,
    RESEARCH_PAYLOAD_ROWS,
    'the live paper block references data.entries (the RESEARCH payload). A backtest row must never be able to ' +
      'reach the live table — the backend withholds deployments with no ledger data, and a fallback here would ' +
      'put back exactly the fabricated rows the split exists to prevent.',
  )
  // Positive: the live table's rows come from the forward payload, and that
  // payload is fetched from the forward endpoint.
  assert.match(live, /liveEntries\.map\(/, 'expected the live table to map over liveEntries')
  assert.match(source, /const liveEntries = liveData\?\.entries \?\? \[\]/, 'liveEntries must derive from liveData only')
  assert.match(source, /\/api\/leaderboard\/live-paper/, 'expected the forward board to be fetched from its own endpoint')
})

test('the live table only renders when the forward payload actually has rows', () => {
  const live = sliceBetween(source, LIVE_BEGIN, LIVE_END)
  assert.match(
    live,
    /liveEntries\.length > 0 && \(/,
    'the live table must be gated on liveEntries.length > 0 — never rendered off a null/empty payload',
  )
  // ...and the complementary branch says the honest thing rather than
  // rendering a zeroed row.
  assert.match(
    live,
    /liveEntries\.length === 0 && \(/,
    'expected an explicit empty branch for a thin ledger, not an implicitly-blank table',
  )
})

test('the honest empty state is the exact sentence, and it is not an error state', () => {
  assert.match(
    source,
    new RegExp(HONEST_EMPTY_STATE.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')),
    `expected the live tab's empty state to read "${HONEST_EMPTY_STATE}"`,
  )
  const live = sliceBetween(source, LIVE_BEGIN, LIVE_END)
  // An empty ledger is a true statement, not a failure: the empty branch must
  // be gated on NOT loading, NOT errored and NOT degraded, so it can never
  // claim "nothing is live" when the real answer is "we could not tell".
  assert.match(
    live,
    /!liveLoading && !liveError && liveData && !liveData\.degraded && liveEntries\.length === 0/,
    'the empty state must be pre-empted by the loading, error and degraded states — otherwise it asserts ' +
      '"nothing is paper trading" when the truth is "the ledger could not be read"',
  )
  assert.match(live, /degraded_reason/, 'a degraded forward board must say so rather than render as empty')
})

test('live rows display the ledger-derived fields, not a bare number', () => {
  const live = sliceBetween(source, LIVE_BEGIN, LIVE_END)
  for (const field of ['cumulative_return', 'days_live', 'inception_date', 'as_of']) {
    assert.match(live, new RegExp(`row\\.${field}\\b`), `expected the live row to render ${field} from the payload`)
  }
  // The return is realised-to-date and must not be dressed up as a rate.
  assert.match(live, /realised, not annualised/, 'the forward return must be labelled as realised, not annualised')
})

// ── 2. every displayed number carries its basis ─────────────────────────────

test('the shared basis badge exists and is keyed off the API field, not hard-coded copy', () => {
  assert.match(source, /function BasisBadge\(/, 'expected a shared provenance badge component')
  assert.match(source, /const BASIS_COPY = \{/, 'expected badge copy to be a lookup keyed by the API basis value')
  assert.match(source, /backtest_research:/, 'expected the backtest basis key from the API')
  assert.match(source, /live_paper:/, 'expected the live-paper basis key from the API')
})

test('rows on both boards carry a basis badge sourced from the payload', () => {
  const live = sliceBetween(source, LIVE_BEGIN, LIVE_END)
  const research = sliceBetween(source, RESEARCH_BEGIN, RESEARCH_END)
  assert.match(
    research,
    /<BasisBadge basis=\{e\.performance_basis\} \/>/,
    'each research row must carry the basis the API stamped on it (never a literal)',
  )
  assert.match(
    live,
    /<BasisBadge basis=\{row\.performance_basis\} \/>/,
    'each live row must carry the basis the API stamped on it (never a literal)',
  )
})

test('research rows label the window their numbers were measured over', () => {
  const research = sliceBetween(source, RESEARCH_BEGIN, RESEARCH_END)
  assert.match(
    research,
    /<WindowLabel start=\{e\.backtest_start\} end=\{e\.backtest_end\} \/>/,
    'each research row must show the backtest window its metrics came from',
  )
  // A row with no recorded window must SAY so — an omitted qualifier reads as
  // "these numbers apply now", which is the misreading the split prevents.
  assert.match(source, /window not recorded/, 'a row without a window must say so rather than render unqualified')
})

test('the research tab shows the methodology sentence from engine metadata, not restated by hand', () => {
  const research = sliceBetween(source, RESEARCH_BEGIN, RESEARCH_END)
  assert.match(
    research,
    /\{engine\.methodology\}/,
    'the conviction methodology line must come from the API engine metadata so it cannot drift from the formula run',
  )
})

// ── 3. anti-goal: no blended score anywhere in this component ───────────────

test('no blended cross-basis score is computed in the component', () => {
  // The anti-goal, source-checked: the page must not combine a conviction
  // score with a forward return into one figure. Any arithmetic mixing the
  // two payloads' fields is the smell.
  const BLEND = /conviction_score[^\n]*cumulative_return|cumulative_return[^\n]*conviction_score/
  assert.match(
    'const blended = e.conviction_score * 0.5 + row.cumulative_return * 0.5',
    BLEND,
    'BLEND no longer matches its own canonical bad example — it is guarding nothing',
  )
  assert.doesNotMatch(
    source,
    BLEND,
    'Leaderboard.jsx appears to combine a backtest conviction score with a live-paper return into one number. ' +
      'The two bases are never blended — a strong backtest must not be able to carry a strategy that has ' +
      'traded forward for a handful of days.',
  )
})
