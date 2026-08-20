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
// ("Linked anything.") instead of being treated as untrusted input.
// AccountSettings.jsx now calls this before trusting `linked` for anything.
export function isKnownConnectedProvider(providerId) {
  return Object.hasOwn(CONNECTED_PROVIDER_LABELS, providerId)
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
