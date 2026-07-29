import { useState, useEffect, useRef } from 'react'
import WalletConnect from './WalletConnect'
import Breadcrumbs from './Breadcrumbs'
import { NEW_CONTRACTS, getStoredWalletName } from '../config'
import { getStoredTheme, applyTheme } from '../theme'
import { visibleNavigation } from '../routes'
import { lockBodyScroll, unlockBodyScroll } from '../utils/scrollLock'

// Sidebar groups separate Home (anchor / landing) from the three product-state
// bands. Empty group label is intentional for the Home entry — it renders as a
// header-less section so Home reads as the top-of-shell anchor, not a peer of
// the other groups. The three labelled groups split the remaining surfaces
// along the gating boundary:
//   DISCOVER — open to anonymous visitors (no wallet needed)
//   STRATEGY — wallet-gated: generate + your saved strategies
//   POSITION — wallet-gated: deployed vaults, on-chain audit, post-hoc review
// Item order inside DISCOVER (Explore → Corpus → Architecture) follows the
// natural user-onboarding read: browse the seed strategies first, see the
// substrate they're drawn from second, see the system that fuses them third.
const NAV = [
  { group: null, items: [
    { id: 'landing', label: 'Home', icon: 'i-lucide-home' },
  ]},
  { group: 'Discover', items: [
    { id: 'explore',      label: 'Explore',      icon: 'i-lucide-compass' },
    { id: 'corpus',       label: 'Corpus',       icon: 'i-lucide-library' },
    { id: 'architecture', label: 'Architecture', icon: 'i-lucide-network' },
  ]},
  { group: 'Strategy', items: [
    { id: 'generate',     label: 'Generate',     icon: 'i-lucide-sparkles' },
    { id: 'library',      label: 'Library',      icon: 'i-lucide-line-chart' },
    // Leaderboard lives in STRATEGY (#1077): it ranks the strategy library —
    // discovery-friendly but strategy-native. (Quant Lab moved to Position.)
    { id: 'leaderboard',  label: 'Leaderboard',  icon: 'i-lucide-trophy' },
  ]},
  { group: 'Position', items: [
    { id: 'portfolio', label: 'Portfolio', icon: 'i-lucide-layout-dashboard' },
    // Re-added (#1060 AC#3, Dan's call 2026-07-14): the livestream-era hiding
    // (#1061) was for the synthetic-sample-data version; this PR wires the
    // panels to live library/vault/trace data with per-section disclaimers.
    // Lives in POSITION (Dan, 2026-07-14): its panels read the user's live
    // vault/trace data, so it belongs with the deployed-state surfaces and is
    // wallet-gated like them (see App.jsx).
    { id: 'quant',     label: 'Quant Lab', icon: 'i-lucide-flask-conical' },
    { id: 'reasoning', label: 'Reasoning', icon: 'i-lucide-brain' },
    { id: 'learnings', label: 'Learnings', icon: 'i-lucide-graduation-cap' },
  ]},
  { group: 'Market', items: [
    { id: 'marketplace',   label: 'Marketplace',   icon: 'i-lucide-shopping-bag' },
    { id: 'publish',       label: 'Publish',       icon: 'i-lucide-megaphone' },
    { id: 'subscriptions', label: 'Subscriptions', icon: 'i-lucide-bell' },
  ]},
  { group: 'Ops', items: [
    { id: 'insights', label: 'Insights', icon: 'i-lucide-bar-chart-3' },
    { id: 'account', label: 'Account', icon: 'i-lucide-user-round-cog' },
  ]},
]

export const PAGE_LABELS = {
  landing: 'Home',
  explore: 'Explore',
  leaderboard: 'Leaderboard',
  generate: 'Generate',
  architecture: 'Architecture',
  library: 'Library',
  corpus: 'Corpus',
  quant: 'Quant Lab',
  portfolio: 'Portfolio',
  reasoning: 'Reasoning',
  learnings: 'Learnings',
  insights: 'Insights',
  'vault-detail': 'Vault Details',
  about: 'About',
  imprint: 'Imprint',
  marketplace: 'Marketplace',
  'market-strategy': 'Strategy Detail',
  publish: 'Publish',
  subscriptions: 'Subscriptions',
  account: 'Account',
}

