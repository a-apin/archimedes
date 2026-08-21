// Pure TTL-cache primitive backing fetchHealth() (./health.js, #1333 review
// follow-up). Deliberately zero imports — like ./chainStatus.js — so it can
// be unit-tested directly with node:test. health.js's own import graph
// (api.js → config.js → circle-wallet.js → wallet/passkey browser SDKs) is
// browser-oriented and not safe to load under a bare `node --test` run, so
// the cache logic lives here, decoupled from that chain, and health.js is a
// thin wrapper around it.
//
// The bug this exists to fix: Layout.jsx (footer chain-status pill, #1321),
// Architecture.jsx, and ModelCostPanel.jsx each called `apiGet("/health")`
// independently, on their own effect. #1321's anti-goal said "/health is
// already fetched by the shell; reuse that response" — but no shell-level
// fetch existed for them to reuse, so once Layout started re-fetching on
// every in-app navigation, landing on /architecture fired /health twice in
// the same render pass: Layout's per-navigation re-fetch plus Architecture's
// own mount fetch. /health is rate-limit-exempt but not free — it does an
// Arc RPC round-trip (`chain_client.is_connected()`) plus several DB reads
// (`backend/archimedes/main.py`).
//
// getCachedHealth() makes the "reuse that response" anti-goal real: a call
// within HEALTH_TTL_MS of the last *resolved* fetch returns that same
// promise instead of invoking `fetcher` again. A failed fetch is not
// cached — cachedAt/cachedPromise are cleared immediately on rejection, so
// the very next call (e.g. Layout's next navigation) gets a fresh retry
// rather than being stuck reusing a rejected promise for the rest of the
// TTL window, which would otherwise pin the chain-status pill on "unknown"
// for up to HEALTH_TTL_MS after a real recovery.

export const HEALTH_TTL_MS = 30_000;

let cachedPromise = null;
let cachedAt = 0;

/**
 * @param {(path: string) => Promise<any>} fetcher — performs the actual
 *   `/health` request; injected so this stays a pure, dependency-free
 *   module (production code passes the real `apiGet`, tests pass a fake).
 * @param {number} [now] — injectable clock for tests; defaults to
 *   `Date.now()`.
 * @returns {Promise<any>} the `/health` response, shared across callers
 *   within the TTL window.
 */
export function getCachedHealth(fetcher, now = Date.now()) {
	if (cachedPromise && now - cachedAt < HEALTH_TTL_MS) {
		return cachedPromise;
	}
	cachedAt = now;
	cachedPromise = fetcher("/health").catch((err) => {
		cachedAt = 0;
		cachedPromise = null;
		throw err;
	});
	return cachedPromise;
}

// Test-only: force the next getCachedHealth() call to bypass the cache.
export function _resetHealthCache() {
	cachedPromise = null;
	cachedAt = 0;
}
