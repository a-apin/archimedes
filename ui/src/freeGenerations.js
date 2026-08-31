/**
 * Free-generation allowance — display logic for the /generate banner (#1643).
 *
 * Pure and DOM-free so it is unit-testable under `node --test` (the same split
 * `generateQuote.js` makes for the x402 state machine). The component that
 * renders it is `components/FreeGenerationBanner.jsx`; everything that decides
 * WHETHER and WHAT to show lives here.
 *
 * The policy this describes: an account is required for every generation, but
 * a wallet is not, for the first `free_generations_allowance` (default 3) runs
 * on that account. After that the existing wallet gate (409) and paywall (402)
 * apply unchanged. The backend is the sole authority — this module never
 * counts generations itself, it only renders what `GET /api/account/usage`
 * reports.
 *
 * The one rule worth stating twice: **a number is shown only when the backend
 * sent one.** `free_generations_remaining` is `null` when the ledger could not
 * be read, and the honest render for that is nothing at all. Substituting a
 * `0` would tell a brand-new account it is locked out; substituting the
 * allowance would promise free runs the gate may refuse. Both are claims the
 * product does not keep.
 */

/** The endpoint that owns this number — the same one the gate claims from. */
export const ACCOUNT_USAGE_ENDPOINT = "/api/account/usage";

function isCount(value) {
	return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

/**
 * Turn a `GET /api/account/usage` body into a banner view, or `null`.
 *
 * `null` means "render nothing", and covers every case where showing a number
 * would be dishonest or meaningless:
 *   - no response yet, or a failed/unauthenticated request;
 *   - `free_generations_remaining` absent or null (ledger unreadable);
 *   - a non-integer or negative count from an unexpected payload;
 *   - the free path switched off entirely (`allowance <= 0`), where a
 *     "0 free generations left" chip would imply a policy that is not running.
 *
 * @param {object|null|undefined} usage — parsed /api/account/usage body
 * @returns {{remaining: number, allowance: number, exhausted: boolean,
 *            chipLabel: string, message: string}|null}
 */
export function deriveFreeGenerationView(usage) {
	if (!usage || typeof usage !== "object") return null;

	const allowance = usage.free_generations_allowance;
	const remaining = usage.free_generations_remaining;
	if (!isCount(allowance) || allowance <= 0) return null;
	if (!isCount(remaining)) return null;

	const shown = Math.min(remaining, allowance);
	const exhausted = shown === 0;
	const plural = shown === 1 ? "generation" : "generations";

	return {
		remaining: shown,
		allowance,
		exhausted,
		chipLabel: exhausted ? "Free generations used" : `${shown} free ${plural} left`,
		message: exhausted
			? `You have used all ${allowance} free generations on this account. ` +
				"Link a wallet to keep generating — see the price below."
			: `Your account has ${shown} of ${allowance} free ${plural} left. ` +
				"No wallet needed until they run out.",
	};
}
