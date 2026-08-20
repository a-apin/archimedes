// Maps the `error` query param Better Auth's OAuth callback redirects with
// back to honest, sign-in-surface copy.
//
// `account_not_linked` is the one this exists for: auth/auth.js's implicit
// (plain sign-in) auto-link path refuses to attach a Google/GitHub identity
// to an existing password account unless that account's emailVerified is
// already true (requireLocalEmailVerified, the library's default — see the
// long comment on accountLinking in auth/auth.js). While
// EMAIL_VERIFICATION_ENFORCED is off (SES sandbox), most password accounts
// — plausibly including the operator's own — sit unverified, so this refusal
// is the common case in practice, not an edge case.
//
// #1420 follow-up shipped the explicit alternative: signed-in "Link Google /
// Link GitHub" buttons in Account Settings → Connected accounts
// (AccountSettings.jsx, linkSocial/unlinkAccount in auth-client.js). That
// path proves ownership via the live session instead of local email
// verification, so it works today regardless of the refusal above — this
// copy now points there instead of dead-ending on "sign in with your
// password" alone.
const OAUTH_ERROR_MESSAGES = {
  account_not_linked:
    "This Google/GitHub account's email already has a password account here. Sign in with your email and password instead, or sign in with your password and link the provider under Account Settings → Connected accounts so it signs you in directly next time.",
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

// Maps the `error` query param the explicit /link-social → /callback/:id
// round trip redirects with (Account Settings' "Link Google/GitHub"
// buttons) back to honest copy. Distinct code space from OAUTH_ERROR_MESSAGES
// above — these come from api/routes/callback.mjs's `if (link)` branch, not
// the plain-sign-in path, and mean something different even where a code
// name looks similar to a sign-in one.
const LINK_ERROR_MESSAGES = {
  access_denied: 'You canceled the authorization — nothing was linked.',
  "email_doesn't_match":
    'That account uses a different email address than your Archimedes account. Sign in to the provider with the matching email, then try linking again.',
  account_already_linked_to_different_user:
    'That account is already linked to a different Archimedes account.',
  unable_to_link_account: 'Could not link that account. Try again.',
}

const GENERIC_LINK_ERROR_MESSAGE =
  'Linking did not complete. Try again, or sign in with your password if the issue persists.'

/**
 * @param {string | null | undefined} error - the `error` query param on the Account
 *   Settings redirect back from /link-social's OAuth round trip, if any.
 * @returns {string | null} honest, user-facing copy, or null if there is no error to show.
 */
export function linkErrorMessage(error) {
  if (!error) return null
  return LINK_ERROR_MESSAGES[error] ?? GENERIC_LINK_ERROR_MESSAGE
}
