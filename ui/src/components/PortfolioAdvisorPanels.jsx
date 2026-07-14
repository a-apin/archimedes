// PortfolioAdvisorPanels — interactive optimizer + Kelly + frontier + drift
// panels that complement the existing read-only PortfolioAdvisor.jsx (NOT a
// replacement — drop this alongside it).
//
// Suggested integration: import into the /portfolio (or a new /optimizer)
// surface, e.g.
//   import PortfolioAdvisorPanels from './components/PortfolioAdvisorPanels'
// and render <PortfolioAdvisorPanels /> beneath <PortfolioAdvisor />.
//
// All inline SVG (no chart lib). Styling reuses shared App.css classes plus a
// small colocated PortfolioAdvisorPanels.css.

import { useState, useMemo } from 'react'
import { apiPost } from '../api'
import { kellyFraction, seededRng } from '../utils/riskMath'
import './PortfolioAdvisorPanels.css'

// Backed by POST /api/portfolio/optimize (portfolio_routes.py), a thin route
// over services/portfolio_optimizer.optimize_weights. Returns 503 when price
// history is unavailable — handled by the error branch below.
const OPTIMIZE_ENDPOINT = '/api/portfolio/optimize'

const OPTIMIZERS = [
  { id: 'mvo', label: 'MVO', full: 'Mean-Variance Optimization (Markowitz)' },
  { id: 'hrp', label: 'HRP', full: 'Hierarchical Risk Parity (López de Prado)' },
  { id: 'bl', label: 'Black-Litterman', full: 'Black-Litterman equilibrium + views' },
  { id: 'robust', label: 'Robust', full: 'Robust MVO — ellipsoidal uncertainty on μ (Goldfarb & Iyengar)' },
]

function fmtPct(v, d = 1) {
  return v != null && Number.isFinite(v) ? `${(v * 100).toFixed(d)}%` : '—'
}

