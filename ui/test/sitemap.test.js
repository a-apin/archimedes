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

// Deliberately scoped to the <loc> values, not the raw file text: the header
// comment (explaining WHY insights is excluded) legitimately says the word
// "insights" several times, so a whole-file substring check would either
// false-positive on the comment or have to special-case it — checking only
// the actual URLs is both the real property that matters and immune to that.
const locs = [...sitemapSrc.matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => m[1]);

test("sitemap.xml never advertises the admin-only /app/insights page", () => {
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
