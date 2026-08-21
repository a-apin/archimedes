import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { resolveRoute, pageToPath } from "../src/routes.js";

// Guards for the /privacy and /terms policy pages.
//
// Two of these carry weight beyond ordinary regression cover:
//
//   1. ANONYMOUS REACHABILITY. The privacy URL is submitted to Google's OAuth
//      consent screen, and Google fetches it with no session. If these routes
//      ever stop resolving public — moved under /app, gated, feature-flagged —
//      the consent screen's verification breaks and the only symptom is an
//      email from Google weeks later. Hence a test, not a comment.
//   2. THE DRAFT BANNER. Both pages are unreviewed drafts. The banner and the
//      "[pending owner approval]" date are the honest label on that, and only
//      the owner removes them. A test is the thing that stops a tidy-up pass
//      from quietly deleting an inconvenient disclaimer.
//
// Same file-read + regex shape as public-visuals.test.js / a11y.test.js: no
// DOM, no renderer. Every assertion below was mutation-checked against the
// tree — see the PR body for the list of edits that make each one fail.

const src = (p) => readFileSync(new URL(`../src/${p}`, import.meta.url), "utf8");

const privacy = src("components/Privacy.jsx");
const terms = src("components/Terms.jsx");
const banner = src("components/PolicyBanner.jsx");
const publicLayout = src("components/PublicLayout.jsx");
const layout = src("components/Layout.jsx");
const landing = src("components/Landing.jsx");
const app = src("App.jsx");

// ── Routing: anonymous, public, and not feature-gated ───────────────────

test("/privacy and /terms resolve as public routes", () => {
	for (const [path, page] of [
		["/privacy", "privacy"],
		["/terms", "terms"],
	]) {
		const route = resolveRoute(path);
		assert.equal(route.kind, "public", `${path} must be a public route`);
		assert.equal(route.page, page);
		assert.equal(route.redirect, null);
	}
});

// The bounce-to-sign-in effect in App.jsx keys off `route.kind !== 'app'`, so
// "public" IS the anonymous guarantee — but only while these pages stay out of
// the app shell. This pins both halves: the routes are public, and the effect
// still gates on kind === 'app'.
test("an anonymous visitor is never bounced off the policy pages", () => {
	for (const path of ["/privacy", "/terms"]) {
		assert.notEqual(
			resolveRoute(path).kind,
			"app",
			`${path} must not be an /app route — those bounce anonymous visitors to /sign-in`,
		);
	}
	assert.match(app, /if \(route\.kind !== 'app' \|\| route\.anonymousOk \|\| authLoading \|\| user\) return/);
});

// featureEnabled() is applied to app routes only, but a future edit could
// route these through it. If that happens with the flag off, Google fetches a
// 404 where the privacy policy should be.
test("policy routes survive a features payload that disables everything", () => {
	const nothingEnabled = { quant: false, roadmapSurfaces: false };
	assert.equal(resolveRoute("/privacy", "", nothingEnabled).kind, "public");
	assert.equal(resolveRoute("/terms", "", nothingEnabled).kind, "public");
});

test("pageToPath round-trips both policy pages", () => {
	assert.equal(pageToPath("privacy"), "/privacy");
	assert.equal(pageToPath("terms"), "/terms");
	assert.equal(resolveRoute(pageToPath("privacy")).page, "privacy");
	assert.equal(resolveRoute(pageToPath("terms")).page, "terms");
});

test("App.jsx renders both policy pages in the public shell", () => {
	assert.match(app, /import Privacy from '\.\/components\/Privacy'/);
	assert.match(app, /import Terms from '\.\/components\/Terms'/);
	assert.match(app, /privacy: <Privacy \/>/);
	assert.match(app, /terms: <Terms \/>/);
});

// ── The draft banner and the un-dated date ──────────────────────────────

test("both policy pages carry the draft banner", () => {
	assert.match(privacy, /import PolicyBanner from "\.\/PolicyBanner"/);
	assert.match(terms, /import PolicyBanner from "\.\/PolicyBanner"/);
	assert.match(privacy, /<PolicyBanner \/>/);
	assert.match(terms, /<PolicyBanner \/>/);
});

