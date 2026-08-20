import assert from "node:assert/strict";
import test from "node:test";

import {
	linkSocial,
	listAccounts,
	requestPasswordReset,
	resendVerificationEmail,
	resetPassword,
	unlinkAccount,
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

// ── #1420 follow-up: explicit account linking (Account Settings → Connected
// accounts) ────────────────────────────────────────────────────────────
// listAccounts/linkSocial/unlinkAccount all call Better Auth's own
// /list-accounts, /link-social, /unlink-account endpoints — mocked here at
// the fetch boundary, same as the pre-existing tests above.

function withStubWindow(t, assigned) {
	const original = globalThis.window;
	globalThis.window = { location: { assign: (url) => assigned.push(url) } };
	t.after(() => {
		globalThis.window = original;
	});
}

test("listAccounts GETs /api/auth/list-accounts with credentials included", async (t) => {
	let seen;
	mockFetch(t, async (url, options) => {
		seen = { url, method: options.method, credentials: options.credentials };
		return jsonResponse(200, [{ id: "1", providerId: "credential", accountId: "1" }]);
	});

	const result = await listAccounts();

	assert.equal(seen.url, "/api/auth/list-accounts");
	assert.equal(seen.credentials, "include");
	assert.deepEqual(result, [{ id: "1", providerId: "credential", accountId: "1" }]);
});

test("linkSocial POSTs provider + callbackURL + errorCallbackURL to /api/auth/link-social, then navigates to the returned url", async (t) => {
	let seen;
	mockFetch(t, async (url, options) => {
		seen = { url, method: options.method, body: JSON.parse(options.body) };
		return jsonResponse(200, { url: "https://accounts.google.com/o/oauth2/v2/auth?state=abc", redirect: false, status: true });
	});
	const assigned = [];
	withStubWindow(t, assigned);

	await linkSocial("google", "https://app.test/app/account", "https://app.test/app/account");

	assert.equal(seen.url, "/api/auth/link-social");
	assert.equal(seen.method, "POST");
	assert.deepEqual(seen.body, {
		provider: "google",
		callbackURL: "https://app.test/app/account",
		errorCallbackURL: "https://app.test/app/account",
		disableRedirect: true,
	});
	// The whole point of the redirect-based flow: the browser actually
	// navigates to the provider's authorize URL, not an SPA transition.
	assert.deepEqual(assigned, ["https://accounts.google.com/o/oauth2/v2/auth?state=abc"]);
});

test("linkSocial throws instead of silently doing nothing when the server returns no redirect url", async (t) => {
	mockFetch(t, async () => jsonResponse(200, { redirect: false, status: true }));
	const assigned = [];
	withStubWindow(t, assigned);

	await assert.rejects(() => linkSocial("google", "https://app.test/app/account"), /did not return a redirect/);
	assert.deepEqual(assigned, []);
});

test("unlinkAccount POSTs providerId + accountId to /api/auth/unlink-account", async (t) => {
	let seen;
	mockFetch(t, async (url, options) => {
		seen = { url, method: options.method, body: JSON.parse(options.body) };
		return jsonResponse(200, { status: true });
	});

	await unlinkAccount("google", "google-sub-1");

	assert.equal(seen.url, "/api/auth/unlink-account");
	assert.equal(seen.method, "POST");
	assert.deepEqual(seen.body, { providerId: "google", accountId: "google-sub-1" });
});

test("unlinkAccount surfaces the server's last-credential guard message rather than a generic failure", async (t) => {
	mockFetch(t, async () => jsonResponse(400, { message: "You can't unlink your last account", code: "FAILED_TO_UNLINK_LAST_ACCOUNT" }));

	await assert.rejects(() => unlinkAccount("credential"), /You can't unlink your last account/);
});
