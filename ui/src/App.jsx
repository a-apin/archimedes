import { useState, useEffect, useCallback } from 'react'
import { disconnectWallet, reconnectWallet } from './config'
import Layout from './components/Layout'
import Landing from './components/Landing'
import Explore from './components/Explore'
import Generate from './components/Generate'
import Portfolio from './components/Portfolio'
import Learnings from './components/Learnings'
import Strategies from './components/Strategies'   // serves /library route ("Example Library")
import StrategyPassport from './components/StrategyPassport'
import CorpusExplorer from './components/CorpusExplorer'
import Reasoning from './components/Reasoning'
import Architecture from './components/Architecture'
import VaultDetail from './components/VaultDetail'
import OnboardingTour, { hasCompletedOnboarding } from './components/OnboardingTour'
import WalletGate from './components/WalletGate'
import MobileBanner from './components/MobileBanner'
import './App.css'

const openConnectModal = () => window.dispatchEvent(new Event('open-wallet-modal'))

// Spine routing per docs/user-stories.md. Anything not in this map is gone.
// vault-detail is a deep-link only (reached from Portfolio); reasoning is a
// top-level page until per-card trace-modal affordances are wired everywhere.
const PAGE_TO_PATH = {
  landing:   '/',
  explore:   '/explore',
  generate:  '/generate',
  architecture: '/architecture',
  library:   '/library',
  corpus:    '/corpus',
  portfolio: '/portfolio',
  reasoning: '/reasoning',
  learnings: '/learnings',
  about:     '/about',
  imprint:   '/imprint',
}

const PATH_TO_PAGE = Object.fromEntries(
  Object.entries(PAGE_TO_PATH).map(([page, path]) => [path, page])
)

function resolveRoute(pathname = '/', search = '') {
  const params = new URLSearchParams(search)
  const highlight = params.get('highlight')
  const traceId = params.get('trace_id')
  const tab = params.get('tab')

  if (PATH_TO_PAGE[pathname]) {
    return { page: PATH_TO_PAGE[pathname], vaultAddress: null, traceId, strategyId: null, highlight, tab, matched: true }
  }

  if (pathname.startsWith('/portfolio/vaults/')) {
    const rawAddress = pathname.replace('/portfolio/vaults/', '')
    if (rawAddress) return { page: 'vault-detail', vaultAddress: rawAddress, traceId: null, strategyId: null, highlight, tab: null, matched: true }
  }

  if (pathname.startsWith('/reasoning/')) {
    const id = pathname.replace('/reasoning/', '')
    if (id) return { page: 'reasoning', vaultAddress: null, traceId: id, strategyId: null, highlight, tab: null, matched: true }
  }

  if (pathname.startsWith('/strategy/')) {
    const id = pathname.replace('/strategy/', '')
    if (id) return { page: 'strategy', vaultAddress: null, traceId: null, strategyId: id, highlight, tab: null, matched: true }
  }

  // Legacy paths still in the wild — funnel them to the spine.
  const vaultAddress = params.get('vault')
  if (vaultAddress) return { page: 'vault-detail', vaultAddress, traceId: null, strategyId: null, highlight, tab: null, matched: true }

  return { page: 'landing', vaultAddress: null, traceId: null, strategyId: null, highlight: null, tab: null, matched: false }
}

function pageToPath(page, selectedVault = null, highlight = null, strategyId = null, traceId = null, tab = null) {
  if (page === 'vault-detail' && selectedVault) return `/portfolio/vaults/${selectedVault}`
  if (page === 'strategy' && strategyId) return `/strategy/${encodeURIComponent(strategyId)}`
  const base = PAGE_TO_PATH[page] ?? '/'
  const params = new URLSearchParams()
  if (highlight && page === 'library') params.set('highlight', highlight)
  if (traceId && page === 'reasoning') params.set('trace_id', traceId)
  if (tab && page === 'library') params.set('tab', tab)
  const qs = params.toString()
  return qs ? `${base}?${qs}` : base
}

// ─── Main App ────────────────────────────────────────────────

