import { useState } from "react";
import { resendVerificationEmail } from "../auth-client";
import { VERIFICATION_REQUESTED_MESSAGE } from "../freeGenerations";

// On-demand verification resend for the Generate page (locked free-tier
// banner + the 409 wallet-gate panel). Account Settings already has this
// button; #1658 deferred the Generate-page copy to the #1642 redesign,
// which landed without it. One control, two mount points, so the two
// surfaces cannot drift on honesty (queued ≠ delivered).
//
// Renders nothing when there is no email to send to — a button that POSTs
// an empty address would be a claim this control cannot keep.
export default function ResendVerificationControl({ email }) {
	const [status, setStatus] = useState("idle");
	const [error, setError] = useState("");

	if (!email) return null;

	const send = async () => {
		setStatus("sending");
		setError("");
		try {
			await resendVerificationEmail(email, `${window.location.origin}/app`);
			setStatus("sent");
		} catch (err) {
			setError(err?.message || "Could not request a verification email.");
			setStatus("error");
		}
	};

	return (
		<div className="generate-resend" style={{ marginTop: 8 }}>
			<button
				type="button"
				className="btn btn-outline btn-sm"
				onClick={send}
				disabled={status === "sending"}
			>
				{status === "sending" ? "Sending…" : "Resend verification email"}
			</button>
			{status === "sent" && (
				<p className="caption mb-0" role="status" style={{ marginTop: 6 }}>
					{VERIFICATION_REQUESTED_MESSAGE}
				</p>
			)}
			{status === "error" && (
				<p className="caption mb-0" role="alert" style={{ marginTop: 6 }}>
					{error}
				</p>
			)}
		</div>
	);
}
