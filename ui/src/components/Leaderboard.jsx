import { useState, useEffect, useCallback } from 'react'
import { apiGet } from '../api'

// The public, gamified strategy leaderboard (North Star §5 — the testnet
// engagement engine). Ranks the library by the backend's transparent conviction
// score (real rigor gate + backtest), and pairs an honest, pending StockBench /
// live-P&L "forward axis". Nothing here is fabricated: validation metrics are
// real passport fields; the forward axis renders as "pending" until that data
// flows. Public — no wallet required.

const SORT_OPTIONS = [
  { id: 'conviction_score', label: 'Conviction' },
  { id: 'deflated_sharpe_ratio', label: 'Deflated Sharpe' },
  { id: 'dsr_p_value', label: 'DSR confidence' },
  { id: 'sharpe_ratio', label: 'Sharpe' },
  { id: 'cagr', label: 'CAGR' },
  { id: 'pbo_score', label: 'Overfitting (PBO)' },
]

const REGIMES = [
  { id: '', label: 'All regimes' },
  { id: 'bull', label: 'Bull' },
  { id: 'bear', label: 'Bear' },
  { id: 'regime_neutral', label: 'Neutral' },
]

const MEDAL = { gold: '🥇', silver: '🥈', bronze: '🥉' }

function fmt(v, d = 2) {
  return v != null ? Number(v).toFixed(d) : '—'
}
function fmtPct(v, d = 1) {
  return v != null ? `${(v * 100).toFixed(d)}%` : '—'
}

function rigorBadge(entry) {
  if (entry.is_backtest_placeholder) {
    return <span className="tag-muted" title="No real backtest yet">No backtest</span>
  }
  if (entry.passes_rigor_gate) {
    return <span className="tag-positive" title="Passes the selection-bias rigor gate (DSR / PBO / OOS)">✓ Rigor gate</span>
  }
  return <span className="tag-warning" title="Did not pass the rigor gate — surfaced honestly">Gate failed</span>
}

// Compact stacked bar of the four real score components, each scaled by its weight.
function ScoreBar({ components, weights }) {
  if (!components || !weights) return null
  const parts = [
    { key: 'gate', color: 'var(--accent)', label: 'Rigor gate' },
    { key: 'dsr_confidence', color: '#4f9be0', label: 'DSR confidence' },
    { key: 'oos_performance', color: '#5fc08a', label: 'Out-of-sample' },
    { key: 'overfitting_resistance', color: '#b07fd0', label: 'Overfit-resistant' },
  ]
  return (
    <div style={{ display: 'flex', height: 6, borderRadius: 3, overflow: 'hidden', background: 'var(--surface-3)', width: 120 }}>
      {parts.map(p => {
        const w = (weights[p.key] ?? 0) * (components[p.key] ?? 0) * 100
        return <div key={p.key} title={`${p.label}: ${((components[p.key] ?? 0) * 100).toFixed(0)}% × weight ${weights[p.key]}`} style={{ width: `${w}%`, background: p.color }} />
      })}
    </div>
  )
}

