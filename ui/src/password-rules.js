// Client-side mirror of the SERVER's password policy — auth/auth.js
// (`emailAndPassword`: minPasswordLength 12, maxPasswordLength 128).
//
// The server enforces LENGTH ONLY. This module deliberately lists no
// composition rules (uppercase/digits/symbols), so the UI never promises —
// or demands — anything the server doesn't enforce (#1290). The strength
// meter below is guidance, never a gate.
//
// Drift guard: ui/test/password-rules.test.js parses auth/auth.js and fails
// the suite if these literals stop matching the server config.

export const PASSWORD_MIN = 12
export const PASSWORD_MAX = 128

/** The enforced requirements, each with a live `met` flag for the checklist. */
export function passwordRules(password) {
  const length = (password ?? '').length
  return [
    {
      id: 'length',
      label: `${PASSWORD_MIN}–${PASSWORD_MAX} characters`,
      met: length >= PASSWORD_MIN && length <= PASSWORD_MAX,
    },
  ]
}

export function passwordRulesMet(password) {
  return passwordRules(password).every((rule) => rule.met)
}

export function passwordsMatch(password, confirm) {
  return (password ?? '').length > 0 && password === confirm
}

/**
 * Guidance-only strength score, 0–4. Length does most of the work (matching
 * the server's own philosophy — length is the only enforced rule); character
 * variety adds one step. Never used to block submission.
 */
export function passwordStrength(password) {
  const value = password ?? ''
  if (value.length === 0) return { score: 0, label: 'empty' }
  if (value.length < PASSWORD_MIN) return { score: 0, label: 'too short' }
  let variety = 0
  if (/[a-z]/.test(value)) variety += 1
  if (/[A-Z]/.test(value)) variety += 1
  if (/[0-9]/.test(value)) variety += 1
  if (/[^A-Za-z0-9]/.test(value)) variety += 1
  const lengthTier = value.length >= 20 ? 2 : value.length >= 16 ? 1 : 0
  const score = Math.max(1, Math.min(4, 1 + lengthTier + (variety >= 3 ? 1 : 0)))
  const label = ['empty', 'weak', 'fair', 'good', 'strong'][score]
  return { score, label }
}
