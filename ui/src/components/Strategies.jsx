import { useState, useEffect, useCallback } from 'react'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

async function apiGet(path) {
  const res = await fetch(`${API_BASE}${path}`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

async function apiPost(path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

const RISK_PROFILES = [
  { id: 'conservative', label: 'Conservative' },
  { id: 'moderate', label: 'Moderate' },
  { id: 'aggressive', label: 'Aggressive' },
  { id: 'hyper_risky', label: 'Hyper-risky' },
]

const STATUS_ORDER = ['live', 'validated', 'candidate', 'retired']

function statusTag(status) {
  if (status === 'live') return 'tag-positive'
  if (status === 'validated') return 'tag-accent'
  return 'tag-muted'
}

function fmt(v, decimals = 2) {
  return v != null ? v.toFixed(decimals) : '—'
}
function fmtPct(v) {
  return v != null ? `${(v * 100).toFixed(1)}%` : '—'
}
function truncHash(h) {
  return h ? `${h.slice(0, 8)}…${h.slice(-6)}` : '—'
}

function WeightBar({ weight, accent }) {
  return (
    <div style={{ background: 'rgba(255,255,255,0.06)', borderRadius: 4, height: 8, overflow: 'hidden' }}>
      <div style={{ width: `${(weight * 100).toFixed(1)}%`, height: '100%', background: accent || 'var(--accent)' }} />
    </div>
  )
}

// ── Featured Passport Card ────────────────────────────────────

function FeaturedCard({ s }) {
  const [open, setOpen] = useState(false)
  const hasBacktest = s.sharpe_ratio != null
  const pctOfClaim = s.paper_claimed_sharpe && s.sharpe_ratio != null
    ? ((s.sharpe_ratio / s.paper_claimed_sharpe) * 100).toFixed(0)
    : null

  return (
    <div className="card-elevated mb-6 fade-up fade-up-3">
      <div className="flex items-center justify-between mb-5">
        <div>
          <div className="flex items-center gap-3 mb-2">
            <h3 style={{ fontSize: '1.2rem' }}>{s.paper_title}</h3>
            <span className={`tag ${statusTag(s.status)}`} style={{ textTransform: 'capitalize' }}>{s.status}</span>
            {s.is_backtest_placeholder && (
              <span className="strat-placeholder-note">est. metrics</span>
            )}
          </div>
          <div className="body">{s.methodology_summary}</div>
        </div>
      </div>

      <div className="strat-grid-3" style={{ gap: 14 }}>
        {/* Source Paper */}
        <div className="card-flat" style={{ padding: 20 }}>
          <div className="label mb-3">Source Paper</div>
          <div style={{ fontWeight: 600, fontSize: '0.88rem', marginBottom: 4, lineHeight: 1.4 }}>
            "{s.paper_title}"
          </div>
          <div className="caption mb-3">
            {s.paper_authors?.slice(0, 2).join(', ')}{s.paper_authors?.length > 2 ? ' et al.' : ''}{s.paper_year ? ` (${s.paper_year})` : ''}
            {s.paper_venue ? ` · ${s.paper_venue}` : ''}
          </div>
          <div className="flex gap-2 mb-3">
            {s.asset_universe?.slice(0, 3).map(a => (
              <span key={a} className="tag tag-muted">{a}</span>
            ))}
          </div>
          {s.paper_doi && <div className="caption">DOI: <span className="mono" style={{ color: 'var(--info)', fontSize: '0.75rem' }}>{s.paper_doi.slice(0, 20)}…</span></div>}
          {s.paper_citation_count != null && <div className="caption">Citations: {s.paper_citation_count.toLocaleString()}</div>}
          {s.curator_note && <div className="caption" style={{ marginTop: 6, fontStyle: 'italic' }}>Curator: Dan</div>}
        </div>

        {/* Backtest */}
        <div className="card-flat" style={{ padding: 20 }}>
          <div className="label mb-3">Backtest Metrics{s.is_backtest_placeholder ? ' (est.)' : ''}</div>
          {hasBacktest ? (
            <>
              <div className="strat-metric-grid">
                <div><div className="caption">Sharpe</div><div style={{ fontWeight: 700, fontSize: '1.1rem' }}>{fmt(s.sharpe_ratio)}</div></div>
                <div><div className="caption">CAGR</div><div className="positive" style={{ fontWeight: 700, fontSize: '1.1rem' }}>{fmtPct(s.cagr)}</div></div>
                <div><div className="caption">Max DD</div><div className="negative" style={{ fontWeight: 700, fontSize: '1.1rem' }}>−{fmtPct(s.max_drawdown)}</div></div>
                <div><div className="caption">Win Rate</div><div style={{ fontWeight: 700, fontSize: '1.1rem' }}>{fmtPct(s.win_rate)}</div></div>
                <div><div className="caption">Calmar</div><div style={{ fontWeight: 700, fontSize: '1.1rem' }}>{fmt(s.calmar_ratio)}</div></div>
                <div><div className="caption">Corr SPY</div><div style={{ fontWeight: 700, fontSize: '1.1rem' }}>{fmt(s.correlation_to_spy)}</div></div>
              </div>
              {s.paper_claimed_sharpe != null && (
                <>
                  <div className="divider" style={{ margin: '10px 0 8px' }} />
                  <div className="caption">Paper claimed: <strong>{fmt(s.paper_claimed_sharpe)}</strong></div>
                  <div className="caption">Backtest: <strong>{fmt(s.sharpe_ratio)}</strong>
                    {pctOfClaim && (
                      <span className={pctOfClaim >= 50 ? 'positive' : 'negative'} style={{ marginLeft: 6 }}>
                        ({pctOfClaim}% {pctOfClaim >= 50 ? '✓' : '⚠'})
                      </span>
                    )}
                  </div>
                </>
              )}
            </>
          ) : (
            <div className="caption" style={{ color: 'var(--text-4)', paddingTop: 8 }}>Not yet evaluated — awaiting backtest engine run.</div>
          )}
        </div>

        {/* On-chain Provenance */}
        <div className="card-flat" style={{ padding: 20 }}>
          <div className="label mb-3">On-Chain Provenance</div>
          <div className="caption mb-3">
            Strategy ID<br />
            <span className="mono" style={{ color: 'var(--info)', wordBreak: 'break-all', fontSize: '0.75rem' }}>{truncHash(s.id)}</span>
          </div>
          {s.methodology_hash && (
            <div className="caption mb-3">
              Methodology Hash<br />
              <span className="mono" style={{ color: 'var(--text-2)', wordBreak: 'break-all', fontSize: '0.75rem' }}>{truncHash(s.methodology_hash)}</span>
            </div>
          )}
          {s.on_chain_registration_tx ? (
            <div className="caption mb-3">
              Registration Tx<br />
              <span className="mono" style={{ color: 'var(--info)', fontSize: '0.75rem' }}>{truncHash(s.on_chain_registration_tx)}</span>
            </div>
          ) : (
            <div className="caption mb-3" style={{ color: 'var(--text-4)' }}>Registration Tx: pending</div>
          )}
          <div className="caption mb-3">
            Extraction: <span className="mono">{s.extraction_llm || 'hand-curated'}</span>
          </div>
          <div className={s.methodology_hash ? 'verify-panel' : ''} style={{ borderRadius: 6, padding: s.methodology_hash ? '8px 10px' : 0 }}>
            {s.methodology_hash ? (
              <div className="verify-badge">
                <svg width="13" height="13" viewBox="0 0 16 16" fill="none">
                  <circle cx="8" cy="8" r="7" stroke="currentColor" strokeWidth="1.5" />
                  <path d="M5 8l2 2 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                </svg>
                Hash Computed
              </div>
            ) : (
              <div className="verify-badge-pending">Hash Pending</div>
            )}
          </div>
        </div>
      </div>

      {/* Curator note expansion */}
      {s.curator_note && (
        <div className="passport-expand">
          <div className="passport-expand-header" onClick={() => setOpen(o => !o)}>
            <span className="label">Curator Note</span>
            <span className="caption">{open ? '▲' : '▼'}</span>
          </div>
          {open && (
            <div className="passport-expand-body">
              <p className="body" style={{ fontStyle: 'italic' }}>{s.curator_note}</p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Strategy Grid Card ────────────────────────────────────────

function StrategyCard({ s }) {
  const [open, setOpen] = useState(false)
  const hasBacktest = s.sharpe_ratio != null

  return (
    <div className="card fade-up fade-up-4">
      <div className="flex items-center gap-3 mb-3" style={{ flexWrap: 'wrap' }}>
        <h3 style={{ flex: 1 }}>{s.paper_title}</h3>
        <span className={`tag ${statusTag(s.status)}`} style={{ textTransform: 'capitalize' }}>{s.status}</span>
      </div>
      <div className="caption mb-3">
        {s.paper_authors?.slice(0, 2).join(', ')}{s.paper_authors?.length > 2 ? ' et al.' : ''}
        {s.paper_year ? ` (${s.paper_year})` : ''}
        {s.paper_venue ? ` · ${s.paper_venue}` : ''}
      </div>
      {hasBacktest ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8, marginBottom: 12 }}>
          <div><div className="caption">Sharpe</div><div style={{ fontWeight: 700 }}>{fmt(s.sharpe_ratio)}{s.is_backtest_placeholder ? '*' : ''}</div></div>
          <div><div className="caption">CAGR</div><div className="positive" style={{ fontWeight: 700 }}>{fmtPct(s.cagr)}</div></div>
          <div><div className="caption">Max DD</div><div className="negative" style={{ fontWeight: 700 }}>−{fmtPct(s.max_drawdown)}</div></div>
          <div><div className="caption">Corr SPY</div><div style={{ fontWeight: 700 }}>{fmt(s.correlation_to_spy)}</div></div>
        </div>
      ) : (
        <div className="caption" style={{ marginBottom: 12, color: 'var(--text-4)' }}>Backtest pending</div>
      )}
      <div className="flex gap-2 mb-3" style={{ flexWrap: 'wrap' }}>
        {s.asset_universe?.slice(0, 3).map(a => <span key={a} className="tag tag-muted">{a}</span>)}
        {s.is_backtest_placeholder && <span className="tag tag-muted">est.</span>}
      </div>

      {/* Expandable passport detail */}
      <div
        className="caption"
        style={{ cursor: 'pointer', color: 'var(--accent)', marginTop: 4 }}
        onClick={() => setOpen(o => !o)}
      >
        {open ? '▲ hide details' : '▼ full passport'}
      </div>
      {open && (
        <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--glass-border)' }}>
          <div className="body" style={{ marginBottom: 10 }}>{s.methodology_summary}</div>
          <div className="caption mb-2">
            <strong>Position sizing:</strong> {s.position_sizing} · <strong>Rebalance:</strong> {s.rebalance_frequency}
          </div>
          {s.methodology_hash && (
            <div className="caption mb-2">
              <strong>Methodology hash:</strong><br />
              <span className="mono" style={{ fontSize: '0.73rem', color: 'var(--text-3)', wordBreak: 'break-all' }}>{s.methodology_hash.slice(0, 32)}…</span>
            </div>
          )}
          {s.paper_citation_count != null && (
            <div className="caption mb-2"><strong>Citations:</strong> {s.paper_citation_count.toLocaleString()}</div>
          )}
          {s.paper_claimed_sharpe != null && s.sharpe_ratio != null && (
            <div className="caption">
              <strong>Paper claim:</strong> Sharpe {fmt(s.paper_claimed_sharpe)} →
              backtest {fmt(s.sharpe_ratio)}{s.is_backtest_placeholder ? '*' : ''}
              <span className={s.sharpe_ratio / s.paper_claimed_sharpe >= 0.5 ? 'positive' : 'negative'} style={{ marginLeft: 6 }}>
                ({((s.sharpe_ratio / s.paper_claimed_sharpe) * 100).toFixed(0)}%)
              </span>
            </div>
          )}
          {s.curator_note && (
            <div className="caption" style={{ marginTop: 8, fontStyle: 'italic', color: 'var(--text-3)' }}>
              "{s.curator_note.slice(0, 120)}{s.curator_note.length > 120 ? '…' : ''}"
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Paper Corpus Table ────────────────────────────────────────

function CorpusTable({ strategies }) {
  const paperStrategies = strategies.filter(s => s.paper_year && s.paper_title !== 'Buy-and-Hold Baseline')
  if (!paperStrategies.length) return null

  return (
    <div style={{ marginTop: 40 }}>
      <div className="label mb-3">Paper Corpus</div>
      <div className="caption mb-4">{paperStrategies.length} papers curated · hand-curated seed library</div>
      <div className="table-container">
        <table>
          <thead>
            <tr>
              <th>Paper</th>
              <th>Authors</th>
              <th>Year</th>
              <th>Venue</th>
              <th className="text-right">Citations</th>
              <th>Status</th>
              <th>Curator</th>
            </tr>
          </thead>
          <tbody>
            {paperStrategies.map(s => (
              <tr key={s.id}>
                <td style={{ fontWeight: 500, maxWidth: 240 }}>{s.paper_title}</td>
                <td className="caption">{s.paper_authors?.slice(0, 2).join(', ')}{s.paper_authors?.length > 2 ? ' et al.' : ''}</td>
                <td>{s.paper_year ?? '—'}</td>
                <td className="caption" style={{ maxWidth: 160 }}>{s.paper_venue ?? '—'}</td>
                <td className="text-right">{s.paper_citation_count?.toLocaleString() ?? '—'}</td>
                <td>
                  <span className={`tag ${statusTag(s.status)}`} style={{ textTransform: 'capitalize', fontSize: '0.68rem' }}>
                    {s.status}
                  </span>
                </td>
                <td className="mono caption" style={{ color: 'var(--info)' }}>
                  {s.extraction_llm ? s.extraction_llm.split('-').slice(0, 2).join('-') : 'Dan'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Strategy Architect form ───────────────────────────────────

function StrategyArchitect({ strategies }) {
  const [intent, setIntent] = useState(
    'I have idle USDC and want thoughtful, research-backed growth without stomach-churning drawdowns.',
  )
  const [riskProfile, setRiskProfile] = useState('moderate')
  const [capital, setCapital] = useState(5000)
  const [result, setResult] = useState(null)
  const [constructing, setConstructing] = useState(false)
  const [constructError, setConstructError] = useState('')

  const construct = useCallback(async () => {
    setConstructing(true)
    setConstructError('')
    setResult(null)
    try {
      const data = await apiPost('/api/strategies/construct', {
        intent,
        risk_profile: riskProfile,
        capital_usdc: Number(capital),
      })
      setResult(data)
    } catch (e) {
      setConstructError(e.message || 'Construction failed')
    } finally {
      setConstructing(false)
    }
  }, [intent, riskProfile, capital])

  const isFallback = result?.model_id === 'canned-fallback'

  return (
    <div style={{ maxWidth: 700, margin: '0 auto' }}>
      <h2 style={{ fontFamily: 'var(--serif)', fontSize: '1.6rem', marginBottom: 8 }}>Strategy Architect</h2>
      <p className="hint">
        Describe what you want. The agent selects paper-grounded strategies, weights them
        under hard risk constraints, and anchors a verifiable reasoning trace.
      </p>

      <div className="card" style={{ marginTop: 20 }}>
        <div className="form-group">
          <label className="label">What do you want from this portfolio?</label>
          <textarea
            className="chat-input"
            style={{ width: '100%', minHeight: 80, resize: 'vertical' }}
            value={intent}
            onChange={(e) => setIntent(e.target.value)}
            placeholder="e.g. steady growth, low drawdowns, trend-following…"
          />
        </div>
        <div className="form-row" style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginTop: 12 }}>
          <div className="form-group">
            <label className="label">Risk profile</label>
            <select className="chat-input" value={riskProfile} onChange={(e) => setRiskProfile(e.target.value)}>
              {RISK_PROFILES.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
            </select>
          </div>
          <div className="form-group">
            <label className="label">Capital (USDC)</label>
            <input
              className="chat-input"
              type="number"
              min="1"
              value={capital}
              onChange={(e) => setCapital(e.target.value)}
            />
          </div>
          <div className="form-group" style={{ display: 'flex', alignItems: 'flex-end' }}>
            <button className="btn btn-primary" onClick={construct} disabled={constructing || !intent.trim()}>
              {constructing ? 'Constructing…' : 'Construct portfolio'}
            </button>
          </div>
        </div>
      </div>

      {constructError && (
        <div className="info-box warning" style={{ marginTop: 16 }}>
          Construction failed: {constructError}
        </div>
      )}

      {result && (
        <div className="card" style={{ marginTop: 20 }}>
          {isFallback && (
            <div className="info-box warning" style={{ marginBottom: 16 }}>
              ⚠️ Offline fallback (no <code>ANTHROPIC_API_KEY</code>) — equal-weighted,
              not model reasoning. The guardrail + trace are still real.
            </div>
          )}
          <h3>Proposed portfolio</h3>
          <p className="hint" style={{ marginTop: 4 }}>
            {result.risk_profile} · {Number(result.capital_usdc).toLocaleString()} USDC ·
            model: <span className="mono">{result.model_id}</span>
          </p>
          {result.overall_reasoning && (
            <p style={{ marginTop: 12, lineHeight: 1.5 }}>{result.overall_reasoning}</p>
          )}
          <div style={{ marginTop: 16, display: 'flex', flexDirection: 'column', gap: 14 }}>
            {result.selected.map((s) => (
              <div key={s.strategy_id}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                  <strong>{s.paper_title || s.strategy_id}</strong>
                  <span className="mono">{(s.weight * 100).toFixed(1)}%</span>
                </div>
                <WeightBar weight={s.weight} />
                {s.rationale && <p className="hint" style={{ marginTop: 6 }}>{s.rationale}</p>}
                {s.paper_citation && (
                  <p className="caption" style={{ marginTop: 2 }}>📄 {s.paper_citation}</p>
                )}
              </div>
            ))}
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                <strong>USYC (cash-yield sleeve)</strong>
                <span className="mono">{(result.usyc_weight * 100).toFixed(1)}%</span>
              </div>
              <WeightBar weight={result.usyc_weight} accent="#10B981" />
            </div>
          </div>
          {result.risk_notes && (
            <p className="hint" style={{ marginTop: 16 }}><strong>Risk notes:</strong> {result.risk_notes}</p>
          )}
          {result.guardrail_notes?.length > 0 && (
            <div style={{ marginTop: 16 }}>
              <div className="label">Guardrail adjustments</div>
              <ul className="hint" style={{ marginTop: 6, paddingLeft: 18 }}>
                {result.guardrail_notes.map((n, i) => <li key={i}>{n}</li>)}
              </ul>
            </div>
          )}
          {result.trace && (
            <div className="trace-card" style={{ marginTop: 18 }}>
              <div className="trace-id">
                {result.trace.decision_type} · {result.trace.trigger}
                <span
                  className={`badge ${result.trace.is_anchored ? 'tier-1' : ''}`}
                  style={{ marginLeft: 8 }}
                >
                  {result.trace.is_anchored ? 'anchored on-chain' : 'pending anchor'}
                </span>
              </div>
              <div className="mono" style={{ marginTop: 6, wordBreak: 'break-all', fontSize: 12 }}>
                {result.trace.trace_hash}
              </div>
              <p className="caption" style={{ marginTop: 6 }}>
                SHA-256 of the decision — recompute it from this response to verify.
                Anchored on Arc via ReasoningTraceRegistry.
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Main export ───────────────────────────────────────────────

export default function Strategies() {
  const [strategies, setStrategies] = useState([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [activeFilter, setActiveFilter] = useState('all')

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError('')
    try {
      const data = await apiGet('/api/strategies/')
      const sorted = [...(data.strategies || [])].sort(
        (a, b) => STATUS_ORDER.indexOf(a.status) - STATUS_ORDER.indexOf(b.status)
      )
      setStrategies(sorted)
    } catch (e) {
      setLoadError(e.message || 'Failed to load strategies')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const counts = {
    all: strategies.length,
    live: strategies.filter(s => s.status === 'live').length,
    validated: strategies.filter(s => s.status === 'validated').length,
    candidate: strategies.filter(s => s.status === 'candidate').length,
  }

  const visible = activeFilter === 'all'
    ? strategies
    : strategies.filter(s => s.status === activeFilter)

  // Featured = highest Sharpe among visible live strategies; fall back to first live, then first
  const liveWithSharpe = visible
    .filter(s => s.status === 'live' && s.sharpe_ratio != null)
    .sort((a, b) => b.sharpe_ratio - a.sharpe_ratio)
  const featured = liveWithSharpe[0]
    ?? visible.find(s => s.status === 'live')
    ?? visible[0]

  const grid = featured ? visible.filter(s => s.id !== featured.id) : visible

  const FILTERS = [
    { key: 'all', label: `All (${counts.all})` },
    { key: 'live', label: `Live (${counts.live})` },
    { key: 'validated', label: `Validated (${counts.validated})` },
    { key: 'candidate', label: `Candidate (${counts.candidate})` },
  ]

  return (
    <div>
      {/* ── Explorer ──────────────────────────────────────── */}
      <div className="fade-up fade-up-1" style={{ maxWidth: 640, marginBottom: 28 }}>
        <h2 className="serif" style={{ fontSize: '2rem', marginBottom: 10 }}>Paper-Grounded Strategies</h2>
        <p className="body">
          Every strategy traces back to published academic research. Methodology extracted by AI
          or curated by hand, backtested against real data, with paper-claim deltas surfaced honestly.
        </p>
      </div>

      {/* Filter tabs */}
      <div className="strat-filter-bar fade-up fade-up-2">
        {FILTERS.map(f => (
          <span
            key={f.key}
            className={`tag ${activeFilter === f.key ? 'tag-accent' : 'tag-muted'}`}
            onClick={() => setActiveFilter(f.key)}
          >
            {f.label}
          </span>
        ))}
      </div>

      {/* Loading / error */}
      {loading && <div className="caption" style={{ marginBottom: 16 }}>Loading strategies…</div>}
      {loadError && (
        <div className="info-box warning" style={{ marginBottom: 16 }}>
          Couldn't load strategies: {loadError}
          <div className="hint" style={{ marginTop: 6 }}>
            If empty in Docker, the backend needs <code>analytics-engine/strategies/</code> mounted.
          </div>
        </div>
      )}
      {!loading && !loadError && visible.length === 0 && (
        <p className="caption" style={{ marginBottom: 16 }}>No strategies match this filter.</p>
      )}

      {/* Featured passport card */}
      {featured && <FeaturedCard s={featured} />}

      {/* Strategy grid */}
      {grid.length > 0 && (
        <div className="strat-grid-2 mb-6">
          {grid.map(s => <StrategyCard key={s.id} s={s} />)}
        </div>
      )}

      {/* Paper corpus table */}
      {!loading && strategies.length > 0 && <CorpusTable strategies={strategies} />}

      {/* Placeholder disclaimer */}
      {strategies.some(s => s.is_backtest_placeholder) && (
        <div className="caption" style={{ marginTop: 16, color: 'var(--text-4)' }}>
          * Estimated metrics — sourced from paper claims with McLean-Pontiff post-publication decay
          applied. Replace with real BacktestResult once the analytics engine runs (Önder's IBacktestEvaluator).
        </div>
      )}

      {/* ── Strategy Architect ────────────────────────────── */}
      <hr className="section-divider" />
      <StrategyArchitect strategies={strategies} />
    </div>
  )
}
