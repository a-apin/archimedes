import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''
const RANGES = ['1D', '1W', '1M', '1Y', '5Y', '10Y', 'MAX']

function fmtPrice(v) {
  if (v == null || Number.isNaN(v)) return '—'
  if (v >= 1000) return `$${v.toFixed(0)}`
  if (v >= 10) return `$${v.toFixed(2)}`
  return `$${v.toFixed(4)}`
}

function fmtPct(v) {
  if (v == null || Number.isNaN(v)) return '—'
  const sign = v >= 0 ? '+' : ''
  return `${sign}${v.toFixed(2)}%`
}

function changeClass(v) {
  if (v == null || Number.isNaN(v)) return ''
  return v >= 0 ? 'positive' : 'negative'
}

function sourceLabel(price_source) {
  if (price_source === 'oracle') return 'On-chain PriceOracle (Arc)'
  if (price_source === 'yfinance') return 'yfinance (off-chain fallback)'
  return 'No source available'
}

/**
 * PriceHistoryChart — SVG line chart of one symbol's price series.
 *
 * Honest fallback: when `points` is empty (range unsupported or upstream
 * feed returned nothing) we render an explicit empty state. We never
 * synthesize a flat line.
 */
function PriceHistoryChart({ points, loading, error }) {
  const SVG_W = 720
  const SVG_H = 260
  const PAD_L = 56
  const PAD_R = 18
  const PAD_T = 16
  const PAD_B = 36

  if (loading) {
    return (
      <div style={{ height: SVG_H, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div className="caption" style={{ color: 'var(--text-4)' }}>Loading price history…</div>
      </div>
    )
  }
  if (error || !points || points.length === 0) {
    return (
      <div
        style={{
          height: SVG_H,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          background: 'rgba(255,255,255,0.02)',
          border: '1px dashed var(--glass-border)',
          borderRadius: 6,
        }}
      >
        <div className="caption" style={{ color: 'var(--text-4)', textAlign: 'center', maxWidth: 380 }}>
          Historical chart unavailable on this asset for the selected range.
          {error ? <><br /><span style={{ fontSize: '0.7rem', opacity: 0.7 }}>{error}</span></> : null}
        </div>
      </div>
    )
  }

  const prices = points.map(p => p.price)
  const minP = Math.min(...prices)
  const maxP = Math.max(...prices)
  const range = maxP - minP || Math.max(1e-6, Math.abs(maxP) * 0.01)
  // Light vertical padding so the line never touches the top/bottom border.
  const yPad = range * 0.08

  const toX = i => PAD_L + (i / Math.max(1, points.length - 1)) * (SVG_W - PAD_L - PAD_R)
  const toY = p => SVG_H - PAD_B - ((p - minP + yPad) / (range + 2 * yPad)) * (SVG_H - PAD_T - PAD_B)

  const linePath = points
    .map((pt, i) => `${i === 0 ? 'M' : 'L'} ${toX(i).toFixed(1)} ${toY(pt.price).toFixed(1)}`)
    .join(' ')
  const areaPath =
    `M ${toX(0).toFixed(1)} ${(SVG_H - PAD_B).toFixed(1)} ` +
    points.map((pt, i) => `L ${toX(i).toFixed(1)} ${toY(pt.price).toFixed(1)}`).join(' ') +
    ` L ${toX(points.length - 1).toFixed(1)} ${(SVG_H - PAD_B).toFixed(1)} Z`

  // y-axis labels (5 ticks)
  const yTicks = [0, 1, 2, 3, 4].map(i => {
    const p = minP + (range * i) / 4
    return { y: toY(p), label: fmtPrice(p) }
  })

  // x-axis labels (4 ticks at first/quarter/half/three-quarter/last)
  const xTickIdx = [0, Math.floor((points.length - 1) / 3), Math.floor((2 * (points.length - 1)) / 3), points.length - 1]
  // Heuristic: if the first two timestamps differ by less than a day, this
  // is intraday data and we want HH:MM labels. Otherwise show MM-DD.
  const isIntraday = (() => {
    if (points.length < 2) return false
    const a = Date.parse(points[0].ts.replace(' ', 'T'))
    const b = Date.parse(points[1].ts.replace(' ', 'T'))
    if (Number.isNaN(a) || Number.isNaN(b)) return false
    return Math.abs(b - a) < 12 * 60 * 60 * 1000
  })()
  const xTicks = xTickIdx
    .filter((idx, i, arr) => i === 0 || idx !== arr[i - 1])  // dedupe at edges
    .map(idx => {
      const tsStr = points[idx]?.ts || ''
      // pandas Timestamps stringify as "2026-05-25 00:00:00+00:00" or
      // "2026-05-25". Normalize and format based on intraday / daily.
      const parsed = Date.parse(tsStr.replace(' ', 'T'))
      let label = tsStr.slice(0, 10)
      if (!Number.isNaN(parsed)) {
        const d = new Date(parsed)
        if (isIntraday) {
          label = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
        } else {
          label = `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
        }
      }
      return { x: toX(idx), label }
    })

  // Coloring: green if last > first, red otherwise — matches the 24h
  // change badge convention.
  const first = points[0].price
  const last = points[points.length - 1].price
  const stroke = last >= first ? 'var(--positive)' : 'var(--negative)'
  const fill = last >= first ? 'rgba(34,197,94,0.10)' : 'rgba(239,68,68,0.10)'

  return (
    <svg viewBox={`0 0 ${SVG_W} ${SVG_H}`} width="100%" style={{ display: 'block', maxHeight: 320 }}>
      {/* horizontal grid */}
      {yTicks.map((t, i) => (
        <line key={i} x1={PAD_L} y1={t.y} x2={SVG_W - PAD_R} y2={t.y}
          stroke="rgba(255,255,255,0.06)" strokeWidth="1" />
      ))}
      {/* axes */}
      <line x1={PAD_L} y1={PAD_T} x2={PAD_L} y2={SVG_H - PAD_B}
        stroke="rgba(255,255,255,0.2)" strokeWidth="1" />
      <line x1={PAD_L} y1={SVG_H - PAD_B} x2={SVG_W - PAD_R} y2={SVG_H - PAD_B}
        stroke="rgba(255,255,255,0.2)" strokeWidth="1" />

      {/* y-axis labels */}
      {yTicks.map((t, i) => (
        <text key={i} x={PAD_L - 6} y={t.y + 4} textAnchor="end"
          fill="rgba(255,255,255,0.42)" fontSize="9" className="mono">
          {t.label}
        </text>
      ))}
      {/* x-axis labels */}
      {xTicks.map((t, i) => (
        <text key={i} x={t.x} y={SVG_H - PAD_B + 14} textAnchor="middle"
          fill="rgba(255,255,255,0.42)" fontSize="9">
          {t.label}
        </text>
      ))}

      {/* area under the line */}
      <path d={areaPath} fill={fill} stroke="none" />
      {/* main line */}
      <path d={linePath} fill="none" stroke={stroke} strokeWidth="1.8" strokeLinejoin="round" />
    </svg>
  )
}

export default function AssetModal({ asset, onClose }) {
  const [range, setRange] = useState('1M')
  const [history, setHistory] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  // Esc closes
  useEffect(() => {
    const onKey = e => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // Lock body scroll while modal is open
  useEffect(() => {
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = prev }
  }, [])

  // Fetch on range change. We do NOT silently swallow errors — if the
  // upstream feed returns nothing, the chart renders an explicit empty
  // state instead of a faked flat line.
  useEffect(() => {
    if (!asset?.symbol) return
    let cancelled = false
    setLoading(true)
    setError('')
    fetch(`${API_BASE}/api/explore/assets/${asset.symbol}/history?range=${range}`)
      .then(async res => {
        if (!res.ok) {
          // 404 just means "no series for this range"; fall through with empty data.
          if (res.status === 404) {
            if (!cancelled) { setHistory([]); setError('') }
            return null
          }
          throw new Error(await res.text() || `History fetch failed (${res.status})`)
        }
        return res.json()
      })
      .then(data => {
        if (cancelled || data == null) return
        setHistory(Array.isArray(data.points) ? data.points : [])
      })
      .catch(e => {
        if (!cancelled) {
          setError(e?.message || 'Failed to load price history')
          setHistory([])
        }
      })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [asset?.symbol, range])

  // 24h high/low: try to compute from the 1D intraday series when available.
  // This is the honest computation — falls back to "—" if we don't have data.
  const intradayStats = useMemo(() => {
    if (range !== '1D' || history.length === 0) return { high: null, low: null }
    const prices = history.map(p => p.price).filter(p => p != null && !Number.isNaN(p))
    if (prices.length === 0) return { high: null, low: null }
    return { high: Math.max(...prices), low: Math.min(...prices) }
  }, [history, range])

  if (!asset) return null

  const high24 = asset.high_24h ?? intradayStats.high
  const low24 = asset.low_24h ?? intradayStats.low

  return createPortal(
    <div
      className="fixed inset-0 flex items-center justify-center z-[1000]"
      style={{ background: 'rgba(0,0,0,0.78)', backdropFilter: 'blur(6px)' }}
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-labelledby="asset-modal-title"
    >
      <div
        className="card-elevated p-6"
        onClick={e => e.stopPropagation()}
        style={{
          background: 'var(--surface-1)',
          maxHeight: '90vh', overflowY: 'auto',
          width: 'min(820px, 94vw)',
        }}
      >
        {/* Header */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16 }}>
          <div>
            <h3 id="asset-modal-title" className="serif" style={{ fontSize: '1.6rem', margin: 0 }}>
              {asset.symbol}
            </h3>
            <div className="caption" style={{ color: 'var(--text-3)', marginTop: 2 }}>
              {asset.name || '—'}
              {asset.asset_class && (
                <span className="tag tag-muted" style={{ marginLeft: 8, fontSize: '0.65rem' }}>
                  {asset.asset_class.replace(/_/g, ' ')}
                </span>
              )}
            </div>
          </div>
          <button
            className="btn btn-outline btn-sm"
            onClick={onClose}
            aria-label="Close asset details"
          >
            Close (Esc)
          </button>
        </div>

        {/* Price block */}
        <div style={{ display: 'flex', gap: 24, alignItems: 'baseline', marginTop: 18, flexWrap: 'wrap' }}>
          <div>
            <div className="caption" style={{ color: 'var(--text-4)', fontSize: '0.7rem' }}>Current price</div>
            <div className="mono" style={{ fontSize: '2rem', fontWeight: 600 }}>
              {fmtPrice(asset.current_price)}
            </div>
          </div>
          <div>
            <div className="caption" style={{ color: 'var(--text-4)', fontSize: '0.7rem' }}>24h change</div>
            <div className={`mono ${changeClass(asset.change_24h_pct)}`} style={{ fontSize: '1.1rem', fontWeight: 600 }}>
              {fmtPct(asset.change_24h_pct)}
            </div>
          </div>
          <div>
            <div className="caption" style={{ color: 'var(--text-4)', fontSize: '0.7rem' }}>24h high</div>
            <div className="mono" style={{ fontSize: '1.1rem' }}>{fmtPrice(high24)}</div>
          </div>
          <div>
            <div className="caption" style={{ color: 'var(--text-4)', fontSize: '0.7rem' }}>24h low</div>
            <div className="mono" style={{ fontSize: '1.1rem' }}>{fmtPrice(low24)}</div>
          </div>
        </div>

        {/* Range toggle */}
        <div style={{ marginTop: 18, display: 'flex', gap: 6, alignItems: 'center' }}>
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
            {range === '1D' ? 'Intraday 5-minute bars' : 'Daily close'}
          </span>
        </div>

        {/* Chart */}
        <div style={{ marginTop: 10 }}>
          <PriceHistoryChart points={history} loading={loading} error={error} />
        </div>

        {/* Meta grid: source, last updated, longer-window changes, vol */}
        <div
          style={{
            marginTop: 18,
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
            gap: 14,
            paddingTop: 14,
            borderTop: '1px solid var(--glass-border)',
          }}
        >
          <div>
            <div className="caption" style={{ color: 'var(--text-4)', fontSize: '0.7rem' }}>Source</div>
            <div className="body" style={{ fontSize: '0.9rem' }}>{sourceLabel(asset.price_source)}</div>
          </div>
          <div>
            <div className="caption" style={{ color: 'var(--text-4)', fontSize: '0.7rem' }}>Last updated</div>
            <div className="mono" style={{ fontSize: '0.82rem' }}>
              {asset.last_updated ? new Date(asset.last_updated).toLocaleString() : '—'}
            </div>
          </div>
          <div>
            <div className="caption" style={{ color: 'var(--text-4)', fontSize: '0.7rem' }}>7d change</div>
            <div className={`mono ${changeClass(asset.change_7d_pct)}`}>{fmtPct(asset.change_7d_pct)}</div>
          </div>
          <div>
            <div className="caption" style={{ color: 'var(--text-4)', fontSize: '0.7rem' }}>30d change</div>
            <div className={`mono ${changeClass(asset.change_30d_pct)}`}>{fmtPct(asset.change_30d_pct)}</div>
          </div>
          <div>
            <div className="caption" style={{ color: 'var(--text-4)', fontSize: '0.7rem' }}>
              Realized vol (30d, annualized)
            </div>
            <div className="mono">
              {asset.realized_vol_30d != null ? asset.realized_vol_30d.toFixed(2) : '—'}
            </div>
          </div>
          {asset.oracle_address && (
            <div>
              <div className="caption" style={{ color: 'var(--text-4)', fontSize: '0.7rem' }}>Oracle address</div>
              <div className="mono" style={{ fontSize: '0.72rem', wordBreak: 'break-all' }}>
                {asset.oracle_address}
              </div>
            </div>
          )}
        </div>

        {asset.is_stale && (
          <div className="info-box warning" style={{ marginTop: 14, fontSize: '0.8rem' }}>
            The displayed price for this asset is older than the freshness threshold.
            The upstream feed ({sourceLabel(asset.price_source)}) has not updated recently.
          </div>
        )}
      </div>
    </div>,
    document.body
  )
}
