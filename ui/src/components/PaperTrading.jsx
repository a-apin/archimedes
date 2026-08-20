import { useCallback, useEffect, useState } from 'react'
import { apiGet, apiPost } from '../api'

// /app/paper — the act-on step of the MVP spine (generate → verdict → paper).
// Lists the signed-in account's paper deployments from GET /api/paper/deployments
// (deployment_summary shape: deployment_id, strategy_id, deployed_at, status,
// days, total_return, drift_detected_at, series[{date, daily_return,
// equity_index}]). Deployments are SIMULATED — account-owned, no wallet, no
// funds — and free by design (Dan's call: paper stays free even after the
// generation paywall flips). Strategy display names come from a client-side
// join against the library lists; the paper API deliberately returns ids only.

function nameOf(row) {
  return row?.strategy_name || row?.name || row?.paper_title || null
}

function pct(x) {
  if (x == null || Number.isNaN(x)) return '—'
  return `${x >= 0 ? '+' : ''}${(x * 100).toFixed(2)}%`
}

function StatusChip({ status, driftAt }) {
  const active = status === 'active'
  return (
    <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
      <span
        style={{
          padding: '2px 10px',
          borderRadius: 999,
          fontSize: 12,
          fontFamily: 'var(--mono, monospace)',
          background: active ? 'var(--accent-muted)' : 'var(--surface-3)',
          color: active ? 'var(--accent)' : 'var(--text-3)',
          border: `1px solid ${active ? 'var(--accent)' : 'var(--border, var(--surface-3))'}`,
        }}
      >
        {active ? 'ACTIVE' : 'STOPPED'}
      </span>
      {driftAt && (
        <span
          title={`A fresh replay disagreed with the recorded ledger on ${driftAt}. The track record is frozen pending investigation — honest ledgers do not silently rewrite.`}
          style={{
            padding: '2px 10px',
            borderRadius: 999,
            fontSize: 12,
            fontFamily: 'var(--mono, monospace)',
            background: 'var(--surface-3)',
            color: 'var(--warning, #b45309)',
            border: '1px solid var(--warning, #b45309)',
          }}
        >
          DRIFT
        </span>
      )}
    </span>
  )
}

// Minimal equity sparkline over series[].equity_index. Starts the path at the
// 1.0 baseline so day-1 deployments still draw a meaningful segment.
function Sparkline({ series }) {
  if (!series || series.length === 0) return null
  const values = [1.0, ...series.map((p) => p.equity_index)]
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  const W = 220
  const H = 48
  const pts = values
    .map((v, i) => {
      const x = (i / (values.length - 1)) * (W - 4) + 2
      const y = H - 6 - ((v - min) / span) * (H - 12)
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
  const up = values[values.length - 1] >= values[0]
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} aria-hidden="true">
      <polyline
        points={pts}
        fill="none"
        stroke={up ? 'var(--accent)' : 'var(--danger, #b91c1c)'}
        strokeWidth="1.5"
      />
    </svg>
  )
}

