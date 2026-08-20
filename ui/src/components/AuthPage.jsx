import { useEffect, useState } from 'react'

import { useAuth } from '../AuthContext'
import { oauthErrorMessage } from '../auth-errors'
import {
  getProviders,
  requestPasswordReset,
  resendVerificationEmail,
  resetPassword,
  signInEmail,
  signInSocial,
  signUpEmail,
} from '../auth-client'
import {
  PASSWORD_MAX,
  PASSWORD_MIN,
  passwordRules,
  passwordRulesMet,
  passwordsMatch,
  passwordStrength,
} from '../password-rules'
import { postAuthPath } from '../routes'

const STRENGTH_COLORS = ['var(--text-4)', 'var(--text-4)', 'var(--warning, #b08a3e)', 'var(--accent)', 'var(--accent)']

// Better Auth returns this identical response whether or not the address has
// an account (auth/auth.js sendResetPassword sits behind that check) — the UI
// shows the same copy in both cases and must never branch on the result.
const RESET_REQUESTED_MESSAGE = 'If that email has an Archimedes account, a reset link is on its way.'
const VERIFICATION_RESENT_MESSAGE = 'Verification email sent — check your inbox.'

/** Live checklist + guidance strength meter, shared by sign-up and reset-password. */
function PasswordChecklist({ password, confirm, showMismatch }) {
  const rules = passwordRules(password)
  const match = passwordsMatch(password, confirm)
  const strength = passwordStrength(password)
  return (
    <div aria-live="polite" className="flex flex-col gap-1.5">
      <div className="flex gap-1" aria-hidden="true">
        {[1, 2, 3, 4].map((step) => (
          <span
            key={step}
            className="h-1 flex-1 rounded"
            style={{ background: step <= strength.score ? STRENGTH_COLORS[strength.score] : 'var(--glass-border)' }}
          />
        ))}
      </div>
      {password.length > 0 && (
        <span className="caption">
          Strength: {strength.label} — guidance only; the length rule below is the requirement.
        </span>
      )}
      <ul className="caption flex flex-col gap-0.5" id="password-rules" aria-label="Password requirements">
        {rules.map((rule) => (
          <li key={rule.id} className={rule.met ? 'text-[var(--accent)]' : undefined}>
            {rule.met ? '✓' : '○'} {rule.label}
          </li>
        ))}
        <li className={match ? 'text-[var(--accent)]' : undefined}>
          {match ? '✓' : '○'} Passwords match
        </li>
      </ul>
      {showMismatch && (
        <div className="status" role="alert" id="confirm-mismatch">
          Passwords do not match.
        </div>
      )}
    </div>
  )
}