// ─── Optimizer selector (segmented control + POST) ──────────
function OptimizerSelector() {
  const [selected, setSelected] = useState('mvo')
  const [status, setStatus] = useState('idle') // idle | loading | done | error
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)

  async function runOptimizer(id) {
    setSelected(id)
    setStatus('loading')
    setError('')
    setResult(null)
    try {
      const data = await apiPost(OPTIMIZE_ENDPOINT, { method: id })
      setResult(data)
      setStatus('done')
    } catch (e) {
      // Endpoint may not exist yet — surface gracefully rather than crashing.
      setError(e.message || 'Optimizer endpoint unavailable')
      setStatus('error')
    }
  }

  const active = OPTIMIZERS.find((o) => o.id === selected)

  return (
    <div className="card-elevated" style={{ padding: 24, marginBottom: 20 }}>
      <div className="label mb-3">Optimizer</div>
      <div className="pap-segmented" role="tablist" aria-label="Portfolio optimizer">
        {OPTIMIZERS.map((o) => (
          <button
            key={o.id}
            role="tab"
            aria-selected={selected === o.id}
            className={`pap-seg-btn${selected === o.id ? ' active' : ''}`}
            onClick={() => runOptimizer(o.id)}
          >
            {o.label}
          </button>
        ))}
      </div>
      <p className="caption" style={{ marginTop: 10, color: 'var(--text-3)' }}>
        {active.full}
      </p>
      {status === 'loading' && <div className="caption" style={{ marginTop: 8 }}>Optimizing allocation…</div>}
      {status === 'error' && (
        <div className="info-box warning" style={{ marginTop: 8 }}>
          Optimizer unavailable: {error}. (POST {OPTIMIZE_ENDPOINT})
        </div>
      )}
      {status === 'done' && result?.weights && (
        <div style={{ marginTop: 12 }}>
          <div className="caption" style={{ color: 'var(--text-2)', marginBottom: 6 }}>
            {active.label} allocation — USDC floor {fmtPct(result.usdc_weight)}, synth sleeve{' '}
            {Object.keys(result.weights).length} assets:
          </div>
          <div className="pap-weights">
            {Object.entries(result.weights)
              .sort((a, b) => b[1] - a[1])
              .filter(([, w]) => w > 0.0005)
              .map(([sym, w]) => (
                <div key={sym} className="pap-weight-row">
                  <span className="mono" style={{ fontSize: '0.8rem' }}>{sym}</span>
                  <div className="pap-weight-track">
                    <div className="pap-weight-fill" style={{ width: `${Math.min(100, w * 100).toFixed(1)}%` }} />
                  </div>
                  <span className="mono caption">{fmtPct(w)}</span>
                </div>
              ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ─── Efficient frontier scatter ─────────────────────────────
function EfficientFrontier({ points }) {
  const W = 700
  const H = 280
  const PAD_L = 48
  const PAD_R = 16
  const PAD_T = 16
  const PAD_B = 36

  const { plot, frontierPath, optimal, xTicks, yTicks } = useMemo(() => {
    if (!points.length) return { plot: [], frontierPath: '', optimal: null, xTicks: [], yTicks: [] }
    const xs = points.map((p) => p.risk)
    const ys = points.map((p) => p.ret)
    const xlo = Math.min(...xs) * 0.9
    const xhi = Math.max(...xs) * 1.05
    const ylo = Math.min(...ys) * 0.9
    const yhi = Math.max(...ys) * 1.05
    const toX = (v) => PAD_L + ((v - xlo) / (xhi - xlo)) * (W - PAD_L - PAD_R)
    const toY = (v) => PAD_T + (1 - (v - ylo) / (yhi - ylo)) * (H - PAD_T - PAD_B)
    const mapped = points.map((p) => ({ ...p, cx: toX(p.risk), cy: toY(p.ret), sharpe: p.ret / p.risk }))
    // frontier = upper envelope (sort by risk, keep running-max return)
    const sorted = [...mapped].sort((a, b) => a.risk - b.risk)
    const env = []
    let best = -Infinity
    for (const p of sorted) {
      if (p.ret >= best) {
        best = p.ret
        env.push(p)
      }
    }
    const path = env.length ? `M ${env.map((p) => `${p.cx.toFixed(1)},${p.cy.toFixed(1)}`).join(' L ')}` : ''
    const opt = mapped.reduce((a, b) => (b.sharpe > a.sharpe ? b : a), mapped[0])
    const xt = [xlo, (xlo + xhi) / 2, xhi].map((v) => ({ x: toX(v), label: fmtPct(v) }))
    const yt = [ylo, (ylo + yhi) / 2, yhi].map((v) => ({ y: toY(v), label: fmtPct(v) }))
    return { plot: mapped, frontierPath: path, optimal: opt, xTicks: xt, yTicks: yt }
  }, [points])

  if (!optimal) {
    return (
      <div className="card-flat" style={{ padding: 20, marginBottom: 20 }}>
        <div className="label mb-2">Efficient Frontier</div>
        <p className="caption" style={{ color: 'var(--text-4)' }}>No candidate allocations to plot yet.</p>
      </div>
    )
  }

  return (
    <div className="card-flat" style={{ padding: 20, marginBottom: 20 }}>
      <div className="label mb-2">Efficient Frontier</div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Efficient frontier: expected return vs risk" style={{ display: 'block' }}>
        {yTicks.map((t, i) => (
          <line key={i} x1={PAD_L} y1={t.y} x2={W - PAD_R} y2={t.y} stroke="var(--chart-grid)" strokeWidth="1" />
        ))}
        {yTicks.map((t, i) => (
          <text key={`yl${i}`} x={PAD_L - 6} y={t.y + 3} textAnchor="end" fill="var(--chart-label)" fontSize="9" className="mono">
            {t.label}
          </text>
        ))}
        {xTicks.map((t, i) => (
          <text key={`xl${i}`} x={t.x} y={H - PAD_B + 16} textAnchor="middle" fill="var(--chart-label)" fontSize="9" className="mono">
            {t.label}
          </text>
        ))}
        {/* axis titles */}
        <text x={(W + PAD_L) / 2} y={H - 4} textAnchor="middle" fill="var(--chart-label-strong)" fontSize="10">
          Risk (annualized σ)
        </text>
        {frontierPath && <path d={frontierPath} fill="none" stroke="var(--accent)" strokeWidth="1.6" strokeLinejoin="round" />}
        {plot.map((p, i) => (
          <circle key={i} cx={p.cx} cy={p.cy} r={p === optimal ? 5 : 3} fill={p === optimal ? 'var(--positive)' : 'rgba(192,132,252,0.55)'}>
            <title>
              {p.label || `Portfolio ${i + 1}`}: return {fmtPct(p.ret)}, risk {fmtPct(p.risk)}, Sharpe {p.sharpe.toFixed(2)}
            </title>
          </circle>
        ))}
      </svg>
      <p className="caption" style={{ marginTop: 8, color: 'var(--text-4)', lineHeight: 1.5 }}>
        Each dot is a candidate allocation; the purple curve is the efficient frontier (max return per unit
        of risk). The green dot is the max-Sharpe (tangency) portfolio at{' '}
        <span className="mono">{fmtPct(optimal.ret)}</span> return /{' '}
        <span className="mono">{fmtPct(optimal.risk)}</span> risk.
      </p>
    </div>
  )
}

// ─── Kelly fraction widget ──────────────────────────────────
function KellyWidget() {
  const [p, setP] = useState(0.55)
  const [b, setB] = useState(1.2)

  const full = kellyFraction(p, b)
  const half = full / 2

  return (
    <div className="card-flat" style={{ padding: 20, marginBottom: 20 }}>
      <div className="label mb-3">Kelly Fraction Calculator</div>
      <div className="pap-kelly-grid">
        <label className="pap-field">
          <span className="caption">Win probability (p)</span>
          <input
            type="number"
            min="0"
            max="1"
            step="0.01"
            value={p}
            onChange={(e) => setP(Math.min(1, Math.max(0, Number(e.target.value))))}
          />
        </label>
        <label className="pap-field">
          <span className="caption">Net payoff odds (b)</span>
          <input
            type="number"
            min="0"
            step="0.1"
            value={b}
            onChange={(e) => setB(Math.max(0, Number(e.target.value)))}
          />
        </label>
      </div>
      <div className="pap-kelly-out">
        <div>
          <div className="caption">Full Kelly f*</div>
          <div className="mono" style={{ fontWeight: 700, fontSize: '1.6rem', color: 'var(--accent)' }}>
            {fmtPct(full)}
          </div>
        </div>
        <div>
          <div className="caption">Half-Kelly (recommended)</div>
          <div className="mono positive" style={{ fontWeight: 700, fontSize: '1.6rem' }}>
            {fmtPct(half)}
          </div>
        </div>
      </div>
      <p className="caption" style={{ marginTop: 12, color: 'var(--text-4)', lineHeight: 1.5 }}>
        f* = (b·p − q) / b, where q = 1 − p. Full Kelly maximizes long-run log-growth but is volatile and
        assumes p and b are known exactly. <strong>Half-Kelly</strong> is the standard safety margin: it
        captures ~75% of the growth at ~50% of the variance and is far more robust to estimation error in p.
      </p>
    </div>
  )
}

// ─── Allocation drift bars (current vs target) ──────────────
function AllocationDrift({ rows }) {
  return (
    <div className="card-flat" style={{ padding: 20, marginBottom: 20 }}>
      <div className="label mb-3">Allocation Drift (current vs target)</div>
      {rows.map((r) => {
        const drift = r.current - r.target
        const driftColor = Math.abs(drift) < 0.01 ? 'var(--text-3)' : drift > 0 ? '#f59e0b' : '#60a5fa'
        return (
          <div key={r.symbol} className="pap-drift-row">
            <div className="pap-drift-head">
              <span className="mono" style={{ fontWeight: 600, fontSize: '0.84rem' }}>{r.symbol}</span>
              <span className="caption" style={{ color: driftColor }}>
                {drift > 0 ? '+' : ''}{(drift * 100).toFixed(1)}% vs target
              </span>
            </div>
            <div className="pap-drift-track">
              {/* target marker */}
              <div className="pap-drift-target" style={{ left: `${(r.target * 100).toFixed(1)}%` }} title={`target ${fmtPct(r.target)}`} />
              {/* current fill */}
              <div className="pap-drift-fill" style={{ width: `${(r.current * 100).toFixed(1)}%` }} />
            </div>
            <div className="pap-drift-legend caption">
              <span>current <span className="mono">{fmtPct(r.current)}</span></span>
              <span>target <span className="mono">{fmtPct(r.target)}</span></span>
            </div>
          </div>
        )
      })}
      <p className="caption" style={{ marginTop: 6, color: 'var(--text-4)', lineHeight: 1.5 }}>
        The vertical tick marks each sleeve&apos;s target weight; the bar shows the current weight. Drift
        beyond a band triggers the agent&apos;s rebalance — every rebalance decision is hashed and anchored
        on Arc.
      </p>
    </div>
  )
}

// ─── default mock data ──────────────────────────────────────
function buildMockFrontier() {
  const rng = seededRng(99)
  const pts = []
  for (let i = 0; i < 40; i++) {
    const risk = 0.05 + rng() * 0.22
    // concave-ish return vs risk with noise
    const ret = 0.02 + 0.9 * risk - 1.4 * risk * risk + (rng() - 0.5) * 0.03
    pts.push({ risk, ret: Math.max(0.005, ret) })
  }
  return pts
}

const MOCK_DRIFT = [
  { symbol: 'sSPY', current: 0.34, target: 0.3 },
  { symbol: 'sBTC', current: 0.12, target: 0.18 },
  { symbol: 'sGOLD', current: 0.21, target: 0.2 },
  { symbol: 'USDC', current: 0.33, target: 0.32 },
]

export default function PortfolioAdvisorPanels({ frontierPoints, driftRows } = {}) {
  const points = useMemo(() => frontierPoints ?? buildMockFrontier(), [frontierPoints])
  const rows = driftRows ?? MOCK_DRIFT

  return (
    <div>
      <div style={{ maxWidth: 680, marginBottom: 28 }}>
        <h2 className="serif" style={{ fontSize: '2rem', marginBottom: 10 }}>
          Optimizer &amp; Sizing
        </h2>
        <p className="body">
          Choose an allocation method, inspect the efficient frontier, size positions with Kelly, and watch
          how far the live book has drifted from its targets.
        </p>
      </div>

      <OptimizerSelector />
      <EfficientFrontier points={points} />
      <KellyWidget />
      <AllocationDrift rows={rows} />
    </div>
  )
}
