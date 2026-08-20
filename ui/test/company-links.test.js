import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { companyLinks } from "../src/companyLinks.js";
import { activeSocialLinks, SOCIAL_LINK_META } from "../src/socialLinks.js";

// ── companyLinks.js: the drop-in config itself ──────────────────────────────
//
// github is real today (the public repo, wired at launch). discord/x/company
// are pinned to '' — not "probably empty", a hard assertion. If the owner
// fills one in without touching this file, the assertion for that field goes
// from pass to fail, which is deliberate: the whole point (per the config's
// own comment) is that a real URL is a conscious edit to this test, not a
// silent drop-in. See socialLinks.js's activeSocialLinks() below for the
// production behaviour this state produces.

test("companyLinks: github is real, discord/x/company are pinned empty today", () => {
	assert.equal(companyLinks.github, "https://github.com/a-apin/archimedes");
	assert.equal(companyLinks.discord, "");
	assert.equal(companyLinks.x, "");
	assert.equal(companyLinks.company, "");
});

// ── socialLinks.js: activeSocialLinks() is the ONE filter both shells rely
// on. These two tests are independent of companyLinks.js's current values —
// they pass a synthetic links object so the filtering behaviour itself stays
// pinned even after the owner fills discord/x/company in.

test("activeSocialLinks: empty-string entries are dropped entirely, non-empty entries pass through with their icon/label", () => {
	const result = activeSocialLinks({
		github: "https://github.com/a-apin/archimedes",
		discord: "",
		x: "https://x.com/example",
		company: "",
	});
	const keys = result.map((r) => r.key);
	assert.deepEqual(keys, ["github", "x"]);
	assert.deepEqual(result[0], {
		key: "github",
		url: "https://github.com/a-apin/archimedes",
		...SOCIAL_LINK_META.github,
	});
	assert.deepEqual(result[1], {
		key: "x",
		url: "https://x.com/example",
		...SOCIAL_LINK_META.x,
	});
});

test("activeSocialLinks: all-empty config renders nothing — mutation-check target", () => {
	// If the filter in socialLinks.js were ever weakened to render
	// unconditionally (e.g. mapping every key instead of filtering first),
	// this would start returning 4 entries — including url: "" anchors, i.e.
	// exactly the dead links the claims-must-be-true rule forbids. Verified
	// manually: commenting out the `.filter((key) => Boolean(links[key]))`
	// line in socialLinks.js makes this assertion fail (4 entries returned,
	// three with url: ""); see PR body for the transcript.
	const result = activeSocialLinks({ github: "", discord: "", x: "", company: "" });
	assert.deepEqual(result, []);
});

test("activeSocialLinks: today's real companyLinks.js produces exactly one active link — github", () => {
	// This is the load-bearing assertion for the PR: it runs the real filter
	// against the real config, not a synthetic stand-in. Combined with the
	// companyLinks pin above, this fails the moment either (a) the filter
	// breaks or (b) a placeholder ships into discord/x/company without this
	// test being touched.
	const result = activeSocialLinks(companyLinks);
	assert.deepEqual(result.map((r) => r.key), ["github"]);
	assert.equal(result[0].url, "https://github.com/a-apin/archimedes");
});

// ── CompanyLinksFooter.jsx: renders only what activeSocialLinks() hands it,
// with an accessible name and a real external-link pattern per anchor ──────

const companyLinksFooter = readFileSync(
	new URL("../src/components/CompanyLinksFooter.jsx", import.meta.url),
	"utf8",
);

test("CompanyLinksFooter: uses the shared filter, not an inline re-check, and renders nothing when it returns empty", () => {
	assert.match(companyLinksFooter, /from ["']\.\.\/socialLinks["']/);
	assert.match(companyLinksFooter, /activeSocialLinks\(\)/);
	assert.match(companyLinksFooter, /if \(links\.length === 0\) return null;/);
	// No second, independent emptiness check on companyLinks itself — that
	// would be exactly the "re-implementing the check inline" drift
	// socialLinks.js's comment warns against.
	assert.doesNotMatch(companyLinksFooter, /companyLinks\[/);
});

test("CompanyLinksFooter: every anchor is a real external link with an accessible name", () => {
	assert.match(companyLinksFooter, /target="_blank"/);
	assert.match(companyLinksFooter, /rel="noopener noreferrer"/);
	assert.match(companyLinksFooter, /aria-label=\{label\}/);
	// Icon itself is decorative — the anchor already carries the name.
	assert.match(companyLinksFooter, /aria-hidden="true"/);
});

// ── Wiring: both shells mount CompanyLinksFooter, per shell's existing
// footer pattern ─────────────────────────────────────────────────────────

const publicLayout = readFileSync(
	new URL("../src/components/PublicLayout.jsx", import.meta.url),
	"utf8",
);
const layout = readFileSync(
	new URL("../src/components/Layout.jsx", import.meta.url),
	"utf8",
);
const landing = readFileSync(
	new URL("../src/components/Landing.jsx", import.meta.url),
	"utf8",
);
const css = readFileSync(new URL("../src/App.css", import.meta.url), "utf8");

test("PublicLayout: mounts the shared footer once for every public page (Landing, Architecture, not-found)", () => {
	assert.match(publicLayout, /import CompanyLinksFooter from ["']\.\/CompanyLinksFooter["']/);
	assert.match(publicLayout, /<footer className="public-footer">/);
	assert.match(publicLayout, /<CompanyLinksFooter \/>/);
	// The per-page duplicate in Landing.jsx must be gone — otherwise Landing
	// renders two footers while Architecture renders none, and the links
	// would only ever appear on one of the two public pages.
	assert.doesNotMatch(landing, /<footer className="public-footer">/);
});

test("Layout: mounts the shared footer at the bottom of every /app page", () => {
	assert.match(layout, /import CompanyLinksFooter from ["']\.\/CompanyLinksFooter["']/);
	assert.match(layout, /<footer className="app-footer">/);
	assert.match(layout, /<CompanyLinksFooter \/>/);
});

test("App.css: both footers style the shared link row with the existing icon-button idiom, not a new one", () => {
	assert.match(css, /^\.footer-icon-link \{/m);
	// Mirrors .topbar-icon-btn's hover/focus treatment (Layout.jsx's existing
	// icon idiom) rather than inventing new tokens.
	assert.match(
		css,
		/\.footer-icon-link:hover,\n\.footer-icon-link:focus-visible \{\n\tcolor: var\(--accent\);/,
	);
	assert.match(css, /^\.app-site \.app-footer \{/m);
});

// ── uno.config.js: dynamic icon classes must be safelisted or they silently
// never render — the icon key comes from a JS object, not a static string
// UnoCSS's scanner can see in the JSX. ─────────────────────────────────────

const unoConfig = readFileSync(new URL("../uno.config.js", import.meta.url), "utf8");

test("uno.config.js: every brand icon used in SOCIAL_LINK_META is safelisted", () => {
	for (const { icon } of Object.values(SOCIAL_LINK_META)) {
		assert.match(
			unoConfig,
			new RegExp(`'${icon}'`),
			`${icon} is not in uno.config.js's safelist — it would never render`,
		);
	}
});
