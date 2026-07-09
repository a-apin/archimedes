// BacktestVisualizer — equity+drawdown dual chart, chronological IS/OOS bands,
// parameter-sweep heatmap, filterable trade log, and rolling-stat confidence
// band. Accepts real props; fetches daily returns from the API when strategyId
// is set (#passport-honesty: never falls back to mock data for the equity curve).
//
// Suggested integration: import into the StrategyPassport / backtest detail
// surface, e.g.
//   import BacktestVisualizer from './components/BacktestVisualizer'
// and render <BacktestVisualizer result={backtestResult} strategyId={id} weights={weights} />.
//
// All inline SVG (no chart lib). Styling reuses shared App.css classes plus a
// small colocated BacktestVisualizer.css.

import { useState, useMemo, useEffect } from 'react'
import {
  equityFromReturns,
  drawdownSeries,
  rollingSharpe,
  seededRng,
  walkForwardFolds,
} from '../utils/riskMath'
import SampleDataBadge from './SampleDataBadge'
import './BacktestVisualizer.css'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

function fmt(v, d = 2) {
  return v != null && Number.isFinite(v) ? v.toFixed(d) : '—'
}

// ─── Equity curve + drawdown (dual axis) ────────────────────
function EquityDrawdownChart({ returns, oosStartFrac }) {
  const W = 720
  const H = 300
  const PAD_L = 52
  const PAD_R = 52
  const PAD_T = 16
  const PAD_B = 26

  const { equityLine, ddArea, eqTicks, ddTicks, oosX } = useMemo(() => {
    const equity = equityFromReturns(returns)
    const dd = drawdownSeries(equity)
    const n = equity.length
    const eqLo = Math.min(...equity)
    const eqHi = Math.max(...equity)
    // A flat series (e.g. an all-zero returns row) makes eqHi === eqLo; guard
    // the span so the scale stays finite instead of NaN-ing every y attribute.
    const eqSpan = eqHi - eqLo || 1
    const ddLo = Math.min(...dd, -0.01)
    const toX = (i) => PAD_L + (i / Math.max(1, n - 1)) * (W - PAD_L - PAD_R)
    const toYeq = (v) => PAD_T + (1 - (v - eqLo) / eqSpan) * (H - PAD_T - PAD_B)
    const toYdd = (v) => PAD_T + (v / ddLo) * (H - PAD_T - PAD_B)
    const eqPts = equity.map((v, i) => `${toX(i).toFixed(1)},${toYeq(v).toFixed(1)}`)
    const ddPts = dd.map((v, i) => `${toX(i).toFixed(1)},${toYdd(v).toFixed(1)}`)
    const ddBase = toYdd(0)
    return {
      equityLine: `M ${eqPts.join(' L ')}`,
      ddArea: `M ${PAD_L},${ddBase} L ${ddPts.join(' L ')} L ${toX(n - 1)},${ddBase} Z`,
      eqTicks: [eqHi, (eqHi + eqLo) / 2, eqLo].map((v) => ({ y: toYeq(v), label: v.toFixed(2) })),
      ddTicks: [0, ddLo / 2, ddLo].map((v) => ({ y: toYdd(v), label: `${(v * 100).toFixed(0)}%` })),
      oosX: toX(Math.floor(oosStartFrac * (n - 1))),
    }
  }, [returns, oosStartFrac])

  return (
    <div className="card-elevated" style={{ padding: 24, marginBottom: 20 }}>
      <div className="label mb-2">Equity Curve &amp; Drawdown</div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Equity curve with drawdown overlay" style={{ display: 'block' }}>
        {eqTicks.map((t, i) => (
          <line key={i} x1={PAD_L} y1={t.y} x2={W - PAD_R} y2={t.y} stroke="var(--chart-grid)" strokeWidth="1" />
        ))}
        {/* left axis = equity (accent) */}
        {eqTicks.map((t, i) => (
          <text key={`eq${i}`} x={PAD_L - 6} y={t.y + 3} textAnchor="end" fill="var(--accent)" fontSize="9" className="mono">
            {t.label}
          </text>
        ))}
        {/* right axis = drawdown (red) */}
        {ddTicks.map((t, i) => (
          <text key={`dd${i}`} x={W - PAD_R + 6} y={t.y + 3} textAnchor="start" fill="var(--negative)" fontSize="9" className="mono">
            {t.label}
          </text>
        ))}
        {/* OOS divider */}
        <line x1={oosX} y1={PAD_T} x2={oosX} y2={H - PAD_B} stroke="var(--chart-grid-strong)" strokeWidth="1" strokeDasharray="4 3" />
        <text x={oosX + 4} y={PAD_T + 10} fill="var(--chart-label-strong)" fontSize="9">
          OOS →
        </text>
        <path d={ddArea} fill="rgba(239,68,68,0.14)" stroke="none" />
        <path d={equityLine} fill="none" stroke="var(--accent)" strokeWidth="1.8" strokeLinejoin="round" />
      </svg>
      <p className="caption" style={{ marginTop: 8, color: 'var(--text-4)', lineHeight: 1.5 }}>
        <span style={{ color: 'var(--accent)' }}>Purple</span> = cumulative equity (left axis);{' '}
        <span style={{ color: 'var(--negative)' }}>red</span> = drawdown from peak (right axis). The dashed
        line marks the in-sample / out-of-sample boundary — equity that keeps compounding past it without a
        drawdown cliff is the signal we want.
      </p>
    </div>
  )
}

