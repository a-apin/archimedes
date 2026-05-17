/**
 * Breadcrumb navigation for interior pages.
 *
 * Derives the crumb trail from the current page ID using the same
 * NAV group structure defined in Layout.jsx. Each crumb is clickable
 * and navigates via the `setPage` callback.
 */

import { PAGE_LABELS } from './Layout'

const CRUMB_MAP = {
  explore:      { group: 'Markets', groupPage: 'explore' },
  strategies:   { group: 'Markets', groupPage: 'explore' },
  trade:        { group: 'Markets', groupPage: 'explore' },
  dashboard:    { group: 'Portfolio', groupPage: 'dashboard' },
  mint:         { group: 'Portfolio', groupPage: 'dashboard' },
  liquidity:    { group: 'Portfolio', groupPage: 'dashboard' },
  vaults:       { group: 'Portfolio', groupPage: 'vaults' },
  'create-vault': { group: 'Portfolio', groupPage: 'vaults' },
  financial:    { group: 'Portfolio', groupPage: 'dashboard' },
  'vault-detail': { group: 'Portfolio', groupPage: 'vaults' },
  reasoning:    { group: 'Intelligence', groupPage: 'reasoning' },
  risk:         { group: 'Intelligence', groupPage: 'reasoning' },
}

export default function Breadcrumbs({ page, setPage }) {
  const info = CRUMB_MAP[page]
  if (!info) return null

  const crumbs = [
    { label: 'Home', page: 'explore' },
    { label: info.group, page: info.groupPage },
    { label: PAGE_LABELS[page] ?? page, page: null },
  ]

  return (
    <nav className="breadcrumbs" aria-label="Breadcrumb">
      {crumbs.map((crumb, i) => {
        const isLast = i === crumbs.length - 1
        return (
          <span key={i} className="breadcrumb-item">
            {i > 0 && <span className="breadcrumb-sep">/</span>}
            {isLast ? (
              <span className="breadcrumb-current">{crumb.label}</span>
            ) : (
              <button
                type="button"
                className="breadcrumb-link"
                onClick={() => setPage(crumb.page)}
              >
                {crumb.label}
              </button>
            )}
          </span>
        )
      })}
    </nav>
  )
}
