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
    : `${base} No further rows have been added since, and the recorded disagreement stands.`
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
  if (err.status === 404) return 'This deployment could not be found — it may already have been stopped or removed.'
  if (err.status != null) return 'Paper trading is temporarily unavailable — try again in a moment.'
  return err.message || fallback
}