// ─── Walk-forward IS/OOS band visualizer ────────────────────
function WalkForwardBands({ folds, sample }) {
  const W = 720
  const H = 90
  const PAD_L = 8
  const PAD_R = 8

  const segW = (W - PAD_L - PAD_R) / folds.length

  return (
    <div className="card-flat" style={{ padding: 20, marginBottom: 20 }}>
      <div className="label mb-2">
        Walk-Forward Validation (IS / OOS folds)
        {sample && <SampleDataBadge />}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Walk-forward in-sample and out-of-sample folds" style={{ display: 'block' }}>
        {folds.map((f, i) => {
          const x = PAD_L + i * segW
          const isW = segW * 0.7
          const oosW = segW * 0.28
          const oosColor = f.oosSharpe > 0 ? 'var(--positive)' : 'var(--negative)'
          return (
            <g key={i}>
              <rect x={x} y={20} width={isW} height={40} rx={3} fill="rgba(192,132,252,0.28)" stroke="var(--accent)" strokeWidth="0.8">
                <title>Fold {i + 1} in-sample: train Sharpe {f.isSharpe.toFixed(2)}</title>
              </rect>
              <rect x={x + isW + 2} y={20} width={oosW} height={40} rx={3} fill={oosColor} opacity="0.85">
                <title>Fold {i + 1} out-of-sample: OOS Sharpe {f.oosSharpe.toFixed(2)}</title>
              </rect>
              <text x={x + segW / 2} y={14} textAnchor="middle" fill="var(--chart-label)" fontSize="8">
                F{i + 1}
              </text>
              <text x={x + segW / 2} y={76} textAnchor="middle" fill="var(--chart-label-strong)" fontSize="8" className="mono">
                {f.oosSharpe.toFixed(2)}
              </text>
            </g>
          )
        })}
      </svg>
      <div style={{ display: 'flex', gap: 16, marginTop: 4 }}>
        <span className="caption" style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
          <span style={{ width: 12, height: 8, background: 'rgba(192,132,252,0.4)', border: '1px solid var(--accent)', borderRadius: 2 }} /> In-sample (train)
        </span>
        <span className="caption" style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
          <span style={{ width: 12, height: 8, background: 'var(--positive)', borderRadius: 2 }} /> OOS Sharpe &gt; 0
        </span>
        <span className="caption" style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
          <span style={{ width: 12, height: 8, background: 'var(--negative)', borderRadius: 2 }} /> OOS Sharpe ≤ 0
        </span>
      </div>
      <p className="caption" style={{ marginTop: 8, color: 'var(--text-4)', lineHeight: 1.5 }}>
        Each fold trains on its purple in-sample window, then tests on the adjacent out-of-sample slice. A
        strategy whose OOS bars stay green and close to their IS Sharpe is stable; a sharp IS→OOS drop is
        the overfitting signature that PBO quantifies.
      </p>
    </div>
  )
}

