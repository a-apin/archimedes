import { useState, useEffect, useCallback } from 'react'
import { useAuth } from '../AuthContext'
import { apiGet } from '../api'

// Single-user leaderboard (MVP pivot — no publish mechanism exists yet, so
// ranking a global cohort was incoherent; nobody had opted into competing).
// Signed in: ranks YOUR OWN strategies against each other by the backend's
// transparent conviction score (real rigor gate + backtest). Signed out (or
// explicitly toggled): shows the curated seed library as an honestly-labeled
// REFERENCE set, never framed as competition. Nothing here is fabricated:
// validation metrics are real passport fields; the forward axis (per-strategy
// StockBench + live paper-P&L) is not scored yet and is called out as
// "pending" in its own section, not rendered per row. Never auth-gated — public browse stays;
// see backend's `scope` field (own|curated), which reports what was actually
// served, not just what was requested.

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
  const { user } = useAuth()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [sortBy, setSortBy] = useState('conviction_score')
  const [order, setOrder] = useState('desc')
  const [minRigor, setMinRigor] = useState(false)
  const [regime, setRegime] = useState('')
  // null = let the backend pick the default (own if signed in, else curated).
  // Explicit only when the caller toggles — see the scope switch below.
  const [scopeParam, setScopeParam] = useState(null)

  // If auth resolves (or the user signs in/out) while this page is open, drop
  // back to the server default rather than keep stale explicit scope — e.g. a
  // visitor who toggled to "Curated" pre-login should not stay stuck there
  // post-login for no reason.
  useEffect(() => { setScopeParam(null) }, [user?.id])

  const load = useCallback(() => {
    setLoading(true)
    const params = new URLSearchParams({ sort_by: sortBy, order, limit: '100' })
    if (minRigor) params.set('min_rigor', 'true')
    if (regime) params.set('regime_tag', regime)
    if (scopeParam) params.set('scope', scopeParam)
    apiGet(`/api/leaderboard?${params.toString()}`)
      .then(d => { setData(d); setError(null) })
      .catch(e => setError(e.message || 'Failed to load leaderboard'))
      .finally(() => setLoading(false))
  }, [sortBy, order, minRigor, regime, scopeParam])

  useEffect(() => { load() }, [load])

  const engine = data?.scoring_engine
  const sb = engine?.stockbench_global
  // The scope actually served (may differ from scopeParam — an anonymous
  // request for "own" is transparently served "curated"). This, not the
  // request param, is the source of truth for labeling the page.
  const servedScope = data?.scope ?? (user ? 'own' : 'curated')
  const isOwn = servedScope === 'own'

  return (
    <div className="leaderboard-page" style={{ maxWidth: 1100 }}>
      <div style={{ marginBottom: 18 }}>
        <h2 className="serif" style={{ fontSize: '2rem', marginBottom: 8 }}>
          {isOwn ? 'Your Strategy Leaderboard' : 'Strategy Leaderboard'}
        </h2>
        <p className="body" style={{ maxWidth: 760 }}>
          {isOwn
            ? <>Your strategies, ranked against each other by a transparent <strong>conviction score</strong> built
                from real rigor-gate and backtest results — the ugly numbers included. Build your track record now;
                it carries to mainnet.</>
            : <>The curated seed library, ranked by a transparent <strong>conviction score</strong> built from real
                rigor-gate and backtest results — the ugly numbers included. A reference set, not a competition.</>}
        </p>

        {!user && (
          <div
            role="status"
            style={{
              marginTop: 10,
              padding: '10px 14px',
              borderLeft: '3px solid var(--accent)',
              background: 'var(--accent-muted)',
              borderRadius: 4,
              fontSize: 13,
              color: 'var(--text-2)',
              display: 'flex',
              flexWrap: 'wrap',
              gap: 8,
              alignItems: 'center',
            }}
          >
            <span>
              <strong style={{ color: 'var(--accent)' }}>Sign in to rank your strategies.</strong>{' '}
              What you're seeing below is the curated library — reference rows, not strategies you're
              competing against.
            </span>
            <a
              className="btn-primary"
              style={{ marginLeft: 'auto', flexShrink: 0 }}
              href={`/sign-in?next=${encodeURIComponent(`${window.location.pathname}${window.location.search}`)}`}
            >
              Sign in
            </a>
          </div>
        )}

        {user && (
          <div style={{ marginTop: 10, display: 'flex', gap: 6, alignItems: 'center' }}>
            <button type="button" onClick={() => setScopeParam('own')}
              className={`tag-tab ${isOwn ? 'tag-accent' : 'tag-muted'}`}
              style={{ cursor: 'pointer', border: 'none', borderRadius: 14, fontSize: 12 }}>
              My strategies
            </button>
            <button type="button" onClick={() => setScopeParam('curated')}
              className={`tag-tab ${!isOwn ? 'tag-accent' : 'tag-muted'}`}
              style={{ cursor: 'pointer', border: 'none', borderRadius: 14, fontSize: 12 }}
              title="The curated seed library — reference rows, not strategies you're competing against">
              Curated library (reference)
            </button>
          </div>
        )}
        {/* The broad provisional-data banner that lived here named TWO defects.
            The routing defect (#1203) is retired everywhere: the post-fix
            re-run landed fresh rows for every curated strategy (verified
            against prod 2026-08-19 — backtest_results max created_at
            2026-08-20 03:28 UTC; zero curated strategies stale). The
            backtest/live interpreter divergence is fully retired as of
            2026-08-20: F1/F4–F10 landed earlier, and F2 (live position FSM)
            + F3 (rebalance cadence) landed via PR #1320 — parity-pinned
            per-bar in test_interpreter_parity.py — and were verified running
            on the redeployed agent runner, so the old divergence clause here
            was removed (its claim became false). The ONE remaining residual:
            the own view still holds July-era rows for older generated
            strategies that predate the corrections. Those numbers are FIXED
            at generation time — no re-backtest loop exists for generated
            strategies (#1365; the prior claim of a backtest-cycle refresh was
            false and is retired) — hence the single-caveat banner below,
            scoped to the own view; curated rows are reference-only backtests,
            all verified fresh. */}
        {isOwn && (
          <div
            role="status"
            style={{
              marginTop: 10,
              padding: '10px 14px',
              borderLeft: '3px solid var(--warning, #b45309)',
              background: 'var(--warning-bg, rgba(180,83,9,0.10))',
              borderRadius: 4,
              fontSize: 13,
              color: 'var(--text-2)',
            }}
          >
            <strong style={{ color: 'var(--warning, #b45309)' }}>One caveat on your figures.</strong>{' '}
            These numbers are <strong>fixed at generation time</strong> — a strategy generated
            before the August engine corrections keeps the numbers it was scored with. Generated
            strategies are not re-backtested; generate again to get a strategy scored by the
            current engine.
          </div>
        )}
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
            className={`tag-tab ${sortBy === o.id ? 'tag-accent' : 'tag-muted'}`}
            style={{ cursor: 'pointer', border: 'none', borderRadius: 14, fontSize: 12 }}>
            {o.label}
          </button>
        ))}
        <button type="button" onClick={() => setOrder(o => o === 'desc' ? 'asc' : 'desc')}
          className="tag-tab tag-muted" style={{ cursor: 'pointer', border: 'none', borderRadius: 14, fontSize: 12 }}
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
        {data && (
          <span style={{ fontSize: 12, color: 'var(--text-3)', marginLeft: 'auto' }}>
            {data.degraded ? '—' : `${data.total} strateg${data.total === 1 ? 'y' : 'ies'}`}
          </span>
        )}
      </div>

      {loading && <div className="body" style={{ color: 'var(--text-3)' }}>Loading the board…</div>}
      {error && <div className="tag-warning" style={{ display: 'inline-block', padding: '6px 10px' }}>Couldn’t load the leaderboard: {error}</div>}

      {/* A degraded board (#1356: the strategy provider raised, or the
          curated cohort came back empty for a reason other than a
          legitimate filter — e.g. the corpus missing from the build) is a
          200 with intact scoring-engine metadata, not an `error` — so it
          must be surfaced here, and it must pre-empt BOTH honest-empty
          messages below, which claim something specific ("you haven't
          generated any" / "no strategies match these filters") that is not
          what actually happened. */}
      {!loading && !error && data?.degraded && (
        <div role="status" className="tag-warning" style={{ display: 'block', padding: '10px 14px', marginBottom: 14, borderRadius: 4 }}>
          <strong>Board data is degraded.</strong>{' '}
          {data.degraded_reason || 'Some strategies could not be loaded.'}{' '}
          <button type="button" className="btn btn-sm btn-outline" onClick={load} style={{ marginLeft: 4 }}>
            Retry
          </button>
        </div>
      )}

      {!loading && !error && data && !data.degraded && data.entries.length === 0 && isOwn && (
        <div className="body" style={{ color: 'var(--text-3)', padding: 20, textAlign: 'center', border: '1px dashed var(--glass-border)', borderRadius: 8 }}>
          You haven't generated any strategies yet.{' '}
          <a href="/app/generate" style={{ color: 'var(--accent)' }}>Generate one</a>, or{' '}
          <button type="button" onClick={() => setScopeParam('curated')}
            style={{ background: 'none', border: 'none', padding: 0, color: 'var(--accent)', cursor: 'pointer', textDecoration: 'underline', font: 'inherit' }}>
            browse the curated library
          </button> for reference.
        </div>
      )}

      {!loading && !error && data && !data.degraded && data.entries.length === 0 && !isOwn && (
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
                <th style={{ padding: '8px 10px' }} title="Deflated Sharpe Ratio — Sharpe adjusted for selection bias / multiple testing">DSR</th>
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
                      {isOwn
                        // Every row here is the caller's own by construction (server-scoped) —
                        // `creator` is curator_wallet provenance, not ownership, and is often
                        // unset for generated strategies, so it's redundant/misleading here.
                        ? 'Your strategy'
                        : (e.creator === 'Archimedes' ? 'Archimedes (curated)' : `by ${e.creator.slice(0, 6)}…${e.creator.slice(-4)}`)}
                      {e.regime_tag && e.regime_tag !== 'regime_neutral' ? ` · ${e.regime_tag}` : ''}
                    </div>
                  </td>
                  <td style={{ padding: '10px', whiteSpace: 'nowrap' }}>
                    <div style={{ fontWeight: 600, color: 'var(--accent)' }}>{fmt(e.conviction_score, 1)}</div>
                    <ScoreBar components={e.score_components} weights={engine?.weights} />
                  </td>
                  <td style={{ padding: '10px', whiteSpace: 'nowrap' }}>{fmt(e.sharpe_ratio)}</td>
                  <td style={{ padding: '10px', whiteSpace: 'nowrap' }}>{fmtPct(e.cagr)}</td>
                  <td style={{ padding: '10px', whiteSpace: 'nowrap', color: 'var(--negative)' }}>
                    {e.max_drawdown != null ? `−${fmtPct(Math.abs(e.max_drawdown))}` : '—'}
                  </td>
                  <td style={{ padding: '10px', whiteSpace: 'nowrap' }}>
                    {rigorBadge(e)}
                    {(e.dsr_p_value != null || e.pbo_score != null || e.out_of_sample_sharpe != null) && (
                      <div style={{ fontSize: 11, color: 'var(--text-3)', marginTop: 2 }} title="DSR confidence (0–1, higher is better): probability the Sharpe survives deflation/multiple-testing. Not a classical p-value. OOS = out-of-sample Sharpe.">
                        {[
                          e.dsr_p_value != null && `DSR conf=${fmt(e.dsr_p_value)}`,
                          e.pbo_score != null && `PBO ${fmt(e.pbo_score)}`,
                          e.out_of_sample_sharpe != null && `OOS ${fmt(e.out_of_sample_sharpe)}`,
                        ]
                          .filter(Boolean)
                          .join(' · ')}
                      </div>
                    )}
                  </td>
                  <td style={{ padding: '10px', whiteSpace: 'nowrap' }}>{fmt(e.deflated_sharpe_ratio)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
