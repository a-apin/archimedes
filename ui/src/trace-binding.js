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
