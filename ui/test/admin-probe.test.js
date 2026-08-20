import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
	_resetAdminProbeCache,
	ADMIN_PROBE_TTL_MS,
	getCachedAdminProbe,
} from "../src/adminProbeCache.js";

// ── getCachedAdminProbe: shared TTL cache backing fetchAdminProbe()
// (src/adminProbe.js) — same shape as healthCache.js's getCachedHealth
// (#1333) / agentStatusCache.js's getCachedAgentStatus (#1382 review).
// Owner directive 2026-08-20: /app/insights is admin-only, gated by
// GET /api/metrics/private/whoami; this cache is what lets Layout.jsx's Ops
// nav item and App.jsx's page gate share one probe per navigation.

test("getCachedAdminProbe: a second call within the TTL window reuses the first call's promise instead of probing again", async () => {
	_resetAdminProbeCache();
	let calls = 0;
	const fetcher = async () => {
		calls += 1;
		return { admin: true, wallet: "0xadmin" };
	};
	const p1 = getCachedAdminProbe(fetcher, 1_000);
	const p2 = getCachedAdminProbe(fetcher, 1_000 + ADMIN_PROBE_TTL_MS - 1);
	assert.equal(p1, p2);
	await p1;
	assert.equal(calls, 1);
});

test("getCachedAdminProbe: a call at/after the TTL window probes again", async () => {
	_resetAdminProbeCache();
	let calls = 0;
	const fetcher = async () => {
		calls += 1;
		return { admin: false, wallet: null };
	};
	await getCachedAdminProbe(fetcher, 1_000);
	await getCachedAdminProbe(fetcher, 1_000 + ADMIN_PROBE_TTL_MS);
	assert.equal(calls, 2);
});

test("getCachedAdminProbe: a rejected fetch is not cached — the very next call retries rather than reusing the rejection", async () => {
	_resetAdminProbeCache();
	let calls = 0;
	const failingFetcher = async () => {
		calls += 1;
		throw new Error("network down");
	};
	const okFetcher = async () => {
		calls += 1;
		return { admin: true, wallet: "0xadmin" };
	};
	await assert.rejects(getCachedAdminProbe(failingFetcher, 1_000));
	assert.equal(calls, 1);
	const result = await getCachedAdminProbe(okFetcher, 1_001);
	assert.equal(calls, 2);
	assert.deepEqual(result, { admin: true, wallet: "0xadmin" });
});

test("_resetAdminProbeCache: forces the next call to probe again even within the TTL window", async () => {
	_resetAdminProbeCache();
	let calls = 0;
	const fetcher = async () => {
		calls += 1;
		return { admin: false, wallet: null };
	};
	await getCachedAdminProbe(fetcher, 1_000);
	_resetAdminProbeCache();
	await getCachedAdminProbe(fetcher, 1_001); // well within TTL of the first call
	assert.equal(calls, 2);
});

// ── adminProbe.js: classifies apiGet's thrown Error shape (err.status set
// on every non-2xx HTTP response, per api.js) into an authoritative
// {admin:false} vs. a genuine network failure that must NOT be cached. ────

const adminProbeSrc = readFileSync(
	new URL("../src/adminProbe.js", import.meta.url),
	"utf8",
);

test("adminProbe.js: an authoritative 401/403 (err.status set) resolves to {admin:false}, not a rethrow", () => {
	assert.match(adminProbeSrc, /typeof err\.status === "number"/);
	assert.match(adminProbeSrc, /return \{ admin: false, wallet: null \}/);
});

test("adminProbe.js: a genuine network/parse failure (no err.status) is rethrown so adminProbeCache does not cache it", () => {
	// Mutation-check (CLAUDE.md § "Before you approve a merge", rule 4): if
	// this rethrow were replaced with another `return {admin:false,...}`,
	// EVERY failure (including a transient outage) would get cached as a
	// definitive "not admin" for the full TTL window, hiding a real admin's
	// nav item / page after a blip until the cache naturally expires.
	// Verified by temporarily changing the `throw err` line to
	// `return { admin: false, wallet: null }` and re-running this file
	// alone, which reproduced exactly the failure this assertion exists to
	// catch (the regex below stopped matching).
	assert.match(adminProbeSrc, /\n\t\tthrow err;\n/);
});

