import { useEffect, useState } from 'react'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

// Renders the stress_engine.py output for a portfolio. Six canonical historical
// shocks per backend (equity crash 2008, COVID 2020, rate shock 2022, EM crisis
// 2018, etc.). Per [`docs/chuan-architecture-survey.md`](../../docs/chuan-architecture-survey.md)
// gap #13 — the engine exists but had no UI surface.
//
// Phase 4 scaffold: surfaces scenarios + portfolio-PnL view against a
// caller-supplied allocations array. Drop into Portfolio.jsx (next to or under
// the Allocation Advisor) for the demo path.

function fmtPct(v) {
  if (v == null || !Number.isFinite(v)) return '—'
  const sign = v > 0 ? '+' : ''
  return `${sign}${(Number(v) * 100).toFixed(2)}%`
}

function fmtUsd(v) {
  if (v == null || !Number.isFinite(v)) return '—'
  return `$${Number(v).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`
}

export default function StressScenarioPanel({ allocations, usdcWeight, portfolioValue = 10000 }) {
  const [results, setResults] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const hasAllocations = allocations && allocations.length > 0
  const usdc = usdcWeight != null ? usdcWeight : 0
  // Stringified for stable dep comparison — arrays from the parent are a
  // new identity on every render even when the contents are unchanged.
  const allocationsKey = JSON.stringify(allocations)

  useEffect(() => {
    if (!hasAllocations) return undefined
    let cancelled = false
    async function run() {
      setLoading(true)
      setError('')
      try {
        const res = await fetch(`${API_BASE}/api/strategies/stress/run`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            allocations,
            scenario: 'all',
            usdc_weight: usdc,
          }),
        })
        if (!res.ok) throw new Error(await res.text())
        const data = await res.json()
        if (!cancelled) setResults(data.results || [])
      } catch (e) {
        if (!cancelled) setError(e.message || 'Failed to run stress test')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    run()
    return () => { cancelled = true }
    // allocations intentionally excluded — covered by `allocationsKey` (stable identity).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allocationsKey, usdc, hasAllocations])

  return (
    <div className="card p-5">
      <div className="flex items-center justify-between flex-wrap gap-2 mb-3">
        <div>
          <div className="label">Stress scenarios</div>
          <p className="caption mt-1 leading-relaxed">
            Six historical shocks applied to your allocation per
            <code style={{ marginLeft: 4 }}>stress_engine.py</code>. Each scenario
            shocks asset classes by historically-calibrated amounts;
            portfolio P&amp;L is the weighted sum.
          </p>
        </div>
      </div>

      {!hasAllocations && (
        <div className="info-box" style={{ fontSize: '0.85rem' }}>
          Deploy a vault to see how its allocation survives historical shocks.
        </div>
      )}

      {loading && <div className="caption">Computing scenarios…</div>}
      {error && <div className="info-box warning">{error}</div>}

      {hasAllocations && results && results.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-[var(--glass-border)]">
          <table className="lib-table" style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
            <thead>
              <tr style={{ background: 'rgba(255,255,255,0.03)', textAlign: 'left', borderBottom: '1px solid var(--glass-border)' }}>
                <th style={{ padding: '10px 14px' }}>Scenario</th>
                <th style={{ padding: '10px 14px', textAlign: 'right' }}>P&amp;L %</th>
                <th style={{ padding: '10px 14px', textAlign: 'right' }}>Value after</th>
                <th style={{ padding: '10px 14px' }}>Description</th>
              </tr>
            </thead>
            <tbody>
              {results.map(r => {
                const pnl = r.portfolio_pnl
                const negativeClass = pnl < 0 ? 'negative' : pnl > 0 ? 'positive' : ''
                return (
                  <tr key={r.scenario} style={{ borderBottom: '1px solid var(--glass-border)' }}>
                    <td style={{ padding: '8px 14px', fontWeight: 600 }}>{r.label}</td>
                    <td className={`mono ${negativeClass}`} style={{ padding: '8px 14px', textAlign: 'right' }}>
                      {fmtPct(pnl)}
                    </td>
                    <td className="mono" style={{ padding: '8px 14px', textAlign: 'right' }}>
                      {fmtUsd(portfolioValue * (1 + (pnl || 0)))}
                    </td>
                    <td className="caption" style={{ padding: '8px 14px', maxWidth: 320 }}>
                      {r.description}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      <p className="caption mt-3 text-[var(--text-4)] leading-relaxed">
        Beta-1 per-asset-class shock model — coarse on purpose so the assumptions
        are inspectable. Factor models + Monte Carlo tail-risk are a v2 problem.
      </p>
    </div>
  )
}
