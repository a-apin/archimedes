import { useEffect, useState } from 'react'
import AssetModal from './AssetModal'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

// /explore — Read-only viewer for the market data the strategy engine sees.
// No wallet required, no trade affordance. Per docs/specs/page-roles-spec.md,
// this is the discovery surface that helps a user form an opinion about what
// to ask Generate to build around.

function fmtPrice(v) {
  if (v == null || Number.isNaN(v)) return '—'
  if (v >= 1000) return `$${v.toFixed(0)}`
  if (v >= 10) return `$${v.toFixed(2)}`
  return `$${v.toFixed(4)}`
}

function fmtPct(v, digits = 2) {
  if (v == null || Number.isNaN(v)) return '—'
  const sign = v >= 0 ? '+' : ''
  return `${sign}${v.toFixed(digits)}%`
}

function changeClass(v) {
  if (v == null || Number.isNaN(v)) return ''
  return v >= 0 ? 'positive' : 'negative'
}

export default function Explore() {
  const [assets, setAssets] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [filterClass, setFilterClass] = useState('all')
  const [openAsset, setOpenAsset] = useState(null)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/explore/assets`)
        if (!res.ok) throw new Error(`Backend returned ${res.status}`)
        const data = await res.json()
        if (!cancelled) {
          setAssets(data.assets || [])
          setError('')
        }
      } catch (e) {
        // Never render the response body as the error message — nginx 502s
        // come back as multi-line HTML and would splat across the page.
        // Same anti-pattern fixed in GenerationStatus.jsx (#323).
        const msg = e?.message && e.message.length < 120 ? e.message : 'Failed to load assets'
        if (!cancelled) setError(msg)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    // Reload every minute — page is read-only but oracle data drifts.
    const interval = setInterval(load, 60_000)
    return () => { cancelled = true; clearInterval(interval) }
  }, [])

  const classes = ['all', ...Array.from(new Set(assets.map(a => a.asset_class).filter(Boolean)))]
  const filtered = filterClass === 'all' ? assets : assets.filter(a => a.asset_class === filterClass)

  // Banner only fires when *every* asset's displayed price is itself stale.
  // The backend now treats a missing on-chain oracle as "not stale" when
  // yfinance is the actual price source, so this banner is honest: it means
  // the feed pipeline is genuinely broken, not just "the oracle slot is
  // unused for this asset". See asset_market_service.py docstring.
  const allStale = assets.length > 0 && assets.every(a => a.is_stale)
  const someStale = !allStale && assets.some(a => a.is_stale)

  return (
    <div>
      {/* Top-of-page header & explanation */}
      <div style={{ maxWidth: 760, marginBottom: 28 }}>
        <h2 className="serif" style={{ fontSize: '2rem', marginBottom: 10 }}>Explore</h2>
        <p className="body" style={{ marginBottom: 8, fontWeight: 500 }}>
          Explore is a read-only viewer for the market data the strategy engine sees.
        </p>
        <p className="body" style={{ color: 'var(--text-3)' }}>
          Browse the universe of synthetic assets that Archimedes can allocate into,
          look at current spot prices, recent moves, and 30-day volatility, then form
          an opinion about what looks over- or under-valued. When you're ready,
          head to Generate and describe a strategy around the names that caught your eye —
          nothing on this page places a trade or moves a position.
        </p>
        <p className="caption" style={{ color: 'var(--text-4)', marginTop: 8 }}>
          Click any card for full detail, price-history chart, and the upstream source the
          price came from (on-chain oracle vs. off-chain fallback).
        </p>
      </div>

      {/* Filter pills */}
      <div className="strat-filter-bar" style={{ marginBottom: 18 }}>
        {classes.map(c => (
          <span
            key={c}
            className={`tag ${filterClass === c ? 'tag-accent' : 'tag-muted'}`}
            onClick={() => setFilterClass(c)}
            style={{ cursor: 'pointer' }}
          >
            {c === 'all' ? 'All' : c.replace(/_/g, ' ')}
            {c !== 'all' && ` (${assets.filter(a => a.asset_class === c).length})`}
          </span>
        ))}
      </div>

      {/* Loading / error / empty states */}
      {loading && !assets.length && <div className="caption">Loading market data…</div>}
      {error && !assets.length && (
        <div className="info-box warning" style={{ marginBottom: 16 }}>
          Couldn't load assets: {error}.
        </div>
      )}
      {!loading && !error && assets.length === 0 && (
        <div className="info-box" style={{ marginBottom: 16 }}>
          No market data available right now. This page refreshes automatically.
        </div>
      )}

      {/* Banner — only when something is actually wrong with the feed. */}
      {allStale && (
        <div className="info-box warning" style={{ marginBottom: 16 }}>
          Every asset's price feed is older than the freshness threshold.
          The upstream market-data pipeline appears to be paused; values shown may be outdated.
        </div>
      )}
      {someStale && (
        <div className="info-box" style={{ marginBottom: 16, fontSize: '0.82rem' }}>
          Some assets have stale price feeds (marked with a STALE badge on the card).
          Most assets are current.
        </div>
      )}

      {/* Asset card grid */}
      {filtered.length > 0 && (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
            gap: 14,
          }}
        >
          {filtered.map(a => (
            <button
              key={a.symbol}
              type="button"
              onClick={() => setOpenAsset(a)}
              className="card-flat"
              style={{
                textAlign: 'left',
                padding: 16,
                background: 'rgba(255,255,255,0.02)',
                border: '1px solid var(--glass-border)',
                borderRadius: 8,
                cursor: 'pointer',
                color: 'inherit',
                font: 'inherit',
                transition: 'background 0.15s, border-color 0.15s',
              }}
              onMouseEnter={e => {
                e.currentTarget.style.background = 'rgba(255,255,255,0.05)'
                e.currentTarget.style.borderColor = 'var(--text-4)'
              }}
              onMouseLeave={e => {
                e.currentTarget.style.background = 'rgba(255,255,255,0.02)'
                e.currentTarget.style.borderColor = 'var(--glass-border)'
              }}
              aria-label={`Open details for ${a.symbol}`}
            >
              <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 6 }}>
                <div>
                  <div style={{ fontSize: '1.15rem', fontWeight: 700, lineHeight: 1.1 }}>{a.symbol}</div>
                  <div className="caption" style={{
                    color: 'var(--text-4)',
                    fontSize: '0.7rem',
                    marginTop: 2,
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    maxWidth: 180,
                  }}>
                    {a.name || '—'}
                  </div>
                </div>
                {a.is_stale && (
                  <span
                    className="tag"
                    style={{
                      fontSize: '0.6rem',
                      background: 'rgba(239,68,68,0.10)',
                      color: 'var(--negative)',
                      borderRadius: 4,
                      padding: '1px 5px',
                      whiteSpace: 'nowrap',
                    }}
                    title="The displayed price is older than the freshness window"
                  >
                    STALE
                  </span>
                )}
              </div>

              <div className="mono" style={{ fontSize: '1.4rem', fontWeight: 600, marginTop: 12 }}>
                {fmtPrice(a.current_price)}
              </div>

              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginTop: 6 }}>
                <span className={`mono ${changeClass(a.change_24h_pct)}`} style={{ fontSize: '0.85rem' }}>
                  {fmtPct(a.change_24h_pct)}
                </span>
                <span className="caption" style={{ color: 'var(--text-4)', fontSize: '0.65rem' }}>
                  24h
                </span>
              </div>
            </button>
          ))}
        </div>
      )}

      {/* Footer disclosure */}
      <p className="caption" style={{ marginTop: 22, color: 'var(--text-4)' }}>
        Prices come from the on-chain PriceOracle when available, with yfinance as the
        off-chain fallback. "STALE" means the displayed price is itself older than the
        freshness window (5 minutes for the oracle, ~4 days for daily-close fallback).
        The "Vol 30d" metric in the detail modal is annualized realized volatility
        (std of daily returns × √252).
      </p>

      {openAsset && <AssetModal asset={openAsset} onClose={() => setOpenAsset(null)} />}
    </div>
  )
}
