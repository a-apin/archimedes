import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

import { oauthErrorMessage } from "../src/auth-errors.js";

// ── #1289: OAuth account-not-linked error goes silent ───────────────────
// auth/auth.js sets accountLinking.disableImplicitLinking: true (a deliberate
// security posture — do not weaken it to "fix" this). Its consequence: an
// existing email/password user who clicks "Continue with Google/GitHub" gets
// 302'd back with `?error=account_not_linked` and the UI previously rendered
// nothing. These tests pin the error->message mapping and its wiring into
// the actual sign-in surface.

test("account_not_linked maps to honest copy that does not promise a linking flow", () => {
	const message = oauthErrorMessage("account_not_linked");
	assert.match(message, /password account/);
	assert.match(message, /sign in with your email and password/i);
	// No account-linking UI exists in this app (verified: no `linkSocial`
	// call site in auth/ or ui/) — the message must not promise one.
	assert.doesNotMatch(message, /link your accounts?/i);
	assert.doesNotMatch(message, /account settings/i);
});

test("an unrecognized error value gets a generic, still-honest message rather than nothing", () => {
	const message = oauthErrorMessage("some_future_error_code_nobody_mapped_yet");
	assert.equal(typeof message, "string");
	assert.ok(message.length > 0);
	// The generic fallback must not fabricate a specific claim about what
	// happened — it can only say sign-in didn't complete.
	assert.doesNotMatch(message, /password account here/i);
});

test("no error param means no message — a plain sign-in visit must not show a banner", () => {
	assert.equal(oauthErrorMessage(null), null);
	assert.equal(oauthErrorMessage(undefined), null);
	assert.equal(oauthErrorMessage(""), null);
});

// Mutation-prove: delete the `account_not_linked` entry from
// OAUTH_ERROR_MESSAGES in src/auth-errors.js (so every code falls through to
// the generic message) and this test fails, because the specific and generic
// messages would become identical. Confirmed by hand before commit — see the
// PR body for the transcript.
test("account_not_linked gets a MORE SPECIFIC message than the generic fallback, not the same one", () => {
	const specific = oauthErrorMessage("account_not_linked");
	const generic = oauthErrorMessage("totally_unknown_code");
	assert.notEqual(specific, generic);
});

// ── Wiring: prove the mapping is actually reachable from the DOM, not just
// defined and unused. node:test has no DOM/renderer here (see routes.test.js
// precedent at "public shell lazy-loads..." and the Strategies.jsx checks),
// so wiring is proven by source assertion instead.

test("routes.js actually threads the error query param into the auth route (not dropped)", () => {
	const src = readFileSync(new URL("../src/routes.js", import.meta.url), "utf8");
	assert.match(src, /error: params\.get\('error'\)/);
	// The landing-route redirect must carry the literal value forward, not a
	// canned string — otherwise every error code would collapse onto the same
	// redirect target and the mapping in AuthPage would never see anything
	// but one hardcoded value.
	assert.match(src, /redirect: `\/sign-in\?error=\$\{encodeURIComponent\(query\.error\)\}`/);
});

test("App.jsx passes the resolved route's error through to AuthPage as a prop", () => {
	const src = readFileSync(new URL("../src/App.jsx", import.meta.url), "utf8");
	assert.match(src, /<AuthPage mode=\{route\.page\} oauthError=\{route\.error\} \/>/);
});

test("AuthPage.jsx renders the mapped message via oauthErrorMessage, gated to an alert role", () => {
	const src = readFileSync(new URL("../src/components/AuthPage.jsx", import.meta.url), "utf8");
	assert.match(src, /import \{ oauthErrorMessage \} from '\.\.\/auth-errors'/);
	assert.match(src, /oauthErrorMessage\(oauthError\)/);
	// role="alert" so assistive tech announces it the moment the sign-in
	// screen mounts with an error in the URL — not just a silent paragraph.
	assert.match(src, /\{oauthNotice && \(\s*<div className="status mb-4" role="alert" id="oauth-error">/);
});
