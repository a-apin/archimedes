import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import PriceHistoryChart from './PriceHistoryChart'
import AssetGroupIcon from './AssetGroupIcon'
import { groupMeta } from '../assetGroups'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''
const RANGES = ['1D', '1W', '1M', '1Y', '5Y', '10Y', 'MAX']
// Aggregating every member of a large bucket (crypto = 71 symbols) on every
// range change is expensive for both the browser and the backend cache. Cap
// how many symbols feed the aggregate chart; the card/list below still shows
// the full membership and true asset count.
const MAX_AGGREGATE_MEMBERS = 20

function fmtPct(v) {
  if (v == null || Number.isNaN(v)) return '—'
  const sign = v >= 0 ? '+' : ''
  return `${sign}${v.toFixed(2)}%`
}

function changeClass(v) {
  if (v == null || Number.isNaN(v)) return ''
  return v >= 0 ? 'positive' : 'negative'
}

/**
 * Aggregate several symbols' history series into a single equal-weight
 * "index" series, expressed as % change from each series' own first point.
 * Normalizing to % change is what makes it valid to average across symbols
 * with wildly different price scales (e.g. a $0.001 token next to a $60,000
 * one) — anchoring on any single symbol's price units would misrepresent
 * the group.
 *
 * We align on index position rather than timestamp: all history requests
 * share the same `range`, so the backend returns bars on the same cadence
 * per symbol; index alignment is the same approach the single-asset x-axis
 * tick heuristic already assumes (see PriceHistoryChart's isIntraday check).
 */
function buildGroupIndex(seriesList) {
  const usable = seriesList.filter(s => s.points && s.points.length > 1)
  if (usable.length === 0) return []

  const maxLen = Math.max(...usable.map(s => s.points.length))
  const out = []
  for (let i = 0; i < maxLen; i++) {
    let sum = 0
    let count = 0
    let ts = null
    for (const s of usable) {
      const pt = s.points[i]
      if (!pt) continue
      const base = s.points[0].price
      if (base == null || base === 0) continue
      sum += ((pt.price - base) / base) * 100
      count += 1
      if (!ts) ts = pt.ts
    }
    if (count > 0) out.push({ ts: ts || String(i), price: sum / count })
  }
  return out
}

