// Claim-integrity guards for the board-level FDR column on the research board
// (#1564).
//
// The owner's instruction was "render it as best we can", and the honest
// rendering has three properties that are all one careless edit away from
// being lost. Each is source-checkable, so each is guarded here:
//
//   1. AN ABSENT CORRECTION IS AN EM-DASH, NEVER A VERDICT. A row with no DSR
//      confidence was never corrected. Rendering it through a plain ternary
//      (`significant ? 'Clears' : 'Not distinguishable'`) prints a
//      board-level verdict for a row that has no board-level evidence —
//      fabricating exactly the kind of statistic docs/architectural-principles.md
//      § fail-soft names. The em-dash is the loud absence.
//
//   2. THE HONEST COPY SURVIVES. "Not yet distinguishable from selection noise
//      at board level" is the sentence the correction actually supports today
//      (prod pull, #1555: nothing on the board clears BH at any conventional
//      level). Softening it into "pending" or dropping it re-hides the thing
//      this issue exists to show.
//
//   3. THE HEADLINE COUNTS COME OFF THE PAYLOAD, NOT THE VISIBLE ROWS. The
//      backend corrects over the WHOLE board cohort on purpose — BH's adjusted
//      p is p×m/k, so a smaller m makes every row look more significant, and a
//      count recomputed from the filtered/paged table would be a different,
//      flattering number.
//
// Same shape as leaderboard-boards.test.js / leaderboard-selectivity.test.js:
// a raw source-text scan (readFileSync, no JSX parsing), every pattern proved
// non-vacuous against its own canonical bad example, and a positive assertion
// that the honest path exists — so no guard here can pass merely because the
// feature was deleted.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test } from 'node:test'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')
const LEADERBOARD_FILE = 'components/Leaderboard.jsx'

const source = readFileSync(join(SRC, LEADERBOARD_FILE), 'utf8')

const CELL_BEGIN = 'BOARD-FDR-CELL:BEGIN'
const CELL_END = 'BOARD-FDR-CELL:END'
const SUMMARY_BEGIN = 'BOARD-FDR-SUMMARY:BEGIN'
const SUMMARY_END = 'BOARD-FDR-SUMMARY:END'

