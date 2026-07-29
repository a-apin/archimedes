import { lazy, Suspense, useCallback, useEffect, useState } from 'react'

import { useAuth } from './AuthContext'
import { defaultFeatures, fetchFeatures } from './features'
import { pageToPath, resolveRoute } from './routes'
import Architecture from './components/Architecture'
import AuthPage from './components/AuthPage'
import Landing from './components/Landing'
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
    if (route.kind !== 'app' || authLoading || user) return
    const next = `${window.location.pathname}${window.location.search}`
    window.location.replace(`/sign-in?next=${encodeURIComponent(next)}`)
  }, [route.kind, authLoading, user])

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
      'sign-in': 'Sign in · Archimedes',
      'sign-up': 'Create account · Archimedes',
    }
    document.title = titles[route.page] ?? 'Archimedes'
  }, [route.page])

  const navigateToPage = useCallback((page, options = {}) => {
    const path = pageToPath(page, options)
    if (`${window.location.pathname}${window.location.search}` !== path) {
      window.history[options.replace ? 'replaceState' : 'pushState']({}, '', path)
    }
    setRoute(resolveRoute(window.location.pathname, window.location.search, features))
  }, [features])

  if (route.kind === 'redirect') return null
  if (route.kind === 'auth') return <AuthPage mode={route.page} />

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
    return (
      <PublicLayout user={user}>
        <main className="min-h-[70vh] flex flex-col items-center justify-center gap-4 px-4 text-center">
          <h1 className="serif text-[2rem]">Page not found</h1>
          <a className="btn-primary" href={user ? '/app' : '/'}>{user ? 'Open app' : 'Go home'}</a>
        </main>
      </PublicLayout>
    )
  }

  if (authLoading || !user) {
    return <main className="min-h-screen grid place-items-center">Loading account…</main>
  }

  return (
    <Suspense fallback={<main className="min-h-screen grid place-items-center">Loading application…</main>}>
      <AuthenticatedApp route={route} features={features} navigateToPage={navigateToPage} user={user} />
    </Suspense>
  )
}
