import { apiGet } from '../api'
import { useState, useEffect } from 'react'
import { regimeMeta } from '../regime'



const RISK_PROFILES = [
  { id: 'fixed_income', label: 'Fixed Income' },
  { id: 'conservative', label: 'Conservative' },
  { id: 'moderate', label: 'Moderate' },
  { id: 'aggressive', label: 'Aggressive' },
  { id: 'hyper_risky', label: 'Hyper-Risky' },
]

function fmtPct(v) {
  // Guard against Infinity / NaN leaking from upstream stats (otherwise
  // toFixed renders the literal strings "Infinity" / "NaN" on the page).
  return (v != null && Number.isFinite(v)) ? `${(v * 100).toFixed(1)}%` : '—'
}

function fmt(v, d = 2) {
  return (v != null && Number.isFinite(v)) ? v.toFixed(d) : '—'
}

function AllocationBar({ label, weight, isUsdc, kelly, rigorPassed, isCandidate }) {
  const pct = (weight * 100).toFixed(1)
  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 5 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ fontWeight: 600, fontSize: '0.88rem' }}>{label}</span>
          {isUsdc && (
            <span className="caption" style={{ color: 'var(--text-4)', fontSize: '0.72rem' }}>safety floor</span>
          )}
          {!isUsdc && kelly != null && (
            <span className="caption" style={{ color: 'var(--text-3)', fontSize: '0.72rem' }}>
              Kelly f*={fmtPct(kelly)}
            </span>
          )}
          {!isUsdc && rigorPassed && (
            <span className="inline-flex items-center gap-1 text-[0.68rem] font-semibold px-1.5 py-px rounded" style={{
              background: 'rgba(16,185,129,0.12)', color: 'var(--positive)',
              border: '1px solid rgba(16,185,129,0.3)',
            }}>
              Rigor Gate <span className="i-lucide-check w-3 h-3" />
            </span>
          )}
          {!isUsdc && !rigorPassed && isCandidate !== undefined && (
            <span style={{
              fontSize: '0.68rem', fontWeight: 600, padding: '1px 6px', borderRadius: 3,
              background: 'var(--glass)', color: 'var(--text-4)',
              border: '1px solid var(--glass-border)',
            }}>
              Candidate
            </span>
          )}
        </div>
        <span className="mono" style={{ fontWeight: 700, fontSize: '0.9rem' }}>{pct}%</span>
      </div>
      <div style={{ background: 'var(--glass-hover)', borderRadius: 4, height: 10, overflow: 'hidden' }}>
        <div style={{
          width: `${pct}%`,
          height: '100%',
          background: isUsdc ? 'var(--text-4)' : 'var(--accent)',
          transition: 'width 0.4s ease',
        }} />
      </div>
    </div>
  )
}