test("adminProbe.js: fetchAdminProbe never rejects — a non-admin/anonymous caller and a network failure both resolve to {admin:false}", () => {
	assert.match(
		adminProbeSrc,
		/getCachedAdminProbe\(_fetchWhoami\)\.catch\(\(\) => \(\{ admin: false, wallet: null \}\)\)/,
	);
});

test("adminProbe.js probes the private whoami endpoint, not the public metrics surface", () => {
	assert.match(adminProbeSrc, /apiGet\("\/api\/metrics\/private\/whoami"\)/);
});

// ── Wiring: App.jsx's page gate + Layout.jsx's nav gate both go through the
// shared fetchAdminProbe(), never a bespoke/duplicated check. ─────────────

const app = readFileSync(new URL("../src/App.jsx", import.meta.url), "utf8");
const layout = readFileSync(
	new URL("../src/components/Layout.jsx", import.meta.url),
	"utf8",
);
const notFoundSrc = readFileSync(
	new URL("../src/components/NotFound.jsx", import.meta.url),
	"utf8",
);
const insights = readFileSync(
	new URL("../src/components/Insights.jsx", import.meta.url),
	"utf8",
);

test("App.jsx: the insights page gate calls the shared fetchAdminProbe(), not a bespoke apiGet call", () => {
	assert.match(app, /from '\.\/adminProbe\.js'/);
	assert.match(app, /fetchAdminProbe\(\)\.then\(\(\{ admin \}\) => \{/);
	assert.doesNotMatch(
		app,
		/apiGet\(['"]\/api\/metrics\/private\/whoami['"]\)/,
		"App.jsx must not call the whoami endpoint directly — it has to go through the shared cache",
	);
});

test("App.jsx: a denied insights probe renders the SAME NotFound component as a true 404 — not a second, divergent 'access denied' block", () => {
	assert.match(app, /import NotFound from '\.\/components\/NotFound'/);
	// Both branches return the identical <NotFound user={user} /> call —
	// counting occurrences (rather than matching each branch separately)
	// is what actually proves they're the same component, not two markup
	// blocks that happen to render similarly today and can drift apart.
	const matches = app.match(/return <NotFound user=\{user\} \/>/g) || [];
	assert.equal(
		matches.length,
		2,
		"expected exactly 2 call sites: the true not-found branch and the denied-insights branch",
	);
});

test("App.jsx: an anonymous visitor hitting /app/insights is never redirected to /sign-in (that would advertise the page exists)", () => {
	assert.match(
		app,
		/route\.anonymousOk \|\| route\.page === 'insights' \|\| authLoading \|\| user\) return/,
	);
});

test("Layout.jsx: the Ops nav item is filtered on the shared admin probe, defaulting closed", () => {
	assert.match(layout, /from "\.\.\/adminProbe\.js"/);
	assert.match(layout, /const \[isInsightsAdmin, setIsInsightsAdmin\] = useState\(false\)/);
	assert.match(
		layout,
		/item\.id !== "insights" \|\| isInsightsAdmin/,
	);
});

test("Layout.jsx: an anonymous visitor (no user) never even attempts the admin probe", () => {
	assert.match(layout, /if \(!userId\) \{\n\t\t\tsetIsInsightsAdmin\(false\);\n\t\t\treturn;\n\t\t\}/);
});

test("NotFound.jsx exists and is the single source of the 'does not exist' treatment", () => {
	assert.match(notFoundSrc, /Page not found/);
	assert.match(notFoundSrc, /export default function NotFound/);
});

test("Insights.jsx documents the admin-only gate and the #1028 D8 supersession", () => {
	assert.match(insights, /ADMIN-ONLY/);
	assert.match(insights, /SUPERSEDES issue #1028 D8/);
});

test("Insights.jsx loads the new admin-only engagement endpoint alongside the public aggregates", () => {
	assert.match(insights, /apiGet\('\/api\/metrics\/private\/engagement'\)/);
	// Public aggregate endpoints stay untouched — the gate moved on the page
	// and the router, not on these.
	assert.match(insights, /apiGet\('\/api\/metrics'\)/);
	assert.match(insights, /apiGet\('\/api\/metrics\/funnel'\)/);
	assert.match(insights, /apiGet\('\/api\/metrics\/visitors'\)/);
});

test("Insights.jsx never claims a settled payment volume outside the dry-run note (claims-must-be-true)", () => {
	assert.match(insights, /engagement\.payments\?\.settled_volume_usd == null \? '—'/);
});
