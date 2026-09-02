/**
 * Verification-email delivery feedback — display logic for the resend control
 * (#1748 item 2).
 *
 * THE DEFECT THIS REPLACES. `POST /api/auth/send-verification-email` answers
 * `200 {status:true}` forever: for an address Amazon SES has already dropped
 * onto the account suppression list (it accepts the send, returns a MessageId,
 * and bins the mail), for an address whose last send threw, for every address.
 * The UI's only honest response to that was `VERIFICATION_REQUESTED_MESSAGE` —
 * "requested, delivery isn't confirmed" — which stays true forever and
 * therefore never becomes useful. `GET /api/auth/verification-status`
 * (auth/verification-status.js) now answers with what the auth service
 * actually recorded, and this module turns that into what a human reads.
 *
 * Pure and DOM-free so it is unit-testable under `node --test` — the same
 * split `freeGenerations.js` makes for the free-tier banner. The component
 * that renders it is `components/VerificationDeliveryStatus.jsx`; everything
 * that DECIDES what to say lives here.
 *
 * The rule that governs every string below: **never claim delivery.** A
 * recorded `sent` means our mail provider ACCEPTED the message, which is not
 * the same fact, and the copy says so. The one state that is genuinely
 * actionable — `suppressed` — is the only one that tells the user to stop
 * pressing the button.
 */

/** The endpoint that owns these states. Session-required; reports the caller's own address. */
export const VERIFICATION_STATUS_ENDPOINT = "/api/auth/verification-status";

/**
 * The six states `resolveVerificationStatus` can return. Matched exactly,
 * never truthy-tested: a state this build has never heard of (a future one,
 * deployed server-side ahead of the UI) renders NOTHING rather than being
 * mapped onto the nearest familiar message, which would be an invented claim
 * about someone's mail. Same rule `freeGenerations.js` applies to an unknown
 * `free_generations_locked_reason`.
 */
export const DELIVERY_STATES = Object.freeze({
	VERIFIED: "verified",
	SUPPRESSED: "suppressed",
	FAILED: "failed",
	RATE_LIMITED: "rate_limited",
	SENT: "sent",
	UNKNOWN: "unknown",
});

/**
 * What the client itself knows when the resend POST comes back 429.
 *
 * Better Auth's limiter is keyed per client IP, not per address, so a refusal
 * can arrive while our own delivery log shows room — the client's 429 is a
 * fact the server-side status cannot always see. Feed this to
 * `deriveVerificationDeliveryView` to render the same rate-limited state
 * without a seconds count we do not have.
 */
export const RATE_LIMITED_BY_CLIENT = Object.freeze({
	state: DELIVERY_STATES.RATE_LIMITED,
	retryAfterSeconds: null,
});

function plural(count, noun) {
	return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

/**
 * Turn a `GET /api/auth/verification-status` body into what the UI shows.
 *
 * Returns `null` for "render nothing": no response yet, a malformed one, a
 * state this build does not recognise, or a verified account (whose surface
 * already says "Email verified ✓" and has no pending delivery to describe).
 *
 * @param {object|null|undefined} status — parsed /api/auth/verification-status body
 * @returns {{state: string, tone: string, message: string, canResend: boolean,
 *            retryAfterSeconds: number|null}|null}
 */
export function deriveVerificationDeliveryView(status) {
	if (!status || typeof status !== "object") return null;
	const state = status.state;
	const retryAfterSeconds =
		typeof status.retryAfterSeconds === "number" && status.retryAfterSeconds > 0
			? Math.ceil(status.retryAfterSeconds)
			: null;

	if (state === DELIVERY_STATES.VERIFIED) return null;

	if (state === DELIVERY_STATES.SUPPRESSED) {
		// The only state where pressing the button again cannot work, so it is
		// the only one that takes the button away. `reason` is SES's own
		// vocabulary (BOUNCE / COMPLAINT); an unrecognised value is passed
		// through rather than translated into a guess.
		const because =
			status.suppression?.reason === "COMPLAINT"
				? "a spam complaint was recorded for it"
				: "mail to it bounced";
		return {
			state,
			tone: "blocked",
			message:
				`Our mail provider is refusing to send to this address — ${because}, ` +
				"so it is on the account suppression list and further attempts are dropped without reaching you. " +
				"Resending will not help. Use a different email address, or contact the team to have the block reviewed.",
			canResend: false,
			retryAfterSeconds: null,
		};
	}

	if (state === DELIVERY_STATES.FAILED) {
		// The send never left the building. Distinct from suppressed (which is
		// permanent for this address) and from sent (which at least got as far
		// as the provider).
		const code = typeof status.lastError === "string" && status.lastError ? ` (${status.lastError})` : "";
		return {
			state,
			tone: "error",
			message:
				`The last attempt to send a verification email was refused by our mail provider${code}. ` +
				"Nothing went out. Try again in a moment — if it keeps failing, contact the team.",
			canResend: retryAfterSeconds === null,
			retryAfterSeconds,
		};
	}

	if (state === DELIVERY_STATES.RATE_LIMITED) {
		return {
			state,
			tone: "waiting",
			message: retryAfterSeconds
				? `Too many verification emails requested for this address. Try again in ${plural(retryAfterSeconds, "second")}.`
				: "Too many verification emails requested. Wait about a minute, then try again.",
			canResend: false,
			retryAfterSeconds,
		};
	}

	if (state === DELIVERY_STATES.SENT) {
		// "Accepted by our mail provider" is the strongest TRUE statement
		// available: SES returns a MessageId for a suppressed address too, so
		// an id is acceptance, never delivery.
		const spam = status.checkSpam
			? ` ${plural(typeof status.sends === "number" ? status.sends : 2, "request")} in the last 24 hours have gone out — look in your spam or junk folder before requesting another.`
			: "";
		return {
			state,
			tone: "ok",
			message:
				"Our mail provider accepted the last verification email for this address. " +
				`That is acceptance, not proof it reached you — it can take a few minutes.${spam}`,
			canResend: retryAfterSeconds === null,
			retryAfterSeconds,
		};
	}

	if (state === DELIVERY_STATES.UNKNOWN) {
		// `sends: null` (the log could not be read) and `sends: 0` (nothing was
		// ever sent) are different facts and the server keeps them apart, so
		// this does too. Neither may be rendered as "it was sent".
		const unreadable = status.sends === null || status.sends === undefined;
		return {
			state,
			tone: "unknown",
			message: unreadable
				? "We cannot read this account's email delivery history right now, so we cannot say whether a verification email went out."
				: "No verification email has been recorded for this address yet. Request one below.",
			canResend: retryAfterSeconds === null,
			retryAfterSeconds,
		};
	}

	return null;
}
