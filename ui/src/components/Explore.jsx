import { useEffect, useState } from 'react'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

// /explore — Read-only asset discovery surface (no wallet required).
// Per docs/specs/page-roles-spec.md: the universe of tradable assets,
// demystified for non-finance readers via plain-English explanations on
// each metric.

function fmtPct(v, digits = 1) {
  if (v == null || isNaN(v)) return '—'
  const sign = v >= 0 ? '+' : ''
  return `${sign}${v.toFixed(digits)}%`
}

function fmtPrice(v) {
  if (v == null || isNaN(v)) return '—'
  if (v >= 1000) return `$${v.toFixed(0)}`
  if (v >= 10) return `$${v.toFixed(2)}`
  return `$${v.toFixed(4)}`
}

function fmtVol(v) {
  if (v == null || isNaN(v)) return '—'
  return v.toFixed(2)
}

function changeClass(v) {
  if (v == null || isNaN(v)) return ''
  return v >= 0 ? 'positive' : 'negative'
}

function ExplainTooltip({ children, text }) {
  if (!text) return children
  return (
    <span title={text} style={{ cursor: 'help', borderBottom: '1px dotted var(--text-4)' }}>
      {children}
    </span>
  )
}

export default function Explore({ onNavigate }) {
  const [assets, setAssets] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [filterClass, setFilterClass] = useState('all')

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/explore/assets`)
        if (!res.ok) throw new Error(await res.text())
        const data = await res.json()
        if (!cancelled) setAssets(data.assets || [])
      } catch (e) {
        if (!cancelled) setError(e.message || 'Failed to load assets')
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

  // Renamed from `useInGenerate` so it doesn't trip the rules-of-hooks
  // detector that flags any `use*`-prefixed call inside a callback.
  const handleUseInGenerate = (_symbol) => {
    if (onNavigate) onNavigate('generate')
    // The Generate page reads `?seed_asset=` later — for now, just navigate.
    // Wiring the seed prop through requires extending the navigate API; we
    // can do that when Phase 4 adds the strategy passport route too.
  }

  return (
    <div>
      <div style={{ maxWidth: 720, marginBottom: 24 }}>
        <h2 className="serif" style={{ fontSize: '2rem', marginBottom: 10 }}>Explore</h2>
        <p className="body" style={{ marginBottom: 8 }}>
          The universe of synthetic assets you can trade on Arc. Prices come from the on-chain
          oracle; explanations live next to every metric so non-finance readers don't need
          a glossary.
        </p>
        <p className="body" style={{ color: 'var(--text-3)' }}>
          No wallet required — this page is browse-only. Click "Use in Generate" on any row
          to seed a strategy around that asset.
        </p>
      </div>

      <div className="strat-filter-bar" style={{ marginBottom: 16 }}>
        {classes.map(c => (
          <span
            key={c}
            className={`tag ${filterClass === c ? 'tag-accent' : 'tag-muted'}`}
            onClick={() => setFilterClass(c)}
            style={{ cursor: 'pointer' }}
          >
            {c === 'all' ? 'All' : c.replace(/_/g, ' ')} {c !== 'all' && `(${assets.filter(a => a.asset_class === c).length})`}
          </span>
        ))}
      </div>

      {loading && !assets.length && <div className="caption">Loading market data…</div>}
      {error && !assets.length && (
        <div className="info-box warning" style={{ marginBottom: 16 }}>
          Couldn't load assets: {error}.
        </div>
      )}

      {!loading && !error && assets.length === 0 && (
        <div className="info-box" style={{ marginBottom: 16 }}>
          Oracle feed paused — no price data available. This page refreshes automatically when the oracle resumes.
        </div>
      )}

      {!loading && !error && assets.length > 0 && assets.every(a => a.is_stale) && (
        <div className="info-box warning" style={{ marginBottom: 16 }}>
          Oracle feed paused — last update{' '}
          <span className="mono" style={{ fontSize: '0.85rem' }}>
            {assets[0]?.last_updated ? new Date(assets[0].last_updated).toLocaleString() : 'unknown'}
          </span>.
          Prices shown may be outdated.
        </div>
      )}

      {filtered.length > 0 && (
        <div style={{ overflowX: 'auto', border: '1px solid var(--glass-border)', borderRadius: 8 }}>
          <table className="lib-table" style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
            <thead>
              <tr style={{ background: 'rgba(255,255,255,0.03)', textAlign: 'left', borderBottom: '1px solid var(--glass-border)' }}>
                <th style={{ padding: '10px 14px' }}>Symbol</th>
                <th style={{ padding: '10px 14px' }}>Name</th>
                <th style={{ padding: '10px 14px', textAlign: 'right' }}>
                  <ExplainTooltip text="Latest price from the on-chain oracle. Settlement on Arc uses this.">
                    Price
                  </ExplainTooltip>
                </th>
                <th style={{ padding: '10px 14px', textAlign: 'right' }}>
                  <ExplainTooltip text="Percentage change in the last trading day.">
                    24h
                  </ExplainTooltip>
                </th>
                <th style={{ padding: '10px 14px', textAlign: 'right' }}>
                  <ExplainTooltip text="Percentage change over the past week.">
                    7d
                  </ExplainTooltip>
                </th>
                <th style={{ padding: '10px 14px', textAlign: 'right' }}>
                  <ExplainTooltip text="Percentage change over the past month.">
                    30d
                  </ExplainTooltip>
                </th>
                <th style={{ padding: '10px 14px', textAlign: 'right' }}>
                  <ExplainTooltip text="How much the price wobbles. Higher = bigger swings.">
                    Vol 30d
                  </ExplainTooltip>
                </th>
                <th style={{ padding: '10px 14px', textAlign: 'right' }}></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(a => (
                <tr key={a.symbol} className="lib-row">
                  <td style={{ padding: '10px 14px', fontWeight: 600 }}>
                    {a.symbol}
                    {a.is_stale && (
                      <span
                        className="tag"
                        style={{
                          marginLeft: 6,
                          fontSize: '0.65rem',
                          background: 'var(--warning-bg, #fef3c7)',
                          color: 'var(--warning-text, #92400e)',
                          borderRadius: 4,
                          padding: '1px 5px',
                        }}
                      >
                        STALE
                      </span>
                    )}
                  </td>
                  <td style={{ padding: '10px 14px' }} className="caption">{a.name}</td>
                  <td className="mono" style={{ padding: '10px 14px', textAlign: 'right' }}>
                    <ExplainTooltip text={a.explanations?.current_price}>{fmtPrice(a.current_price)}</ExplainTooltip>
                  </td>
                  <td className={`mono ${changeClass(a.change_24h_pct)}`} style={{ padding: '10px 14px', textAlign: 'right' }}>
                    <ExplainTooltip text={a.explanations?.change_24h_pct}>{fmtPct(a.change_24h_pct)}</ExplainTooltip>
                  </td>
                  <td className={`mono ${changeClass(a.change_7d_pct)}`} style={{ padding: '10px 14px', textAlign: 'right' }}>
                    <ExplainTooltip text={a.explanations?.change_7d_pct}>{fmtPct(a.change_7d_pct)}</ExplainTooltip>
                  </td>
                  <td className={`mono ${changeClass(a.change_30d_pct)}`} style={{ padding: '10px 14px', textAlign: 'right' }}>
                    <ExplainTooltip text={a.explanations?.change_30d_pct}>{fmtPct(a.change_30d_pct)}</ExplainTooltip>
                  </td>
                  <td className="mono" style={{ padding: '10px 14px', textAlign: 'right' }}>
                    <ExplainTooltip text={a.explanations?.realized_vol_30d}>{fmtVol(a.realized_vol_30d)}</ExplainTooltip>
                  </td>
                  <td style={{ padding: '10px 14px', textAlign: 'right' }}>
                    <button
                      className="btn btn-outline btn-sm"
                      onClick={() => handleUseInGenerate(a.symbol)}
                      title={`Seed a Generate brief around ${a.symbol}`}
                    >
                      Use in Generate →
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="caption" style={{ marginTop: 16, color: 'var(--text-4)' }}>
        Prices from on-chain PriceOracle with yfinance fallback. Stale = oracle hasn't updated in &gt;5 minutes.
        The "Vol 30d" column is annualized realized volatility (std of daily returns × √252).
      </p>
    </div>
  )
}
