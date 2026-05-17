import WalletConnect from './WalletConnect'
import Breadcrumbs from './Breadcrumbs'
import { NEW_CONTRACTS } from '../config'

const NAV = [
  { group: 'Markets', items: [
    { id: 'explore',    label: 'Explore' },
    { id: 'strategies', label: 'Strategies' },
    { id: 'trade',      label: 'Trade' },
  ]},
  { group: 'Portfolio', items: [
    { id: 'dashboard',    label: 'Dashboard' },
    { id: 'mint',         label: 'Mint / Burn' },
    { id: 'liquidity',    label: 'Liquidity' },
    { id: 'vaults',       label: 'Vaults' },
    { id: 'create-vault', label: 'Create Vault' },
    { id: 'financial',    label: 'Financial Analysis' },
  ]},
  { group: 'Intelligence', items: [
    { id: 'reasoning',  label: 'Reasoning' },
    { id: 'risk',        label: 'Risk Analysis' },
  ]},
]

export const PAGE_LABELS = {
  explore: 'Explore',
  strategies: 'Strategies',
  trade: 'Trade',
  dashboard: 'Dashboard',
  mint: 'Mint / Burn',
  liquidity: 'Liquidity',
  vaults: 'Vaults',
  'create-vault': 'Create Vault',
  financial: 'Financial Analysis',
  'vault-detail': 'Vault Details',
  reasoning: 'Reasoning',
  risk: 'Risk Analysis',
  about: 'About',
  imprint: 'Imprint',
}

export default function Layout({ page, setPage, walletAddr, onConnect, onDisconnect, children }) {
  const blockLabel = Object.keys(NEW_CONTRACTS).length ? 'Arc · Testnet live' : 'Arc · Connecting'

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="sidebar-brand">
          <div className="logo-mark">
            <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <path d="M8 1L14 5v6l-6 4-6-4V5l6-4z" stroke="#09090B" strokeWidth="1.5"/>
              <circle cx="8" cy="8" r="2.5" stroke="#09090B" strokeWidth="1.5"/>
            </svg>
          </div>
          <div>
            <div className="logo-text">Archimedes</div>
            <div className="logo-sub">Portfolio Intelligence</div>
          </div>
        </div>

        <nav>
          {NAV.map(group => (
            <div key={group.group} className="nav-group">
              <div className="nav-group-label">{group.group}</div>
              {group.items.map(item => (
                <button
                  key={item.id}
                  type="button"
                  className={`nav-link${page === item.id || (item.id === 'vaults' && page === 'vault-detail') ? ' active' : ''}`}
                  onClick={() => setPage(item.id)}
                >
                  {item.label}
                </button>
              ))}
            </div>
          ))}
        </nav>

        <div className="sidebar-footer">
          <span className="live-dot" />
          {blockLabel}
        </div>
      </aside>

      <div className="main-area">
        <div className="topbar">
          <Breadcrumbs page={page} setPage={setPage} />
          <WalletConnect address={walletAddr} onConnect={onConnect} onDisconnect={onDisconnect} />
        </div>
        <main className="page-content">{children}</main>
      </div>
    </div>
  )
}