export default function Layout({ page, setPage, walletAddr, onConnect, onDisconnect, onOpenTour, user, features, children }) {
  const [menuOpen, setMenuOpen] = useState(false)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [theme, setTheme] = useState(getStoredTheme)
  const hamburgerRef = useRef(null)
  const blockLabel = Object.keys(NEW_CONTRACTS).length ? 'Arc · Testnet live' : 'Arc · Connecting'

  // Lock body scroll while the mobile nav drawer is open — otherwise the
  // page content underneath can still scroll behind the fixed overlay/drawer,
  // which reads as janky rather than a clean modal-style drawer.
  //
  // Uses the shared ref-counted lock (utils/scrollLock.js) rather than
  // saving/restoring document.body.style.overflow directly: AssetModal.jsx
  // can be open at the same time as this drawer, and a naive save/restore
  // in either place can wrongly re-enable scroll while the other is still
  // open (whichever closes first "restores" a stale value). The counter
  // only clears overflow once every locker has released it. AssetModal.jsx
  // isn't touched here — it keeps its own independent lock for now — but
  // this same helper is available for it to adopt.
  useEffect(() => {
    if (!menuOpen) return
    lockBodyScroll()
    return () => unlockBodyScroll()
  }, [menuOpen])

  const closeMenu = () => {
    setMenuOpen(false)
    // Return focus to the hamburger button on close for keyboard/screen-reader
    // parity — otherwise focus is dropped when the drawer unmounts/hides.
    hamburgerRef.current?.focus()
  }

  const toggleTheme = () => {
    const next = theme === 'light' ? 'dark' : 'light'
    applyTheme(next)
    setTheme(next)
  }

  // Circle wallet names describe wallet, never application identity.
  const displayName = walletAddr ? getStoredWalletName(walletAddr) : null

  const handleNav = (id) => {
    setPage(id)
    setMenuOpen(false)
  }

  return (
    <div className={`shell${sidebarCollapsed ? ' shell-sidebar-collapsed' : ''}`}>
      {/* Mobile overlay — uses UnoCSS `fixed inset-0` + App.css `.sidebar-overlay` */}
      {menuOpen && (
        <div
          className="fixed inset-0 sidebar-overlay"
          onClick={closeMenu}
          aria-hidden="true"
        />
      )}

      <aside className={`sidebar${menuOpen ? ' sidebar-open' : ''}${sidebarCollapsed ? ' sidebar-collapsed' : ''}`}>
        <div className="sidebar-brand">
          <div className="sidebar-brand-main">
            <div className="logo-mark">
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
                <rect width="32" height="32" rx="4" fill="#0a0a0b"/>
                <text x="16" y="23" textAnchor="middle" fontFamily="serif" fontSize="22" fill="#e0a64f">Λ</text>
              </svg>
            </div>
            <div className="logo-copy flex-1 min-w-0">
              <div className="logo-text">Archimedes</div>
              <div className="logo-sub">Portfolio Intelligence</div>
            </div>
            <button
              className="sidebar-close-btn"
              onClick={closeMenu}
              aria-label="Close menu"
            >
              <span className="i-lucide-x" style={{width:16,height:16}} />
            </button>
          </div>
        </div>

        <nav>
          {NAV.map((group, gi) => (
            <div key={group.group || gi} className="nav-group">
              {group.group && <div className="nav-group-label">{group.group}</div>}
              {visibleNavigation(group.items, features).map(item => (
                <button
                  key={item.id}
                  type="button"
                  data-tour={item.id}
                  className={`nav-link${page === item.id || (item.id === 'portfolio' && page === 'vault-detail') ? ' active' : ''}`}
                  onClick={() => handleNav(item.id)}
                  aria-label={item.label}
                  title={sidebarCollapsed ? item.label : undefined}
                >
                  <span className={`nav-icon ${item.icon}`} aria-hidden="true" />
                  <span className="nav-label">{item.label}</span>
                </button>
              ))}
            </div>
          ))}
        </nav>

        <div className="sidebar-footer">
          <span className="live-dot" />
          <span className="sidebar-footer-label">{blockLabel}</span>
          <button
            type="button"
            className="sidebar-collapse-btn"
            onClick={() => setSidebarCollapsed(v => !v)}
            aria-label={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            aria-expanded={!sidebarCollapsed}
            title={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          >
            <span className={sidebarCollapsed ? 'i-lucide-panel-left-open' : 'i-lucide-panel-left-close'} style={{width:18,height:18}} />
          </button>
        </div>
      </aside>

      <div className="main-area">
        <div className="topbar">
          {/* Left: hamburger (mobile) + breadcrumbs */}
          <div className="flex items-center gap-3">
            <button
              ref={hamburgerRef}
              className={`hamburger-btn${menuOpen ? ' open' : ''}`}
              onClick={() => setMenuOpen(v => !v)}
              aria-label="Toggle navigation"
              aria-expanded={menuOpen}
            >
              <span className="hamburger-line" />
              <span className="hamburger-line" />
              <span className="hamburger-line" />
            </button>
            <Breadcrumbs page={page} setPage={setPage} />
          </div>
          <div className="flex items-center gap-2">
            {/* Personalized greeting moved into the WalletConnect dropdown
                header so the topbar stays compact + the greeting lives next
                to the wallet identity it belongs to. */}
            <button
              type="button"
              className="topbar-icon-btn"
              onClick={toggleTheme}
              aria-label={theme === 'light' ? 'Switch to dark theme' : 'Switch to light theme'}
              title={theme === 'light' ? 'Switch to dark theme' : 'Switch to light theme'}
            >
              <span className={theme === 'light' ? 'i-lucide-moon' : 'i-lucide-sun'} style={{width:18,height:18}} />
            </button>
            {onOpenTour && (
              <button
                type="button"
                className="topbar-icon-btn"
                onClick={onOpenTour}
                aria-label="Open onboarding tour"
                title="What is Archimedes? — open the tour"
              >
                <span className="i-lucide-help-circle" style={{width:18,height:18}} />
              </button>
            )}
            <button
              type="button"
              className="wallet-chip"
              onClick={() => handleNav('account')}
              title={user?.email}
            >
              <span className="i-lucide-user-round" style={{width:14,height:14}} />
              <span>{user?.name || 'Account'}</span>
            </button>
            <WalletConnect
              address={walletAddr}
              displayName={displayName}
              onConnect={onConnect}
              onDisconnect={onDisconnect}
              onEditProfile={() => handleNav('account')}
            />
          </div>
        </div>
        <main className={`page-content page-${page}`}>{children}</main>
      </div>
    </div>
  )
}