export default function AssetGroupModal({ assetClass, assets, onClose }) {
  const [range, setRange] = useState('1M')
  const [seriesBySymbol, setSeriesBySymbol] = useState({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const meta = groupMeta(assetClass)
  const members = useMemo(
    () => [...assets].sort((a, b) => (b.current_price ?? 0) - (a.current_price ?? 0)),
    [assets]
  )
  const aggregateMembers = useMemo(() => members.slice(0, MAX_AGGREGATE_MEMBERS), [members])

  useEffect(() => {
    const onKey = e => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  useEffect(() => {
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = prev }
  }, [])

  useEffect(() => {
    if (aggregateMembers.length === 0) return
    let cancelled = false
    setLoading(true)
    setError('')
    Promise.all(
      aggregateMembers.map(a =>
        fetch(`${API_BASE}/api/explore/assets/${a.symbol}/history?range=${range}`)
          .then(res => (res.ok ? res.json() : { points: [] }))
          .then(data => ({ symbol: a.symbol, points: Array.isArray(data.points) ? data.points : [] }))
          .catch(() => ({ symbol: a.symbol, points: [] }))
      )
    )
      .then(results => {
        if (cancelled) return
        const bySymbol = {}
        for (const r of results) bySymbol[r.symbol] = r.points
        setSeriesBySymbol(bySymbol)
        if (results.every(r => r.points.length === 0)) {
          setError('No historical data available for this group in the selected range.')
        }
      })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [aggregateMembers, range])

  const groupIndexPoints = useMemo(
    () => buildGroupIndex(Object.entries(seriesBySymbol).map(([symbol, points]) => ({ symbol, points }))),
    [seriesBySymbol]
  )

  // Aggregate 24h change across the whole group (not just the capped
  // aggregate-chart subset) — cheap since it's already in the /assets payload.
  const avgChange24h = useMemo(() => {
    const vals = members.map(a => a.change_24h_pct).filter(v => v != null && !Number.isNaN(v))
    if (vals.length === 0) return null
    return vals.reduce((a, b) => a + b, 0) / vals.length
  }, [members])

  if (!assetClass) return null

  return createPortal(
    <div
      className="fixed inset-0 flex items-center justify-center z-[1000]"
      style={{ background: 'rgba(0,0,0,0.78)', backdropFilter: 'blur(6px)' }}
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-labelledby="asset-group-modal-title"
    >
      <div
        className="card-elevated p-6"
        onClick={e => e.stopPropagation()}
        style={{
          background: 'var(--surface-1)',
          maxHeight: '90vh', overflowY: 'auto',
          width: 'min(860px, 94vw)',
        }}
      >
        {/* Header */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16 }}>
          <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
            <div
              style={{
                width: 40, height: 40, borderRadius: 8,
                background: 'var(--glass)', border: '1px solid var(--glass-border)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                color: 'var(--accent)', flexShrink: 0,
              }}
            >
              <AssetGroupIcon icon={meta.icon} size={22} />
            </div>
            <div>
              <h3 id="asset-group-modal-title" className="serif" style={{ fontSize: '1.5rem', margin: 0 }}>
                {meta.label}
              </h3>
              <div className="caption" style={{ color: 'var(--text-3)', marginTop: 2 }}>
                {members.length} asset{members.length === 1 ? '' : 's'} in this group
              </div>
            </div>
          </div>
          <button className="btn btn-outline btn-sm" onClick={onClose} aria-label="Close group details">
            Close (Esc)
          </button>
        </div>

        {/* Plain-English description */}
        <p className="body" style={{ marginTop: 14, color: 'var(--text-3)', maxWidth: 700 }}>
          {meta.description}
        </p>

        {/* Aggregate stat */}
        <div style={{ display: 'flex', gap: 24, alignItems: 'baseline', marginTop: 16, flexWrap: 'wrap' }}>
          <div>
            <div className="caption" style={{ color: 'var(--text-4)', fontSize: '0.7rem' }}>
              Avg 24h change (equal-weight, {members.length} assets)
            </div>
            <div className={`mono ${changeClass(avgChange24h)}`} style={{ fontSize: '1.3rem', fontWeight: 600 }}>
              {fmtPct(avgChange24h)}
            </div>
          </div>
        </div>

        {/* Range toggle */}
        <div style={{ marginTop: 18, display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
          {RANGES.map(r => (
            <button
              key={r}
              onClick={() => setRange(r)}
              className={`btn btn-sm ${range === r ? '' : 'btn-outline'}`}
              style={{
                minWidth: 48,
                background: range === r ? 'var(--accent-muted)' : undefined,
                color: range === r ? 'var(--accent)' : undefined,
                borderColor: range === r ? 'var(--accent)' : undefined,
              }}
              aria-pressed={range === r}
            >
              {r}
            </button>
          ))}
          <span className="caption" style={{ marginLeft: 'auto', color: 'var(--text-4)', fontSize: '0.7rem' }}>
            Equal-weight % change index
            {members.length > MAX_AGGREGATE_MEMBERS ? ` · first ${MAX_AGGREGATE_MEMBERS} of ${members.length} assets` : ''}
          </span>
        </div>

        {/* Aggregated chart — reuses the same PriceHistoryChart component/pattern
            as the single-asset detail view (AssetModal.jsx), plotting % change
            instead of $ price via the formatValue override. */}
        <div style={{ marginTop: 10 }}>
          <PriceHistoryChart
            points={groupIndexPoints}
            loading={loading}
            error={error}
            formatValue={v => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`}
          />
        </div>

        {/* Member list */}
        <div style={{ marginTop: 20, paddingTop: 14, borderTop: '1px solid var(--glass-border)' }}>
          <div className="caption" style={{ color: 'var(--text-4)', fontSize: '0.7rem', marginBottom: 8 }}>
            Assets in this group
          </div>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))',
              gap: 8,
              maxHeight: 220,
              overflowY: 'auto',
            }}
          >
            {members.map(a => (
              <div
                key={a.symbol}
                style={{
                  display: 'flex', justifyContent: 'space-between', alignItems: 'baseline',
                  padding: '6px 8px', background: 'var(--glass)', borderRadius: 6,
                  border: '1px solid var(--glass-border)',
                }}
              >
                <span className="mono" style={{ fontSize: '0.78rem', fontWeight: 600 }}>{a.symbol}</span>
                <span className={`mono ${changeClass(a.change_24h_pct)}`} style={{ fontSize: '0.7rem' }}>
                  {fmtPct(a.change_24h_pct)}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>,
    document.body
  )
}
