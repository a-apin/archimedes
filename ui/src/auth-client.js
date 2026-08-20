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
