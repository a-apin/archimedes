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
 * on that account — once the account's email is VERIFIED (owner decision D1,
 * 2026-08-31). After that the existing wallet gate (409) and paywall (402)
 * apply unchanged. The backend is the sole authority — this module never
 * counts generations itself, and never decides whether the account is
 * verified; it only renders what `GET /api/account/usage` reports.
 *
 * That gives three states worth showing and one worth hiding:
 *   - AVAILABLE  — verified, slots left: "2 free generations left".
 *   - LOCKED     — `free_generations_locked_reason: "email_unverified"` with
 *                  slots left: the carrot. Silence here would be the worst
 *                  render of the three — a new account would conclude the free
 *                  tier is a fiction and bounce at the wallet gate, when the
 *                  unlock is an inbox it already owns.
 *   - EXHAUSTED  — no slots left. Locked or not, verification unlocks nothing
 *                  once the ledger is spent, so it gets the wallet message.
 *   - (nothing)  — no honest number to show; see below.
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

/**
 * The one `free_generations_locked_reason` this module knows how to explain —
 * it must match `services/free_generations.LOCK_EMAIL_UNVERIFIED`.
 *
 * Matched exactly, never truthy-tested: a lock reason this build has never
 * heard of (a future one, added server-side and deployed ahead of the UI) is
 * still a lock, so the count must not be offered as available — but this
 * module has no idea what would unlock it, and "verify your email" would then
 * be a fabricated instruction. Unknown reason ⇒ render nothing.
 */
export const LOCK_EMAIL_UNVERIFIED = "email_unverified";

function isCount(value) {
	return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

/**
 * Turn a `GET /api/account/usage` body into a banner view, or `null`.
 *
 * `null` means "render nothing", and covers every case where showing a number
 * would be dishonest or meaningless:
 *   - no response yet, or a failed/unauthenticated request;
 *   - `free_generations_remaining` absent or null (ledger unreadable) —
 *     including when the account is ALSO locked. An unreadable ledger and a
 *     locked account are not the same fact and must not be conflated: we do
 *     not know how many slots verification would unlock, so we do not offer a
 *     number, and a carrot with no number is a promise we cannot size;
 *   - a non-integer or negative count from an unexpected payload;
 *   - a `free_generations_locked_reason` this build does not recognise;
 *   - the free path switched off entirely (`allowance <= 0`), where a
 *     "0 free generations left" chip would imply a policy that is not running.
 *
 * @param {object|null|undefined} usage — parsed /api/account/usage body
 * @returns {{remaining: number, allowance: number, exhausted: boolean,
 *            locked: boolean, lockedReason: string|null, state: string,
 *            chipLabel: string, message: string}|null}
 */
export function deriveFreeGenerationView(usage) {
	if (!usage || typeof usage !== "object") return null;

	const allowance = usage.free_generations_allowance;
	const remaining = usage.free_generations_remaining;
	if (!isCount(allowance) || allowance <= 0) return null;
	// Deliberately BEFORE the lock branch: an unreadable ledger renders nothing
	// whether or not the account is locked. (The backend sends `null` only for
	// an unreadable ledger; a lock never produces one.)
	if (!isCount(remaining)) return null;

	const rawLock = usage.free_generations_locked_reason;
	// An older backend omits the field entirely — that is "not locked", not an
	// error, so the pre-D1 payload keeps rendering the plain available state.
	const lockedReason = typeof rawLock === "string" && rawLock ? rawLock : null;
	if (lockedReason !== null && lockedReason !== LOCK_EMAIL_UNVERIFIED) return null;

	const shown = Math.min(remaining, allowance);
	const exhausted = shown === 0;
	const plural = shown === 1 ? "generation" : "generations";
	const locked = lockedReason !== null;

	// Exhausted wins over locked, and that ordering is the honest one: once the
	// ledger is spent, verifying an email unlocks nothing, so offering it as
	// the way forward would send the user to a dead end. This is reachable —
	// an account that spent its slots before this gate shipped is exhausted AND
	// unverified.
	if (locked && !exhausted) {
		return {
			remaining: shown,
			allowance,
			exhausted: false,
			locked: true,
			lockedReason,
			state: "locked",
			chipLabel: `${shown} free ${plural} locked`,
			message:
				`Verify your email to unlock ${shown} free ${plural} on this account — ` +
				"no wallet and no payment needed. Check your inbox for the link we sent when you signed up.",
		};
	}

	return {
		remaining: shown,
		allowance,
		exhausted,
		locked,
		lockedReason,
		state: exhausted ? "exhausted" : "available",
		chipLabel: exhausted ? "Free generations used" : `${shown} free ${plural} left`,
		message: exhausted
			? `You have used all ${allowance} free generations on this account. ` +
				"Link a wallet to keep generating — see the price below."
			: `Your account has ${shown} of ${allowance} free ${plural} left. ` +
				"No wallet needed until they run out.",
	};
}
