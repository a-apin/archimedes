import { useEffect, useState } from 'react'

import { useAuth } from '../AuthContext'
import { getProviders, signInEmail, signInSocial, signUpEmail } from '../auth-client'
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

export default function AuthPage({ mode }) {
  const creating = mode === 'sign-up'
  const next = postAuthPath(window.location.search)
  const callbackURL = `${window.location.origin}${next}`
  const { user, refresh } = useAuth()
  const [providers, setProviders] = useState({ emailPassword: true, google: false, github: false })
  const [form, setForm] = useState({ name: '', email: '', password: '', confirm: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  // Sign-up validity mirrors the SERVER's rules exactly (length only — see
  // password-rules.js). The strength meter is guidance and never gates.
  const rules = passwordRules(form.password)
  const rulesMet = passwordRulesMet(form.password)
  const match = passwordsMatch(form.password, form.confirm)
  const showMismatch = creating && form.confirm.length > 0 && !match
  const strength = passwordStrength(form.password)
  const canSubmit = !creating || (rulesMet && match)

  useEffect(() => {
    getProviders().then(setProviders).catch(() => {})
  }, [])

  useEffect(() => {
    if (user) window.location.replace(next)
  }, [user, next])

  const submit = async (event) => {
    event.preventDefault()
    setBusy(true)
    setError('')
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

  return (
    <main className="min-h-screen flex items-center justify-center px-4 py-12 bg-[var(--canvas)]">
      <section className="card-flat w-full max-w-[430px] p-7" aria-labelledby="auth-title">
        <a href="/" className="caption text-[var(--accent)]">← Archimedes</a>
        <h1 id="auth-title" className="serif text-[2rem] mt-4 mb-2">
          {creating ? 'Create your account' : 'Sign in'}
        </h1>
        <p className="body mb-6">
          Account owns your strategies and settings. Wallets stay optional and link only after signature proof.
        </p>

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
              <div aria-live="polite" className="flex flex-col gap-1.5">
                <div className="flex gap-1" aria-hidden="true">
                  {[1, 2, 3, 4].map((step) => (
                    <span
                      key={step}
                      className="h-1 flex-1 rounded"
                      style={{
                        background: step <= strength.score ? STRENGTH_COLORS[strength.score] : 'var(--glass-border)',
                      }}
                    />
                  ))}
                </div>
                {form.password.length > 0 && (
                  <span className="caption">
                    Strength: {strength.label} — guidance only; the length rule below is the requirement.
                  </span>
                )}
                {/* Unmet rules inherit .caption's --text-3 rather than --text-4:
                    on the base palette --text-4 is #3f3f46, i.e. 1.73:1 on the
                    card — the rule text a user needs precisely when their
                    password was rejected was the least readable text here.
                    The ✓/○ glyph stays the state signal (1.4.1). */}
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
              </div>
              {showMismatch && (
                <div className="status" role="alert" id="confirm-mismatch">
                  Passwords do not match.
                </div>
              )}
            </>
          )}
          {error && <div className="status" role="alert">{error}</div>}
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
      </section>
    </main>
  )
}