test("the banner says it is an unreviewed draft and not legal advice", () => {
	assert.match(banner, /Draft — under review/);
	assert.match(banner, /not been reviewed by a lawyer/);
	assert.match(banner, /not legal advice/);
	assert.match(banner, /className="policy-banner"/);
});

// The date is a claim that a human signed off on a particular day. Until the
// owner has, the honest value is the placeholder — so pin the placeholder and
// pin the absence of a real date, since only the second half catches someone
// "helpfully" filling one in.
test("policy pages stay undated until the owner approves them", () => {
	for (const [name, page] of [
		["Privacy", privacy],
		["Terms", terms],
	]) {
		assert.match(page, /Last updated: \[pending owner approval\]/, `${name} must keep the placeholder date`);
		assert.doesNotMatch(
			page,
			/Last updated:\s*(?!\[pending owner approval\])\S/,
			`${name} must not carry a real last-updated date while it is an unreviewed draft`,
		);
	}
});

// ── Footer links from BOTH shells ───────────────────────────────────────

test("both shells link to both policy pages", () => {
	for (const [name, shell] of [
		["PublicLayout", publicLayout],
		["Layout", layout],
	]) {
		assert.match(shell, /href="\/privacy"/, `${name} must link to /privacy`);
		assert.match(shell, /href="\/terms"/, `${name} must link to /terms`);
		assert.match(shell, /aria-label="Policies"/, `${name}'s policy nav needs an accessible name`);
	}
});

// The public footer moved out of Landing.jsx into PublicLayout.jsx so that
// Architecture, Privacy, Terms and not-found carry it too. If it is ever
// copied back, every public page renders two footers and the policy links
// appear twice on the landing page.
test("the public footer lives in the shell, not on the landing page", () => {
	assert.match(publicLayout, /<footer className="public-footer">/);
	assert.doesNotMatch(landing, /<footer/);
});

test("the app shell footer sits below main, inside the sidebar-offset column", () => {
	assert.match(layout, /<\/main>\s*(?:\{\/\*[\s\S]*?\*\/\})?\s*<footer className="app-footer">/);
});

// ── No dead links ───────────────────────────────────────────────────────

const INTERNAL_HREF = /href="(\/[^"]*)"/g;

test("every internal link on the policy pages and in both footers resolves", () => {
	const sources = [
		["Privacy.jsx", privacy],
		["Terms.jsx", terms],
		["PublicLayout.jsx", publicLayout],
		["Layout.jsx", layout],
	];
	let checked = 0;
	for (const [name, source] of sources) {
		for (const [, href] of source.matchAll(INTERNAL_HREF)) {
			// Query/template hrefs (e.g. Layout's `/sign-in?next=...`) are built at
			// runtime; take the path half, which is what resolveRoute needs.
			const path = href.split("?")[0];
			if (path.includes("${")) continue;
			const route = resolveRoute(path);
			assert.notEqual(route.kind, "not-found", `${name}: dead internal link ${href}`);
			checked += 1;
		}
	}
	// Guards the guard: a regex that stops matching would make the loop above
	// vacuously pass. Four is the floor — /privacy and /terms from each shell.
	assert.ok(checked >= 4, `expected to check at least 4 internal links, checked ${checked}`);
});

test("external links on the policy pages are safe and point at the real repo", () => {
	for (const [name, page] of [
		["Privacy", privacy],
		["Terms", terms],
	]) {
		for (const [match] of page.matchAll(/<a[\s\S]*?href="(https?:[^"]*)"[\s\S]*?>/g)) {
			assert.match(match, /rel="noopener noreferrer"/, `${name}: external link missing rel=noopener`);
		}
		// a-apin/archimedes is the canonical repo (README.md, CLAUDE.md). The old
		// pre-rename name still redirects, so a stale link would not 404 — which
		// is exactly why it needs pinning rather than eyeballing.
		assert.match(page, /https:\/\/github\.com\/a-apin\/archimedes\/issues/);
		assert.doesNotMatch(page, /archimedes-arcadia/);
	}
});

