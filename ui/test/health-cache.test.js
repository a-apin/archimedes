import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { _resetHealthCache, getCachedHealth, HEALTH_TTL_MS } from '../src/healthCache.js'

// ── getCachedHealth: shared TTL cache backing fetchHealth() (#1368 review) ──
// The bug: CorpusKG.jsx fetched /health directly on its own effect
// (`apiGet('/health')`), uncached — on top of whatever else on the page
// already reads /health in the same render pass. /health is rate-limit-exempt
// but not free — it does an Arc RPC round-trip (`chain_client.is_connected()`)
// plus several DB reads (`backend/archimedes/main.py`). getCachedHealth()
// (../src/healthCache.js, zero imports so it's exercised directly here) plus
// its thin production wrapper fetchHealth() (../src/health.js) fix that by
// sharing one in-flight/recent response across callers within the TTL.

test('getCachedHealth: a second call within the TTL window reuses the first call\'s promise instead of fetching again', async () => {
  _resetHealthCache()
  let calls = 0
  const fetcher = async () => {
    calls += 1
    return { chain_connected: true }
  }
  const p1 = getCachedHealth(fetcher, 1_000)
  const p2 = getCachedHealth(fetcher, 1_000 + HEALTH_TTL_MS - 1)
  assert.equal(p1, p2, 'the second call should return the exact same promise, not a new fetch')
  await p1
  assert.equal(calls, 1, 'fetcher should have been invoked exactly once')
})

test('getCachedHealth: a call at/after the TTL window fetches again', async () => {
  _resetHealthCache()
  let calls = 0
  const fetcher = async () => {
    calls += 1
    return { chain_connected: true }
  }
  await getCachedHealth(fetcher, 1_000)
  await getCachedHealth(fetcher, 1_000 + HEALTH_TTL_MS)
  assert.equal(calls, 2, 'the TTL boundary must not extend the cache indefinitely')
})

test('getCachedHealth: a failed fetch is not cached — the very next call retries rather than reusing the rejection', async () => {
  // Mutation-check target: if the .catch() didn't clear cachedPromise/
  // cachedAt, this second call would return the same rejected promise and
  // `calls` would stay at 1 — an outage would pin every caller on the same
  // failure for the rest of the TTL window instead of getting a fresh retry.
  _resetHealthCache()
  let calls = 0
  const failingFetcher = async () => {
    calls += 1
    throw new Error('network down')
  }
  await assert.rejects(getCachedHealth(failingFetcher, 1_000))
  const okFetcher = async () => {
    calls += 1
    return { chain_connected: true }
  }
  await getCachedHealth(okFetcher, 1_001)
  assert.equal(calls, 2, 'a call right after a failure should retry, not reuse the rejected promise')
})

// ── health.js: the thin production wrapper ──────────────────────────────

const health = readFileSync(new URL('../src/health.js', import.meta.url), 'utf8')

test('health.js: fetchHealth() delegates to the shared getCachedHealth cache with the real apiGet, not its own logic', () => {
  assert.match(health, /from ["']\.\/api["']/)
  assert.match(health, /from ["']\.\/healthCache\.js["']/)
  assert.match(health, /getCachedHealth\(apiGet\)/)
})

// ── CorpusKG.jsx: must read /health through the shared cache (#1368 review) ─
//
// An adversarial review of the first version of this fix found CorpusKG.jsx
// calling `apiGet('/health')` directly — the exact duplicate-fetch shape
// this cache exists to prevent, reintroduced on the one public corpus
// surface this PR touches. Layout.jsx, Architecture.jsx, and
// ModelCostPanel.jsx are NOT asserted here: on this branch they still call
// apiGet('/health') directly too (that refactor landed on `main` as a
// separate, later change this branch predates) — asserting against files
// this PR doesn't touch would fail for a reason unrelated to this fix.
// Reconciling all four callers under one guard is follow-up work for
// whichever side merges second.

const corpusKg = readFileSync(new URL('../src/components/CorpusKG.jsx', import.meta.url), 'utf8')

test('CorpusKG.jsx: /health is read through the shared fetchHealth cache, not a direct apiGet("/health") call', () => {
  assert.doesNotMatch(
    corpusKg,
    /apiGet\(["']\/health["']\)/,
    'CorpusKG.jsx calls apiGet("/health") directly instead of the shared fetchHealth() — this reintroduces a duplicate /health fetch',
  )
  assert.match(corpusKg, /from ["']\.\.\/health["']/, 'CorpusKG.jsx does not import from ../health')
  assert.match(corpusKg, /fetchHealth\(\)/, 'CorpusKG.jsx does not call the shared fetchHealth()')
})
