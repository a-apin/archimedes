import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

import {
	canUnlink,
	connectableProvidersIntro,
	connectedActionErrorState,
	connectedProviderLabel,
	isLinkableProvider,
} from "../src/account-linking.js";

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

// Round-3 review (minor): isKnownConnectedProvider is true for 'credential'
// — present in connectedAccounts for every password user, but never
// something the Link buttons in AccountSettings.jsx can produce. Using it
// to gate the `?linked=` toast let `?linked=credential` through.
// isLinkableProvider is the narrower, correct gate: only the providers this
// UI can actually initiate a link for. (Round-4 review, minor:
// isKnownConnectedProvider itself is gone — it had zero consumers left once
// isLinkableProvider took over this one real call site; the prototype-safety
// assertions its own test used to carry — empty string / `__proto__` /
// `toString` — are migrated onto isLinkableProvider below instead of lost.)
test("isLinkableProvider: true only for google/github, false for credential and anything else", () => {
	assert.equal(isLinkableProvider("google"), true);
	assert.equal(isLinkableProvider("github"), true);
	assert.equal(isLinkableProvider("credential"), false);
	assert.equal(isLinkableProvider("some-future-provider"), false);
	assert.equal(isLinkableProvider(""), false);
	assert.equal(isLinkableProvider("__proto__"), false);
	assert.equal(isLinkableProvider("toString"), false);
});

// Mutation-prove: change LINKABLE_PROVIDERS in src/account-linking.js to
// include 'credential' and this fails. Confirmed by hand before commit.
test("isLinkableProvider actually excludes credential, not just an incomplete allowlist", () => {
	assert.notEqual(isLinkableProvider("credential"), isLinkableProvider("google"));
});

// ── Round-4 review finding (major): stale-session UX ─────────────────────
// /link-social and /unlink-account are fresh-session gated (24h) while app
// sessions live 7 days, so for most of a session's life both Link and
// Unlink render enabled and any click 403s. connectedActionErrorState is
// the pure mapping AccountSettings.jsx's link()/unlinkConnected() catch
// blocks both use to react to that 403 HONESTLY: attempt the action, then
// branch on what the server actually said — never on a client-guessed
// session age (this function takes no clock/timestamp input at all).

test("connectedActionErrorState: a SESSION_NOT_FRESH error produces ONLY the honest re-auth state — no message of its own, no fabricated notice", () => {
	const state = connectedActionErrorState({ code: "SESSION_NOT_FRESH", message: "Session is not fresh" });
	assert.deepEqual(state, { stale: true, message: null });
});

test("connectedActionErrorState: any other error passes its own message through, and is not marked stale", () => {
	const state = connectedActionErrorState({ code: "SOME_OTHER_ERROR", message: "Could not link that account. Try again." });
	assert.deepEqual(state, { stale: false, message: "Could not link that account. Try again." });
});

test("connectedActionErrorState: a message-less error still resolves to a non-empty fallback, never undefined", () => {
	const state = connectedActionErrorState({});
	assert.equal(state.stale, false);
	assert.equal(typeof state.message, "string");
	assert.ok(state.message.length > 0);
});

// Mutation-prove: hard-code `connectedActionErrorState` to always return
// `{ stale: false, message: err?.message }` (i.e. drop the SESSION_NOT_FRESH
// branch entirely) and the first test above fails — `stale` comes back
// `false` and `message` comes back "Session is not fresh" (the raw library
// string this exists to avoid showing). Confirmed by hand before commit,
// reverted.
test("connectedActionErrorState actually branches on err.code, not just returning the same shape for everything", () => {
	const stale = connectedActionErrorState({ code: "SESSION_NOT_FRESH" });
	const other = connectedActionErrorState({ code: "SOME_OTHER_ERROR", message: "x" });
	assert.notEqual(stale.stale, other.stale);
});

// ── Round-4 review finding (minor): the Connected-accounts intro copy used
// to promise Google/GitHub linking unconditionally, even when the same
// provider-discovery fetch that gates the Link buttons says neither is
// configured on this deployment. connectableProvidersIntro derives the
// honest sentence from that result instead.

test("connectableProvidersIntro: names both providers when both are configured", () => {
	assert.match(connectableProvidersIntro({ google: true, github: true }), /Google and GitHub/);
});

test("connectableProvidersIntro: names only the one that's actually configured", () => {
	assert.match(connectableProvidersIntro({ google: true, github: false }), /^Google /);
	assert.doesNotMatch(connectableProvidersIntro({ google: true, github: false }), /GitHub/);
	assert.match(connectableProvidersIntro({ google: false, github: true }), /^GitHub /);
	assert.doesNotMatch(connectableProvidersIntro({ google: false, github: true }), /Google/);
});

