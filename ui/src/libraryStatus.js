// The strategy-row status pill, in one place.
//
// `statusTag` (which CSS class) and `statusLabel` (which words) used to live as
// module-private copies inside `components/Strategies.jsx` — one pair driving
// the desktop table row and the mobile library card. They are here now for two
// reasons, and the second is the load-bearing one:
//
//   1. `.jsx` is not importable under `node --test` (see
//      ui/test/account-management.test.js), so a pill rendered from a helper
//      inside a `.jsx` file can only ever be guarded by asserting on its source
//      TEXT. A plain `.js` module can be imported and its actual behaviour
//      asserted — which is what ui/test/library-status.test.js now does.
//   2. These helpers decide whether a strategy renders GREEN. Before the
//      verdict of record (docs/adr/rigor-verdict-of-record.md) the Library's
//      Generated tab fed them a `status` and a `passes_rigor_gate` that were
//      BOTH derived from the same generation-time fusion blob, so the one
//      condition that demotes a row — "status says live, the gate says fail" —
//      could not occur on that tab at all. Twenty-one rows rendered "Live ✓"
//      beside their own passports reading "Reference only — gate failed".
//
// The fix is not a wider boolean check. `passesRigor !== true` would paint an
// UNGRADED row as a failure, which is the mirror-image lie (#1358: nothing
// failed, nothing ran) and would contradict the clock icon the same row already
// renders. These helpers consume the four-state `rigor_gate_status` instead —
// the same four words `ui/src/rigorGateStatus.js` lists and the API has served
// since #1184 — so each state gets its own answer.

import { RIGOR_GATE_STATES } from "./rigorGateStatus.js";

/** The demotion label, shared so the Library and the Passport cannot drift.
 *
 * Both surfaces render this exact string for the same row; when they were two
 * literals in two files, "byte-identical" was a convention a one-character edit
 * could break silently. It is a constant now, imported by both.
 */
export const GATE_FAILED_LABEL = "Reference only — gate failed";

/** What an ungraded strategy says. NOT "gate failed" — no gate ran. */
export const NOT_GRADED_LABEL = "Not yet graded";

/** What a zero-variance persisted series says (#1184's fourth state).
 *
 * Distinct from both: "pending" would claim nothing was evaluated (false — the
 * returns are there, they are just flat), "fail" would claim the strategy was
 * graded and lost (also false — a constant series was never a legitimate
 * DSR/OOS input).
 */
export const DEGENERATE_LABEL = "Unevaluable — flat returns";

/** True when this row carries no verdict at all.
 *
 * Two shapes mean the same thing and both must be caught: an explicit
 * `rigor_gate_status === "pending"`, and a row whose `passes_rigor_gate` is
 * null/undefined (never graded, so the API sends no boolean — see
 * `_UNGRADED_VERDICT_FIELDS` in backend/archimedes/api/strategies_routes.py).
 * A row carrying a KNOWN non-pending status is not ungraded even if some other
 * field is missing.
 */
function isUngraded(passesRigor, rigorGateStatus) {
	if (rigorGateStatus === "pending") return true;
	if (RIGOR_GATE_STATES.includes(rigorGateStatus)) return false;
	return passesRigor == null;
}

/** CSS class for the status pill.
 *
 * `tag-positive` (green) is reachable from exactly one place: an admin/store
 * status of "live" on a row whose gate verdict is a literal `true`. Every other
 * arm is deliberately non-green.
 */
export function statusTag(status, passesRigor, rigorGateStatus) {
	if (rigorGateStatus === "degenerate") return "tag-muted";
	if (isUngraded(passesRigor, rigorGateStatus)) return "tag-muted";
	if (status === "live" && passesRigor === false) return "tag-muted";
	if (status === "live") return "tag-positive";
	if (status === "validated") return "tag-accent";
	if (status === "pending_backtest") return "tag-warning";
	return "tag-muted";
}

/** Words for the status pill. Same arm order as `statusTag` — they must agree. */
export function statusLabel(status, passesRigor, rigorGateStatus) {
	if (rigorGateStatus === "degenerate") return DEGENERATE_LABEL;
	if (isUngraded(passesRigor, rigorGateStatus)) return NOT_GRADED_LABEL;
	if (status === "live" && passesRigor === false) return GATE_FAILED_LABEL;
	if (status === "pending_backtest") return "Pending Backtest";
	if (!status) return "Candidate";
	return status.charAt(0).toUpperCase() + status.slice(1);
}
