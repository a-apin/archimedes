// Claim-integrity guard for the leaderboard's selectivity headline (WP-7,
// docs/sprint/a6-rerun.md's rejection-rate item): "of N candidates evaluated,
// only K clear the rigor bar" must be DERIVED at render time from the same
// leaderboard payload the page already fetches (data.entries), never a
// hard-coded literal. CLAUDE.md's corollary is explicit: "Don't quote a
// curated-library strategy pass count — anywhere." A hard-coded "3 of 34" on
// this flagship public page is exactly the defect that corollary exists to
// prevent.
//
// Same shape as roadmap-copy.test.js / signin-claims.test.js: a raw
// source-text scan (readFileSync, no JSX parsing) plus anti-vacuity coverage
// (the pattern must reject its own canonical bad example) and a positive
// check that the derived-count code path actually exists — so this guard
// cannot pass merely because the feature was deleted rather than kept
// honest.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test } from 'node:test'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')
const LEADERBOARD_FILE = 'components/Leaderboard.jsx'

// Matches any "<digits> of <digits>" shape — the literal-count smell this
// guard exists to catch (e.g. "3 of 34", "34 of 34"). Deliberately generic
// (not anchored to "34" specifically) so it also catches a future literal
// with different numbers.
const HARD_CODED_PASS_COUNT = /\b\d+\s+of\s+\d+\b/

function readLeaderboardSource() {
  return readFileSync(join(SRC, LEADERBOARD_FILE), 'utf8')
}

test('Leaderboard.jsx exists (scan cannot silently shrink)', () => {
  assert.doesNotThrow(
    readLeaderboardSource,
    `${LEADERBOARD_FILE} is missing or moved — update this guard, do not let the scan silently shrink`,
  )
})

test('HARD_CODED_PASS_COUNT pattern rejects its own canonical bad example (guard is not vacuous)', () => {
  assert.match(
    '3 of 34',
    HARD_CODED_PASS_COUNT,
    'HARD_CODED_PASS_COUNT no longer matches "3 of 34" — it is guarding nothing',
  )
  assert.match(
    '34 of 34',
    HARD_CODED_PASS_COUNT,
    'HARD_CODED_PASS_COUNT no longer matches "34 of 34" — it is guarding nothing',
  )
})

test('Leaderboard.jsx derives the selectivity headline from fetched data, not a literal count', () => {
  const source = readLeaderboardSource()

  // Positive: the derived-count code path exists, and it reads from
  // data.entries — the same array the results table renders — rather than
  // from a separate or fabricated source.
  assert.match(
    source,
    /data\.entries/,
    'expected Leaderboard.jsx to read the selectivity counts from data.entries (the live leaderboard payload)',
  )
  assert.match(
    source,
    /passes_rigor_gate/,
    'expected Leaderboard.jsx to derive the passing count from entries[].passes_rigor_gate',
  )
  assert.match(
    source,
    /evaluatedCount/,
    'expected a derived evaluatedCount value backing the headline claim',
  )
  assert.match(
    source,
    /passingCount/,
    'expected a derived passingCount value backing the headline claim',
  )

  // Negative: no literal "<digits> of <digits>" anywhere in the file — see
  // CLAUDE.md: "Don't quote a curated-library strategy pass count —
  // anywhere." Mutation-check performed by hand while authoring this guard:
  // temporarily inserting the literal string "3 of 34" into Leaderboard.jsx
  // makes this assertion fail; removing it restores green (see PR body for
  // the before/after run).
  assert.doesNotMatch(
    source,
    HARD_CODED_PASS_COUNT,
    'Leaderboard.jsx contains a literal "<N> of <M>" pass-count — the selectivity headline must be ' +
      'derived from data.entries at render time, never hard-coded (CLAUDE.md: "Don\'t quote a ' +
      'curated-library strategy pass count — anywhere")',
  )
})

test('the selectivity headline never claims counts before data arrives (no "0 of 0")', () => {
  const source = readLeaderboardSource()
  // The headline block must be gated on `data` truthiness and a positive
  // evaluatedCount, mirroring the guard already used for the results table's
  // non-empty state — this is what keeps loading/error/degraded/empty states
  // from ever rendering a claim.
  assert.match(
    source,
    /evaluatedCount > 0/,
    'expected the selectivity headline to be gated on evaluatedCount > 0 so it never renders "0 of 0"',
  )
})
