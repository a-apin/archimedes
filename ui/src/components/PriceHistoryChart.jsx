// Shared SVG line-chart for a price/index time series. Extracted from
// AssetModal.jsx (#464) so the grouped-asset detail view can reuse the exact
// same charting approach instead of introducing a second pattern or a new
// charting library.

export function fmtPrice(v) {
  if (v == null || Number.isNaN(v)) return '—'
  if (v >= 1000) return `$${v.toFixed(0)}`
  if (v >= 10) return `$${v.toFixed(2)}`
  return `$${v.toFixed(4)}`
}

/**
 * PriceHistoryChart — SVG line chart of one series of {ts, price} points.
 *
 * Honest fallback: when `points` is empty (range unsupported or upstream
 * feed returned nothing) we render an explicit empty state. We never
 * synthesize a flat line.
 *
 * `formatValue` lets callers (e.g. a group-index chart plotting a % change
 * rather than a dollar price) override the y-axis / label formatting without
 * forking the chart.
 */
export default function PriceHistoryChart({
  points,
  loading,
  error,
  formatValue = fmtPrice,
  // Callers can restore context lost when this chart was extracted from
  // AssetModal (e.g. the single-asset modal's more specific 'on this asset'
  // wording) without forking the component (review).
  emptyHeadline = 'Historical chart unavailable for the selected range.',
}) {
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
          background: 'var(--glass)',
          border: '1px dashed var(--glass-border)',
          borderRadius: 6,
        }}
      >
        <div className="caption" style={{ color: 'var(--text-4)', textAlign: 'center', maxWidth: 380 }}>
          {emptyHeadline}
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
    return { y: toY(p), label: formatValue(p) }
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
          stroke="var(--chart-grid)" strokeWidth="1" />
      ))}
      {/* axes */}
      <line x1={PAD_L} y1={PAD_T} x2={PAD_L} y2={SVG_H - PAD_B}
        stroke="var(--chart-grid-strong)" strokeWidth="1" />
      <line x1={PAD_L} y1={SVG_H - PAD_B} x2={SVG_W - PAD_R} y2={SVG_H - PAD_B}
        stroke="var(--chart-grid-strong)" strokeWidth="1" />

      {/* y-axis labels */}
      {yTicks.map((t, i) => (
        <text key={i} x={PAD_L - 6} y={t.y + 4} textAnchor="end"
          fill="var(--chart-label)" fontSize="9" className="mono">
          {t.label}
        </text>
      ))}
      {/* x-axis labels */}
      {xTicks.map((t, i) => (
        <text key={i} x={t.x} y={SVG_H - PAD_B + 14} textAnchor="middle"
          fill="var(--chart-label)" fontSize="9">
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