export default function App() {
  const initialRoute = resolveRoute(window.location.pathname, window.location.search)

  const [page, setPage] = useState(initialRoute.page)
  const [walletAddr, setWalletAddr] = useState(null)
  const [selectedVault, setSelectedVault] = useState(initialRoute.vaultAddress)
  const [selectedStrategy, setSelectedStrategy] = useState(initialRoute.strategyId)
  const [tourOpen, setTourOpen] = useState(() => !hasCompletedOnboarding())
  const [highlightStrategyId, setHighlightStrategyId] = useState(initialRoute.highlight)
  const [defaultTab, setDefaultTab] = useState(initialRoute.tab)

  // Reconnect a previously connected wallet on mount (silent — uses eth_accounts,
  // no popup). The wallet-changed event keeps state in sync if the user changes
  // accounts or chains from within the extension.
  useEffect(() => {
    reconnectWallet().then(result => {
      if (result) setWalletAddr(result.address)
    })
  }, [])

  useEffect(() => {
    const handler = (e) => setWalletAddr(e.detail.address)
    window.addEventListener('wallet-changed', handler)
    return () => window.removeEventListener('wallet-changed', handler)
  }, [])

  const handleConnect = (addr) => setWalletAddr(addr)
  const handleDisconnect = () => { disconnectWallet(); setWalletAddr(null) }

  const navigateToPage = useCallback((nextPage, opts = {}) => {
    const nextVault = opts.vaultAddress ?? selectedVault
    const nextStrategy = Object.prototype.hasOwnProperty.call(opts, 'strategyId')
      ? opts.strategyId
      : (nextPage === 'strategy' ? selectedStrategy : null)
    const nextHighlight = Object.prototype.hasOwnProperty.call(opts, 'highlight')
      ? opts.highlight
      : (nextPage === 'library' ? highlightStrategyId : null)
    const nextTraceId = Object.prototype.hasOwnProperty.call(opts, 'traceId')
      ? opts.traceId
      : null
    const nextTab = Object.prototype.hasOwnProperty.call(opts, 'tab')
      ? opts.tab
      : null
    const nextPath = pageToPath(nextPage, nextVault, nextHighlight, nextStrategy, nextTraceId, nextTab)
    const method = opts.replace ? 'replaceState' : 'pushState'

    if (window.location.pathname + window.location.search !== nextPath) {
      window.history[method]({}, '', nextPath)
    }

    setPage(nextPage)
    if (Object.prototype.hasOwnProperty.call(opts, 'vaultAddress')) {
      setSelectedVault(opts.vaultAddress)
    } else if (nextPage !== 'vault-detail') {
      setSelectedVault(null)
    }
    if (Object.prototype.hasOwnProperty.call(opts, 'strategyId')) {
      setSelectedStrategy(opts.strategyId)
    } else if (nextPage !== 'strategy') {
      setSelectedStrategy(null)
    }
    setHighlightStrategyId(nextPage === 'library' ? nextHighlight : null)
    setDefaultTab(nextPage === 'library' ? nextTab : null)
  }, [selectedVault, selectedStrategy, highlightStrategyId])

  const selectVault = (addr) => navigateToPage('vault-detail', { vaultAddress: addr })
  const backToPortfolio = () => navigateToPage('portfolio', { vaultAddress: null })
  const selectTrace = (id) => navigateToPage('reasoning', { replace: false, traceId: id })

  // Per-route document.title so browser tabs are distinguishable when users
  // have multiple Archimedes tabs open.
  useEffect(() => {
    const titles = {
      landing:        'Archimedes',
      explore:        'Explore · Archimedes',
      generate:       'Generate · Archimedes',
      architecture:   'Architecture · Archimedes',
      library:        'Library · Archimedes',
      corpus:         'Corpus · Archimedes',
      portfolio:      'Portfolio · Archimedes',
      reasoning:      'Reasoning · Archimedes',
      learnings:      'Learnings · Archimedes',
      'vault-detail': 'Vault · Archimedes',
      strategy:       'Strategy · Archimedes',
    }
    document.title = titles[page] ?? 'Archimedes'
  }, [page])

  useEffect(() => {
    if (!initialRoute.matched) {
      window.history.replaceState({}, '', '/')
    }
    const onPopState = () => {
      const route = resolveRoute(window.location.pathname, window.location.search)
      setPage(route.page)
      setSelectedVault(route.vaultAddress)
      setSelectedStrategy(route.strategyId)
      setHighlightStrategyId(route.highlight)
      setDefaultTab(route.tab)
    }
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [initialRoute.matched])

  const renderPage = () => {
    switch (page) {
      case 'landing':     return <Landing onNavigate={navigateToPage} />
      case 'explore':      return <Explore />
      case 'generate':     return (
        <WalletGate
          walletAddr={walletAddr}
          pageName="Generate"
          description="Generate uses an LLM-powered multi-agent pipeline against a 9,873-paper q-fin corpus. Connect a wallet — sign in with a passkey, no extension needed — to run the agent and persist your generated strategies in your library."
          onConnect={openConnectModal}
        >
          <Generate onNavigate={navigateToPage} />
        </WalletGate>
      )
      case 'architecture': return <Architecture onNavigate={navigateToPage} />
      case 'library':      return (
        <WalletGate
          walletAddr={walletAddr}
          pageName="Your Strategies"
          description="Library shows strategies you've generated, plus a clearly-separated set of paper-grounded example strategies. Connect a wallet — sign in with a passkey, no extension needed — to see your generations and deploy them as vaults."
          onConnect={openConnectModal}
        >
          <Strategies highlightStrategyId={highlightStrategyId} defaultTab={defaultTab} onNavigate={navigateToPage} />
        </WalletGate>
      )
      case 'strategy':     return <StrategyPassport strategyId={selectedStrategy} onNavigate={navigateToPage} walletAddr={walletAddr} />
      case 'corpus':       return <CorpusExplorer />
      case 'portfolio':    return (
        <WalletGate
          walletAddr={walletAddr}
          pageName="Portfolio"
          description="Portfolio shows your AUM, your deployed vaults, and the autonomous agent's rebalance decisions. Connect a wallet to deposit USDC and start tracking — this is a non-custodial vault you control, not an account on our platform."
          onConnect={openConnectModal}
        >
          <Portfolio walletAddr={walletAddr} onSelectVault={selectVault} onSelectTrace={selectTrace} onNavigate={navigateToPage} />
        </WalletGate>
      )
      case 'reasoning':    return (
        <WalletGate
          walletAddr={walletAddr}
          pageName="Reasoning"
          description="Reasoning is the audit trail for every autonomous agent decision — hashed off-chain and anchored on Arc via the ReasoningTraceRegistry contract. Connect a wallet to inspect traces and verify hashes against the on-chain registry."
          onConnect={openConnectModal}
        >
          <Reasoning onNavigate={navigateToPage} />
        </WalletGate>
      )
      case 'learnings':    return (
        <WalletGate
          walletAddr={walletAddr}
          pageName="Learnings"
          description="Learnings reviews the strategies you've deployed — winners and losers, both first-class — with the agent's reasoning available for each rebalance. Connect a wallet to see your deployments."
          onConnect={openConnectModal}
        >
          <Learnings onNavigate={navigateToPage} />
        </WalletGate>
      )
      case 'vault-detail': return <VaultDetail address={selectedVault} onBack={backToPortfolio} />
      default:             return <NotFound page={page} onNavigate={navigateToPage} />
    }
  }

  return (
    <>
      <MobileBanner />
      <Layout
        page={page}
        setPage={navigateToPage}
        walletAddr={walletAddr}
        onConnect={handleConnect}
        onDisconnect={handleDisconnect}
        onOpenTour={() => setTourOpen(true)}
      >
        {renderPage()}
      </Layout>
      <OnboardingTour open={tourOpen} onClose={() => setTourOpen(false)} setPage={navigateToPage} />
    </>
  )
}

function NotFound({ page, onNavigate }) {
  return (
    <div className="max-w-[640px]">
      <h2 className="font-serif text-[2rem] mb-3">Page not found</h2>
      <p className="body mb-4">
        We don't have a page at <code>{String(page)}</code>. The spine has six destinations:
        Explore, Generate, Library, Corpus, Portfolio, and Reasoning. Use the sidebar
        on the left, or jump back to the landing page.
      </p>
      <div className="flex gap-3">
        <button className="btn-primary" onClick={() => onNavigate('landing')}>← Home</button>
        <button className="btn-secondary" onClick={() => onNavigate('generate')}>Generate a Strategy</button>
      </div>
    </div>
  )
}