export default function Leaderboard() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [sortBy, setSortBy] = useState('conviction_score')
  const [order, setOrder] = useState('desc')
  const [minRigor, setMinRigor] = useState(false)
  const [regime, setRegime] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    const params = new URLSearchParams({ sort_by: sortBy, order, limit: '100' })
    if (minRigor) params.set('min_rigor', 'true')
    if (regime) params.set('regime_tag', regime)
    apiGet(`/api/leaderboard?${params.toString()}`)
      .then(d => { setData(d); setError(null) })
      .catch(e => setError(e.message || 'Failed to load leaderboard'))
      .finally(() => setLoading(false))
  }, [sortBy, order, minRigor, regime])

  useEffect(() => { load() }, [load])

  const engine = data?.scoring_engine
  const sb = engine?.stockbench_global

  return (
    <div className="leaderboard-page" style={{ maxWidth: 1100 }}>
      <div style={{ marginBottom: 18 }}>
        <h2 className="serif" style={{ fontSize: '2rem', marginBottom: 8 }}>Strategy Leaderboard</h2>
        <p className="body" style={{ maxWidth: 760 }}>
          Every library strategy, ranked by a transparent <strong>conviction score</strong> built from
          real rigor-gate and backtest results — the ugly numbers included. Build your track record now;
          it carries to mainnet.
        </p>
        {engine?.disclaimer && (
          <div style={{ marginTop: 10, padding: '8px 12px', borderLeft: '3px solid var(--accent)', background: 'var(--accent-muted)', borderRadius: 4, fontSize: 13, color: 'var(--text-2)' }}>
            <strong style={{ color: 'var(--accent)' }}>Testnet — paper/simulated.</strong> {engine.disclaimer}
          </div>
        )}
      </div>

      {/* Scoring engine: weights + methodology + the one real StockBench datum, as honest context */}
      {engine && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, marginBottom: 18, padding: 14, background: 'var(--surface-2)', borderRadius: 8, border: '1px solid var(--glass-border)' }}>
          <div style={{ flex: '1 1 320px' }}>
            <div style={{ fontSize: 12, textTransform: 'uppercase', letterSpacing: 0.5, color: 'var(--text-3)', marginBottom: 6 }}>Scoring engine · validation axis (live)</div>
            <div style={{ fontSize: 13, color: 'var(--text-2)' }}>{engine.methodology}</div>
          </div>
          <div style={{ flex: '1 1 260px' }}>
            <div style={{ fontSize: 12, textTransform: 'uppercase', letterSpacing: 0.5, color: 'var(--text-3)', marginBottom: 6 }}>Forward axis (pending)</div>
            <div style={{ fontSize: 13, color: 'var(--text-2)' }}>
              Per-strategy <strong>StockBench</strong> + <strong>live paper-P&L</strong> pair into this engine next.
              StockBench today is a single whole-pipeline run (honest, not per-strategy):{' '}
              {sb && <span title={`${sb.window} · ${sb.source}`}>Sortino {fmt(sb.sortino)}, return {sb.return_pct}%, rank {sb.rank}</span>}.
            </div>
          </div>
        </div>
      )}

      {/* Controls */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', marginBottom: 14 }}>
        <span style={{ fontSize: 12, color: 'var(--text-3)' }}>Sort</span>
        {SORT_OPTIONS.map(o => (
          <button key={o.id} type="button" onClick={() => setSortBy(o.id)}
            className={sortBy === o.id ? 'tag-accent' : 'tag-muted'}
            style={{ cursor: 'pointer', border: 'none', padding: '4px 10px', borderRadius: 14, fontSize: 12 }}>
            {o.label}
          </button>
        ))}
        <button type="button" onClick={() => setOrder(o => o === 'desc' ? 'asc' : 'desc')}
          className="tag-muted" style={{ cursor: 'pointer', border: 'none', padding: '4px 10px', borderRadius: 14, fontSize: 12 }}
          title="Toggle sort direction">
          {order === 'desc' ? '↓ desc' : '↑ asc'}
        </button>
        <span style={{ width: 1, height: 18, background: 'var(--glass-border)', margin: '0 4px' }} />
        <label style={{ fontSize: 12, color: 'var(--text-2)', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4 }}>
          <input type="checkbox" checked={minRigor} onChange={e => setMinRigor(e.target.checked)} /> Rigor-gated only
        </label>
        <select value={regime} onChange={e => setRegime(e.target.value)}
          style={{ background: 'var(--surface-3)', color: 'var(--text-2)', border: '1px solid var(--glass-border)', borderRadius: 6, padding: '4px 8px', fontSize: 12 }}>
          {REGIMES.map(r => <option key={r.id} value={r.id}>{r.label}</option>)}
        </select>
        {data && <span style={{ fontSize: 12, color: 'var(--text-3)', marginLeft: 'auto' }}>{data.total} strateg{data.total === 1 ? 'y' : 'ies'}</span>}
      </div>

      {loading && <div className="body" style={{ color: 'var(--text-3)' }}>Loading the board…</div>}
      {error && <div className="tag-warning" style={{ display: 'inline-block', padding: '6px 10px' }}>Couldn’t load the leaderboard: {error}</div>}

      {!loading && !error && data && data.entries.length === 0 && (
        <div className="body" style={{ color: 'var(--text-3)', padding: 20, textAlign: 'center', border: '1px dashed var(--glass-border)', borderRadius: 8 }}>
          No strategies match these filters yet.
        </div>
      )}

      {!loading && !error && data && data.entries.length > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ textAlign: 'left', color: 'var(--text-3)', fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.5 }}>
                <th style={{ padding: '8px 10px' }}>#</th>
                <th style={{ padding: '8px 10px' }}>Strategy</th>
                <th style={{ padding: '8px 10px' }}>Conviction</th>
                <th style={{ padding: '8px 10px' }}>Sharpe</th>
                <th style={{ padding: '8px 10px' }}>CAGR</th>
                <th style={{ padding: '8px 10px' }}>Max DD</th>
                <th style={{ padding: '8px 10px' }}>Rigor</th>
                <th style={{ padding: '8px 10px' }}>Forward</th>
              </tr>
            </thead>
            <tbody>
              {data.entries.map(e => (
                <tr key={e.id} style={{ borderTop: '1px solid var(--glass-border)' }}>
                  <td style={{ padding: '10px', whiteSpace: 'nowrap', fontWeight: 600 }}>
                    {e.medal ? <span style={{ marginRight: 4 }}>{MEDAL[e.medal]}</span> : null}{e.rank}
                  </td>
                  <td style={{ padding: '10px', maxWidth: 280 }}>
                    <div style={{ color: 'var(--text-1)', fontWeight: 500 }}>{e.name}</div>
                    <div style={{ fontSize: 11, color: 'var(--text-3)' }}>
                      {e.creator === 'Archimedes' ? 'Archimedes (curated)' : `by ${e.creator.slice(0, 6)}…${e.creator.slice(-4)}`}
                      {e.regime_tag && e.regime_tag !== 'regime_neutral' ? ` · ${e.regime_tag}` : ''}
                    </div>
                  </td>
                  <td style={{ padding: '10px', whiteSpace: 'nowrap' }}>
                    <div style={{ fontWeight: 600, color: 'var(--accent)' }}>{fmt(e.conviction_score, 1)}</div>
                    <ScoreBar components={e.score_components} weights={engine?.weights} />
                  </td>
                  <td style={{ padding: '10px', whiteSpace: 'nowrap' }}>{fmt(e.sharpe_ratio)}</td>
                  <td style={{ padding: '10px', whiteSpace: 'nowrap' }}>{fmtPct(e.cagr)}</td>
                  <td style={{ padding: '10px', whiteSpace: 'nowrap', color: 'var(--negative)' }}>{fmtPct(e.max_drawdown)}</td>
                  <td style={{ padding: '10px', whiteSpace: 'nowrap' }}>
                    {rigorBadge(e)}
                    {e.dsr_p_value != null && <div style={{ fontSize: 11, color: 'var(--text-3)', marginTop: 2 }} title="DSR confidence (0–1, higher is better): probability the Sharpe survives deflation/multiple-testing. Not a classical p-value.">DSR conf={fmt(e.dsr_p_value)}{e.pbo_score != null ? ` · PBO ${fmt(e.pbo_score)}` : ''}</div>}
                  </td>
                  <td style={{ padding: '10px', whiteSpace: 'nowrap' }}>
                    <span className="tag-muted" title="Per-strategy StockBench eval is pending">SB pending</span>{' '}
                    <span className="tag-muted" title="Live paper-P&L tracking is pending (testnet — paper)">P&L pending</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
