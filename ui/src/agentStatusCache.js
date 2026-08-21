// Pure TTL-cache primitive backing fetchAgentStatus() (./agentStatus.js,
// PR #1382 review follow-up — same shape as ./healthCache.js, #1333). See
// that file's comment for the full "why a separate module" rationale
// (`node --test` can't safely load api.js's browser-oriented import graph);
// this is the same split for a second endpoint.
//
// The bug this exists to fix: Architecture.jsx called
// `apiGet("/api/agent/status")` directly on its own mount effect, uncached.
// The endpoint is unauthenticated, carries no `@limiter.limit` (so it falls
// under the 60/minute default), and does four Redis reads
// (`get_heartbeat` / `load_regime` / `load_ensemble_consensus` /
// `get_events`) plus an Arc RPC round-trip (`chain_executor.get_all_vaults()`
// → `factory.functions.getVaults().call()`) per call
// (`backend/archimedes/api/agent_routes.py`). Every nav onto /architecture
// re-ran all of that from scratch.
//
// getCachedAgentStatus() mirrors getCachedHealth(): a call within
// AGENT_STATUS_TTL_MS of the last *resolved* fetch returns that same
// promise instead of invoking `fetcher` again. A failed fetch is not
// cached — cachedAt/cachedPromise are cleared immediately on rejection, so
// the next call gets a fresh retry rather than being stuck reusing a
// rejected promise for the rest of the TTL window.

export const AGENT_STATUS_TTL_MS = 30_000;

let cachedPromise = null;
let cachedAt = 0;

/**
 * @param {(path: string) => Promise<any>} fetcher — performs the actual
 *   `/api/agent/status` request; injected so this stays a pure,
 *   dependency-free module (production code passes the real `apiGet`,
 *   tests pass a fake).
 * @param {number} [now] — injectable clock for tests; defaults to
 *   `Date.now()`.
 * @returns {Promise<any>} the `/api/agent/status` response, shared across
 *   calls within the TTL window.
 */
export function getCachedAgentStatus(fetcher, now = Date.now()) {
	if (cachedPromise && now - cachedAt < AGENT_STATUS_TTL_MS) {
		return cachedPromise;
	}
	cachedAt = now;
	cachedPromise = fetcher("/api/agent/status").catch((err) => {
		cachedAt = 0;
		cachedPromise = null;
		throw err;
	});
	return cachedPromise;
}

// Test-only: force the next getCachedAgentStatus() call to bypass the cache.
export function _resetAgentStatusCache() {
	cachedPromise = null;
	cachedAt = 0;
}
