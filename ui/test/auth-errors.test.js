import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

import { linkErrorMessage, oauthErrorMessage } from "../src/auth-errors.js";

// ── #1420: OAuth account-not-linked error goes silent ───────────────────
// auth/auth.js's implicit auto-link path refuses to attach a Google/GitHub
// identity to an existing password account whose emailVerified isn't
// already true (requireLocalEmailVerified — see the long comment on
// accountLinking in auth/auth.js; never weakened to "fix" this). Its
// consequence: an existing, plausibly-unverified email/password user who
// clicks "Continue with Google/GitHub" gets 302'd back with
// `?error=account_not_linked` and the UI previously rendered nothing. These
// tests pin the error->message mapping and its wiring into the actual
// sign-in surface.

test("account_not_linked maps to honest copy that now points at the account-linking follow-up (#1420 follow-up shipped)", () => {
	const message = oauthErrorMessage("account_not_linked");
	assert.match(message, /password account/);
	assert.match(message, /sign in with your email and password/i);
	// The explicit link flow now exists (AccountSettings.jsx "Connected
	// accounts") — the message must point at it, not stay silent about it.
	assert.match(message, /account settings.*connected accounts/i);
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

// ── #1420 follow-up: explicit link/unlink (Account Settings → Connected
// accounts) ────────────────────────────────────────────────────────────

test("linkErrorMessage maps the explicit link callback's error codes to honest, distinct copy", () => {
	assert.match(linkErrorMessage("email_doesn't_match"), /different email/i);
	assert.match(linkErrorMessage("account_already_linked_to_different_user"), /already linked/i);
	assert.match(linkErrorMessage("access_denied"), /canceled/i);
});

test("linkErrorMessage falls back to a generic honest message for unmapped codes, and to null for none", () => {
	assert.equal(linkErrorMessage(null), null);
	assert.equal(linkErrorMessage(undefined), null);
	assert.equal(linkErrorMessage(""), null);
	const generic = linkErrorMessage("some_future_code");
	assert.equal(typeof generic, "string");
	assert.ok(generic.length > 0);
});

// Mutation-prove: delete the "email_doesn't_match" entry from
// LINK_ERROR_MESSAGES and this fails, because the specific and generic
// messages collapse to the same string. Confirmed by hand before commit.
test("email_doesn't_match gets a MORE SPECIFIC message than the generic link-error fallback", () => {
	const specific = linkErrorMessage("email_doesn't_match");
	const generic = linkErrorMessage("totally_unmapped_code");
	assert.notEqual(specific, generic);
});

test("linkErrorMessage and oauthErrorMessage are independent maps — a link-flow code never falls into the sign-in map by accident", () => {
	// email_doesn't_match only means something on the explicit-link surface;
	// the sign-in map (account_not_linked's home) must not also define it,
	// or the two code spaces have silently merged.
	assert.equal(oauthErrorMessage("email_doesn't_match"), oauthErrorMessage("totally_unknown_code"));
});

test("AuthenticatedApp.jsx threads the resolved route's error through to AccountSettings as linkError", () => {
	const src = readFileSync(new URL("../src/AuthenticatedApp.jsx", import.meta.url), "utf8");
	assert.match(src, /<AccountSettings[\s\S]*?linkError=\{route\.error\}[\s\S]*?\/>/);
});

test("AccountSettings.jsx renders linkErrorMessage(linkError) gated to an alert role", () => {
	const src = readFileSync(new URL("../src/components/AccountSettings.jsx", import.meta.url), "utf8");
	assert.match(src, /import \{ linkErrorMessage \} from '\.\.\/auth-errors'/);
	assert.match(src, /const linkErrorNotice = linkErrorMessage\(linkError\)/);
	assert.match(src, /\{linkErrorNotice && <div className="status mb-3" role="alert">\{linkErrorNotice\}<\/div>\}/);
});