// ─── Trade / rebalance log with date filter ─────────────────
function TradeLog({ trades, sample }) {
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')

  const filtered = useMemo(() => {
    return trades.filter((t) => {
      if (from && t.date < from) return false
      if (to && t.date > to) return false
      return true
    })
  }, [trades, from, to])

  // Real rows from /api/traces carry {amount} (token units); the synthetic
  // scaffold carries {weightDelta, price}. Column set follows the data so we
  // never render a fabricated price next to a real trade.
  const hasAmount = trades.some((t) => t.amount != null)

  return (
    <div className="card-flat" style={{ padding: 20, marginBottom: 20 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
        <div className="label mb-0">
          Trade / Rebalance Log
          {sample && <SampleDataBadge />}
        </div>
        <div className="bv-filter">
          <label className="caption">
            From <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
          </label>
          <label className="caption">
            To <input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
          </label>
          {(from || to) && (
            <button className="bv-clear" onClick={() => { setFrom(''); setTo('') }}>Clear</button>
          )}
        </div>
      </div>
      <div className="table-container" style={{ marginTop: 12 }}>
        <table>
          <thead>
            <tr>
              <th>Date</th>
              <th>Action</th>
              <th>Asset</th>
              {hasAmount ? (
                <th className="text-right">Amount</th>
              ) : (
                <>
                  <th className="text-right">Weight Δ</th>
                  <th className="text-right">Price</th>
                </>
              )}
            </tr>
          </thead>
          <tbody>
            {filtered.map((t, i) => (
              <tr key={i}>
                <td className="mono" style={{ fontSize: '0.76rem' }}>{t.date}</td>
                <td>
                  <span className={t.action === 'BUY' ? 'positive' : t.action === 'SELL' ? 'negative' : ''} style={{ fontWeight: 600, fontSize: '0.8rem' }}>
                    {t.action}
                  </span>
                </td>
                <td className="mono" style={{ fontSize: '0.78rem' }}>{t.asset}</td>
                {hasAmount ? (
                  <td className="text-right mono">{fmt(t.amount)}</td>
                ) : (
                  <>
                    <td className={`text-right mono ${t.weightDelta > 0 ? 'positive' : t.weightDelta < 0 ? 'negative' : ''}`}>
                      {t.weightDelta > 0 ? '+' : ''}{(t.weightDelta * 100).toFixed(1)}%
                    </td>
                    <td className="text-right mono">{fmt(t.price)}</td>
                  </>
                )}
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={hasAmount ? 4 : 5} className="caption" style={{ textAlign: 'center', padding: 16, color: 'var(--text-4)' }}>
                  No trades in the selected date range.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <p className="caption" style={{ marginTop: 8, color: 'var(--text-4)' }}>
        Showing {filtered.length} of {trades.length} rebalance events.
        {sample
          ? ' These rows are generated samples that demonstrate the log format — no real trades happened.'
          : ' Each row comes from a recorded agent rebalance trace.'}
      </p>
    </div>
  )
}

// ─── Rolling statistic with confidence band ─────────────────
function RollingStatBand({ returns, window }) {
  const W = 720
  const H = 220
  const PAD_L = 44
  const PAD_R = 12
  const PAD_T = 14
  const PAD_B = 22

  const { line, bandPath, yTicks, zeroY } = useMemo(() => {
    const series = rollingSharpe(returns, window)
    const valid = series.map((v, i) => ({ v, i })).filter((d) => d.v != null)
    const ys = valid.map((d) => d.v)
    // crude ±1 std confidence band around each point's neighborhood
    const lo = Math.min(-1, ...ys)
    const hi = Math.max(1, ...ys)
    const n = returns.length
    const toX = (i) => PAD_L + (i / Math.max(1, n - 1)) * (W - PAD_L - PAD_R)
    const toY = (v) => PAD_T + (1 - (v - lo) / (hi - lo)) * (H - PAD_T - PAD_B)
    // standard error of a Sharpe estimate ~ sqrt((1 + 0.5*S^2)/window)
    const se = (s) => Math.sqrt((1 + 0.5 * s * s) / window)
    const upper = valid.map((d) => `${toX(d.i).toFixed(1)},${toY(d.v + se(d.v)).toFixed(1)}`)
    const lower = valid.map((d) => `${toX(d.i).toFixed(1)},${toY(d.v - se(d.v)).toFixed(1)}`).reverse()
    const band = valid.length ? `M ${upper.join(' L ')} L ${lower.join(' L ')} Z` : ''
    const mid = valid.length ? `M ${valid.map((d) => `${toX(d.i).toFixed(1)},${toY(d.v).toFixed(1)}`).join(' L ')}` : ''
    const ticks = [hi, 0, lo].map((v) => ({ y: toY(v), label: v.toFixed(1) }))
    return { line: mid, bandPath: band, yTicks: ticks, zeroY: toY(0) }
  }, [returns, window])

  return (
    <div className="card-flat" style={{ padding: 20, marginBottom: 20 }}>
      <div className="label mb-2">Rolling Sharpe with Confidence Band ({window}d)</div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Rolling Sharpe with confidence band" style={{ display: 'block' }}>
        {yTicks.map((t, i) => (
          <line key={i} x1={PAD_L} y1={t.y} x2={W - PAD_R} y2={t.y} stroke="var(--chart-grid)" strokeWidth="1" />
        ))}
        {yTicks.map((t, i) => (
          <text key={`l${i}`} x={PAD_L - 6} y={t.y + 3} textAnchor="end" fill="var(--chart-label)" fontSize="9" className="mono">
            {t.label}
          </text>
        ))}
        <line x1={PAD_L} y1={zeroY} x2={W - PAD_R} y2={zeroY} stroke="var(--chart-grid-strong)" strokeWidth="1" strokeDasharray="3 3" />
        {bandPath && <path d={bandPath} fill="rgba(192,132,252,0.15)" stroke="none" />}
        {line && <path d={line} fill="none" stroke="var(--accent)" strokeWidth="1.6" strokeLinejoin="round" />}
      </svg>
      <p className="caption" style={{ marginTop: 8, color: 'var(--text-4)', lineHeight: 1.5 }}>
        Shaded band is the ±1 standard-error envelope on the rolling Sharpe estimate (SE ≈ √((1 + ½S²)/n)).
        When the band straddles 0, the estimated edge is not statistically distinguishable from noise over
        that window.
      </p>
    </div>
  )
}

// ─── mock scaffolding for walk-forward/sweep/trade sections ─
// Returns (equity curve) are fetched from the real API — no mock data for those.
function buildMockScaffold() {
  const rng = seededRng(5)
  const folds = Array.from({ length: 6 }, (_, i) => ({
    isSharpe: 1.0 + rng() * 0.8,
    oosSharpe: 0.6 + rng() * 0.9 - (i === 4 ? 1.1 : 0), // one weak fold for realism
  }))
  const rows = [10, 20, 40, 60, 90]
  const cols = [0.5, 1.0, 1.5, 2.0]
  const grid = rows.map((r) =>
    cols.map((c) => {
      const peak = Math.exp(-(((r - 40) / 40) ** 2) - ((c - 1.5) / 1.2) ** 2)
      return 0.3 + 1.6 * peak + (rng() - 0.5) * 0.25
    }),
  )
  const flat = grid.flat()
  const sweep = {
    rows,
    cols,
    grid,
    max: Math.max(...flat),
    min: Math.min(...flat),
    param1Name: 'lookback',
    param2Name: 'threshold',
    metric: 'sharpe_ratio',
  }
  const actions = ['BUY', 'SELL', 'REBAL']
  const assets = ['sSPY', 'sBTC', 'sGOLD', 'sNVDA']
  const trades = Array.from({ length: 14 }, (_, i) => {
    const d = new Date(2025, 0, 1 + i * 18)
    return {
      date: d.toISOString().slice(0, 10),
      action: actions[Math.floor(rng() * actions.length)],
      asset: assets[Math.floor(rng() * assets.length)],
      weightDelta: (rng() - 0.5) * 0.16,
      price: 50 + rng() * 400,
    }
  })
  return { folds, sweep, trades }
}

const METRIC_OPTIONS = [
  { value: 'sharpe_ratio', label: 'Sharpe Ratio' },
  { value: 'cagr', label: 'CAGR' },
  { value: 'max_drawdown', label: 'Max Drawdown' },
  { value: 'calmar_ratio', label: 'Calmar Ratio' },
]

export default function BacktestVisualizer({ result, strategyId, weights, realTrades } = {}) {
  const [sweepData, setSweepData] = useState(null)
  const [sweepLoading, setSweepLoading] = useState(false)
  const [sweepError, setSweepError] = useState('')
  const [sweepConfig, setSweepConfig] = useState({
    param1Name: 'rebalance_days',
    param1Range: [5, 10, 20, 30, 60],
    param2Name: 'tx_cost_bps',
    param2Range: [2, 5, 10, 20, 50],
    metric: 'sharpe_ratio',
  })

  // ── Real returns state (#passport-honesty) ───────────────────────────────
  // Fetched from GET /api/strategies/{id}/returns. Never falls back to mock
  // data: on 404 ("no persisted returns") we render an honest empty state.
  const [returnsLoading, setReturnsLoading] = useState(false)
  const [realReturns, setRealReturns] = useState(null)   // float[] | null
  const [returnsNoData, setReturnsNoData] = useState(false)

  // Depend on the minimal inputs (strategyId + the returns array itself), NOT
  // the whole `result` object — parents recreate `result` on unrelated
  // re-renders and that identity churn must not trigger refetches.
  const propReturns = result?.returns ?? null

  useEffect(() => {
    // If the caller passes real returns via the result prop, use those directly.
    // Clear any loading state a prior strategyId's in-flight fetch left behind.
    if (propReturns) {
      setRealReturns(propReturns)
      setReturnsNoData(false)
      setReturnsLoading(false)
      return
    }
    if (!strategyId) {
      setRealReturns(null)
      setReturnsNoData(false)
      setReturnsLoading(false)
      return
    }
    // Cancellation guard: abort the in-flight request and drop late setState
    // when strategyId changes or the component unmounts.
    const controller = new AbortController()
    let cancelled = false
    setReturnsLoading(true)
    setRealReturns(null)
    setReturnsNoData(false)
    // credentials:'include' sends the SIWE session cookie (same idiom as
    // ui/src/api.js apiGet) so owners of private strategies get their returns
    // when VITE_API_BASE is cross-origin instead of always seeing the 404 state.
    fetch(`${API_BASE}/api/strategies/${encodeURIComponent(strategyId)}/returns`, {
      credentials: 'include',
      signal: controller.signal,
    })
      .then((res) => {
        if (res.status === 404) {
          if (!cancelled) setReturnsNoData(true)
          return null
        }
        if (!res.ok) throw new Error(`Server error ${res.status}`)
        return res.json()
      })
      .then((data) => {
        if (!cancelled && data?.daily_returns) setRealReturns(data.daily_returns)
      })
      .catch((err) => {
        // Abort is not an error state; network/unexpected errors read as no data
        // rather than crashing — still never a mock fallback.
        if (!cancelled && err?.name !== 'AbortError') setReturnsNoData(true)
      })
      .finally(() => {
        if (!cancelled) setReturnsLoading(false)
      })
    return () => {
      cancelled = true
      controller.abort()
    }
  }, [strategyId, propReturns])

  // Real walk-forward folds, computed from the fetched daily returns. Empty
  // when no real returns exist (or the series is too short for honest folds).
  const liveFolds = useMemo(
    () => (realReturns ? walkForwardFolds(realReturns) : []),
    [realReturns],
  )

  // Mock scaffold for walk-forward/sweep/trade sections — used only where no
  // real source resolved; those sections keep their sample badge.
  const data = useMemo(() => {
    const mock = buildMockScaffold()
    return {
      folds: result?.folds ?? (liveFolds.length ? liveFolds : mock.folds),
      sweep: result?.sweep ?? mock.sweep,
      trades: result?.trades ?? realTrades ?? mock.trades,
    }
  }, [result, liveFolds, realTrades])

  async function fetchSweep() {
    if (!strategyId || !weights) return
    setSweepLoading(true)
    setSweepError('')
    try {
      const res = await fetch(`${API_BASE}/api/portfolio/parameter-sweep`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          strategy_id: strategyId,
          weights,
          param1_name: sweepConfig.param1Name,
          param1_range: sweepConfig.param1Range,
          param2_name: sweepConfig.param2Name,
          param2_range: sweepConfig.param2Range,
          metric: sweepConfig.metric,
        }),
      })
      if (!res.ok) throw new Error(`Server error ${res.status}`)
      const json = await res.json()
      setSweepData(json)
    } catch (e) {
      setSweepError(e.message || 'Sweep failed')
    } finally {
      setSweepLoading(false)
    }
  }

  const sweepForHeatmap = useMemo(() => {
    if (sweepData) {
      const flat = sweepData.grid_2d.flat()
      const validFlat = flat.filter((v) => Number.isFinite(v))
      return {
        rows: sweepData.rows,
        cols: sweepData.cols,
        grid: sweepData.grid_2d,
        max: validFlat.length ? Math.max(...validFlat) : 2,
        min: validFlat.length ? Math.min(...validFlat) : 0,
        param1Name: sweepData.param1_name,
        param2Name: sweepData.param2_name,
        metric: sweepData.metric,
      }
    }
    return data.sweep
  }, [sweepData, data.sweep])

  const sensRatio = sweepData?.sensitivity_ratio ?? null

  // Per-section sample flags (#1060): true while a section still renders the
  // mock scaffold; flips off as soon as real data arrives via props or API.
  const foldsAreSample = result?.folds == null && liveFolds.length === 0
  const sweepIsSample = sweepData == null && result?.sweep == null
  const tradesAreSample = result?.trades == null && realTrades == null

  return (
    <div>
      <div style={{ maxWidth: 680, marginBottom: 28 }}>
        <h2 className="serif" style={{ fontSize: '2rem', marginBottom: 10 }}>
          Backtest Visualizer
        </h2>
        <p className="body">
          The full diagnostic surface behind a strategy passport: equity and drawdown, chronological
          OOS stability, parameter robustness, the rebalance log, and the statistical-significance band on
          rolling performance.
        </p>
      </div>

      {/* ── Equity curve: real returns from API or honest empty state ── */}
      {returnsLoading && (
        <div className="card-elevated" style={{ padding: 24, marginBottom: 20, textAlign: 'center', color: 'var(--text-3)' }}>
          Loading backtest returns…
        </div>
      )}
      {!returnsLoading && realReturns && (
        <EquityDrawdownChart returns={realReturns} oosStartFrac={0.7} />
      )}
      {!returnsLoading && returnsNoData && (
        <div className="card-elevated" style={{ padding: 24, marginBottom: 20 }}>
          <div className="label mb-2">Equity Curve &amp; Drawdown</div>
          <p className="body" style={{ color: 'var(--text-3)', fontStyle: 'italic' }}>
            No real backtest returns persisted yet — this strategy has not completed a real-data backtest.
          </p>
        </div>
      )}
      <WalkForwardBands folds={data.folds} sample={foldsAreSample} />

      {/* Parameter sweep section */}
      <div className="card-flat" style={{ padding: 20, marginBottom: 20 }}>
        <div className="label mb-3">
          Parameter Sweep ({sweepForHeatmap.metric ?? 'metric'} by parameter pair)
          {sweepIsSample && <SampleDataBadge />}
        </div>

        {strategyId && (
          <div className="bv-sweep-controls" style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14, flexWrap: 'wrap' }}>
            <select
              className="bv-metric-select"
              value={sweepConfig.metric}
              onChange={(e) => setSweepConfig((c) => ({ ...c, metric: e.target.value }))}
              style={{ fontSize: '0.82rem', padding: '4px 8px', background: 'var(--glass)', border: '1px solid var(--glass-border)', borderRadius: 4, color: 'var(--text-1)' }}
            >
              {METRIC_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
            <button
              className="bv-run-sweep"
              onClick={fetchSweep}
              disabled={sweepLoading}
              style={{
                fontSize: '0.82rem',
                padding: '4px 14px',
                background: sweepLoading ? 'rgba(192,132,252,0.2)' : 'var(--accent)',
                color: sweepLoading ? 'var(--text-3)' : '#fff',
                border: 'none',
                borderRadius: 4,
                cursor: sweepLoading ? 'not-allowed' : 'pointer',
                fontWeight: 600,
              }}
            >
              {sweepLoading ? 'Computing…' : 'Run Parameter Sweep'}
            </button>
            {sweepLoading && <span className="caption" style={{ color: 'var(--text-4)' }}>Computing sweep…</span>}
            {sweepError && <div className="info-box warning" style={{ flex: '1 1 100%', marginTop: 4 }}>{sweepError}</div>}
          </div>
        )}

        <div className="bv-sweep" style={{ gridTemplateColumns: `auto repeat(${sweepForHeatmap.cols.length}, 1fr)` }}>
          <div className="bv-sweep-corner caption">
            {`${sweepForHeatmap.param1Name ?? 'param1'} ↓ / ${sweepForHeatmap.param2Name ?? 'param2'} →`}
          </div>
          {sweepForHeatmap.cols.map((c) => (
            <div key={`c${c}`} className="bv-sweep-head mono">{c}</div>
          ))}
          {sweepForHeatmap.rows.map((r, i) => {
            const range = (sweepForHeatmap.max - sweepForHeatmap.min) || 1
            return (
              <div key={`r${r}`} style={{ display: 'contents' }}>
                <div className="bv-sweep-head mono" style={{ textAlign: 'right', paddingRight: 8 }}>{r}</div>
                {sweepForHeatmap.cols.map((c, j) => {
                  const v = sweepForHeatmap.grid[i][j]
                  const t = (v - sweepForHeatmap.min) / range
                  const bg = `rgba(192,132,252,${(0.08 + 0.82 * t).toFixed(3)})`
                  const metricName = sweepForHeatmap.metric ?? 'metric'
                  const p1 = sweepForHeatmap.param1Name ?? 'param1'
                  const p2 = sweepForHeatmap.param2Name ?? 'param2'
                  return (
                    <div
                      key={`${r}-${c}`}
                      className="bv-sweep-cell mono"
                      style={{ background: bg }}
                      title={`${p1}=${r}, ${p2}=${c} → ${metricName} ${v.toFixed(2)}`}
                    >
                      {v.toFixed(2)}
                    </div>
                  )
                })}
              </div>
            )
          })}
        </div>

        <p className="caption" style={{ marginTop: 10, color: 'var(--text-4)', lineHeight: 1.5 }}>
          {sensRatio != null
            ? `Sensitivity ratio: ${sensRatio.toFixed(2)} — ${sensRatio > 0.5 ? 'fragile (high sensitivity)' : 'robust (low sensitivity)'}.`
            : null}
          {sensRatio != null ? ' ' : null}
          {sweepForHeatmap.metric ?? 'metric'} across a 2D parameter grid. A single bright island surrounded by dark cells is a fragile
          peak (likely overfit); a broad bright plateau means the edge is robust to parameter choice. We
          report the full grid rather than only the best cell — that&apos;s what the deflated-Sharpe
          multiple-testing correction accounts for.
        </p>
      </div>

      <TradeLog trades={data.trades} sample={tradesAreSample} />
      {realReturns && <RollingStatBand returns={realReturns} window={60} />}
    </div>
  )
}
