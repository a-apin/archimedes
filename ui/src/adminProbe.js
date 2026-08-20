// Admin-gate probe — the frontend half of the server-truth gate on
// /app/insights (owner directive 2026-08-20, supersedes issue #1028 D8
// "public Insights page"). GET /api/metrics/private/whoami is the ONLY
// thing that decides "is this visitor an admin" — this module never guesses
// from local state (a linked-wallet address, a signed-in user id) because
// none of those imply PLATFORM_ADMIN_WALLETS membership, which only the
// server can check.
//
// Two independent mount points share this probe within one TTL window
// (Layout.jsx's Ops nav item, Insights.jsx's page gate) via
// ./adminProbeCache.js — see that file for why.
import { apiGet } from "./api";
import { getCachedAdminProbe, _resetAdminProbeCache } from "./adminProbeCache.js";

async function _fetchWhoami() {
	try {
		const body = await apiGet("/api/metrics/private/whoami");
		return { admin: body?.admin === true, wallet: body?.wallet ?? null };
	} catch (err) {
		// An AUTHORITATIVE answer from the server (401 anonymous / 403
		// verified-non-admin) resolves to a real, cacheable "not admin" —
		// apiGet sets `.status` on every non-2xx HTTP response, so this
		// branch only fires once a response was actually received.
		if (err && typeof err.status === "number") {
			return { admin: false, wallet: null };
		}
		// A genuine network/parse failure (no `.status`) is NOT an answer —
		// rethrow so adminProbeCache does not cache it, and the next probe
		// gets a fresh attempt rather than being stuck "not admin" for the
		// rest of the TTL window on a transient blip.
		throw err;
	}
}

/**
 * Resolves to `{admin: boolean, wallet: string | null}`. Never rejects for
 * an anonymous/non-admin caller — that resolves to `{admin: false, wallet:
 * null}`, same as a genuine network failure the caller should also treat as
 * "not admin" (fail closed, matching the not-found treatment this gates).
 */
export function fetchAdminProbe() {
	return getCachedAdminProbe(_fetchWhoami).catch(() => ({ admin: false, wallet: null }));
}

export { _resetAdminProbeCache };
