// import.meta.env?. (not .env.), so this module loads under plain node in
// ui/test/ too — see featureFlags.js's header comment for the same pattern.
const API_BASE = import.meta.env?.VITE_API_BASE ?? ''

async function authRequest(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: 'include',
    ...options,
    headers: options.body ? { 'Content-Type': 'application/json', ...options.headers } : options.headers,
  })
  const data = response.status === 204 ? null : await response.json().catch(() => null)
  if (!response.ok) {
    const error = new Error(data?.message || data?.error || 'Authentication failed')
    error.status = response.status
    // Better Auth's own APIError body carries a stable machine `code`
    // alongside the human `message` (e.g. { message: 'Session is not
    // fresh', code: 'SESSION_NOT_FRESH' }) — expose it so callers can branch
    // on the code instead of string-matching the prose, which is fragile
    // and would silently stop matching if the library ever reworded it.
    error.code = data?.code
    throw error
  }
  return data
}

export const getSession = () => authRequest('/api/auth/get-session')
export const getProviders = () => authRequest('/api/auth/providers')

export const signInEmail = (email, password, callbackURL) => authRequest('/api/auth/sign-in/email', {
  method: 'POST',
  body: JSON.stringify({ email, password, callbackURL }),
})

export const signUpEmail = (name, email, password, callbackURL) => authRequest('/api/auth/sign-up/email', {
  method: 'POST',
  body: JSON.stringify({ name, email, password, callbackURL }),
})

export async function signInSocial(provider, callbackURL) {
  const result = await authRequest('/api/auth/sign-in/social', {
    method: 'POST',
    body: JSON.stringify({ provider, callbackURL, disableRedirect: true }),
  })
  if (!result?.url) throw new Error('OAuth provider did not return a redirect')
  window.location.assign(result.url)
}

export const signOut = () => authRequest('/api/auth/sign-out', { method: 'POST' })

// Same user-visible outcome whether or not the address has an account
// (Better Auth's own anti-enumeration behavior — see auth/auth.js
// sendResetPassword) — callers must not branch UI copy on the result.
export const requestPasswordReset = (email, redirectTo) => authRequest('/api/auth/request-password-reset', {
  method: 'POST',
  body: JSON.stringify({ email, redirectTo }),
})

export const resetPassword = (newPassword, token) => authRequest('/api/auth/reset-password', {
  method: 'POST',
  body: JSON.stringify({ newPassword, token }),
})

export const resendVerificationEmail = (email, callbackURL) => authRequest('/api/auth/send-verification-email', {
  method: 'POST',
  body: JSON.stringify({ email, callbackURL }),
})

// ── #1420 follow-up: explicit account linking (Account Settings → Connected
// accounts) ────────────────────────────────────────────────────────────
//
// listAccounts/linkSocial/unlinkAccount call Better Auth's own /list-accounts,
// /link-social and /unlink-account endpoints directly — no custom backend
// route. The state/CSRF handshake for the OAuth round trip (the `state`
// param + its double-submit cookie) is entirely library-managed on both the
// initiate call here and the /callback/:id redirect target; nothing here
// hand-rolls any part of it.

export const listAccounts = () => authRequest('/api/auth/list-accounts')

// Redirect-based, same shape as signInSocial: POST for the authorize URL,
// then a full navigation (not an SPA transition) so the provider's consent
// screen is a real top-level page. callbackURL/errorCallbackURL land the
// browser back on Account Settings either way — the callback endpoint is
// what actually decides success/failure (email match, provider trust); this
// function only starts the round trip.
export async function linkSocial(provider, callbackURL, errorCallbackURL) {
  const result = await authRequest('/api/auth/link-social', {
    method: 'POST',
    body: JSON.stringify({ provider, callbackURL, errorCallbackURL, disableRedirect: true }),
  })
  if (!result?.url) throw new Error('OAuth provider did not return a redirect')
  window.location.assign(result.url)
}

// Better Auth's own /unlink-account refuses to remove an account's last
// remaining credential (FAILED_TO_UNLINK_LAST_ACCOUNT, 400) — server-
// enforced regardless of the UI guard in AccountSettings.jsx.
export const unlinkAccount = (providerId, accountId) => authRequest('/api/auth/unlink-account', {
  method: 'POST',
  body: JSON.stringify({ providerId, accountId }),
})
