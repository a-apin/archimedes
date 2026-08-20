import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

import { canUnlink, connectedProviderLabel, isKnownConnectedProvider } from "../src/account-linking.js";

// ── #1420 follow-up: explicit link/unlink (Account Settings → Connected
// accounts) ────────────────────────────────────────────────────────────
//
// canUnlink() is the client-side half of "never allow unlinking the last
// sign-in method". Better Auth's own /unlink-account (auth/auth.js:
// accountLinking.allowUnlinkingAll stays false) already enforces this
// server-side — see auth/test/auth.test.js's
// "unlinking the account's only remaining credential is refused" and the
// mutation-proof transcript in the PR body. This is the independent client
// guard: the Unlink button must never even be clickable in that state, not
// merely rejected after a round trip.

test("canUnlink: false at exactly one account, true above it", () => {
	assert.equal(canUnlink(0), false);
	assert.equal(canUnlink(1), false);
	assert.equal(canUnlink(2), true);
	assert.equal(canUnlink(3), true);
});

// Mutation-prove: change `return accountCount > 1` to `return true` in
// src/account-linking.js and this fails (canUnlink(1) becomes true).
// Confirmed by hand before commit — see the PR body for the transcript.
test("canUnlink is not just 'always true' or 'always false' — it actually branches on count", () => {
	assert.notEqual(canUnlink(1), canUnlink(2));
});

test("connectedProviderLabel: known providers get a human label, unknown ones pass through verbatim", () => {
	assert.equal(connectedProviderLabel("credential"), "Email & password");
	assert.equal(connectedProviderLabel("google"), "Google");
	assert.equal(connectedProviderLabel("github"), "GitHub");
	assert.equal(connectedProviderLabel("some-future-provider"), "some-future-provider");
});

// Round-2 review (minor): added alongside the fix for the `?linked=`
// success notice, which used to trust any URL value verbatim.
test("isKnownConnectedProvider: true only for the three real provider ids", () => {
	assert.equal(isKnownConnectedProvider("credential"), true);
	assert.equal(isKnownConnectedProvider("google"), true);
	assert.equal(isKnownConnectedProvider("github"), true);
	assert.equal(isKnownConnectedProvider("some-future-provider"), false);
	assert.equal(isKnownConnectedProvider(""), false);
	assert.equal(isKnownConnectedProvider("__proto__"), false);
	assert.equal(isKnownConnectedProvider("toString"), false);
});

// ── Wiring: prove the guard, the fetch calls, and the error surface are
// actually reachable from the rendered component, not just defined and
// unused. No DOM/renderer in this suite (see routes.test.js/auth-errors.
// test.js precedent) — wiring is proven by source assertion.

test("AccountSettings.jsx disables Unlink using canUnlink(connectedAccounts.length), not an inline count check", () => {
	const src = readFileSync(new URL("../src/components/AccountSettings.jsx", import.meta.url), "utf8");
	assert.match(src, /disabled = connectedBusy === account\.id \|\| connectedSessionStale \|\| !canUnlink\(connectedAccounts\.length\)/);
});

// Round-2 review (blocker): auth.js now gates /link-social with the same
// session-freshness check /unlink-account already had. Once a link/unlink
// attempt surfaces that as err.code === 'SESSION_NOT_FRESH', both Link and
// Unlink controls must stop looking clickable rather than just 403ing again
// on the next click.
test("AccountSettings.jsx disables both Link and Unlink once a SESSION_NOT_FRESH error is seen, not just Unlink", () => {
	const src = readFileSync(new URL("../src/components/AccountSettings.jsx", import.meta.url), "utf8");
	assert.match(src, /err\.code === 'SESSION_NOT_FRESH'/);
	assert.match(src, /disabled=\{connectedBusy === 'google' \|\| connectedSessionStale\}/);
	assert.match(src, /disabled=\{connectedBusy === 'github' \|\| connectedSessionStale\}/);
});

test("AccountSettings.jsx confirms before unlinking a connected provider (no single unconfirmed destructive click — 3.3.4)", () => {
	const src = readFileSync(new URL("../src/components/AccountSettings.jsx", import.meta.url), "utf8");
	assert.match(src, /window\.confirm\(`Unlink \$\{label\}\? You will no longer be able to sign in with \$\{label\}\.`\)/);
});

test("AccountSettings.jsx imports listAccounts/linkSocial/unlinkAccount from auth-client, not a hand-rolled fetch", () => {
	const src = readFileSync(new URL("../src/components/AccountSettings.jsx", import.meta.url), "utf8");
	assert.match(src, /import \{ getProviders, linkSocial, listAccounts, unlinkAccount \} from '\.\.\/auth-client'/);
});

test("AccountSettings.jsx uses the shared canUnlink/connectedProviderLabel/isKnownConnectedProvider module — not a locally re-defined copy", () => {
	const src = readFileSync(new URL("../src/components/AccountSettings.jsx", import.meta.url), "utf8");
	assert.match(src, /import \{ canUnlink, connectedProviderLabel, isKnownConnectedProvider \} from '\.\.\/account-linking'/);
	assert.doesNotMatch(src, /function canUnlink\(/);
});

// Round-2 review (minor): the `?linked=` success notice used to render
// straight off the URL with no check at all — isKnownConnectedProvider's own
// unit tests are above, alongside canUnlink/connectedProviderLabel.
test("AccountSettings.jsx's linked-notice checks isKnownConnectedProvider AND presence in the reloaded connectedAccounts list, not the URL alone", () => {
	const src = readFileSync(new URL("../src/components/AccountSettings.jsx", import.meta.url), "utf8");
	assert.match(src, /if \(!linked \|\| !isKnownConnectedProvider\(linked\)\) return/);
	assert.match(src, /connectedAccounts\.some\(\(account\) => account\.providerId === linked\)/);
});

// Round-2 review (minor): a rejected listAccounts() used to leave
// connectedAccounts at its initial [], which the render was reusing as the
// "still loading" sentinel — so a real fetch failure rendered "Loading…"
// forever instead of the error already sitting right below it.
test("AccountSettings.jsx tracks connectedLoaded independently of connectedAccounts.length, so a failed load doesn't render Loading… forever", () => {
	const src = readFileSync(new URL("../src/components/AccountSettings.jsx", import.meta.url), "utf8");
	assert.match(src, /setConnectedError\(err\.message\); setConnectedLoaded\(true\)/);
	// The render's loading check must key off connectedLoaded, not off
	// connectedAccounts.length === 0 (which a rejected fetch also leaves
	// true, forever).
	assert.match(src, /\{!connectedLoaded \? \(/);
});

test("AccountSettings.jsx's Link buttons are gated on the server's enabled-providers list, not shown unconditionally", () => {
	const src = readFileSync(new URL("../src/components/AccountSettings.jsx", import.meta.url), "utf8");
	assert.match(src, /connectedProviders\.google && !connectedAccounts\.some/);
	assert.match(src, /connectedProviders\.github && !connectedAccounts\.some/);
});
