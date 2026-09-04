// Pure, framework-free copy + formatters for the Paper Trading page
// (PaperTrading.jsx). Extracted so the DRIFT tooltip, the total-return
// formatter, and the error-message mapping are unit-testable without a DOM —
// the pattern ui/src/generateQuote.js and ui/src/password-rules.js already
// establish (see ui/test/generate-quote.test.js, ui/test/password-rules.test.js).
//
// Every export here exists to fix a specific honesty bug (#1362):
//   - driftTooltip: the old inline string promised a freeze/investigation
//     that never happens while a deployment is ACTIVE — advance_all
//     (paper_trading.py) filters on STATUS_ACTIVE and never consults
//     drift_detected_at, so an active, drifted ledger keeps appending. But
//     drift_detected_at is never cleared, so the same chip can still be
//     showing on a STOPPED deployment, where the record genuinely IS
//     frozen — the tooltip must not claim it "keeps advancing" there
//     either. It gates its closing clause on `status` for that reason, and
//     must never interpolate the raw machine timestamp paper_trading.py's
//     deployment_summary emits (`drift_detected_at.isoformat()`) into
//     English prose.
//   - formatTotalReturn: deployment_summary's `total_return` is a real
//     `0.0` (not null) at day 0 — the OLD `pct()` rendered that as a
//     measured-looking "+0.00%". Day 0 is the normal state right after
//     deploy, not an edge case; the discriminator is `days`, never the
//     value, so a genuinely measured zero at day N still prints.
//   - paperErrorMessage: api.js's apiGet/apiPost throw
//     `Error("Backend returned ${status}")` on any non-2xx — that literal
//     string must never reach the `role="alert"` card verbatim. Mirrors
//     the status -> sentence mapping StrategyPassport.jsx's PaperDeployCard
//     already established for the sibling paper CTA.

/**
 * Render a `drift_detected_at` ISO timestamp as a plain human date, in UTC
 * so the output is independent of the caller's local timezone (Node's
 * `node --test` and the browser must agree). Never throws on a malformed
 * input — falls back to a neutral phrase rather than rendering "Invalid
 * Date" or crashing the tooltip.
 */
function formatDriftDate(driftAtIso) {
  const d = new Date(driftAtIso)
  if (Number.isNaN(d.getTime())) return 'an earlier date'
  return d.toLocaleDateString('en-US', { timeZone: 'UTC', year: 'numeric', month: 'short', day: 'numeric' })
}

/**
 * The DRIFT chip's tooltip. States what actually happens when a fresh
 * replay disagrees with rows already written: the ledger is append-only and
 * was NOT rewritten (mirrors the backend's own warning in
 * `paper_trading.py:advance_deployment`) — never a promise of a halt or an
 * investigation, which only the Stop path (PaperTrading.jsx's `stop()`) has
 * actually earned. `drift_detected_at` is overwritten on every recurrence
 * (paper_trading.py:158), so this reads as the MOST RECENT disagreement,
 * not the first.
 *
 * `status` gates the closing clause, because "keeps advancing" is only true
 * while the deployment is active: `advance_all` (paper_trading.py:173)
 * filters on `STATUS_ACTIVE`, so a STOPPED deployment does not advance —
 * Stop (paper_routes.py:142) is the one path that genuinely halts it, and
 * `drift_detected_at` is never cleared, so the chip can still be showing on
 * a stopped row. Pass the same `status` the STOPPED/ACTIVE pill renders
 * from; anything other than `'active'` gets the stopped-true clause.
 */
export function driftTooltip(driftAtIso, status) {
  const when = formatDriftDate(driftAtIso)
  const base =
    `A fresh replay disagreed with rows already recorded, most recently on ${when}. ` +
    'The ledger is append-only and was not rewritten — the discrepancy is surfaced, not hidden.'
  return status === 'active'
    ? `${base} The track record keeps advancing.`
    : `${base} No rows have been added since the deployment was stopped, and the recorded disagreement stands.`
}

/**
 * `deployment_summary.total_return` formatted for the headline figure.
 * Returns '—' when there is nothing measured yet: `days === 0` (the normal
 * state right after deploy — `replay_spec` only emits dates
 * `>= deployed_at`, so a same-day deploy legitimately has zero rows) or the
 * value itself is missing/NaN. Otherwise renders today's signed percentage
 * — including a genuinely measured `0.0` at day N, which is a fact and must
 * print, never suppressed just because the number is zero. The gate is
 * `days`, never the value.
 */
export function formatTotalReturn(totalReturn, days) {
  if (days === 0 || totalReturn == null || Number.isNaN(totalReturn)) return '—'
  return `${totalReturn >= 0 ? '+' : ''}${(totalReturn * 100).toFixed(2)}%`
}

