/**
 * Breadcrumb navigation for interior pages.
 *
 * Derives the crumb trail from the current page ID using the same
 * NAV group structure defined in Layout.jsx. Each crumb is clickable
 * and navigates via the `setPage` callback.
 */

import { PAGE_LABELS } from './Layout'
import { roadmapSurfaceHidden } from '../featureFlags.js'

// `group: null` means "no intermediate label" — breadcrumb reads "Home / <page>".
// Mirrors Layout.jsx NAV groups: Discover, Strategy, Position, Market, Ops.
// Every key must exist in App.jsx PAGE_TO_PATH — subset invariant enforced by
// backend/tests/test_breadcrumbs.py (prevents a fifth stale-map occurrence).
//
// Deliberately excluded:
//   - landing — it *is* Home; breadcrumb would be circular.
//   - vault-detail, strategy, market-strategy — dynamic routes with an id/address
//     param, not entries in PAGE_TO_PATH; reached via deep-link only.
// Primary path (generate, leaderboard, library, corpus, architecture) all have
// entries below; if one is intentionally omitted a comment must explain why.
export const CRUMB_MAP = {
  // Discover — open to anonymous visitors
  explore:      { group: null, groupPage: null },
  corpus:       { group: 'Discover', groupPage: 'explore' },
  architecture: { group: 'Discover', groupPage: 'explore' },
  // Strategy — wallet-gated, owns the primary generation path
  generate:     { group: null, groupPage: null },
  library:      { group: 'Strategy', groupPage: 'generate' },
  paper:        { group: 'Strategy', groupPage: 'generate' },
  leaderboard:  { group: 'Strategy', groupPage: 'generate' },
  // Position — wallet-gated deployed-state surfaces
  portfolio:    { group: null, groupPage: null },
  quant:        { group: 'Position', groupPage: 'portfolio' },
  reasoning:    { group: 'Position', groupPage: 'portfolio' },
  learnings:    { group: 'Position', groupPage: 'portfolio' },
  // Market — strategy marketplace
  marketplace:  { group: null, groupPage: null },
  publish:      { group: 'Market', groupPage: 'marketplace' },
  subscriptions:{ group: 'Market', groupPage: 'marketplace' },
  // Ops
  insights:     { group: null, groupPage: null },
}

export default function Breadcrumbs({ page, setPage }) {
  const info = CRUMB_MAP[page]
  if (!info) return null

  // When the group's landing page is a hidden roadmap surface (#1266 —
  // quant/reasoning point at Portfolio; learnings is itself hidden), the
  // mid-crumb would link into a not-found route: fall back to a flat
  // Home / <page> crumb.
  const crumbs = [
    { label: 'Home', page: 'explore' },
    ...(info.group && !roadmapSurfaceHidden(info.groupPage)
      ? [{ label: info.group, page: info.groupPage }]
      : []),
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
