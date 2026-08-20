import { lazy, Suspense, useCallback, useEffect, useState } from 'react'

import { fetchAdminProbe } from './adminProbe.js'
import { useAuth } from './AuthContext'
import { defaultFeatures, fetchFeatures } from './features'
import { pageToPath, resolveRoute } from './routes'
import Architecture from './components/Architecture'
import AuthPage from './components/AuthPage'
import Landing from './components/Landing'
import NotFound from './components/NotFound'
import PublicLayout from './components/PublicLayout'
import './App.css'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''
const AuthenticatedApp = lazy(() => import('./AuthenticatedApp'))

function currentRoute(features) {
  return resolveRoute(window.location.pathname, window.location.search, features)
}

export default function App() {
  const { user, loading: authLoading } = useAuth()
  const [features, setFeatures] = useState(defaultFeatures)
  const [route, setRoute] = useState(() => currentRoute(defaultFeatures))
  // Admin-gate probe result for /app/insights (owner directive 2026-08-20,
  // supersedes #1028 D8): null while checking, true/false once the server
  // has answered. Server truth only — never derived from `user`/wallet
  // local state, which cannot know PLATFORM_ADMIN_WALLETS membership.
  const [insightsAdmin, setInsightsAdmin] = useState(null)

  useEffect(() => {
    fetchFeatures()
      .then((next) => {
        setFeatures(next)
        setRoute(currentRoute(next))
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (route.kind !== 'redirect') return
    window.history.replaceState({}, '', route.redirect)
    setRoute(currentRoute(features))
  }, [route, features])

  useEffect(() => {
    const onPopState = () => setRoute(currentRoute(features))
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [features])

  useEffect(() => {
    try {
      if (sessionStorage.getItem('archimedes_landed')) return
      sessionStorage.setItem('archimedes_landed', '1')
    } catch {
      // Storage may be blocked; metric stays best-effort.
    }
    fetch(`${API_BASE}/api/metrics/funnel/event`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stage: 'landed' }),
    }).catch(() => {})
  }, [])

  useEffect(() => {
    // Anonymous-OK app pages (Explore/Leaderboard/Corpus/strategy detail —
    // #1194 revision d) never bounce to sign-in; auth is required only to
    // generate, pay, or paper-deploy. Keep in lockstep with the nginx
    // carve-outs, which enforce the same split server-side.
    //
    // `insights` is EXCLUDED here too (owner directive 2026-08-20): bouncing
    // an anonymous visitor to /sign-in?next=/app/insights would itself
    // advertise that the page exists and is worth signing in for. The
    // insights-admin-probe effect below handles it uniformly instead — an
    // anonymous or non-admin visitor lands on the identical not-found
    // treatment a truly unknown path gets, never a sign-in prompt.
    if (route.kind !== 'app' || route.anonymousOk || route.page === 'insights' || authLoading || user) return
    const next = `${window.location.pathname}${window.location.search}`
    window.location.replace(`/sign-in?next=${encodeURIComponent(next)}`)
  }, [route.kind, route.anonymousOk, route.page, authLoading, user])

  useEffect(() => {
    // Re-probe every time navigation LANDS on insights (not on every
    // render while already there) — reset to "checking" first so a stale
    // true/false from a previous visit this session can't flash before the
    // fresh answer arrives.
    if (route.page !== 'insights') {
      setInsightsAdmin(null)
      return
    }
    let cancelled = false
    setInsightsAdmin(null)
    fetchAdminProbe().then(({ admin }) => {
      if (!cancelled) setInsightsAdmin(admin)
    })
    return () => {
      cancelled = true
    }
  }, [route.page])

  useEffect(() => {
    const titles = {
      landing: 'Archimedes',
      explore: 'Explore · Archimedes',
      leaderboard: 'Leaderboard · Archimedes',
      generate: 'Generate · Archimedes',
      architecture: 'Architecture · Archimedes',
      library: 'Library · Archimedes',
      corpus: 'Corpus · Archimedes',
      quant: 'Quant Lab · Archimedes',
      portfolio: 'Portfolio · Archimedes',
      reasoning: 'Reasoning · Archimedes',
      learnings: 'Learnings · Archimedes',
      insights: 'Insights · Archimedes',
      account: 'Account · Archimedes',
      'vault-detail': 'Vault · Archimedes',
      strategy: 'Strategy · Archimedes',
      paper: 'Paper Trading · Archimedes',
      'sign-in': 'Sign in · Archimedes',
      'sign-up': 'Create account · Archimedes',
      'reset-password': 'Reset password · Archimedes',
      // resolveRoute() returns page === null for not-found, so this branch used
      // to fall through to the bare 'Archimedes' title — byte-identical to the
      // landing page, leaving a screen-reader or many-tabs user unable to tell
      // a failed deep link from home (2.4.2 Page Titled).
      'not-found': 'Page not found · Archimedes',
    }
    // A denied insights probe titles the tab identically to a real 404 —
    // "do not advertise existence" applies to the tab title too, not just
    // the rendered page (a many-tabs user should not be able to tell
    // "unknown route" from "gated route I'm not allowed on" apart).
    const deniedInsights = route.page === 'insights' && insightsAdmin === false
    const key = route.kind === 'not-found' || deniedInsights ? 'not-found' : route.page
    document.title = titles[key] ?? 'Archimedes'
  }, [route.kind, route.page, insightsAdmin])

  const navigateToPage = useCallback((page, options = {}) => {
    const path = pageToPath(page, options)
    if (`${window.location.pathname}${window.location.search}` !== path) {
      window.history[options.replace ? 'replaceState' : 'pushState']({}, '', path)
    }
    setRoute(resolveRoute(window.location.pathname, window.location.search, features))
  }, [features])

  if (route.kind === 'redirect') return null
  if (route.kind === 'auth') return <AuthPage mode={route.page} oauthError={route.error} />

  if (route.kind === 'public') {
    return (
      <PublicLayout user={user}>
        {route.page === 'architecture'
          ? <Architecture onNavigate={navigateToPage} />
          : <Landing onNavigate={navigateToPage} />}
      </PublicLayout>
    )
  }

  if (route.kind === 'not-found') {
    return <NotFound user={user} />
  }

  if (route.kind === 'app' && route.page === 'insights') {
    // Server-truth admin gate (owner directive 2026-08-20, supersedes
    // #1028 D8): a denied probe renders EXACTLY the not-found page — same
    // component the true 404 above uses — never a "you need admin access"
    // message, which would itself confirm the page exists. While the probe
    // is in flight, render a neutral chrome-free loader: no "Insights"
    // heading, no sidebar, nothing that would flash gated UI before a
    // non-admin's swap to not-found lands.
    if (insightsAdmin === false) return <NotFound user={user} />
    if (insightsAdmin !== true) {
      return <main className="min-h-screen grid place-items-center">Loading…</main>
    }
    // admin === true falls through to the normal authenticated render below.
  }

  // Anonymous-OK pages render immediately with user === null rather than
  // blocking on auth resolution — a public Explore that shows "Loading
  // account…" to a visitor with no account is a broken front door. If the
  // visitor IS signed in, the user object arrives when auth resolves and the
  // chrome upgrades in place.
  if (!route.anonymousOk && (authLoading || !user)) {
    return <main className="min-h-screen grid place-items-center">Loading account…</main>
  }

  return (
    <Suspense fallback={<main className="min-h-screen grid place-items-center">Loading application…</main>}>
      <AuthenticatedApp route={route} features={features} navigateToPage={navigateToPage} user={user} />
    </Suspense>
  )
}
