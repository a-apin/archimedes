import { useState, useEffect, useMemo } from 'react'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

async function apiGet(path) {
  const res = await fetch(`${API_BASE}${path}`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

// ─── Kelly Criterion Calculator ──────────────────────────────

function KellyCalculator() {
  const [winRate, setWinRate] = useState(0.55)
  const [avgWin, setAvgWin] = useState(0.08)
  const [avgLoss, setAvgLoss] = useState(0.04)
  const [riskFreeRate, setRiskFreeRate] = useState(0.05)

  const kelly = winRate > 0 && avgWin > 0 && avgLoss > 0
    ? (winRate / avgLoss) - ((1 - winRate) / avgWin)
    : 0
  const halfKelly = kelly / 2
  const ev = winRate * avgWin - (1 - winRate) * avgLoss

  return (
    <div className="card" style={{ padding: 20 }}>
      <h3 style={{ marginBottom: 12 }}>Kelly Criterion Calculator</h3>
      <p className="hint" style={{ marginBottom: 16 }}>
        Position sizing based on the Kelly Criterion (Kelly 1956). Optimal fraction
        of capital to allocate given win rate and payoff ratio.
      </p>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
        <div className="form-group">
          <label className="label">Win Rate</label>
          <input
            className="chat-input" type="number" min="0" max="1" step="0.01"
            value={winRate} onChange={e => setWinRate(parseFloat(e.target.value) || 0)}
          />
          <div className="caption">{(winRate * 100).toFixed(0)}%</div>
        </div>
        <div className="form-group">
          <label className="label">Avg Win</label>
          <input
            className="chat-input" type="number" min="0" step="0.01"
            value={avgWin} onChange={e => setAvgWin(parseFloat(e.target.value) || 0)}
          />
          <div className="caption">{(avgWin * 100).toFixed(1)}%</div>
        </div>
        <div className="form-group">
          <label className="label">Avg Loss</label>
          <input
            className="chat-input" type="number" min="0" step="0.01"
            value={avgLoss} onChange={e => setAvgLoss(parseFloat(e.target.value) || 0)}
          />
          <div className="caption">{(avgLoss * 100).toFixed(1)}%</div>
        </div>
        <div className="form-group">
          <label className="label">Risk-free Rate</label>
          <input
            className="chat-input" type="number" min="0" step="0.01"
            value={riskFreeRate} onChange={e => setRiskFreeRate(parseFloat(e.target.value) || 0)}
          />
          <div className="caption">{(riskFreeRate * 100).toFixed(1)}%</div>
        </div>
      </div>

      <div className="divider" style={{ margin: '20px 0' }} />

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16 }}>
        <div className="card-flat" style={{ padding: 16, textAlign: 'center' }}>
          <div className="caption">Full Kelly</div>
          <div style={{ fontSize: '1.6rem', fontWeight: 700, color: kelly > 0 ? 'var(--positive)' : 'var(--negative)' }}>
            {(kelly * 100).toFixed(1)}%
          </div>
          <div className="caption">Optimal allocation</div>
        </div>
        <div className="card-flat" style={{ padding: 16, textAlign: 'center' }}>
          <div className="caption">Half Kelly</div>
          <div style={{ fontSize: '1.6rem', fontWeight: 700, color: 'var(--accent)' }}>
            {(halfKelly * 100).toFixed(1)}%
          </div>
          <div className="caption">Conservative</div>
        </div>
        <div className="card-flat" style={{ padding: 16, textAlign: 'center' }}>
          <div className="caption">Expected Value</div>
          <div style={{ fontSize: '1.6rem', fontWeight: 700, color: ev > 0 ? 'var(--positive)' : 'var(--negative)' }}>
            {(ev * 100).toFixed(2)}%
          </div>
          <div className="caption">Per trade</div>
        </div>
      </div>
    </div>
  )
}

// ─── Strategy Risk Comparison ────────────────────────────────

