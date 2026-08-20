// Maps the `error` query param Better Auth's OAuth callback redirects with
// back to honest, sign-in-surface copy.
//
// `account_not_linked` is the one this exists for: auth/auth.js sets
// `accountLinking.disableImplicitLinking: true` (a deliberate security
// posture — do not remove it to make this message go away) so an existing
// email/password user who clicks "Continue with Google/GitHub" gets refused
// and redirected with this code, previously to a route that rendered nothing.
//
// No account-linking UI exists in this app (verified: no `linkSocial` call
// site anywhere in auth/ or ui/) — this copy must not promise a "link your
// accounts" flow that doesn't exist yet. When one ships, update this message
// to point at it instead of "sign in with your password".
const OAUTH_ERROR_MESSAGES = {
  account_not_linked:
    "This Google/GitHub account's email already has a password account here. Sign in with your email and password instead.",
}

const GENERIC_OAUTH_ERROR_MESSAGE =
  'Sign-in with that provider did not complete. Sign in with your email and password instead, or try again.'

/**
 * @param {string | null | undefined} error - the `error` query param, if any.
 * @returns {string | null} honest, user-facing copy, or null if there is no error to show.
 */
export function oauthErrorMessage(error) {
  if (!error) return null
  return OAUTH_ERROR_MESSAGES[error] ?? GENERIC_OAUTH_ERROR_MESSAGE
}
