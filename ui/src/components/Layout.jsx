import { useState, useEffect } from 'react'
import WalletConnect from './WalletConnect'
import Breadcrumbs from './Breadcrumbs'
import WelcomeProfileModal from './WelcomeProfileModal'
import { NEW_CONTRACTS } from '../config'

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
    { id: 'generate', label: 'Generate', icon: 'i-lucide-sparkles' },
    { id: 'library',  label: 'Library',  icon: 'i-lucide-line-chart' },
  ]},
  { group: 'Position', items: [
    { id: 'portfolio', label: 'Portfolio', icon: 'i-lucide-layout-dashboard' },
    { id: 'reasoning', label: 'Reasoning', icon: 'i-lucide-brain' },
    { id: 'learnings', label: 'Learnings', icon: 'i-lucide-graduation-cap' },
  ]},
]

export const PAGE_LABELS = {
  landing: 'Home',
  explore: 'Explore',
  generate: 'Generate',
  architecture: 'Architecture',
  library: 'Library',
  corpus: 'Corpus',
  portfolio: 'Portfolio',
  reasoning: 'Reasoning',
  learnings: 'Learnings',
  'vault-detail': 'Vault Details',
  about: 'About',
  imprint: 'Imprint',
}

export default function Layout({ page, setPage, walletAddr, onConnect, onDisconnect, onOpenTour, children }) {
  const [menuOpen, setMenuOpen] = useState(false)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [userProfile, setUserProfile] = useState(null)
  const [showWelcomeModal, setShowWelcomeModal] = useState(false)
  const [showEditProfile, setShowEditProfile] = useState(false)
  const blockLabel = Object.keys(NEW_CONTRACTS).length ? 'Arc · Testnet live' : 'Arc · Connecting'

  const API_BASE = import.meta.env.VITE_API_BASE ?? ''

  // Fetch profile when wallet connects
  useEffect(() => {
    if (!walletAddr) {
      setUserProfile(null)
      return
    }
    // Check localStorage gate — only show welcome modal once per wallet
    const seen = localStorage.getItem('archimedes.welcomeProfileSeen.' + walletAddr.toLowerCase())
    fetch(`${API_BASE}/api/user/profile/${walletAddr}`, {
        headers: { 'X-Wallet-Address': walletAddr },
      })
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        setUserProfile(data)
        // If no profile and not seen, show welcome modal
        if (!data && !seen) {
          setShowWelcomeModal(true)
        }
      })
      .catch(() => {
        // Profile fetch failed — show modal if not seen
        if (!seen) setShowWelcomeModal(true)
      })
    // API_BASE is a module-level constant; excluded intentionally.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [walletAddr])

  const handleWelcomeDone = (profile) => {
    setShowWelcomeModal(false)
    if (profile) setUserProfile(profile)
  }

  const handleEditProfileDone = (profile) => {
    setShowEditProfile(false)
    if (profile) setUserProfile(profile)
  }

  const displayName = userProfile?.display_name

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
          onClick={() => setMenuOpen(false)}
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
              onClick={() => setMenuOpen(false)}
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
              {group.items.map(item => (
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
            <WalletConnect
              address={walletAddr}
              displayName={displayName}
              onConnect={onConnect}
              onDisconnect={onDisconnect}
              onEditProfile={() => setShowEditProfile(true)}
            />
          </div>
        </div>
        <main className={`page-content page-${page}`}>{children}</main>
      </div>

      {/* Welcome profile modal — opens once on first wallet connect */}
      {showWelcomeModal && walletAddr && (
        <WelcomeProfileModal
          walletAddr={walletAddr}
          onDone={handleWelcomeDone}
        />
      )}

      {/* Edit profile modal — triggered from the wallet menu dropdown */}
      {showEditProfile && walletAddr && (
        <WelcomeProfileModal
          walletAddr={walletAddr}
          onDone={handleEditProfileDone}
          mode="edit"
          existingProfile={userProfile}
        />
      )}
    </div>
  )
}