export default function PortfolioAdvisor({ initialRiskProfile = 'moderate' } = {}) {
  // initialRiskProfile lets a parent page (e.g. /generate's preview banner)
  // pre-select the profile from the brief the user already typed, so the
  // preview reflects "what would the agent allocate me for the brief I
  // submitted" without forcing the user to re-pick. The user can still
  // switch profiles in the existing selector below.
  const [selectedProfile, setSelectedProfile] = useState(initialRiskProfile)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    setLoading(true)
    setError('')
    setData(null)
    apiGet(`/api/strategies/advisor?risk_profile=${selectedProfile}`)
      .then(d => {
        // Backend may return 200 with an `error` body (e.g. when no
        // strategies are available) — surface that as a user-facing error
        // rather than rendering a half-empty card.
        if (d && d.error) {
          setError(d.error)
        } else {
          setData(d)
        }
      })
      .catch(e => setError(e.message || 'Failed to load advisor'))
      .finally(() => setLoading(false))
  }, [selectedProfile])

  const regime = data?.regime ?? 'unknown'
  const regimeInfo = regimeMeta(regime)
  const regimeLabel = regimeInfo.label
  const rColor = regimeInfo.color

  return (
    <div>
      {/* Header */}
      <div style={{ maxWidth: 640, marginBottom: 28 }}>
        <h2 className="serif" style={{ fontSize: '2rem', marginBottom: 10 }}>Portfolio Advisor</h2>
        <p className="body">
          Kelly Criterion + risk-parity allocation recommendations based on the active strategy
          library and current market regime. Use this before deploying a vault.
        </p>
      </div>

      {/* Risk Profile selector */}
      <div className="card-elevated mb-6" style={{ padding: 24 }}>
        <div className="label mb-3">Risk Profile</div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {RISK_PROFILES.map(rp => (
            <button
              key={rp.id}
              onClick={() => setSelectedProfile(rp.id)}
              style={{
                padding: '6px 16px', borderRadius: 6, border: 'none', cursor: 'pointer',
                fontWeight: selectedProfile === rp.id ? 700 : 400,
                fontSize: '0.82rem',
                background: selectedProfile === rp.id ? 'var(--accent)' : 'var(--glass-hover)',
                color: selectedProfile === rp.id ? '#000' : 'var(--text-2)',
                transition: 'background 0.15s',
              }}
            >
              {rp.label}
            </button>
          ))}
        </div>
      </div>

      {loading && <div className="caption">Computing allocation…</div>}
      {error && (
        <div className="info-box warning">
          {error.includes('No strategies') ? (
            <>No strategies with real backtest data are available yet. Run the analytics engine to generate backtest results.</>
          ) : (
            <>Advisor unavailable: {error}</>
          )}
        </div>
      )}

      {data && !error && (
        <>
          {/* Regime banner */}
          <div className="card-flat" style={{ padding: 20, marginBottom: 20 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 8 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ width: 10, height: 10, borderRadius: '50%', background: rColor, display: 'inline-block' }} />
                <span style={{ fontWeight: 700, fontSize: '1rem', color: rColor, textTransform: 'uppercase' }}>
                  {regimeLabel}
                </span>
              </div>
              <span className="caption">confidence {fmtPct(data.regime_confidence)}</span>
              <span className="caption" style={{ color: 'var(--text-4)' }}>· {regimeInfo.exposure}</span>
            </div>
            <p className="body" style={{ margin: 0, color: 'var(--text-3)', lineHeight: 1.5 }}>
              {data.regime_narrative || regimeInfo.definition}
            </p>
          </div>

          {/* Allocation breakdown */}
          <div className="card-elevated" style={{ padding: 24, marginBottom: 20 }}>
            <div className="label mb-4">Recommended Allocation</div>

            {/* USDC floor */}
            <AllocationBar
              label="USDC"
              weight={data.usdc_weight ?? 0}
              isUsdc
            />

            {/* Synth allocations */}
            {(data.allocations || []).map((a) => (
              <AllocationBar
                key={a.id}
                label={a.symbol || a.title?.slice(0, 30) || a.id}
                weight={a.weight}
                kelly={a.kelly_fraction}
                rigorPassed={a.passes_rigor_gate}
                isCandidate={!a.passes_rigor_gate}
              />
            ))}

            {data.allocations?.length === 0 && (
              <div className="caption" style={{ color: 'var(--text-4)' }}>
                No strategy allocations — all synth budget held as USDC under current regime conditions.
              </div>
            )}
          </div>

          {/* Expected portfolio metrics */}
          {data.expected_portfolio && (
            <div className="card-flat" style={{ padding: 20, marginBottom: 20 }}>
              <div className="label mb-3">Expected Portfolio Metrics</div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16 }}>
                <div>
                  <div className="caption">Sharpe Ratio</div>
                  <div style={{ fontWeight: 700, fontSize: '1.4rem' }}>
                    {fmt(data.expected_portfolio.sharpe)}
                  </div>
                </div>
                <div>
                  <div className="caption">CAGR</div>
                  <div className="positive" style={{ fontWeight: 700, fontSize: '1.4rem' }}>
                    {fmtPct(data.expected_portfolio.cagr)}
                  </div>
                </div>
                <div>
                  <div className="caption">Max Drawdown</div>
                  <div className="negative" style={{ fontWeight: 700, fontSize: '1.4rem' }}>
                    -{fmtPct(data.expected_portfolio.max_drawdown)}
                  </div>
                </div>
              </div>
              <p className="caption" style={{ marginTop: 12, color: 'var(--text-4)' }}>
                Weighted average of strategy metrics at current allocations.
                Actual results will vary — regime shifts and rebalancing will change weights over time.
              </p>
            </div>
          )}

          {/* Agent thesis (LLM-generated) */}
          {data.agent?.used && data.agent?.thesis && (
            <div className="card-flat" style={{ padding: 20, marginBottom: 20, borderLeft: '3px solid var(--accent)' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8, flexWrap: 'wrap', gap: 6 }}>
                <div className="label">Agent Thesis</div>
                <div className="caption" style={{ color: 'var(--text-4)' }}>
                  {data.agent.model_id}{data.agent.iterations > 1 ? ` · ${data.agent.iterations} tool-use turns` : ''} · {data.agent.num_picks} picks
                </div>
              </div>
              <p className="body" style={{ margin: 0, lineHeight: 1.55 }}>{data.agent.thesis}</p>
              {data.agent.tool_calls?.length > 0 && (
                <details style={{ marginTop: 10 }}>
                  <summary className="caption" style={{ cursor: 'pointer' }}>
                    Agent investigation trace ({data.agent.tool_calls.length} tool calls)
                  </summary>
                  <div className="mono" style={{ fontSize: '0.72rem', marginTop: 6, color: 'var(--text-3)' }}>
                    {data.agent.tool_calls.map((tc, i) => (
                      <div key={i}>{i + 1}. {tc.output_summary}</div>
                    ))}
                  </div>
                </details>
              )}
            </div>
          )}

          {/* Rigor summary — make the wedge visible */}
          {data.rigor_summary && data.rigor_summary.total_picks > 0 && (
            <div className="card-flat" style={{ padding: 20, marginBottom: 20 }}>
              <div className="label mb-3">Selection-Bias Rigor (Tier-1 Gate)</div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
                <div>
                  <div className="caption">Rigor Gate Passed</div>
                  <div style={{ fontWeight: 700, fontSize: '1.2rem' }}>
                    {data.rigor_summary.passes_rigor_gate}/{data.rigor_summary.total_picks}
                  </div>
                </div>
                <div>
                  <div className="caption">DSR p&lt;{data.rigor_summary.dsr_significant_threshold}</div>
                  <div style={{ fontWeight: 700, fontSize: '1.2rem' }}>
                    {data.rigor_summary.dsr_significant}/{data.rigor_summary.total_picks}
                  </div>
                </div>
                <div>
                  <div className="caption">PBO &lt; {data.rigor_summary.pbo_acceptable_threshold}</div>
                  <div style={{ fontWeight: 700, fontSize: '1.2rem' }}>
                    {data.rigor_summary.pbo_acceptable}/{data.rigor_summary.total_picks}
                  </div>
                </div>
                <div>
                  <div className="caption">Walk-fwd OOS &gt; 0</div>
                  <div style={{ fontWeight: 700, fontSize: '1.2rem' }}>
                    {data.rigor_summary.oos_positive}/{data.rigor_summary.total_picks}
                  </div>
                </div>
              </div>
              <p className="caption" style={{ marginTop: 12, color: 'var(--text-4)', lineHeight: 1.5 }}>
                Deflated Sharpe (Bailey & López de Prado 2014) discounts multiple-testing inflation;
                PBO (Bailey et al. 2014) estimates backtest-overfitting probability; chronological OOS
                tests out-of-sample stability. Mean DSR p-value: {fmt(data.rigor_summary.avg_dsr_p_value, 3)},
                mean PBO: {fmt(data.rigor_summary.avg_pbo_score, 3)}.
              </p>
            </div>
          )}

          {/* Stress test matrix */}
          {data.stress_tests?.length > 0 && (
            <div className="card-flat" style={{ padding: 20, marginBottom: 20 }}>
              <div className="label mb-3">Stress Tests (instantaneous P&amp;L vs scenario)</div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 10 }}>
                {data.stress_tests.map(s => {
                  const pnl = s.portfolio_pnl
                  const coverage = s.coverage_pct ?? 1.0
                  const lowCoverage = coverage < 0.8
                  const color = !Number.isFinite(pnl) ? 'var(--text-4)'
                    : pnl < -0.10 ? 'var(--negative)'
                    : pnl < 0 ? 'var(--warning)'
                    : 'var(--positive)'
                  return (
                    <div key={s.scenario} style={{ padding: 12, background: 'var(--glass)', borderRadius: 6, borderLeft: `3px solid ${color}` }}>
                      <div style={{ fontWeight: 600, fontSize: '0.78rem', marginBottom: 4 }}>{s.label}</div>
                      <div className="mono" style={{ fontWeight: 700, fontSize: '1.3rem', color }}>
                        {Number.isFinite(pnl) ? `${(pnl * 100).toFixed(1)}%` : '—'}
                      </div>
                      {lowCoverage && (
                        <div className="inline-flex items-center gap-1 mt-0.5 text-[0.65rem] font-semibold text-[var(--warning)]">
                          <span className="i-lucide-alert-triangle w-3 h-3" />
                          only {(coverage * 100).toFixed(0)}% of book modeled
                        </div>
                      )}
                      <div className="caption" style={{ color: 'var(--text-4)', fontSize: '0.68rem', lineHeight: 1.3, marginTop: 4 }}>
                        {(s.description || '').slice(0, 80)}{(s.description || '').length > 80 ? '…' : ''}
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* Risk decomposition + correlation pairs */}
          {(data.risk_decomposition?.length > 0 || data.correlation_pairs?.length > 0) && (
            <div className="card-flat" style={{ padding: 20, marginBottom: 20, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 24 }}>
              {data.risk_decomposition?.length > 0 && (
                <div>
                  <div className="label mb-3">Variance Decomposition</div>
                  <table style={{ width: '100%', fontSize: '0.78rem' }}>
                    <thead>
                      <tr><th style={{ textAlign: 'left' }}>Asset</th><th className="text-right">Weight</th><th className="text-right">Var Contrib</th></tr>
                    </thead>
                    <tbody>
                      {data.risk_decomposition
                        .slice()
                        .sort((a, b) => b.variance_contribution - a.variance_contribution)
                        .slice(0, 8)
                        .map(r => (
                          <tr key={r.symbol}>
                            <td className="mono" style={{ fontSize: '0.72rem' }}>{r.symbol}</td>
                            <td className="text-right mono">{fmtPct(r.weight)}</td>
                            <td className="text-right mono">{fmtPct(r.variance_contribution)}</td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
              )}
              {data.correlation_pairs?.length > 0 && (
                <div>
                  <div className="label mb-3">Top Correlations (1y)</div>
                  <table style={{ width: '100%', fontSize: '0.78rem' }}>
                    <thead>
                      <tr><th style={{ textAlign: 'left' }}>Pair</th><th className="text-right">ρ</th></tr>
                    </thead>
                    <tbody>
                      {data.correlation_pairs.map((p, i) => (
                        <tr key={i}>
                          <td className="mono" style={{ fontSize: '0.72rem' }}>{p.a} ⟷ {p.b}</td>
                          <td className="text-right mono" style={{ color: Math.abs(p.corr) > 0.6 ? 'var(--warning)' : 'inherit' }}>
                            {p.corr > 0 ? '+' : ''}{p.corr.toFixed(2)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {/* Reasoning trace anchor */}
          {data.reasoning_trace?.trace_hash && (
            <div className="card-flat" style={{ padding: 16, marginBottom: 20 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
                <span className="label" style={{ margin: 0 }}>Reasoning Trace</span>
                {data.reasoning_trace.anchored_on_chain ? (
                  <span style={{
                    fontSize: '0.68rem', fontWeight: 600, padding: '1px 6px', borderRadius: 3,
                    background: 'rgba(16,185,129,0.12)', color: 'var(--positive)',
                    border: '1px solid rgba(16,185,129,0.3)',
                  }}>
                    <span className="i-lucide-check w-3 h-3 mr-0.5" /> On-chain
                  </span>
                ) : (
                  <span className="caption" style={{ color: 'var(--text-4)' }}>off-chain (anchored at vault deploy)</span>
                )}
              </div>
              <div className="mono" style={{ fontSize: '0.72rem', color: 'var(--text-3)', wordBreak: 'break-all' }}>
                hash: {data.reasoning_trace.trace_hash}
              </div>
              {data.reasoning_trace.anchor_tx_hash && (
                <div className="mono" style={{ fontSize: '0.72rem', color: 'var(--text-3)', wordBreak: 'break-all', marginTop: 4 }}>
                  tx: {data.reasoning_trace.anchor_tx_hash}
                </div>
              )}
              <p className="caption" style={{ marginTop: 6, color: 'var(--text-4)', lineHeight: 1.4 }}>
                Keccak256 of the canonical recommendation. Anyone can re-derive this hash from the
                portfolio + market context shown above and verify it on Arc's <code>ReasoningTraceRegistry</code>.
              </p>
            </div>
          )}

          {/* Per-pick detail table */}
          {data.allocations?.length > 0 && (
            <div>
              <div className="label mb-3">Per-Pick Breakdown</div>
              <div className="table-container">
                <table>
                  <thead>
                    <tr>
                      <th>Pick</th>
                      <th className="text-right">Weight</th>
                      <th className="text-right">DSR p</th>
                      <th className="text-right">PBO</th>
                      <th className="text-right">OOS Sharpe</th>
                      <th className="text-right">Δ Sharpe vs paper</th>
                      <th>Rigor</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.allocations.map(a => (
                      <tr key={a.id}>
                        <td style={{ maxWidth: 280 }}>
                          <div style={{ fontWeight: 600, fontSize: '0.85rem' }}>
                            {a.symbol}
                            {a.asset_class && (
                              <span className="caption" style={{ marginLeft: 8, color: 'var(--text-4)' }}>
                                [{a.asset_class}{a.exchange && a.exchange !== '?' ? ` · ${a.exchange}` : ''}]
                              </span>
                            )}
                          </div>
                          <div className="caption" style={{ color: 'var(--text-4)', fontSize: '0.7rem' }}>
                            {a.signal_reason || a.title?.slice(0, 80) || a.paper_anchor}
                          </div>
                        </td>
                        <td className="text-right mono">{fmtPct(a.weight)}</td>
                        <td className="text-right mono">{fmt(a.dsr_p_value, 3)}</td>
                        <td className="text-right mono">{fmt(a.pbo_score, 3)}</td>
                        <td className="text-right mono">{fmt(a.out_of_sample_sharpe)}</td>
                        <td className={`text-right mono ${a.paper_delta_sharpe > 0 ? 'positive' : a.paper_delta_sharpe < 0 ? 'negative' : ''}`}>
                          {a.paper_delta_sharpe != null ? (a.paper_delta_sharpe > 0 ? '+' : '') + a.paper_delta_sharpe.toFixed(2) : '—'}
                        </td>
                        <td>
                          {a.passes_rigor_gate ? (
                            <span className="flex items-center gap-1 text-[var(--positive)] text-[0.72rem] font-semibold"><span className="i-lucide-check w-3 h-3" /> Passed</span>
                          ) : (
                            <span style={{ color: 'var(--text-4)', fontSize: '0.72rem' }}>Candidate</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