test("connectableProvidersIntro: promises nothing when neither provider is configured", () => {
	assert.equal(connectableProvidersIntro({ google: false, github: false }), "");
	assert.equal(connectableProvidersIntro({}), "");
});

// Mutation-prove: hard-code connectableProvidersIntro to always return the
// "both" sentence regardless of input, and the "promises nothing" test above
// fails (a deployment with neither provider configured would still get a
// non-empty promise back). Confirmed by hand before commit, reverted.
test("connectableProvidersIntro actually branches on the providers object, not a constant string", () => {
	assert.notEqual(connectableProvidersIntro({ google: false, github: false }), connectableProvidersIntro({ google: true, github: true }));
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
// on the next click. Round-4 review (major): the err.code check itself now
// lives in the shared connectedActionErrorState helper (see the direct unit
// tests above), not inline here — this test checks the WIRING: both catches
// route through that one helper, and the disabled= attributes still key off
// its result (connectedSessionStale).
test("AccountSettings.jsx disables both Link and Unlink once a SESSION_NOT_FRESH error is seen, not just Unlink", () => {
	const src = readFileSync(new URL("../src/components/AccountSettings.jsx", import.meta.url), "utf8");
	assert.match(src, /disabled=\{connectedBusy === 'google' \|\| connectedSessionStale\}/);
	assert.match(src, /disabled=\{connectedBusy === 'github' \|\| connectedSessionStale\}/);
});

test("AccountSettings.jsx's link() and unlinkConnected() both route their catch through the shared connectedActionErrorState helper, not duplicated err.code checks", () => {
	const src = readFileSync(new URL("../src/components/AccountSettings.jsx", import.meta.url), "utf8");
	// Exactly one place computes stale/message from an error...
	assert.match(src, /const \{ stale, message \} = connectedActionErrorState\(err\)/);
	assert.match(src, /if \(stale\) setConnectedSessionStale\(true\)/);
	// ...and both catch blocks call it, not a re-derived err.code check.
	const catchCalls = src.match(/handleConnectedActionError\(err\)/g) || [];
	assert.equal(catchCalls.length, 2, `expected link() and unlinkConnected() to both call the shared handler, found ${catchCalls.length} call(s)`);
	assert.doesNotMatch(src, /err\.code === 'SESSION_NOT_FRESH'/);
});

// Round-4 review finding (major): the stale-session error used to render
// via a plain "Sign in again" button wired to logout() (redirect to '/',
// which has no sign-in form and no way back). It must now route to
// /sign-in preserving a return path to Account Settings — the same pattern
// as every other anonymous bounce in this app — and must render the honest,
// server-authoritative message, never a fabricated success/error state
// alongside it.
test('AccountSettings.jsx\'s "Sign in again" affordance ends the session then routes to /sign-in?next=/app/account, not a bare logout to \'/\'', () => {
	const src = readFileSync(new URL("../src/components/AccountSettings.jsx", import.meta.url), "utf8");
	assert.match(src, /const reauthenticate = async \(\) => \{/);
	assert.match(src, /window\.location\.assign\(`\/sign-in\?next=\$\{encodeURIComponent\('\/app\/account'\)\}`\)/);
	assert.match(src, /onClick=\{reauthenticate\}>Sign in again<\/button>/);
	// The plain Sign out button at the bottom of the page is unaffected —
	// this is a SEPARATE affordance, not a rename of logout().
	assert.match(src, /onClick=\{logout\}>Sign out<\/button>/);
});

test("AccountSettings.jsx's SESSION_NOT_FRESH branch never sets a notice — no fabricated success state alongside the honest error", () => {
	const src = readFileSync(new URL("../src/components/AccountSettings.jsx", import.meta.url), "utf8");
	const handler = src.slice(src.indexOf("const handleConnectedActionError"), src.indexOf("const link = async"));
	assert.doesNotMatch(handler, /setConnectedNotice/);
});

test("AccountSettings.jsx confirms before unlinking a connected provider (no single unconfirmed destructive click — 3.3.4)", () => {
	const src = readFileSync(new URL("../src/components/AccountSettings.jsx", import.meta.url), "utf8");
	assert.match(src, /window\.confirm\(`Unlink \$\{label\}\? You will no longer be able to sign in with \$\{label\}\.`\)/);
});

test("AccountSettings.jsx imports listAccounts/linkSocial/unlinkAccount from auth-client, not a hand-rolled fetch", () => {
	const src = readFileSync(new URL("../src/components/AccountSettings.jsx", import.meta.url), "utf8");
	assert.match(src, /import \{ getProviders, linkSocial, listAccounts, unlinkAccount \} from '\.\.\/auth-client'/);
});

test("AccountSettings.jsx uses the shared canUnlink/connectedProviderLabel/isLinkableProvider module — not a locally re-defined copy", () => {
	const src = readFileSync(new URL("../src/components/AccountSettings.jsx", import.meta.url), "utf8");
	assert.match(src, /import \{ canUnlink, connectableProvidersIntro, connectedActionErrorState, connectedProviderLabel, isLinkableProvider \} from '\.\.\/account-linking'/);
	assert.doesNotMatch(src, /function canUnlink\(/);
});

// Round-2 review (minor): the `?linked=` success notice used to render
// straight off the URL with no check at all.
// Round-3 review (minor): isKnownConnectedProvider (round-2's fix) still let
// `?linked=credential` and a stale/replayed `?linked=google` through — see
// isLinkableProvider's own unit tests above, and the pending-link-marker
// test below for the recency half of this fix.
test("AccountSettings.jsx's linked-notice checks isLinkableProvider AND presence in the reloaded connectedAccounts list, not the URL alone", () => {
	const src = readFileSync(new URL("../src/components/AccountSettings.jsx", import.meta.url), "utf8");
	assert.match(src, /if \(!linked \|\| !isLinkableProvider\(linked\) \|\| linked !== pendingLink\) return/);
	assert.match(src, /connectedAccounts\.some\(\(account\) => account\.providerId === linked\)/);
});

// Round-3 review (minor): the recency half of the `?linked=` fix — a
// one-shot marker set immediately before link()'s own redirect, read (and
// cleared) exactly once by the notice effect, so the toast can only ever
// fire for a link this tab itself just initiated.
test("AccountSettings.jsx sets a one-shot pending-link marker before redirecting, and the notice effect consumes it", () => {
	const src = readFileSync(new URL("../src/components/AccountSettings.jsx", import.meta.url), "utf8");
	assert.match(src, /sessionStorage\.setItem\(PENDING_LINK_KEY, provider\)/);
	assert.match(src, /const pendingLink = sessionStorage\.getItem\(PENDING_LINK_KEY\)/);
	assert.match(src, /sessionStorage\.removeItem\(PENDING_LINK_KEY\)/);
});

// Round-3 review finding (minor): a rejected getProviders() used to be
// swallowed by an empty `.catch(() => {})`, indistinguishable from "no OAuth
// providers configured" — so a real fetch failure silently hid the Link
// controls with no explanation, while the section's own copy kept promising
// them.
test("AccountSettings.jsx surfaces a failed provider-discovery fetch instead of silently swallowing it", () => {
	const src = readFileSync(new URL("../src/components/AccountSettings.jsx", import.meta.url), "utf8");
	assert.doesNotMatch(src, /\.catch\(\(\) => \{\}\)/);
	assert.match(src, /\.catch\(\(\) => setConnectedProvidersError\(true\)\)/);
	assert.match(src, /\{connectedProvidersError && \(/);
});

// Round-4 review finding (minor): the alert explaining a failed
// provider-discovery fetch said the Link buttons "may be missing below" but
// renders physically after (below) the button block in the DOM — backwards,
// since the (possibly absent) buttons are above this text, not below it.
test("AccountSettings.jsx's provider-discovery error alert points the right direction at the controls it explains", () => {
	const src = readFileSync(new URL("../src/components/AccountSettings.jsx", import.meta.url), "utf8");
	assert.match(src, /Link buttons may be missing above\. Reload to try again\./);
	assert.doesNotMatch(src, /Link buttons may be missing below/);
});

// Round-4 review finding (minor): the intro copy used to promise Google AND
// GitHub linking unconditionally; it must now come from the same
// provider-discovery result (connectedProviders) that gates the Link
// buttons themselves, via connectableProvidersIntro (see its own unit tests
// above).
test("AccountSettings.jsx's Connected-accounts intro copy is derived from connectableProvidersIntro(connectedProviders), not a hard-coded promise", () => {
	const src = readFileSync(new URL("../src/components/AccountSettings.jsx", import.meta.url), "utf8");
	assert.match(src, /const providersIntro = connectableProvidersIntro\(connectedProviders\)/);
	assert.match(src, /\{providersIntro && ` \$\{providersIntro\}`\}/);
	assert.doesNotMatch(src, /Google and GitHub link only after you authorize them from here, signed in as you are now\.\s*<\/p>/);
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
