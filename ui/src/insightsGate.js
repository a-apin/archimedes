// Pure decision logic for the /app/insights admin-only gate (owner directive
// 2026-08-20, supersedes #1028 D8) — extracted out of App.jsx/Layout.jsx so
// the decisions the gate actually makes are unit-testable with real inputs
// under bare `node --test`, not merely asserted on source text.
//
// Round-3 review finding: every prior test on this gate was a readFileSync +
// regex match against App.jsx/Layout.jsx's source. None of them executed the
// line that actually consumes a probe result — `setIsInsightsAdmin(admin)`
// at Layout.jsx, `setInsightsAdmin(admin)` at App.jsx — so a mutation
// collapsing either call to `setIsInsightsAdmin(true)` left every one of
// those regexes matching while every signed-in non-admin got the Ops nav
// item and the live dashboard. Routing every consumer of a probe result
// through resolveInsightsAdminState() below, and every render/nav decision
// through isInsightsPageBlocked()/filterInsightsNavItem(), means a mutation
// at any of those three points now has a real behavioral test to fail.
//
// Deliberately zero imports, matching adminProbeCache.js's own "stays
// unit-testable" discipline.

/**
 * Reduces a raw fetchAdminProbe() result to the boolean the gate state
 * (`insightsAdmin` / `isInsightsAdmin`) should hold. This is the ONLY place
 * either caller should read `.admin` off a probe result — inlining
 * `setIsInsightsAdmin(admin)` at the call site is exactly the pattern that
 * silently degrades to `setIsInsightsAdmin(true)` under a typo/mutation with
 * no observable difference until a non-admin wallet loads the page.
 * @param {{admin: boolean, wallet: string|null}} probeResult
 * @returns {boolean}
 */
export function resolveInsightsAdminState(probeResult) {
	return probeResult?.admin === true;
}

/**
 * Should /app/insights render the not-found treatment instead of the
 * dashboard (and should the tab title read as not-found rather than
 * "Insights · Archimedes")? True whenever the gate has not affirmatively
 * resolved `admin === true` — this covers an authoritative denial (`false`)
 * AND an unresolved probe (`null`, still in flight) alike.
 *
 * Round-3 fix: the unresolved case used to render a neutral "Loading…"
 * screen and title the tab "Insights · Archimedes" — both of which a
 * genuinely unknown route never produces on first paint, a real disclosure
 * vector the in-file "does not advertise the page exists" claim did not
 * account for. Treating "unresolved" the same as "denied" here, for both the
 * title and the render branch, closes it: a many-tabs user (or anyone
 * inspecting first paint) can no longer distinguish "probing" from "denied"
 * from "truly unknown route".
 *
 * Returns `false` for any route other than `insights` — nothing to block.
 * @param {string|null} routePage
 * @param {boolean|null} insightsAdmin
 * @returns {boolean}
 */
export function isInsightsPageBlocked(routePage, insightsAdmin) {
	return routePage === "insights" && insightsAdmin !== true;
}

/**
 * Filters the Ops nav group's items down to the ones a visitor with the
 * given admin-gate state should see — i.e. drops the `insights` item unless
 * the probe has affirmatively resolved `true`. Every other item passes
 * through unchanged.
 * @param {Array<{id: string}>} items
 * @param {boolean} isAdmin
 * @returns {Array<{id: string}>}
 */
export function filterInsightsNavItem(items, isAdmin) {
	return items.filter((item) => item.id !== "insights" || isAdmin === true);
}