// The null-guard the cell must carry before it can print any verdict. `== null`
// (loose) is deliberate: it covers both null and an absent field.
const NULL_GUARD = /board_fdr_significant\s*==\s*null/
// The named constant the absent case must return, and its definition — the
// em-dash itself. Checked as two halves so neither can drift: the cell must
// return the constant, and the constant must still BE an em-dash (swapping it
// for "pending" or "n/a" would sail past a name-only check).
const ABSENT_CONSTANT = /\bBOARD_FDR_ABSENT\b/
const ABSENT_CONSTANT_IS_EM_DASH = /const\s+BOARD_FDR_ABSENT\s*=\s*['"]—['"]/
// The research payload's row array. If it appears inside the summary block, the
// headline is being recomputed off the filtered/paged table.
const RECOUNT_OFF_VISIBLE_ROWS = /\bdata\.entries\b/
// The exact sentence the product owes a reader when nothing clears.
const HONEST_COPY = 'Not yet distinguishable from selection noise at board level'

function sliceBetween(text, begin, end) {
  const from = text.indexOf(begin)
  const to = text.indexOf(end)
  assert.ok(from !== -1, `sentinel "${begin}" is missing from ${LEADERBOARD_FILE} — this guard cannot slice`)
  assert.ok(to !== -1, `sentinel "${end}" is missing from ${LEADERBOARD_FILE} — this guard cannot slice`)
  assert.ok(to > from, `sentinel "${end}" precedes "${begin}" in ${LEADERBOARD_FILE}`)
  return text.slice(from, to)
}

// ── the scan cannot silently shrink ─────────────────────────────────────────

test('both board-FDR sentinels exist and bracket non-trivial blocks', () => {
  const cell = sliceBetween(source, CELL_BEGIN, CELL_END)
  const summary = sliceBetween(source, SUMMARY_BEGIN, SUMMARY_END)
  assert.ok(cell.length > 300, 'the board-FDR cell block is suspiciously small — did the column get gutted?')
  assert.ok(summary.length > 300, 'the board-FDR summary block is suspiciously small — did the headline get gutted?')
})

// ── 1. an absent correction renders as an em-dash, never as a verdict ───────

test('NULL_GUARD rejects its own canonical bad example (guard is not vacuous)', () => {
  // The exact shape this rule exists to forbid: a bare ternary that prints a
  // verdict for a row that was never corrected.
  const bad = "{entry.board_fdr_significant ? 'Clears' : 'Not distinguishable'}"
  assert.doesNotMatch(bad, NULL_GUARD, 'NULL_GUARD matches a cell with no null branch — it is guarding nothing')
  // ...and it must still recognise the honest shape.
  const good = "if (entry.board_fdr_significant == null) return BOARD_FDR_ABSENT"
  assert.match(good, NULL_GUARD, 'NULL_GUARD no longer matches the honest null branch — it is guarding nothing')
})

test('the board-FDR cell checks for an absent correction before printing any verdict', () => {
  const cell = sliceBetween(source, CELL_BEGIN, CELL_END)
  assert.match(cell, NULL_GUARD, 'the board-FDR cell must branch on board_fdr_significant == null')
  assert.match(cell, ABSENT_CONSTANT, 'the absent branch must return the BOARD_FDR_ABSENT constant')
  assert.match(
    source,
    ABSENT_CONSTANT_IS_EM_DASH,
    'BOARD_FDR_ABSENT is no longer an em-dash — an absent correction must render as a loud blank, ' +
      'not as a word that reads like a state ("pending", "n/a")',
  )

  // Order matters, not just presence: the null branch has to come BEFORE the
  // significant/not-significant ternary, or the verdict is chosen first and the
  // guard is decoration.
  const nullAt = cell.search(NULL_GUARD)
  const verdictAt = cell.indexOf('Not distinguishable')
  assert.ok(verdictAt !== -1, 'the cell no longer renders the not-significant label')
  assert.ok(
    nullAt < verdictAt,
    'the null check must precede the verdict labels, or an uncorrected row can still be given a verdict',
  )
})

// ── 2. the honest copy survives ────────────────────────────────────────────

test('the honest board-level sentence is rendered verbatim', () => {
  assert.ok(
    source.includes(HONEST_COPY),
    `${LEADERBOARD_FILE} no longer carries the sentence the correction actually supports: "${HONEST_COPY}"`,
  )
  // It must reach BOTH surfaces: the per-row tooltip and the board headline.
  const cell = sliceBetween(source, CELL_BEGIN, CELL_END)
  assert.ok(
    cell.includes('BOARD_FDR_NOT_DISTINGUISHABLE'),
    'the per-row tooltip must use the same honest sentence, not a softened restatement',
  )
  const summary = sliceBetween(source, SUMMARY_BEGIN, SUMMARY_END)
  assert.ok(
    summary.includes('BOARD_FDR_NOT_DISTINGUISHABLE'),
    'the board headline must use the same honest sentence, not a softened restatement',
  )
})

test('the column exists and is labelled for the correction it shows', () => {
  assert.match(source, /Board FDR/, 'the research table must carry a Board FDR column header')
  assert.match(source, /<BoardFdrCell\b/, 'the research table must render the cell component per row')
})

// ── 3. the headline counts come off the payload, not the visible rows ──────

test('RECOUNT_OFF_VISIBLE_ROWS rejects its own canonical bad example (guard is not vacuous)', () => {
  const bad = 'const n = data.entries.filter(e => e.board_fdr_significant).length'
  assert.match(
    bad,
    RECOUNT_OFF_VISIBLE_ROWS,
    'RECOUNT_OFF_VISIBLE_ROWS no longer matches a recount off the rendered rows — it is guarding nothing',
  )
})

test('the board-FDR headline is derived from the payload block, never recounted off the table', () => {
  const summary = sliceBetween(source, SUMMARY_BEGIN, SUMMARY_END)
  assert.doesNotMatch(
    summary,
    RECOUNT_OFF_VISIBLE_ROWS,
    'the board-FDR headline must not be recomputed from data.entries — the table is filtered and paged, ' +
      'the correction is not, so a recount would report a different (and more flattering) number',
  )
  assert.match(summary, /boardFdr\.n_tested/, 'the cohort size must come from the payload block')
  assert.match(summary, /boardFdr\.n_significant/, 'the significant count must come from the payload block')
  assert.match(summary, /boardFdr\.methodology/, 'the methodology sentence must come from the backend, not be restated here')
})

// ── the correction must not be presented as a gate ─────────────────────────

test('the column is labelled advisory, so it is never read as a second rigor gate', () => {
  assert.match(
    source,
    /Advisory — it (?:does not|never) change/i,
    'the board-FDR surface must say plainly that it does not change the rigor-gate badge',
  )
})
