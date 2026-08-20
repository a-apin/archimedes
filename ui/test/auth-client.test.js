import assert from "node:assert/strict";
import test from "node:test";

import {
	requestPasswordReset,
	resendVerificationEmail,
	resetPassword,
} from "../src/auth-client.js";

// ── #1323: password reset + resend-verification wiring ──────────────────
// authRequest() itself (session/sign-in/sign-up/sign-out) is exercised
// end-to-end via the live auth service elsewhere; these three are new and
// get direct fetch-boundary coverage here (mock at the boundary — fetch —
// not at authRequest's internals, per CLAUDE.md's testing conventions).

function jsonResponse(status, body) {
	return new Response(JSON.stringify(body), {
		status,
		headers: { "Content-Type": "application/json" },
	});
}

function mockFetch(t, handler) {
	t.mock.method(globalThis, "fetch", handler);
}

test("requestPasswordReset POSTs email + redirectTo to /api/auth/request-password-reset, credentials included", async (t) => {
	let seen;
	mockFetch(t, async (url, options) => {
		seen = { url, method: options.method, credentials: options.credentials, body: JSON.parse(options.body) };
		return jsonResponse(200, { status: true, message: "If this email exists in our system, check your email for the reset link" });
	});

	const result = await requestPasswordReset("a@example.com", "https://app.test/reset-password");

	assert.equal(seen.url, "/api/auth/request-password-reset");
	assert.equal(seen.method, "POST");
	assert.equal(seen.credentials, "include");
	assert.deepEqual(seen.body, { email: "a@example.com", redirectTo: "https://app.test/reset-password" });
	assert.equal(result.status, true);
});

test("resetPassword POSTs newPassword + token to /api/auth/reset-password", async (t) => {
	let seen;
	mockFetch(t, async (url, options) => {
		seen = { url, body: JSON.parse(options.body) };
		return jsonResponse(200, { status: true });
	});

	await resetPassword("a new correct horse battery", "the-reset-token");

	assert.equal(seen.url, "/api/auth/reset-password");
	assert.deepEqual(seen.body, { newPassword: "a new correct horse battery", token: "the-reset-token" });
});

test("resendVerificationEmail POSTs email + callbackURL to /api/auth/send-verification-email", async (t) => {
	let seen;
	mockFetch(t, async (url, options) => {
		seen = { url, body: JSON.parse(options.body) };
		return jsonResponse(200, { status: true });
	});

	await resendVerificationEmail("a@example.com", "https://app.test/app");

	assert.equal(seen.url, "/api/auth/send-verification-email");
	assert.deepEqual(seen.body, { email: "a@example.com", callbackURL: "https://app.test/app" });
});

test("a non-ok response throws using the server's message, for all three", async (t) => {
	mockFetch(t, async () => jsonResponse(400, { message: "Reset password isn't enabled" }));

	await assert.rejects(() => requestPasswordReset("a@example.com"), /Reset password isn't enabled/);
	await assert.rejects(() => resetPassword("x", "tok"), /Reset password isn't enabled/);
	await assert.rejects(() => resendVerificationEmail("a@example.com"), /Reset password isn't enabled/);
});
