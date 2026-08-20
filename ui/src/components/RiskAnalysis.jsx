// RiskAnalysis — quantitative risk dashboard (VaR/CVaR, drawdown, rolling
// Sharpe, correlation heatmap). Renders standalone with mock defaults.
//
// Suggested integration: import into a route alongside PortfolioAdvisor, e.g.
//   import RiskAnalysis from './components/RiskAnalysis'
// and render <RiskAnalysis /> on the /risk or /portfolio surface.
//
// All inline SVG (no chart lib — matches AssetModal.jsx). Styling reuses the
// shared App.css classes (card-elevated, card-flat, label, caption, mono) plus
// a small colocated RiskAnalysis.css for the metric-card grid + tooltips.

import { useEffect, useMemo, useState } from 'react'
import { apiGet } from '../api'
import {
  computeHistoricalVaR,
  computeCVaR,
  rollingSharpe,
  drawdownSeries,
  equityFromReturns,
  correlation,
  mockReturns,
} from '../utils/riskMath'
import SampleDataBadge from './SampleDataBadge'
import './RiskAnalysis.css'

function fmtPct(v, d = 2) {
  return v != null && Number.isFinite(v) ? `${(v * 100).toFixed(d)}%` : '—'
}

const SHARPE_WINDOWS = [
  { w: 30, color: 'var(--accent)', label: '30d' },
  { w: 60, color: 'var(--info)', label: '60d' },
  { w: 90, color: 'var(--positive)', label: '90d' },
]

// ─── default mock data ──────────────────────────────────────
function buildDefaultData() {
  const returns = mockReturns(252, { seed: 7 })
  const assets = ['sSPY', 'sBTC', 'sGOLD', 'sNVDA']
  const series = assets.map((_, i) => mockReturns(252, { seed: 11 + i * 13, vol: 0.012 + i * 0.004 }))
  return { returns, assets, series }
}

// ─── VaR / CVaR stat cards ──────────────────────────────────
function VaRPanel({ returns, cvarData, sample }) {
  // Computed from local mock returns (fallback)
  const localStats = useMemo(
    () => ({
      var95: computeHistoricalVaR(returns, 0.95),
      cvar95: computeCVaR(returns, 0.95),
      var99: computeHistoricalVaR(returns, 0.99),
      cvar99: computeCVaR(returns, 0.99),
    }),
    [returns],
  )

  // When backend data is available, prefer it
  const useBackend = cvarData != null && Array.isArray(cvarData.levels) && cvarData.levels.length > 0

  const lvl95 = useBackend ? cvarData.levels.find((l) => l.confidence === 0.95) : null
  const lvl99 = useBackend ? cvarData.levels.find((l) => l.confidence === 0.99) : null

  const cards = useBackend
    ? [
        {
          label: '95% VaR (1-day)',
          value: lvl95 ? lvl95.var_historical : null,
          tip: 'On 95% of days, the single-day loss is not expected to exceed this. Historical (empirical-quantile) method.',
          fatTails: false,
        },
        {
          label: '95% CVaR',
          value: lvl95 ? lvl95.cvar_historical : null,
          tip: 'Conditional VaR / Expected Shortfall: the average loss on the worst 5% of days — the size of the tail, not just its edge.',
          fatTails: lvl95 ? lvl95.fat_tails === true : false,
        },
        {
          label: '99% VaR (1-day)',
          value: lvl99 ? lvl99.var_historical : null,
          tip: 'On 99% of days, the single-day loss is not expected to exceed this. A more conservative threshold than 95%.',
          fatTails: false,
        },
        {
          label: '99% CVaR',
          value: lvl99 ? lvl99.cvar_historical : null,
          tip: 'Average loss on the worst 1% of days. The deepest-tail expectation.',
          fatTails: lvl99 ? lvl99.fat_tails === true : false,
        },
      ]
    : [
        {
          label: '95% VaR (1-day)',
          value: localStats.var95,
          tip: 'On 95% of days, the single-day loss is not expected to exceed this. Historical (empirical-quantile) method.',
          fatTails: false,
        },
        {
          label: '95% CVaR',
          value: localStats.cvar95,
          tip: 'Conditional VaR / Expected Shortfall: the average loss on the worst 5% of days — the size of the tail, not just its edge.',
          fatTails: false,
        },
        {
          label: '99% VaR (1-day)',
          value: localStats.var99,
          tip: 'On 99% of days, the single-day loss is not expected to exceed this. A more conservative threshold than 95%.',
          fatTails: false,
        },
        {
          label: '99% CVaR',
          value: localStats.cvar99,
          tip: 'Average loss on the worst 1% of days. The deepest-tail expectation.',
          fatTails: false,
        },
      ]

  const sampleSize = useBackend && lvl95 ? lvl95.sample_size : returns.length
  const lookbackNote = useBackend
    ? `Computed from ${sampleSize} trading days (${cvarData.lookback_days}d lookback, ${cvarData.strategy_count} strategies) via the historical method.`
    : `Computed from ${returns.length} historical daily returns via the empirical method (no normality assumption). VaR is the loss threshold at the stated confidence; CVaR (Expected Shortfall) is the mean loss beyond it and is always at least as large.`

  return (
    <div className="card-elevated" style={{ padding: 24, marginBottom: 20 }}>
      <div className="label mb-3">
        Value-at-Risk &amp; Conditional VaR
        {!useBackend && sample && <SampleDataBadge />}
      </div>
      <div className="risk-stat-grid">
        {cards.map((c) => (
          <div className="risk-stat-card" key={c.label}>
            <div className="risk-stat-label">
              {c.label}
              {c.fatTails && (
                <span
                  title="Fat-tail regime detected — CVaR may understate true tail risk under normal assumptions"
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: '50%',
                    background: '#f97316',
                    display: 'inline-block',
                    flexShrink: 0,
                  }}
                />
              )}
              <span className="risk-tooltip" tabIndex={0} aria-label={c.tip}>
                <span className="i-lucide-info" aria-hidden="true" />
                <span className="risk-tooltip-body">{c.tip}</span>
              </span>
            </div>
            <div className="risk-stat-value negative mono">-{fmtPct(c.value)}</div>
          </div>
        ))}
      </div>
      <p className="caption" style={{ marginTop: 12, color: 'var(--text-4)', lineHeight: 1.5 }}>
        {lookbackNote}
      </p>
    </div>
  )
}

