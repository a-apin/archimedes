import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { rectOnScreen } from "../src/tourGeometry.js";

// Onboarding tour regression guards (#1364).
//
// Two independent bugs, two independent fixes:
//   1. Anonymous eject — the tour's anchor-navigation effect drove
//      window.location.replace('/sign-in?…') for anonymous visitors on
//      cards 3–4 ('library', 'reasoning'). Fixed by gating that effect on
//      the pure predicate `canNavigateTo` (routes.js, tested in
//      routes.test.js).
//   2. Off-screen spotlight — below 1024px the sidebar drawer is
//      `transform`-positioned, not removed from layout, so its closed rect
//      is non-zero but entirely off-screen; `measure()`'s zero-size-only
//      check accepted it. Fixed by the pure predicate `rectOnScreen` below.
//
// ui/package.json's `"test": "node --test"` is bare node — no JSX
// transform, no jsdom, no @testing-library (see the anti-goal against
// adding either). So the decision logic (`rectOnScreen`, `canNavigateTo`)
// is genuinely unit-tested here as plain `.js`, and OnboardingTour.jsx's
// *use* of them is pinned by readFileSync + regex, same shape as
// ui/test/a11y.test.js. The regex pins are wiring checks, not behavioural
// ones — they prove the component calls the tested predicate, not that the
// resulting DOM is correct, which no harness here can observe.
//
// Every assertion in this file was confirmed to FAIL against the pre-fix
// tree (transcripts in the PR body).

const onboardingTour = readFileSync(
	new URL("../src/components/OnboardingTour.jsx", import.meta.url),
	"utf8",
);

// ── rectOnScreen: pure geometry predicate ──────────────────────────────────

test("rectOnScreen rejects a closed mobile drawer translated off-screen", () => {
	// getBoundingClientRect() on `.sidebar` with `transform: translateX(-100%)`
	// applied to a 260px-wide drawer: full-size, non-zero, but left ≈ -260.
	assert.equal(
		rectOnScreen({ left: -260, top: 100, right: 0, bottom: 140 }, 390, 844),
		false,
	);
});

test("rectOnScreen accepts the same element once the drawer is open", () => {
	assert.equal(
		rectOnScreen({ left: 0, top: 100, right: 260, bottom: 140 }, 390, 844),
		true,
	);
});

test("rectOnScreen rejects a zero-size rect (element absent/hidden)", () => {
	assert.equal(
		rectOnScreen({ left: 0, top: 100, right: 0, bottom: 100 }, 390, 844),
		false,
	);
});

test("rectOnScreen rejects a rect entirely below or right of the viewport", () => {
	// left/top >= viewport: on-screen origin, but the whole box is past the
	// visible edge — the width/height!=0 check alone would accept this.
	assert.equal(
		rectOnScreen({ left: 400, top: 100, right: 460, bottom: 140 }, 390, 844),
		false,
	);
	assert.equal(
		rectOnScreen({ left: 0, top: 900, right: 60, bottom: 940 }, 390, 844),
		false,
	);
});

test("rectOnScreen accepts an ordinary on-screen rect", () => {
	assert.equal(
		rectOnScreen({ left: 20, top: 20, right: 200, bottom: 60 }, 390, 844),
		true,
	);
});

// ── Wiring: the component calls both predicates ─────────────────────────

test("OnboardingTour is wired to canNavigateTo before it calls setPage", () => {
	assert.match(onboardingTour, /canNavigateTo\(/);
	// The gate must run before setPage in the anchor-navigation effect —
	// pin the shape, not just presence, so a guard added elsewhere in the
	// file (e.g. only around measure()) doesn't satisfy this check.
	assert.match(
		onboardingTour,
		/if \(!canNavigateTo\(card\.anchor, user\)\) return\s*\n\s*setPage\(card\.anchor\)/,
	);
});

test("OnboardingTour is wired to rectOnScreen in measure()", () => {
	assert.match(onboardingTour, /rectOnScreen\(/);
	assert.match(
		onboardingTour,
		/if \(!rectOnScreen\(r, window\.innerWidth, window\.innerHeight\)\)/,
	);
});

test("OnboardingTour accepts a user prop (not just open/onClose/setPage)", () => {
	assert.match(
		onboardingTour,
		/export default function OnboardingTour\(\{ open, onClose, setPage, user \}\)/,
	);
});

// ── Z-index: every tour layer clears the mobile drawer (z-index: 10000) ──

test("every tour layer's z-index clears .sidebar's 10000 (App.css)", () => {
	const zs = [
		...onboardingTour.matchAll(/zIndex:\s*(\d+)|z-\[(\d+)\]/g),
	].map((m) => Number(m[1] ?? m[2]));
	assert.ok(zs.length >= 4, `expected >=4 z-index declarations, found ${zs.length}`);
	assert.ok(
		zs.every((z) => z > 10000),
		`tour z-indices must clear .sidebar (10000): ${zs}`,
	);
});

// ── Anti-goal: no escape hatch keyed on a hard-coded anchor id ───────────

test("the navigation gate is not a hard-coded id check on 'generate'", () => {
	// 'generate' is in ANON_NAV_IDS (visible in the anon nav) but NOT in
	// ANON_APP_PAGES (routes.js) — an id-keyed escape hatch here would look
	// like it fixes the reported cards while leaving 'generate' itself
	// exposed to the exact same eject the moment it fails to measure
	// (mobile drawer, collapsed rail, slow mount).
	assert.doesNotMatch(onboardingTour, /anchor === ['"]generate['"]/);
});