/**
 * Map an api.js error (apiGet/apiPost — `err.status` set, `err.message`
 * always the literal `Backend returned ${status}`) to a human sentence,
 * mirroring StrategyPassport.jsx's PaperDeployCard mapping. Never falls
 * back to `err.message` once `err.status` is set — that message is always
 * the raw "Backend returned NNN" string and must never reach the
 * `role="alert"` card verbatim. `fallback` is used only for a status-less
 * error (a genuine network failure, where `err.message` — e.g. "Failed to
 * fetch" — is actually informative) or a missing error object.
 */
export function paperErrorMessage(err, fallback = 'Something went wrong.') {
  if (!err) return fallback
  if (err.status === 401) return 'Your session expired — sign in again to see your paper deployments.'
  if (err.status === 404) return 'This deployment is no longer available on your account — reload the list.'
  if (err.status != null) return 'Paper trading is temporarily unavailable — try again in a moment.'
  return err.message || fallback
}

// ── Intraday marks (design §5.1) ─────────────────────────────────────────────
//
// A mark is a re-PRICING of the ASSET BASKET the daily replay established —
// not a re-decision, and NOT a claim about what the strategy is holding right
// now (see MARK_BASIS_DISCLOSURE: v1 has no position vector, so a strategy
// sitting in cash is still marked as if invested). The settled daily ledger is
// the paper track record — Arc testnet, no real funds (#1807); a mark is an
// unsettled decoration the backend deletes past 90 days.
// Every helper below exists so the card can never state more than that:
//
//   - markLabel: never a bare number. Always value + as-of time, and the word
//     "delayed" whenever the row says so. `is_delayed` is a STORED column set
//     by the fetch path from what the provider declares — this function reads
//     that fact, it does not infer delay from a timestamp.
//   - The existence gate is `mark == null`, never the mark's value — the same
//     discriminator lesson as formatTotalReturn's `days`: a genuinely marked
//     flat 0.00% is a measurement and must print, while an absent mark must
//     never be dressed as "+0.00%".
//   - marksStalenessNote: a frozen number must read as "last marked Friday
//     16:00", not as a broken ticker. This is the #1378 shape — a time-labelled
//     number going stale across a weekend/gap — so the note states the OBSERVED
//     age and never asserts a market state ("closed", "halted") the client has
//     no way to know.
//   - markBasisNote / MARK_BASIS_DISCLOSURE: the v1 limitation, disclosed AT
//     THE POINT OF RENDER rather than only in docs — see below.

/** HH:MM in UTC for a mark's `ts`, or null if the timestamp is unusable.
 * Never renders "Invalid Date" and never throws — same defensive contract as
 * formatDriftDate. */
function formatUtcTime(iso) {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  return d.toLocaleTimeString('en-GB', { timeZone: 'UTC', hour: '2-digit', minute: '2-digit' })
}

/** "Fri 16:00" in UTC, for a mark old enough that the day matters. */
function formatUtcDayTime(iso) {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  const day = d.toLocaleDateString('en-US', { timeZone: 'UTC', weekday: 'short' })
  return `${day} ${formatUtcTime(iso)}`
}

/**
 * The live-value line: `+0.42% · as of 14:45 UTC · delayed`.
 *
 * `portfolio_value` is an INDEX with 1.0 == deploy-time capital (there is no
 * deployed-capital amount anywhere in the system, so a dollar figure would be
 * invented), which makes the percentage `value - 1`.
 *
 * Returns '—' only when there is no mark at all, or when the mark carries no
 * usable value or timestamp — because a value without its as-of time is
 * exactly the bare number §2.4 rule 3 forbids, and half a claim is worse than
 * none. The gate is never the value itself.
 */
export function markLabel(mark) {
  if (!mark) return '—'
  const value = mark.portfolio_value
  if (value == null || Number.isNaN(value)) return '—'
  const at = formatUtcTime(mark.ts)
  if (!at) return '—'
  const pct = (value - 1) * 100
  const signed = `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`
  return `${signed} · as of ${at} UTC${mark.is_delayed ? ' · delayed' : ''}`
}

/**
 * A screen-reader-friendly restatement of the same line. The number updates
 * silently inside a live region otherwise, which is invisible to a screen
 * reader (4.1.3) — the as-of time belongs in the accessible name, not only in
 * the visual glyphs.
 */
