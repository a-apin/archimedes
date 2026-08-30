// Claim-integrity guard for sign-in method claims on the public surfaces
// (#1431): /architecture told visitors to "sign in with any wallet or a
// passkey" while the live providers endpoint reports passkey:false and no
// wallet provider at all — and AuthPage itself states "Circle wallet passkeys
// ... do not sign in to Archimedes". The copy was wrong, not the product
// state, so the fix is copy plus this guard, never enabling passkeys.
//
// Same shape as roadmap-copy.test.js's prose guard: a raw source-text scan
// (readFileSync, no JSX parsing) over the public explainer surfaces. It is
// deliberately generic ("sign in", not a provider list) on the fixed side —
// per #1431's anti-goal, hard-coding "email, Google or GitHub" would drift
// the next time a provider flips — while the guard side pins the specific
// false claims that shipped.
//
// AuthPage.jsx is deliberately NOT scanned: it is the one surface allowed to
// talk about wallet passkeys, because it does so accurately, in negation
// ("They do not sign in to Archimedes").
//
// Anti-vacuity, mirroring roadmap-copy.test.js: every scanned file must
// exist (a rename must fail loudly, not shrink the scan to nothing), and
// every pattern must match its own canonical example (a pattern that stops
// matching anything is guarding nothing).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..", "src");

const SCANNED_SURFACES = [
	"components/Architecture.jsx",
	"components/Landing.jsx",
	"components/PublicLayout.jsx",
	"components/Layout.jsx",
];

// Each entry: a phrase-shape that claims a sign-in method the providers
// endpoint reports as unavailable, plus the canonical example it must match.
const FALSE_SIGNIN_CLAIMS = [
	{
		pattern: /sign[- ]?in with (any |a )?(wallet|passkey)/i,
		canonical: "sign in with any wallet",
	},
	{
		pattern: /wallet or a passkey/i,
		canonical: "sign in with any wallet or a passkey",
	},
	{
		pattern: /passkey to sign[- ]?in/i,
		canonical: "use a passkey to sign in",
	},
];

test("every scanned surface file exists (scan cannot silently shrink)", () => {
	for (const rel of SCANNED_SURFACES) {
		assert.doesNotThrow(
			() => readFileSync(join(SRC, rel), "utf8"),
			`${rel} is declared in SCANNED_SURFACES but unreadable — update the list, do not let the scan shrink silently`,
		);
	}
});

test("every pattern matches its canonical example (guard is not vacuous)", () => {
	for (const { pattern, canonical } of FALSE_SIGNIN_CLAIMS) {
		assert.match(
			canonical,
			pattern,
			`pattern ${pattern} no longer matches its own canonical example — it is guarding nothing`,
		);
	}
});

test("public surfaces never claim wallet or passkey sign-in", () => {
	for (const rel of SCANNED_SURFACES) {
		const source = readFileSync(join(SRC, rel), "utf8");
		for (const { pattern } of FALSE_SIGNIN_CLAIMS) {
			assert.doesNotMatch(
				source,
				pattern,
				`${rel} claims a sign-in method the providers endpoint reports as unavailable (${pattern}) — see #1431; fix the copy, do not enable the method`,
			);
		}
	}
});