function ForgotPasswordForm({ email, onBack, callbackOrigin }) {
  const [address, setAddress] = useState(email)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [sent, setSent] = useState(false)

  const submit = async (event) => {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      await requestPasswordReset(address, `${callbackOrigin}/reset-password`)
      setSent(true)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  if (sent) {
    return (
      <div className="flex flex-col gap-4">
        <p className="body">{RESET_REQUESTED_MESSAGE}</p>
        <button className="btn-secondary" type="button" onClick={onBack}>Back to sign in</button>
      </div>
    )
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <label className="flex flex-col gap-1.5">
        <span className="caption">Email</span>
        <input
          required
          type="email"
          autoComplete="email"
          value={address}
          onChange={(event) => setAddress(event.target.value)}
        />
      </label>
      {error && <div className="status" role="alert">{error}</div>}
      <button className="btn-primary" type="submit" disabled={busy}>
        {busy ? 'Working…' : 'Send reset link'}
      </button>
      <button className="btn-secondary" type="button" onClick={onBack} disabled={busy}>Back to sign in</button>
    </form>
  )
}

function ResetPasswordForm({ token, callbackOrigin }) {
  const [form, setForm] = useState({ password: '', confirm: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState(false)

  const rulesMet = passwordRulesMet(form.password)
  const match = passwordsMatch(form.password, form.confirm)
  const showMismatch = form.confirm.length > 0 && !match

  if (!token) {
    return (
      <div className="flex flex-col gap-4">
        <p className="body">This reset link is invalid or has expired.</p>
        <a className="btn-primary text-center" href={`${callbackOrigin}/sign-in`}>Request a new one</a>
      </div>
    )
  }

  if (done) {
    return (
      <div className="flex flex-col gap-4">
        <p className="body">Password updated. Every prior session was signed out.</p>
        <a className="btn-primary text-center" href={`${callbackOrigin}/sign-in`}>Sign in</a>
      </div>
    )
  }

  const submit = async (event) => {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      await resetPassword(form.password, token)
      setDone(true)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <label className="flex flex-col gap-1.5">
        <span className="caption">New password</span>
        <input
          required
          type="password"
          minLength={PASSWORD_MIN}
          maxLength={PASSWORD_MAX}
          autoComplete="new-password"
          aria-describedby="password-rules"
          value={form.password}
          onChange={(event) => setForm({ ...form, password: event.target.value })}
        />
      </label>
      <label className="flex flex-col gap-1.5">
        <span className="caption">Confirm new password</span>
        <input
          required
          type="password"
          maxLength={PASSWORD_MAX}
          autoComplete="new-password"
          aria-invalid={showMismatch}
          aria-describedby={showMismatch ? 'password-rules confirm-mismatch' : 'password-rules'}
          value={form.confirm}
          onChange={(event) => setForm({ ...form, confirm: event.target.value })}
        />
      </label>
      <PasswordChecklist password={form.password} confirm={form.confirm} showMismatch={showMismatch} />
      {error && <div className="status" role="alert">{error}</div>}
      <button className="btn-primary" type="submit" disabled={busy || !rulesMet || !match}>
        {busy ? 'Working…' : 'Set new password'}
      </button>
    </form>
  )
}

export default function AuthPage({ mode, oauthError }) {
  const creating = mode === 'sign-up'
  const resetting = mode === 'reset-password'
  const next = postAuthPath(window.location.search)
  const callbackURL = `${window.location.origin}${next}`
  const callbackOrigin = window.location.origin
  const resetToken = resetting ? new URLSearchParams(window.location.search).get('token') : null
  // Surfaces the account-linking rejection (routes.js bounces `/?error=...`
  // here) or any other OAuth callback error — never shown on sign-up or
  // reset-password, since those screens can't be the redirect's origin. Named
  // `oauthError` (not `error`) to avoid shadowing the submit-flow `error`
  // state declared below.
  const oauthNotice = !creating && !resetting ? oauthErrorMessage(oauthError) : null
  const { user, refresh } = useAuth()
  const [providers, setProviders] = useState({ emailPassword: true, google: false, github: false })
  const [form, setForm] = useState({ name: '', email: '', password: '', confirm: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [needsVerification, setNeedsVerification] = useState(false)
  const [screen, setScreen] = useState('credentials') // 'credentials' | 'forgot'

  // Sign-up validity mirrors the SERVER's rules exactly (length only — see
  // password-rules.js). The strength meter (PasswordChecklist) is guidance
  // and never gates.
  const rulesMet = passwordRulesMet(form.password)
  const match = passwordsMatch(form.password, form.confirm)
  const showMismatch = creating && form.confirm.length > 0 && !match
  const canSubmit = !creating || (rulesMet && match)

  useEffect(() => {
    getProviders().then(setProviders).catch(() => {})
  }, [])

  useEffect(() => {
    if (user && !resetting) window.location.replace(next)
  }, [user, next, resetting])

  const submit = async (event) => {
    event.preventDefault()
    setBusy(true)
    setError('')
    setNotice('')
    setNeedsVerification(false)
    try {
      if (creating) {
        await signUpEmail(form.name, form.email, form.password, callbackURL)
        await signInEmail(form.email, form.password, callbackURL)
      } else {
        await signInEmail(form.email, form.password, callbackURL)
      }
      await refresh()
      window.location.assign(next)
    } catch (err) {
      setError(err.message)
      // Better Auth answers an unverified sign-in with 403 (BASE_ERROR_CODES
      // .EMAIL_NOT_VERIFIED) — give that specific lockout an escape hatch
      // instead of leaving the user stuck on a bare error string.
      if (err.status === 403) setNeedsVerification(true)
    } finally {
      setBusy(false)
    }
  }

  const resendVerification = async () => {
    setBusy(true)
    setError('')
    try {
      await resendVerificationEmail(form.email, callbackURL)
      setNotice(VERIFICATION_RESENT_MESSAGE)
      setNeedsVerification(false)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const social = async (provider) => {
    setBusy(true)
    setError('')
    try {
      await signInSocial(provider, callbackURL)
    } catch (err) {
      setError(err.message)
      setBusy(false)
    }
  }

  const title = resetting ? 'Reset your password' : creating ? 'Create your account' : screen === 'forgot' ? 'Reset your password' : 'Sign in'

  return (
    <main className="min-h-screen flex items-center justify-center px-4 py-12 bg-[var(--canvas)]">
      <section className="card-flat w-full max-w-[430px] p-7" aria-labelledby="auth-title">
        <a href="/" className="caption text-[var(--accent)]">← Archimedes</a>
        <h1 id="auth-title" className="serif text-[2rem] mt-4 mb-2">{title}</h1>

        {resetting ? (
          <>
            <p className="body mb-6">Choose a new password for your Archimedes account.</p>
            <ResetPasswordForm token={resetToken} callbackOrigin={callbackOrigin} />
          </>
        ) : screen === 'forgot' ? (
          <>
            <p className="body mb-6">Enter your account email and we&rsquo;ll send a reset link.</p>
            <ForgotPasswordForm
              email={form.email}
              callbackOrigin={callbackOrigin}
              onBack={() => setScreen('credentials')}
            />
          </>
        ) : (
          <>
            <p className="body mb-6">
              Account owns your strategies and settings. Wallets stay optional and link only after signature proof.
            </p>

            {oauthNotice && (
              <div className="status mb-4" role="alert" id="oauth-error">
                {oauthNotice}
              </div>
            )}

            <form onSubmit={submit} className="flex flex-col gap-4">
              {creating && (
                <label className="flex flex-col gap-1.5">
                  <span className="caption">Name</span>
                  <input
                    required
                    autoComplete="name"
                    value={form.name}
                    onChange={(event) => setForm({ ...form, name: event.target.value })}
                  />
                </label>
              )}
              <label className="flex flex-col gap-1.5">
                <span className="caption">Email</span>
                <input
                  required
                  type="email"
                  autoComplete="email"
                  value={form.email}
                  onChange={(event) => setForm({ ...form, email: event.target.value })}
                />
              </label>
              <label className="flex flex-col gap-1.5">
                <span className="caption">Password</span>
                <input
                  required
                  type="password"
                  minLength={PASSWORD_MIN}
                  maxLength={PASSWORD_MAX}
                  autoComplete={creating ? 'new-password' : 'current-password'}
                  aria-describedby={creating ? 'password-rules' : undefined}
                  value={form.password}
                  onChange={(event) => setForm({ ...form, password: event.target.value })}
                />
              </label>
              {!creating && (
                <button
                  type="button"
                  className="caption text-[var(--accent)] text-left"
                  onClick={() => { setScreen('forgot'); setError(''); setNotice(''); setNeedsVerification(false) }}
                >
                  Forgot password?
                </button>
              )}
              {creating && (
                <>
                  <label className="flex flex-col gap-1.5">
                    <span className="caption">Confirm password</span>
                    {/* aria-invalid alone said "invalid entry" with no statement of
                        what was wrong: the mismatch alert fires once while the user
                        is still typing and is unreachable afterwards, and the rules
                        list was never associated with the field (3.3.1). */}
                    <input
                      required
                      type="password"
                      maxLength={PASSWORD_MAX}
                      autoComplete="new-password"
                      aria-invalid={showMismatch}
                      aria-describedby={showMismatch ? 'password-rules confirm-mismatch' : 'password-rules'}
                      value={form.confirm}
                      onChange={(event) => setForm({ ...form, confirm: event.target.value })}
                    />
                  </label>
                  {/* Unmet rules inherit .caption's --text-3 rather than --text-4:
                      on the base palette --text-4 is #3f3f46, i.e. 1.73:1 on the
                      card — the rule text a user needs precisely when their
                      password was rejected was the least readable text here.
                      The ✓/○ glyph stays the state signal (1.4.1). */}
                  <PasswordChecklist password={form.password} confirm={form.confirm} showMismatch={showMismatch} />
                </>
              )}
              {notice && <div className="status" role="status">{notice}</div>}
              {error && (
                <div className="status" role="alert">
                  {error}
                  {needsVerification && (
                    <>
                      {' '}
                      <button type="button" className="text-[var(--accent)]" onClick={resendVerification} disabled={busy}>
                        Resend verification email
                      </button>
                    </>
                  )}
                </div>
              )}
              <button
                className="btn-primary"
                type="submit"
                disabled={busy || !canSubmit}
                aria-describedby={creating && !canSubmit ? 'password-rules' : undefined}
              >
                {busy ? 'Working…' : creating ? 'Create account' : 'Sign in'}
              </button>
            </form>

            {(providers.google || providers.github) && (
              <div className="mt-5 flex flex-col gap-2 border-t border-[var(--glass-border)] pt-5">
                {providers.google && <button className="btn-secondary" onClick={() => social('google')} disabled={busy}>Continue with Google</button>}
                {providers.github && <button className="btn-secondary" onClick={() => social('github')} disabled={busy}>Continue with GitHub</button>}
              </div>
            )}

            <p className="caption mt-5 text-center">
              {creating ? 'Already registered?' : 'New to Archimedes?'}{' '}
              <a href={`${creating ? '/sign-in' : '/sign-up'}?next=${encodeURIComponent(next)}`}>
                {creating ? 'Sign in' : 'Create account'}
              </a>
            </p>
            <p className="caption mt-5">
              Circle wallet passkeys authorize Circle smart wallets. They do not sign in to Archimedes.
            </p>
          </>
        )}
      </section>
    </main>
  )
}