export default function PaperTrading({ onNavigate }) {
  const [deployments, setDeployments] = useState(null)
  const [names, setNames] = useState({})
  const [error, setError] = useState('')
  const [stopping, setStopping] = useState(null)

  const load = useCallback(async () => {
    setError('')
    try {
      const res = await apiGet('/api/paper/deployments')
      setDeployments(res.deployments || [])
    } catch (e) {
      setError(e.message || 'Failed to load paper deployments')
      setDeployments([])
      return
    }
    // Name join is best-effort decoration — the list renders with ids if the
    // library calls fail, so these settle independently of the load above.
    const [seed, generated] = await Promise.allSettled([
      apiGet('/api/strategies/'),
      apiGet('/api/strategies/generated'),
    ])
    const map = {}
    for (const res of [seed, generated]) {
      if (res.status !== 'fulfilled') continue
      for (const row of res.value.strategies || []) {
        const id = row.id ?? row.strategy_id
        const label = nameOf(row)
        if (id && label) map[id] = label
      }
    }
    setNames(map)
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const stop = async (dep) => {
    const label = names[dep.strategy_id] || dep.strategy_id
    if (!window.confirm(`Stop paper trading "${label}"? The track record freezes where it is; this cannot be restarted in place.`)) return
    setStopping(dep.deployment_id)
    try {
      await apiPost(`/api/paper/deployments/${encodeURIComponent(dep.deployment_id)}/stop`, {})
      await load()
    } catch (e) {
      setError(e.message || 'Failed to stop deployment')
    } finally {
      setStopping(null)
    }
  }

  return (
    <div style={{ maxWidth: 1100 }}>
      <div style={{ marginBottom: 18 }}>
        <h2 className="serif" style={{ fontSize: '2rem', marginBottom: 8 }}>
          Paper Trading
        </h2>
        <p className="body" style={{ maxWidth: 760 }}>
          Simulated deployments of your strategies — <strong>no funds move</strong>. Each one
          snapshots the strategy spec at deploy time and appends one real-data return per trading
          day; later regeneration of the strategy never rewrites a running ledger. This is the
          track record that carries to mainnet.
        </p>
      </div>

      {error && (
        <div role="alert" className="card" style={{ padding: 14, marginBottom: 14, color: 'var(--danger, #b91c1c)' }}>
          {error}
        </div>
      )}

      {deployments === null && <p className="caption">Loading deployments…</p>}

      {deployments !== null && deployments.length === 0 && !error && (
        <div className="card" style={{ padding: 24, textAlign: 'center' }}>
          <p className="body" style={{ marginBottom: 12 }}>
            No paper deployments yet. Open a strategy in your Library and choose{' '}
            <strong>Start paper trading</strong> on its passport.
          </p>
          <button className="btn-primary" onClick={() => onNavigate('library')}>
            Open Library →
          </button>
        </div>
      )}

      {deployments !== null && deployments.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {deployments.map((dep) => (
            <div
              key={dep.deployment_id}
              className="card"
              style={{ padding: 16, display: 'flex', flexWrap: 'wrap', gap: 16, alignItems: 'center' }}
            >
              <div style={{ flex: '1 1 240px', minWidth: 0 }}>
                <button
                  className="btn-link"
                  style={{
                    fontWeight: 600,
                    fontSize: '1.05rem',
                    background: 'none',
                    border: 'none',
                    padding: 0,
                    cursor: 'pointer',
                    color: 'var(--text-1)',
                    textAlign: 'left',
                  }}
                  title="Open the strategy passport"
                  onClick={() => onNavigate('strategy', { strategyId: dep.strategy_id })}
                >
                  {names[dep.strategy_id] || dep.strategy_id}
                </button>
                <div className="caption" style={{ marginTop: 4 }}>
                  deployed {dep.deployed_at} ·{' '}
                  {dep.days === 0
                    ? 'day 0 — first return lands with the next daily advance'
                    : `${dep.days} trading day${dep.days === 1 ? '' : 's'}`}
                </div>
                <div style={{ marginTop: 8 }}>
                  <StatusChip status={dep.status} driftAt={dep.drift_detected_at} />
                </div>
              </div>

              <div style={{ flex: '0 0 auto' }}>
                <Sparkline series={dep.series} />
              </div>

              <div style={{ flex: '0 0 auto', textAlign: 'right', minWidth: 110 }}>
                <div
                  style={{
                    fontFamily: 'var(--mono, monospace)',
                    fontSize: '1.3rem',
                    fontVariantNumeric: 'tabular-nums',
                    color:
                      dep.total_return > 0
                        ? 'var(--accent)'
                        : dep.total_return < 0
                          ? 'var(--danger, #b91c1c)'
                          : 'var(--text-2)',
                  }}
                >
                  {pct(dep.total_return)}
                </div>
                <div className="caption">total return</div>
                {dep.status === 'active' && (
                  <button
                    className="btn btn-outline btn-sm"
                    style={{ marginTop: 8 }}
                    disabled={stopping === dep.deployment_id}
                    onClick={() => stop(dep)}
                  >
                    {stopping === dep.deployment_id ? 'Stopping…' : 'Stop'}
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