// ── Substance: the claims the pages exist to make ───────────────────────

// These pin the disclosures that are least comfortable and therefore most
// likely to be softened in an edit — the ones a reader is actively looking
// for. Wording can change; the disclosure cannot silently disappear.
test("the privacy policy discloses the things it would be convenient to omit", () => {
	assert.match(privacy, /archimedes_vid/, "the 180-day visitor cookie must be named");
	assert.match(privacy, /180 days/);
	assert.match(privacy, /IP address is stored on each sign-in/i, "session IP storage must be disclosed");
	assert.match(privacy, /no delete-my-account button/i, "the absent deletion path must be stated");
	assert.match(privacy, /public and permanent/i, "the on-chain permanence caveat must be present");
	assert.match(privacy, /pseudonymous, not anonymous/i);
	assert.match(privacy, /Google Fonts/, "the one third-party browser request must be disclosed");
	assert.match(privacy, /do not sell your data/i);
});

test("the terms state the testnet, no-advice and no-real-funds position", () => {
	assert.match(terms, /testnet/i);
	assert.match(terms, /not investment advice/i);
	assert.match(terms, /settlement is\s+switched off in production/i);
	assert.match(terms, /Do not connect a wallet holding assets you care about/i);
	assert.match(terms, /as is/i);
	assert.match(terms, /Limitation of liability/i);
});

// Governing law was the owner's call, and he made it (2026-08-21): Illinois.
// The guard flipped WITH the decision rather than being deleted by it. It used
// to pin the [OWNER TO SPECIFY] marker so nobody could guess a jurisdiction;
// it now pins the answer so nobody can quietly drift off it.
//
// Scoped to the section body, NOT to the whole file: the file-wide form of
// this assertion passed a mutation that replaced the governing-law marker with
// an invented "Delaware law governs", because a second [OWNER TO SPECIFY]
// elsewhere on the page (the liability cap) kept the file-wide match green.
// A guard satisfied by an unrelated line guards nothing.
function sectionBody(source, heading) {
	const re = new RegExp(`<h2>${heading}</h2>([\\s\\S]*?)</section>`);
	const m = source.match(re);
	assert.ok(m, `section not found: ${heading}`);
	return m[1];
}

test("governing law names Illinois, and no other jurisdiction", () => {
	const governing = sectionBody(terms, "Governing law");
	assert.match(governing, /laws of the State of Illinois/, "the owner specified Illinois");
	assert.match(
		governing,
		/courts located in Illinois/,
		"venue must be named too — governing law alone leaves where-you-sue open",
	);
	assert.doesNotMatch(
		governing,
		/\b(Delaware|England|Wales|Singapore|Switzerland|New York|California|Texas|Massachusetts)\b/i,
		"no jurisdiction other than Illinois may appear in this section",
	);
});

// Both owner-decision markers are resolved: governing law -> Illinois, and the
// liability cap -> none (the exclusions stand on their own). Neither page may
// carry the marker again — a page shipped with an unresolved decision printed
// on it is a page nobody finished.
test("no unresolved owner-decision markers remain on either page", () => {
	for (const [name, page] of [
		["Privacy", privacy],
		["Terms", terms],
	]) {
		assert.doesNotMatch(page, /\[OWNER TO SPECIFY\]/, `${name} still carries an unresolved owner decision`);
	}
});

// APRIN Labs is a trading name, not a filed company (owner, 2026-08-21).
// "operated by APRIN Labs" reads as though a legal entity exists to stand
// behind these terms. It doesn't — and asserting one on the two pages whose
// entire purpose is claims that are true is exactly the failure CLAUDE.md's
// first rule exists to prevent.
test("the operator line does not imply a company that does not exist", () => {
	for (const [name, page] of [
		["Privacy", privacy],
		["Terms", terms],
	]) {
		assert.match(page, /operated under the name APRIN&nbsp;Labs/, `${name} must name the operator`);
		assert.match(page, /not a registered company/, `${name} must not leave incorporation implied`);
		assert.doesNotMatch(page, /operated by APRIN/, `${name} must not imply APRIN Labs is a company`);
	}
});
