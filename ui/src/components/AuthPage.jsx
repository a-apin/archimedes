import { useEffect, useState } from 'react'

import { useAuth } from '../AuthContext'
import { getProviders, signInEmail, signInSocial, signUpEmail } from '../auth-client'
import { postAuthPath } from '../routes'

export default function AuthPage({ mode }) {
  const creating = mode === 'sign-up'
  const next = postAuthPath(window.location.search)
  const callbackURL = `${window.location.origin}${next}`
  const { user, refresh } = useAuth()
  const [providers, setProviders] = useState({ emailPassword: true, google: false, github: false })
  const [form, setForm] = useState({ name: '', email: '', password: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

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
              minLength={12}
              autoComplete={creating ? 'new-password' : 'current-password'}
              value={form.password}
              onChange={(event) => setForm({ ...form, password: event.target.value })}
            />
          </label>
          {error && <div className="status" role="alert">{error}</div>}
          <button className="btn-primary" type="submit" disabled={busy}>
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
        <p className="caption mt-5 text-[var(--text-4)]">
          Circle wallet passkeys authorize Circle smart wallets. They do not sign in to Archimedes.
        </p>
      </section>
    </main>
  )
}