// ─── Greeks panel (portfolio sensitivity) ───────────────────
function GreeksPanel({ greeksData }) {
  if (!greeksData || greeksData.strategy_count === 0) return null

  const g = greeksData

  const aggregates = [
    {
      label: 'Portfolio Delta',
      value: g.portfolio_delta,
      tip: 'Delta: sensitivity to a 1% move in the underlying. A delta of 0.5 means a 1% move in the underlying changes portfolio value by ~0.5%.',
    },
    {
      label: 'Portfolio Gamma',
      value: g.portfolio_gamma,
      tip: 'Gamma: convexity (rate of change of delta). High gamma means delta changes rapidly as the underlying moves — exposure accelerates or decelerates.',
    },
    {
      label: 'Portfolio Theta',
      value: g.portfolio_theta,
      tip: 'Theta: time decay per day. Negative theta means the portfolio loses value as time passes, holding all else equal.',
    },
    {
      label: 'Portfolio Vega',
      value: g.portfolio_vega,
      tip: 'Vega: sensitivity to a 1-point change in implied volatility. Positive vega benefits from rising vol; negative vega from falling vol.',
    },
    {
      label: 'Portfolio Rho',
      value: g.portfolio_rho,
      tip: 'Rho: sensitivity to a 1% change in the risk-free rate. Less dominant for short-dated strategies; matters more for long-dated positions.',
    },
  ]

  // Top strategies sorted by |delta| descending, up to 5
  const topStrategies = Array.isArray(g.strategies)
    ? [...g.strategies].sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta)).slice(0, 5)
    : []

  function fmtGreek(v) {
    if (v == null || !Number.isFinite(v)) return '—'
    return v.toFixed(4)
  }

  function truncate(s, n = 32) {
    if (!s) return '—'
    return s.length > n ? s.slice(0, n - 1) + '…' : s
  }

  return (
    <div className="card-elevated" style={{ padding: 24, marginBottom: 20 }}>
      <div className="label mb-3">
        Portfolio Greeks
        <span style={{ marginLeft: 8, fontSize: '0.72rem', fontWeight: 400, color: 'var(--text-4)', textTransform: 'none', letterSpacing: 0 }}>
          {g.strategy_count} {g.strategy_count === 1 ? 'strategy' : 'strategies'} · {g.time_horizon_days}d horizon · IV assumption {fmtPct(g.implied_vol_assumption)}
        </span>
      </div>

      <div className="risk-stat-grid" style={{ marginBottom: 20 }}>
        {aggregates.map((c) => (
          <div className="risk-stat-card" key={c.label}>
            <div className="risk-stat-label">
              {c.label}
              <span className="risk-tooltip" tabIndex={0} aria-label={c.tip}>
                <span className="i-lucide-info" aria-hidden="true" />
                <span className="risk-tooltip-body">{c.tip}</span>
              </span>
            </div>
            <div className="risk-stat-value mono" style={{ fontSize: '1.3rem' }}>
              {fmtGreek(c.value)}
            </div>
          </div>
        ))}
      </div>

      {topStrategies.length > 0 && (
        <>
          <div className="caption" style={{ color: 'var(--text-3)', marginBottom: 8, fontWeight: 600 }}>
            Top strategies by |delta|
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
              <thead>
                <tr
                  style={{
                    background: 'var(--glass)',
                    textAlign: 'left',
                    borderBottom: '1px solid var(--glass-border)',
                  }}
                >
                  <th style={{ padding: '8px 12px', fontWeight: 600, color: 'var(--text-3)' }}>Strategy</th>
                  <th style={{ padding: '8px 12px', fontWeight: 600, color: 'var(--text-3)', textAlign: 'right' }}>Implied Vol</th>
                  <th style={{ padding: '8px 12px', fontWeight: 600, color: 'var(--text-3)', textAlign: 'right' }}>Delta</th>
                  <th style={{ padding: '8px 12px', fontWeight: 600, color: 'var(--text-3)', textAlign: 'right' }}>Vega</th>
                </tr>
              </thead>
              <tbody>
                {topStrategies.map((s) => (
                  <tr key={s.strategy_id} style={{ borderBottom: '1px solid var(--glass-border)' }}>
                    <td style={{ padding: '7px 12px' }} title={s.paper_title}>
                      {truncate(s.paper_title || s.strategy_id)}
                    </td>
                    <td className="mono" style={{ padding: '7px 12px', textAlign: 'right' }}>
                      {fmtPct(s.implied_vol)}
                    </td>
                    <td className="mono" style={{ padding: '7px 12px', textAlign: 'right' }}>
                      {fmtGreek(s.delta)}
                    </td>
                    <td className="mono" style={{ padding: '7px 12px', textAlign: 'right' }}>
                      {fmtGreek(s.vega)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <p className="caption" style={{ marginTop: 12, color: 'var(--text-4)', lineHeight: 1.5 }}>
        Greeks computed at implied vol {fmtPct(g.implied_vol_assumption)}, risk-free rate {fmtPct(g.risk_free_rate, 1)},
        {' '}{g.time_horizon_days}-day horizon. Delta and vega are the primary risk lenses for a long-vol quant portfolio.
      </p>
    </div>
  )
}

// ─── Drawdown / underwater area chart ───────────────────────
function DrawdownPlot({ returns, sample }) {
  const W = 700
  const H = 200
  const PAD_L = 44
  const PAD_R = 12
  const PAD_T = 12
  const PAD_B = 22

  const { areaPath, linePath, minDD, yTicks } = useMemo(() => {
    const equity = equityFromReturns(returns)
    const dd = drawdownSeries(equity) // <= 0
    const n = dd.length
    const lo = Math.min(...dd, -0.01)
    const toX = (i) => PAD_L + (i / Math.max(1, n - 1)) * (W - PAD_L - PAD_R)
    const toY = (v) => PAD_T + (v / lo) * (H - PAD_T - PAD_B) // v in [lo,0] -> [bottom,top]
    const pts = dd.map((v, i) => `${toX(i).toFixed(1)},${toY(v).toFixed(1)}`)
    const baselineY = toY(0)
    const area = `M ${PAD_L},${baselineY} L ${pts.join(' L ')} L ${toX(n - 1)},${baselineY} Z`
    const line = `M ${pts.join(' L ')}`
    const ticks = [0, 0.5, 1].map((f) => {
      const v = lo * f
      return { y: toY(v), label: `${(v * 100).toFixed(0)}%` }
    })
    return { areaPath: area, linePath: line, minDD: lo, yTicks: ticks }
  }, [returns])

  return (
    <div className="card-flat" style={{ padding: 20, marginBottom: 20 }}>
      <div className="label mb-2">
        Drawdown (Underwater Plot)
        {sample && <SampleDataBadge />}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Drawdown over time" style={{ display: 'block' }}>
        {yTicks.map((t, i) => (
          <line key={i} x1={PAD_L} y1={t.y} x2={W - PAD_R} y2={t.y} stroke="var(--chart-grid)" strokeWidth="1" />
        ))}
        {yTicks.map((t, i) => (
          <text key={`l${i}`} x={PAD_L - 6} y={t.y + 3} textAnchor="end" fill="var(--chart-label)" fontSize="9" className="mono">
            {t.label}
          </text>
        ))}
        <path d={areaPath} fill="rgba(239,68,68,0.16)" stroke="none" />
        <path d={linePath} fill="none" stroke="var(--negative)" strokeWidth="1.5" strokeLinejoin="round" />
      </svg>
      <p className="caption" style={{ marginTop: 8, color: 'var(--text-4)' }}>
        Decline from the running high-water mark. Max drawdown over the window:{' '}
        <span className="mono negative">{fmtPct(-minDD)}</span>. Shallower and shorter underwater periods
        indicate a more capital-preserving strategy.
      </p>
    </div>
  )
}

// ─── Rolling Sharpe line chart (30 / 60 / 90d) ──────────────
function RollingSharpePlot({ returns, sample }) {
  const W = 700
  const H = 220
  const PAD_L = 44
  const PAD_R = 12
  const PAD_T = 12
  const PAD_B = 22

  const { lines, yTicks, zeroY } = useMemo(() => {
    const computed = SHARPE_WINDOWS.map((cfg) => ({ ...cfg, series: rollingSharpe(returns, cfg.w) }))
    const all = computed.flatMap((c) => c.series.filter((v) => v != null))
    const lo = Math.min(-1, ...all)
    const hi = Math.max(1, ...all)
    const n = returns.length
    const toX = (i) => PAD_L + (i / Math.max(1, n - 1)) * (W - PAD_L - PAD_R)
    const toY = (v) => PAD_T + (1 - (v - lo) / (hi - lo)) * (H - PAD_T - PAD_B)
    const built = computed.map((c) => {
      const segs = []
      let cur = []
      c.series.forEach((v, i) => {
        if (v == null) {
          if (cur.length) segs.push(cur)
          cur = []
        } else {
          cur.push(`${toX(i).toFixed(1)},${toY(v).toFixed(1)}`)
        }
      })
      if (cur.length) segs.push(cur)
      return { ...c, path: segs.map((s) => `M ${s.join(' L ')}`).join(' ') }
    })
    const ticks = [hi, (hi + lo) / 2, lo, 0]
      .filter((v, i, arr) => arr.indexOf(v) === i)
      .map((v) => ({ y: toY(v), label: v.toFixed(1) }))
    return { lines: built, yTicks: ticks, zeroY: toY(0) }
  }, [returns])

  return (
    <div className="card-flat" style={{ padding: 20, marginBottom: 20 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
        <div className="label mb-2">
          Rolling Sharpe Ratio (annualized)
          {sample && <SampleDataBadge />}
        </div>
        <div style={{ display: 'flex', gap: 14 }}>
          {lines.map((l) => (
            <span key={l.label} className="caption" style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
              <span style={{ width: 14, height: 3, background: l.color, borderRadius: 2, display: 'inline-block' }} />
              {l.label}
            </span>
          ))}
        </div>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Rolling Sharpe ratio over time" style={{ display: 'block' }}>
        {yTicks.map((t, i) => (
          <line key={i} x1={PAD_L} y1={t.y} x2={W - PAD_R} y2={t.y} stroke="var(--chart-grid)" strokeWidth="1" />
        ))}
        {yTicks.map((t, i) => (
          <text key={`l${i}`} x={PAD_L - 6} y={t.y + 3} textAnchor="end" fill="var(--chart-label)" fontSize="9" className="mono">
            {t.label}
          </text>
        ))}
        {/* zero reference line */}
        <line x1={PAD_L} y1={zeroY} x2={W - PAD_R} y2={zeroY} stroke="var(--chart-grid-strong)" strokeWidth="1" strokeDasharray="3 3" />
        {lines.map((l) => (
          <path key={l.label} d={l.path} fill="none" stroke={l.color} strokeWidth="1.6" strokeLinejoin="round" />
        ))}
      </svg>
      <p className="caption" style={{ marginTop: 8, color: 'var(--text-4)', lineHeight: 1.5 }}>
        Trailing-window Sharpe (mean / std of excess returns, annualized ×√252). A stable line well above
        0 indicates persistent risk-adjusted edge; a Sharpe that decays toward 0 out-of-sample is a classic
        overfitting tell — exactly what the DSR/PBO rigor gate is built to catch.
      </p>
    </div>
  )
}

// ─── Correlation heatmap ────────────────────────────────────
function CorrelationHeatmap({ assets, series, sample }) {
  const matrix = useMemo(() => {
    return assets.map((_, i) => assets.map((__, j) => correlation(series[i], series[j])))
  }, [assets, series])

  // diverging color: +1 accent-purple, 0 neutral, -1 blue
  function cellColor(v) {
    if (v >= 0) {
      return `rgba(192,132,252,${(0.12 + 0.7 * v).toFixed(3)})`
    }
    return `rgba(96,165,250,${(0.12 + 0.7 * -v).toFixed(3)})`
  }

  return (
    <div className="card-flat" style={{ padding: 20, marginBottom: 20 }}>
      <div className="label mb-3">
        Correlation Matrix (daily returns, ρ)
        {sample && <SampleDataBadge />}
      </div>
      <div className="risk-heatmap" style={{ gridTemplateColumns: `auto repeat(${assets.length}, 1fr)` }}>
        <div className="risk-heatmap-corner" />
        {assets.map((a) => (
          <div key={`h${a}`} className="risk-heatmap-head mono">
            {a}
          </div>
        ))}
        {assets.map((rowA, i) => (
          <div key={`row${rowA}`} style={{ display: 'contents' }}>
            <div className="risk-heatmap-head mono" style={{ textAlign: 'right', paddingRight: 8 }}>
              {rowA}
            </div>
            {assets.map((colA, j) => {
              const v = matrix[i][j]
              return (
                <div
                  key={`${rowA}-${colA}`}
                  className="risk-heatmap-cell mono"
                  style={{ background: cellColor(v) }}
                  title={`ρ(${rowA}, ${colA}) = ${v.toFixed(2)}`}
                >
                  {v.toFixed(2)}
                </div>
              )
            })}
          </div>
        ))}
      </div>
      <p className="caption" style={{ marginTop: 10, color: 'var(--text-4)', lineHeight: 1.5 }}>
        Pairwise Pearson correlation. Diversification benefit comes from low or negative ρ — a portfolio of
        highly-correlated (ρ near +1, purple) sleeves carries hidden concentration risk that VaR alone can
        understate.
      </p>
    </div>
  )
}

export default function RiskAnalysis({ returns: returnsProp, assets: assetsProp, series: seriesProp } = {}) {
  const data = useMemo(() => {
    const d = buildDefaultData()
    return {
      returns: returnsProp ?? d.returns,
      assets: assetsProp ?? d.assets,
      series: seriesProp ?? d.series,
    }
  }, [returnsProp, assetsProp, seriesProp])

  const [cvarData, setCvarData] = useState(null)
  const [greeksData, setGreeksData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [backendError, setBackendError] = useState(false)

  useEffect(() => {
    let cancelled = false
    async function fetchRiskData() {
      setLoading(true)
      try {
        // apiGet throws on any non-2xx (404 when FEATURE_QUANT is off, 401,
        // 5xx, ...) — unlike a raw fetch(), whose `.ok` check the old version
        // of this effect skipped, silently treating every one of those as
        // success and leaving `backendError` false. The catch below is what
        // now actually reaches the not-live-data notice.
        const [cvar, greeks] = await Promise.all([apiGet('/api/risk/cvar'), apiGet('/api/risk/greeks')])
        if (!cancelled) {
          setCvarData(cvar)
          setGreeksData(greeks)
        }
      } catch (_) {
        if (!cancelled) setBackendError(true)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    fetchRiskData()
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <div>
      <div style={{ maxWidth: 680, marginBottom: 28 }}>
        <h2 className="serif" style={{ fontSize: '2rem', marginBottom: 10 }}>
          Risk Analysis
        </h2>
        <p className="body">
          Tail-risk, drawdown, and correlation diagnostics. These are the
          loss-side counterparts to the return-side metrics in Optimizer &amp; Sizing below — rigor means
          making the downside as legible as the upside.
        </p>
      </div>

      {loading && (
        <div className="caption" style={{ marginBottom: 16, color: 'var(--text-4)' }}>
          Loading live risk metrics…
        </div>
      )}

      {backendError && cvarData == null && (
        <div
          className="info-box"
          style={{ marginBottom: 16, fontSize: '0.85rem' }}
        >
          Live risk metrics aren't available for this deployment right now — the figures below are a
          synthetic sample, marked on each section's badge.
        </div>
      )}

      <VaRPanel returns={data.returns} cvarData={cvarData} sample={returnsProp == null} />
      <GreeksPanel greeksData={greeksData} />
      <DrawdownPlot returns={data.returns} sample={returnsProp == null} />
      <RollingSharpePlot returns={data.returns} sample={returnsProp == null} />
      <CorrelationHeatmap assets={data.assets} series={data.series} sample={seriesProp == null} />
    </div>
  )
}
