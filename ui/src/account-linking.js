// Pure helpers for the #1420 follow-up (explicit account linking, "Account
// Settings → Connected accounts"). Kept out of AccountSettings.jsx so they
// are directly unit-testable under plain `node --test` — .jsx files aren't
// importable there (no JSX transform in the test runner), only readable as
// source text. See ui/test/account-settings.test.js.

const CONNECTED_PROVIDER_LABELS = { credential: 'Email & password', google: 'Google', github: 'GitHub' }

export function connectedProviderLabel(providerId) {
  return CONNECTED_PROVIDER_LABELS[providerId] || providerId
}

// Round-2 review finding (minor): the `?linked=` success notice used to
// render `connectedProviderLabel(linked)` straight off the URL with no
// check at all, so an arbitrary/unrecognized value came back out verbatim
// ("Linked anything.") instead of being treated as untrusted input. Round 2
// introduced `isKnownConnectedProvider` (true for any of the three real
// provider ids, including 'credential') to gate it; round 3 found that too
// permissive for the `?linked=` toast specifically (see isLinkableProvider
// below) and moved the gate there instead. Round-4 review finding (minor):
// once isLinkableProvider took over the one real call site, this predicate
// had zero consumers left anywhere in the tree (verified: `grep -rn
// isKnownConnectedProvider ui/src ui/test` before removal matched only this
// file's own definition and comments) — dead code, removed rather than kept
// "for completeness."
//
// Round-3 review finding (minor): the `?linked=` success toast used to gate
// on isKnownConnectedProvider, which is also true for 'credential' — never
// something this UI's Link buttons can produce, but present in
// connectedAccounts for every password user. `?linked=credential` (or a
// stale `?linked=google` replayed against an account that already linked
// Google at some point) then passed both of AccountSettings.jsx's checks
// (known provider + present in connectedAccounts) and showed a fabricated
// "Linked ..." confirmation for nothing that just happened. Only the
// providers this UI can actually initiate a link for should ever unlock
// that toast; recency is handled separately (see the pending-link marker in
// AccountSettings.jsx's `link()`/notice effect).
const LINKABLE_PROVIDERS = ['google', 'github']

export function isLinkableProvider(providerId) {
  return LINKABLE_PROVIDERS.includes(providerId)
}

// Never let the UI enable unlinking an account's last remaining sign-in
// method. Better Auth's own /unlink-account (auth/auth.js: accountLinking.
// allowUnlinkingAll stays false) already refuses this server-side —
// FAILED_TO_UNLINK_LAST_ACCOUNT, verified in auth/test/auth.test.js — this
// is a second, independent guard so the control is never even clickable,
// not just rejected after the fact.
export function canUnlink(accountCount) {
  return accountCount > 1
}

// Round-4 review finding (major): auth.js gates /link-social and
// /unlink-account behind session freshness (24h) while the app session
// lives 7 days (auth.js's session config) — so for most of a session's
// life, both controls render enabled and any click 403s with
// SESSION_NOT_FRESH. The UI's own job is to handle that honestly: ATTEMPT
// the action and react to the server's 403, never precompute session age
// client-side (the server is the sole authority on freshness — this file
// has no createdAt/freshAge of its own, deliberately). link() and
// unlinkConnected() in AccountSettings.jsx both hit exactly this fork in
// their catch blocks; extracted so the mapping from a caught error to what
// Account Settings renders is itself testable under plain `node --test`
// (the .jsx caller isn't importable there — see the header comment above).
// A SESSION_NOT_FRESH error must produce ONLY the honest re-auth state
// below — `stale: true` and nothing else — never a fabricated success
// notice and never the library's raw "Session is not fresh" string standing
// in for it.
export function connectedActionErrorState(err) {
  return err?.code === 'SESSION_NOT_FRESH'
    ? { stale: true, message: null }
    : { stale: false, message: err?.message || 'Something went wrong.' }
}

// Round-4 review finding (minor): the Connected-accounts intro copy used to
// promise "Google and GitHub link only after you authorize them from here"
// unconditionally, even when the provider-discovery fetch (the SAME fetch
// gating the Link buttons themselves, GET /api/auth/providers) says neither
// is configured on this deployment — a real state on any environment that
// hasn't set up OAuth apps yet. Derives the honest sentence from that same
// result instead of a hard-coded claim, so the copy can never promise an
// affordance the buttons below it don't actually offer.
export function connectableProvidersIntro({ google, github } = {}) {
  if (google && github) return 'Google and GitHub link only after you authorize them from here, signed in as you are now.'
  if (google) return 'Google links only after you authorize it from here, signed in as you are now.'
  if (github) return 'GitHub links only after you authorize it from here, signed in as you are now.'
  return ''
}