export function markAnnouncement(mark) {
  if (!mark) return ''
  const label = markLabel(mark)
  if (label === '—') return ''
  const at = formatUtcTime(mark.ts)
  const pct = ((mark.portfolio_value - 1) * 100).toFixed(2)
  const delayed = mark.is_delayed ? ', from a delayed feed' : ''
  return `Live value ${pct} percent, as of ${at} UTC${delayed}. Unsettled — the daily ledger is the track record.`
}

/**
 * A note when the newest mark has stopped moving — null while it is fresh.
 *
 * "Fresh" is two cadence intervals (`intervalMinutes`, default 15): one missed
 * tick is a hiccup, two is a state worth naming. Equities have market hours and
 * crypto does not, so an equity deployment's value is GENUINELY frozen
 * overnight and at weekends; the note makes that read as an observation age
 * rather than as a broken ticker. It deliberately does NOT say "market closed"
 * — the client cannot observe that, and #1378 is exactly the defect of
 * labelling a gap with a window nobody measured.
 */
export function marksStalenessNote(mark, now = Date.now(), intervalMinutes = 15) {
  if (!mark) return null
  const t = new Date(mark.ts).getTime()
  if (Number.isNaN(t)) return null
  const ageMinutes = (now - t) / 60000
  if (ageMinutes < intervalMinutes * 2) return null
  const when = formatUtcDayTime(mark.ts)
  return when ? `Last marked ${when} UTC — no newer price has been observed since.` : null
}

/**
 * The no-marks-yet state's reason. A deployment created between ticks, or one
 * on SPY before the session opens, legitimately has zero marks — a real state,
 * not an error, and one that renders as an em-dash WITH this reason rather
 * than as a measured-looking +0.00%.
 *
 * Gated on `status` for the same reason driftTooltip is: the marks loop filters
 * on STATUS_ACTIVE, so a stopped deployment will never get a mark and telling
 * its owner to wait for one would be false.
 */
export function noMarksNote(status) {
  return status === 'active'
    ? 'No live value yet — the first intraday mark lands at the next 15-minute tick.'
    : 'No live value — marks stop when a deployment is stopped.'
}

/**
 * THE DISCLOSED v1 LIMITATION, in the two places a reader can actually meet
 * it: the page intro and the live-value line itself.
 *
 * A mark re-prices the strategy's ASSET BASKET — the sleeve weights the last
 * daily settle established — by applying each asset's move since that settle.
 * It does NOT know whether the strategy is currently invested or sitting in
 * CASH: the backend's `replay_spec` returns dated portfolio returns, not a
 * per-sleeve invested/flat vector, so there is no position vector to read and
 * inferring one from the return series would be a guess dressed as a
 * measurement.
 *
 * The consequence is user-visible and must be stated, not implied: a strategy
 * that is flat can show a live value that moves. A settled +0.00% day can sit
 * under a +10.00% mark if the underlying rose 10% (the backend pins that exact
 * fixture in test_a_cash_sleeve_is_still_marked_as_if_invested).
 *
 * The earlier copy here said a mark "re-prices that same position", which is
 * precisely the claim v1 cannot make — it re-prices the BASKET, and the
 * position may be cash. That wording is what this constant replaces.
 *
 * Kept as an exported constant rather than inlined in the JSX so the wording
 * is pinned by a test: the honesty claim is the product, and a silent edit to
 * it should fail a build, not ship.
 */
export const MARK_BASIS_DISCLOSURE =
  "Marks re-price the strategy's asset basket and do not know whether the strategy is currently in cash — " +
  'a flat strategy can still show a live value that moves. The daily settle is the honest number.'

/**
 * The short form of the same disclosure, for the live-value line under a
 * single deployment's total return, where the long sentence does not fit.
 * Says the same thing — never a softer version of it.
 */
export const MARK_BASIS_SHORT = 'basket re-priced · cash not modelled'

/**
 * The disclosure to render beside a live value. Returns the short form when
 * there is a value on screen to qualify, and `null` when there is not: a
 * limitation notice attached to an em-dash qualifies nothing and just adds
 * noise to the one state that is already fully honest.
 */
export function markBasisNote(mark) {
  return mark && markLabel(mark) !== '—' ? MARK_BASIS_SHORT : null
}

/**
 * The marks-fetch-failure state. The deployment card itself loaded fine; only
 * the live value is missing — a partial failure the card must state rather
 * than paper over. Routes through paperErrorMessage so a raw
 * "Backend returned 503" can no more reach this line than the main error card,
 * and says the value is UNAVAILABLE rather than showing the last mark it
 * happened to hold: a stale number under a fresh-looking label is the same
 * defect as writing a duplicated stale row, just in the UI.
 */
export function marksUnavailableNote(err) {
  return `Live value unavailable — ${paperErrorMessage(err, 'the intraday feed could not be reached.')}`
}
