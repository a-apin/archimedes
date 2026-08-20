// Tri-state chain-status derivation for the app-shell footer pill (#1321).
//
// Previously the pill's label was `Object.keys(NEW_CONTRACTS).length ? "Arc
// · Testnet live" : "Arc · Connecting"` — NEW_CONTRACTS is a static object
// literal (ui/src/config.js) with seven hard-coded addresses, so that count
// is the constant 7 and the "problem" branch was unreachable dead code. The
// pill said "live" through the 2026-08-19/20 backend OOM crash loop while
// the site was serving 502/503.
//
// This module derives the label from the REAL `/health` response's
// `chain_connected` boolean instead (backend/archimedes/main.py), with an
// explicit third "unknown" state for the loading window and for a failed
// `/health` fetch. Per docs/architectural-principles.md § fail-soft: the
// correct degraded state is a loud, visible "unknown" — never a plausible
// substitute rendered as healthy.

export const CHAIN_STATUS = {
	CONNECTED: "connected",
	DISCONNECTED: "disconnected",
	UNKNOWN: "unknown",
};

const LABEL = {
	[CHAIN_STATUS.CONNECTED]: "Arc · Testnet live",
	[CHAIN_STATUS.DISCONNECTED]: "Arc · Chain disconnected",
	[CHAIN_STATUS.UNKNOWN]: "Arc · Status unknown",
};

/**
 * Derive the tri-state chain-connection status shown in the shell footer.
 *
 * @param {{chain_connected?: unknown} | null | undefined} health - parsed
 *   `/health` body, or null/undefined before the fetch resolves.
 * @param {boolean} healthError - true if the `/health` fetch itself failed
 *   (network error or non-2xx status).
 * @returns {{tone: "connected"|"disconnected"|"unknown", label: string}}
 */
export function deriveChainStatus(health, healthError) {
	if (healthError || !health || typeof health.chain_connected !== "boolean") {
		return { tone: CHAIN_STATUS.UNKNOWN, label: LABEL[CHAIN_STATUS.UNKNOWN] };
	}
	const tone = health.chain_connected
		? CHAIN_STATUS.CONNECTED
		: CHAIN_STATUS.DISCONNECTED;
	return { tone, label: LABEL[tone] };
}