function StrategyRiskTable({ strategies }) {
  if (!strategies.length) return null

  return (
    <div>
      <div className="label mb-3">Strategy Risk Comparison</div>
      <div className="table-container">
        <table>
          <thead>
            <tr>
              <th>Strategy</th>
              <th>Status</th>
              <th className="text-right">Sharpe</th>
              <th className="text-right">Max DD</th>
              <th className="text-right">CAGR</th>
              <th className="text-right">Win Rate</th>
              <th className="text-right">Calmar</th>
              <th className="text-right">Corr SPY</th>
              <th>Risk Level</th>
            </tr>
          </thead>
          <tbody>
            {strategies.map(s => {
              const sharpe = s.sharpe_ratio ?? 0
              const maxDD = s.max_drawdown ?? 0
              const riskLevel = sharpe > 1 ? 'Low' : sharpe > 0.5 ? 'Medium' : 'High'
              const riskColor = riskLevel === 'Low' ? 'var(--positive)' : riskLevel === 'Medium' ? 'var(--accent)' : 'var(--negative)'
              return (
                <tr key={s.id}>
                  <td style={{ fontWeight: 500, maxWidth: 200 }}>{s.paper_title?.slice(0, 35)}…</td>
                  <td><span className={`tag ${s.status === 'live' ? 'tag-positive' : 'tag-muted'}`}>{s.status}</span></td>
                  <td className="text-right mono">{s.sharpe_ratio?.toFixed(2) ?? '—'}</td>
                  <td className="text-right mono negative">{s.max_drawdown ? `−${(s.max_drawdown * 100).toFixed(1)}%` : '—'}</td>
                  <td className="text-right mono positive">{s.cagr ? `${(s.cagr * 100).toFixed(1)}%` : '—'}</td>
                  <td className="text-right mono">{s.win_rate ? `${(s.win_rate * 100).toFixed(0)}%` : '—'}</td>
                  <td className="text-right mono">{s.calmar_ratio?.toFixed(2) ?? '—'}</td>
                  <td className="text-right mono">{s.correlation_to_spy?.toFixed(2) ?? '—'}</td>
                  <td><span className="tag" style={{ color: riskColor, background: `${riskColor}15` }}>{riskLevel}</span></td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ─── Main Export ─────────────────────────────────────────────

export default function RiskAnalysis() {
  const [strategies, setStrategies] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    apiGet('/api/strategies/')
      .then(data => setStrategies(data.strategies || []))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  return (
    <div>
      <div className="fade-up fade-up-1" style={{ maxWidth: 640, marginBottom: 28 }}>
        <h2 style={{ fontFamily: 'var(--serif)', fontSize: '2rem', marginBottom: 10 }}>Risk Analysis</h2>
        <p className="body">
          Kelly Criterion position sizing, strategy risk comparison, and portfolio-level risk metrics.
          Grounded in quantitative finance research — not vibes.
        </p>
      </div>

      <div className="trade-grid fade-up fade-up-2">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
          <KellyCalculator />

          {/* Portfolio Risk Summary */}
          <div className="card" style={{ padding: 20 }}>
            <h3 style={{ marginBottom: 12 }}>Portfolio Risk Summary</h3>
            {loading ? (
              <div className="caption">Loading…</div>
            ) : error ? (
              <div className="info-box warning">Error: {error}</div>
            ) : strategies.length === 0 ? (
              <div className="caption">No strategies loaded.</div>
            ) : (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
                <div className="card-flat" style={{ padding: 12 }}>
                  <div className="caption">Avg Sharpe</div>
                  <div style={{ fontSize: '1.4rem', fontWeight: 700 }}>
                    {(strategies.reduce((s, st) => s + (st.sharpe_ratio ?? 0), 0) / strategies.length).toFixed(2)}
                  </div>
                </div>
                <div className="card-flat" style={{ padding: 12 }}>
                  <div className="caption">Worst Max DD</div>
                  <div className="negative" style={{ fontSize: '1.4rem', fontWeight: 700 }}>
                    −{Math.max(...strategies.map(s => s.max_drawdown ?? 0)).toFixed(1)}%
                  </div>
                </div>
                <div className="card-flat" style={{ padding: 12 }}>
                  <div className="caption">Best Calmar</div>
                  <div className="positive" style={{ fontSize: '1.4rem', fontWeight: 700 }}>
                    {Math.max(...strategies.map(s => s.calmar_ratio ?? 0)).toFixed(2)}
                  </div>
                </div>
                <div className="card-flat" style={{ padding: 12 }}>
                  <div className="caption">Avg Correlation to SPY</div>
                  <div style={{ fontSize: '1.4rem', fontWeight: 700 }}>
                    {(strategies.reduce((s, st) => s + (st.correlation_to_spy ?? 0), 0) / strategies.length).toFixed(2)}
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>

        <div>
          {loading ? (
            <div className="caption">Loading strategy risk data…</div>
          ) : (
            <StrategyRiskTable strategies={strategies} />
          )}
        </div>
      </div>
    </div>
  )
}
