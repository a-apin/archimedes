// The Library's status pill — class and label — in ONE importable module.
//
// #1747. These two functions used to be module-private in Strategies.jsx, so
// nothing under ui/test/ could execute them (`.jsx` is not importable under
// `node --test`, see ui/test/account-management.test.js) and the pill was
// unpinned by any assertion. The rule they encode is a CLAIM about a strategy —
// green means "this passed the rigor gate" — which makes it exactly the kind of
// thing that must be tested by running it, not by reading the source.
//
// The rule, stated once:
//
//   A green pill requires an AFFIRMATIVE gate pass. It is not enough for the
//   row's admin `status` to say "live". `status` on a generated row is written
//   from the GENERATION-TIME fusion verdict and never re-derived after a
//   backtest (backend/archimedes/models/strategy_store.py couples
//   `status="live"` to `rigor_verdict["passing"]` on the same write), so before
//   #1747 the Library's `status === 'live'` and its `passes_rigor_gate === true`
//   were the same fact wearing two names — and the demotion arm below could
//   never fire on the Generated tab. The backend now serves a LIVE four-state
//   `rigor_gate_status` on those rows; this module consumes it.
//
// Four states, four different sentences. The one thing none of them may do is
// claim something a gate did not say:
//
//   pass        → green "Live"                        (a gate ran and passed)
//   fail        → muted "Reference only — gate failed" (a gate ran and failed)
//   pending     → muted "Not yet graded"               (no gate ran — NOT a failure)
//   degenerate  → muted "Unevaluable — flat returns"   (a gate ran; the series
//                                                       had no variance to grade)
//
// `pending` deliberately does NOT get the "gate failed" label. Widening the
// boolean test from `=== false` to `!== true` and leaving one label would have
// been the smaller diff, and it would have put "Reference only — gate failed"
// on the same row as the clock icon titled "Not yet evaluated — no backtest data
// for the rigor gate to score" (Strategies.jsx) — one row asserting two
// contradictory things, the failing one about a verdict nothing produced. That
// is the #1358 defect class, re-committed.

/** The demotion label, byte-identical to the literal in
 *  ui/src/components/StrategyPassport.jsx.
 *
 *  The passport is NOT refactored onto this module: its `statusTag`/
 *  `statusLabel` are near-duplicates, not duplicates — it maps `validated` to
 *  green (the Library maps it to accent), maps `rejected`/`retired` to muted,
 *  defaults unknown statuses to accent rather than muted, and has no
 *  `pending_backtest` arm at all. Collapsing two different rules into one
 *  "shared" function would have changed the passport's rendering as a side
 *  effect of a Library fix. What MUST agree between the two surfaces is this
 *  sentence, and ui/test/library-status.test.js pins that by reading the
 *  passport's source. */
export const GATE_FAILED_LABEL = "Reference only — gate failed";

/** Ungraded, and saying so. Owner may prefer different copy — this is the
 *  wording defaulted in #1747, not a decided product string. What it may not go
 *  back to being is "Live" (a claim) or "gate failed" (a different claim). */
export const NOT_GRADED_LABEL = "Not yet graded";

/** #1184's fourth state: a persisted return series with zero variance — broken
 *  data or a zero-trade backtest. Not "pending" (it WAS evaluated) and not
 *  "failed" (there was nothing to grade). */
export const DEGENERATE_LABEL = "Unevaluable — flat returns";

/** Did a rigor gate affirmatively PASS this row?
 *
 * When the four-state verdict is on the wire it is the whole answer — including
 * when it disagrees with the boolean, in which case the four-state wins and the
 * row is not green. `null`/`undefined` means this payload carries no four-state
 * (a caller or a build that predates it), and only then does the fail-closed
 * boolean decide; `null` there is "no verdict", which is not a pass.
 */
export function isGatePass(passesRigor, rigorGateStatus) {
	if (rigorGateStatus != null) return rigorGateStatus === "pass";
	return passesRigor === true;
}

/** Pill CSS class for a library row. */
export function statusTag(status, passesRigor, rigorGateStatus) {
	// The only green-producing branch, and it is gated on an affirmative pass.
	// Note this is NOT `passesRigor === false`: a row whose verdict never
	// arrived has nothing to be green about either.
	if (status === "live" && !isGatePass(passesRigor, rigorGateStatus)) return "tag-muted";
	if (status === "live") return "tag-positive";
	if (status === "validated") return "tag-accent";
	if (status === "pending_backtest") return "tag-warning";
	return "tag-muted";
}

/** Pill text for a library row. */
export function statusLabel(status, passesRigor, rigorGateStatus) {
	if (status === "live" && !isGatePass(passesRigor, rigorGateStatus)) {
		// Order matters: each arm names what actually happened, so the most
		// specific non-verdict is answered before the generic failure.
		if (rigorGateStatus === "degenerate") return DEGENERATE_LABEL;
		if (rigorGateStatus === "fail" || passesRigor === false) return GATE_FAILED_LABEL;
		// "pending", and the no-four-state/no-boolean row: nothing graded this.
		return NOT_GRADED_LABEL;
	}
	if (status === "pending_backtest") return "Pending Backtest";
	if (!status) return "Candidate";
	return status.charAt(0).toUpperCase() + status.slice(1);
}
