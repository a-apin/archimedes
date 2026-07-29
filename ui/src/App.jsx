import { useCallback, useEffect, useState } from 'react'

import { useAuth } from './AuthContext'
import { disconnectWallet, reconnectWallet } from './config'
import { defaultFeatures, fetchFeatures } from './features'
import { listLinkedWallets } from './linked-wallets'
import { pageToPath, resolveRoute } from './routes'
import { apiPost } from './api'
import AccountSettings from './components/AccountSettings'
import Architecture from './components/Architecture'
import AuthPage from './components/AuthPage'
import CorpusExplorer from './components/CorpusExplorer'
import Explore from './components/Explore'
import Generate from './components/Generate'
import Insights from './components/Insights'
import Landing from './components/Landing'
import Leaderboard from './components/Leaderboard'
import Layout from './components/Layout'
import Learnings from './components/Learnings'
import MarketplacePage from './components/MarketplacePage'
import OnboardingTour, { hasCompletedOnboarding } from './components/OnboardingTour'
import Portfolio from './components/Portfolio'
import PublicLayout from './components/PublicLayout'
import PublishPage from './components/PublishPage'
import QuantLab from './components/QuantLab'
import Reasoning from './components/Reasoning'
import Strategies from './components/Strategies'
import StrategyDetailPage from './components/StrategyDetailPage'
import StrategyPassport from './components/StrategyPassport'
import SubscriptionsPage from './components/SubscriptionsPage'
import VaultDetail from './components/VaultDetail'
import WalletGate from './components/WalletGate'
import './App.css'

const openConnectModal = () => window.dispatchEvent(new Event('open-wallet-modal'))

function currentRoute(features) {
  return resolveRoute(window.location.pathname, window.location.search, features)
}

export default function App() {
  const { user, loading: authLoading } = useAuth()
  const [features, setFeatures] = useState(defaultFeatures)
  const [route, setRoute] = useState(() => currentRoute(defaultFeatures))
  const [walletAddr, setWalletAddr] = useState(null)
  const [tourOpen, setTourOpen] = useState(() => !hasCompletedOnboarding())

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
    if (!user) {
      setWalletAddr(null)
      return
    }
    reconnectWallet().then(async (result) => {
      if (!result) return
      try {
        const wallets = await listLinkedWallets()
        if (wallets.some((wallet) => wallet.address === result.address.toLowerCase() && wallet.chain_id === 5042002)) {
          setWalletAddr(result.address)
        }
      } catch {
        // Account remains usable without wallet service.
      }
    })
  }, [user])

  useEffect(() => {
    const handler = async (event) => {
      const address = event.detail.address
      if (!address || !user) {
        setWalletAddr(null)
        return
      }
      try {
        const wallets = await listLinkedWallets()
        setWalletAddr(wallets.some((wallet) => wallet.address === address.toLowerCase()) ? address : null)
      } catch {
        setWalletAddr(null)
      }
    }
    window.addEventListener('wallet-changed', handler)
    return () => window.removeEventListener('wallet-changed', handler)
  }, [user])

  useEffect(() => {
    try {
      if (sessionStorage.getItem('archimedes_landed')) return
      sessionStorage.setItem('archimedes_landed', '1')
    } catch {
      // Storage may be blocked; metric stays best-effort.
    }
    apiPost('/api/metrics/funnel/event', { stage: 'landed' }).catch(() => {})
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

  const handleDisconnect = () => {
    disconnectWallet()
    setWalletAddr(null)
  }

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

  const selectVault = (address) => navigateToPage('vault-detail', { vaultAddress: address })
  const selectTrace = (traceId) => navigateToPage('reasoning', { traceId })

  const renderPage = () => {
    switch (route.page) {
      case 'explore': return <Explore />
      case 'leaderboard': return <Leaderboard />
      case 'generate': return <Generate onNavigate={navigateToPage} />
      case 'library': return <Strategies highlightStrategyId={route.highlight} defaultTab={route.tab} onNavigate={navigateToPage} />
      case 'strategy': return <StrategyPassport strategyId={route.strategyId} onNavigate={navigateToPage} walletAddr={walletAddr} />
      case 'corpus': return <CorpusExplorer />
      case 'quant': return <QuantLab />
      case 'portfolio': return (
        <WalletGate
          walletAddr={walletAddr}
          pageName="Portfolio"
          description="Portfolio needs a verified linked wallet because vault deposits and withdrawals are on-chain actions."
          onConnect={openConnectModal}
        >
          <Portfolio walletAddr={walletAddr} onSelectVault={selectVault} onSelectTrace={selectTrace} onNavigate={navigateToPage} />
        </WalletGate>
      )
      case 'reasoning': return <Reasoning onNavigate={navigateToPage} />
      case 'learnings': return (
        <WalletGate
          walletAddr={walletAddr}
          pageName="Learnings"
          description="Link wallet controlling your deployed vaults to review their outcomes."
          onConnect={openConnectModal}
        >
          <Learnings onNavigate={navigateToPage} />
        </WalletGate>
      )
      case 'insights': return <Insights />
      case 'vault-detail': return <VaultDetail address={route.vaultAddress} onBack={() => navigateToPage('portfolio')} />
      case 'marketplace': return <MarketplacePage onNavigate={navigateToPage} />
      case 'market-strategy': return <StrategyDetailPage strategyId={route.strategyId} onNavigate={navigateToPage} />
      case 'publish': return <PublishPage onNavigate={navigateToPage} />
      case 'subscriptions': return <SubscriptionsPage onNavigate={navigateToPage} />
      case 'account': return <AccountSettings walletAddr={walletAddr} onDisconnect={handleDisconnect} />
      default: return null
    }
  }

  return (
    <>
      <Layout
        page={route.page}
        setPage={navigateToPage}
        walletAddr={walletAddr}
        onConnect={setWalletAddr}
        onDisconnect={handleDisconnect}
        onOpenTour={() => setTourOpen(true)}
        user={user}
        features={features}
      >
        {renderPage()}
      </Layout>
      <OnboardingTour
        open={tourOpen}
        onClose={() => {
          setTourOpen(false)
          try { localStorage.setItem('archimedes.onboarding.v1', 'completed') } catch { /* non-fatal */ }
        }}
        setPage={navigateToPage}
      />
    </>
  )
}
