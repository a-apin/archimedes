// Pure TTL-cache primitive backing fetchHealth() (./health.js). Deliberately
// zero imports so it can be unit-tested directly with node:test. health.js's
// own import graph (api.js → config.js → circle-wallet.js → wallet/passkey
// browser SDKs) is browser-oriented and not safe to load under a bare
// `node --test` run, so the cache logic lives here, decoupled from that
// chain, and health.js is a thin wrapper around it.
//
// The bug this exists to fix (#1368 adversarial review, on this component):
// CorpusKG.jsx fetching /health directly on its own effect, uncached, on top
// of whatever else on the page already reads it in the same render pass.
// /health is rate-limit-exempt but not free — it does an Arc RPC round-trip
// (`chain_client.is_connected()`) plus several DB reads
// (`backend/archimedes/main.py`). Main's own #1333 line hit the identical
// problem independently for Layout.jsx/Architecture.jsx/ModelCostPanel.jsx
// and landed this same fix shape there; this file matches that design so the
// two lines merge cleanly rather than reinventing an incompatible cache.
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
