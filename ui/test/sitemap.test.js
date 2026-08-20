import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

// Guard: the admin-only /app/insights page must never appear in the public
// sitemap (round 4 review finding). Insights.jsx is gated server-side
// (GET /api/metrics/private/whoami) and, on the frontend, a non-admin visitor
// gets the EXACT SAME NotFound treatment an unknown route gets
// (ui/src/App.jsx / ui/src/components/NotFound.jsx) — the whole design goal
// is that the page "does not exist" for anyone who isn't an admin. A sitemap
// entry defeats that on its own: it hands every crawler, and anyone who
// simply reads /sitemap.xml, the one URL the NotFound treatment exists to
// keep undiscovered. The entry survived the admin-gate PR itself (rounds
// 1-3) because nothing checked the sitemap — this is that check.
//
// ui/scripts/check-sitemap.mjs (a more general "no gated /app page in the
// sitemap" checker) does not exist on this branch — it landed on `main`
// after this PR's branch point, and this fix round intentionally does not
// rebase 100+ commits of unrelated `main` history onto a PR that is still
// mid-review. This test is the narrower, PR-scoped equivalent: it proves the
// one property this PR's own claims depend on (insights is undiscoverable),
// not the general policy. Whoever rebases this branch before merge should
// confirm check-sitemap.mjs, once present, also passes and consider folding
// this file into it.

const sitemapPath = new URL("../public/sitemap.xml", import.meta.url);
const sitemapSrc = readFileSync(sitemapPath, "utf8");

// The whole FILE is the property, not just the <loc> URLs: sitemap.xml is
// publicly served byte-for-byte, so an XML comment naming the page, its
// path, or its gate mechanism discloses exactly what the NotFound treatment
// exists to hide — to any human who reads /sitemap.xml, which is precisely
// the audience a crawler-only check ignores. (An earlier revision kept an
// explanatory comment in the sitemap and scoped this test to <loc> values
// to accommodate it; that inverted the priority. The rationale lives HERE,
// in a file that is never served.)
const locs = [...sitemapSrc.matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => m[1]);

test("sitemap.xml never advertises the admin-only page — anywhere in the file", () => {
	assert.doesNotMatch(
		sitemapSrc,
		/insights/i,
		"the publicly served sitemap must not name the admin-only page, in a URL, comment, or otherwise",
	);
});

test("sitemap <loc> URLs contain no gated route", () => {
	assert.ok(locs.length > 0, "sitemap parsed to zero <loc> entries — parser or file is broken");
	for (const loc of locs) {
		assert.doesNotMatch(loc, /insights/i, `unexpected gated URL in sitemap: ${loc}`);
	}
});

test("sitemap.xml still lists real public URLs (anti-vacuity)", () => {
	// A guard that also passes against an accidentally emptied sitemap (e.g.
	// a bad merge truncating the file to a bare <urlset/>) proves nothing.
	// Pin real, expected entries so the check above can't be vacuously true.
	assert.ok(locs.length >= 4, `expected several <loc> entries, got ${locs.length}`);
	assert.ok(locs.includes("https://archimedes-arc.com/"));
	assert.ok(locs.includes("https://archimedes-arc.com/explore"));
});
