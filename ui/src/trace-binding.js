// Pure helpers for the Reasoning trace panel's two claim-integrity surfaces
// (#1359): the commit-reveal "block order" copy, and the "Verify hash
// on-chain" button's tri-state result. Dependency-free (no React, no
// ./config) so node --test can exercise it directly — same shape as
// wallet-providers.js.

// ── Block-order panel copy ──────────────────────────────────────────────
//
// Keys off `source` (the API's `temporal_binding_source`), NOT off a bare
// boolean. `TraceResponse` in backend/archimedes/api/schemas.py enforces
// that `temporal_binding_valid` can never be True unless
// `temporal_binding_source === "chain"` — so `source` is the only field
// that can honestly distinguish "the contract's commit()/reveal()/
// executeTrade() path actually ran" from "an off-chain record only".
//
// Before this fix the panel keyed off `temporal_binding_valid` alone and
// unconditionally rendered "(off-chain)" / "not yet enforced on-chain —
// commit-reveal wiring is [still] on the roadmap" copy. Because that copy only
// rendered when `temporal_binding_valid` was truthy, and truthy requires
// `source === "chain"`, the roadmap sentence was wrong 100% of the times a
// user could see it: the contract already enforces the ordering
// (`Vault.rebalance()` reverts without a matching earlier-block commitment
// — see `ReasoningTraceRegistry.executeTrade()`), and `/architecture` says
// so. `valid` is accepted for call-site symmetry with the trace object
// (`{ source: t.temporal_binding_source, valid: t.temporal_binding_valid }`)
// but is not branched on: once `source === "chain"`, the ordering guarantee
// is the contract's, not a computed boolean's.
export function blockOrderCopy({ source, valid: _valid }) {
  if (source === 'chain') {
    return {
      heading: 'Commit → trade → reveal (contract-enforced)',
      note: 'Vault.rebalance() reverts unless this commitment existed in an earlier block — enforced by ReasoningTraceRegistry.executeTrade(), not by our code being well-behaved.',
      tone: 'verified',
    }
  }
  return {
    heading: 'Block order (off-chain record)',
    note: 'Off-chain record only — this trace was anchored without the commit-reveal path, so the ordering is not contract-proven.',
    tone: 'unproven',
  }
}

// ── Verify-hash tri-state ────────────────────────────────────────────────
//
// Mirrors the backend's `TraceVerifyResponse.verification_mode`
// (hash_matched | anchored_only | failed — see traces_routes.py
// `verify_trace`). `anchored_only` is the honest name for "the trace is
// anchored on-chain but nothing was actually re-hashed and compared" — it
// must never render with the same affordance as a real hash match
// (anti-goal: "a hash that was never compared does not get the same
// affordance as one that matched"). Any unrecognised/missing mode
// (including the network-error fallback the UI constructs client-side,
// which never sets verification_mode) degrades to 'failed' rather than
// silently passing.
export function verificationTone(mode) {
  if (mode === 'hash_matched') return 'verified'
  if (mode === 'anchored_only') return 'anchored'
  return 'failed'
}

// ── Per-row anchoring state ──────────────────────────────────────────────
//
// Every surface that lists traces — Reasoning, the Portfolio activity feed,
// the strategy passport's Trading-decisions panel — has to answer "is this
// decision anchored?" for each row, and each one used to answer it with its
// own inline ternary on `is_verified`. Three copies of a claim is three
// chances for one of them to be wrong, and two of them already were:
//
//   * Both fell through to "anchor pending — registry write didn't complete
//     yet (usually transient)" for SKIP traces. A skip anchors nothing BY
//     DESIGN (#714): with no trade there is no tradeId for commit() to bind,
//     so the agent never attempts a registry write. "Pending" promises a
//     write that is never coming; the honest state is a permanent, explained
//     absence.
//   * Neither handled `verification_mode === "anchored_only"`, the projection
//     the API returns when an anchor exists but no off-chain body was there
//     to compare against (#1407). Both rendered it via `is_verified` — which
//     that path deliberately leaves true — as a plain green check, i.e. as a
//     hash match that never happened.
//
// Order is load-bearing. `anchored_only` is checked FIRST because that path
// sets `is_verified: true` on purpose (the anchor genuinely is confirmed; it
// is the *comparison* that didn't happen), so an `is_verified` check placed
// first would swallow it. The skip check comes after the anchored checks
// because a legacy publishTrace-fallback skip can carry a real `arc_tx_hash`
// — "no trade to bind" describes why an anchor is absent, and must not
// override one that is present.
//
// None of these labels claims a hash was compared. That claim belongs only to
// `GET /api/traces/{id}/verify` (see `verificationTone` above) and is only
// ever earned by clicking Verify.
export const ANCHOR_STATES = {
  anchored: {
    state: 'anchored',
    label: 'anchored on Arc',
    icon: 'i-lucide-check-circle',
    tone: 'verified',
    title:
      'The trace hash is anchored on Arc. That confirms the anchor exists, not that the stored body still hashes to it — use Verify to re-fetch the receipt and compare.',
  },
  anchored_unverified: {
    state: 'anchored_unverified',
    label: 'anchored — not re-hashed',
    icon: 'i-lucide-anchor',
    tone: 'anchored',
    title:
      'An anchor exists in the on-chain registry, but no off-chain trace body was stored to compare against it. Zero hashes were compared — this is not a verification.',
  },
  not_anchored_no_trade: {
    state: 'not_anchored_no_trade',
    label: 'not anchored (no trade to bind)',
    icon: 'i-lucide-skip-forward',
    tone: 'absent',
    title:
      'No trade was made, so there is nothing for an on-chain commitment to bind. The trace is hashed and persisted off-chain; no anchor is attempted or pending.',
  },
  anchor_pending: {
    state: 'anchor_pending',
    label: 'anchor pending',
    icon: 'i-lucide-clock',
    tone: 'pending',
    title:
      "Trace hashed + persisted off-chain; on-chain anchor pending (registry write didn't complete yet — usually transient).",
  },
}

export function anchorState(trace) {
  const t = trace || {}
  if (t.verification_mode === 'anchored_only') return ANCHOR_STATES.anchored_unverified
  if (t.arc_tx_hash || t.is_verified) return ANCHOR_STATES.anchored
  if (t.decision_type === 'skip') return ANCHOR_STATES.not_anchored_no_trade
  return ANCHOR_STATES.anchor_pending
}
