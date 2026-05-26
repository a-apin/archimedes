import { useState, useEffect, useCallback, useRef } from 'react'
import { createPortal } from 'react-dom'
import CustomSelect from './CustomSelect'
import EfficientFrontier from './EfficientFrontier'
import RigorExplainer from './RigorExplainer'

import { apiGet, apiPost } from '../api'

const RISK_PROFILES = [
  { id: 'fixed_income', label: 'Fixed Income' },
  { id: 'conservative', label: 'Conservative' },
  { id: 'moderate', label: 'Moderate' },
  { id: 'aggressive', label: 'Aggressive' },
  { id: 'hyper_risky', label: 'Hyper-risky' },
]

const STATUS_ORDER = ['live', 'validated', 'candidate', 'retired']

function downloadStrategy(strategy, format) {
  let content, filename, type
  if (format === 'json') {
    content = JSON.stringify(strategy, null, 2)
    filename = `strategy-${(strategy.id || 'unknown').slice(0, 8)}.json`
    type = 'application/json'
  } else {
    const rows = [
      ['Field', 'Value'],
      ['Title', strategy.paper_title],
      ['Authors', strategy.paper_authors?.join(', ')],
      ['Year', strategy.paper_year],
      ['Status', strategy.status],
      ['Sharpe', strategy.sharpe_ratio],
      ['CAGR', strategy.cagr],
      ['Max Drawdown', strategy.max_drawdown],
      ['Methodology', strategy.methodology_summary],
      ['Assets', strategy.asset_universe?.join(', ')],
      ['Methodology Hash', strategy.methodology_hash],
    ]
    content = rows.map(r => r.map(c => `"${String(c ?? '').replace(/"/g, '""')}"`).join(',')).join('\n')
    filename = `strategy-${(strategy.id || 'unknown').slice(0, 8)}.csv`
    type = 'text/csv'
  }
  const blob = new Blob([content], { type })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = filename; a.click()
  URL.revokeObjectURL(url)
}

function statusTag(status) {
  if (status === 'live') return 'tag-positive'
  if (status === 'validated') return 'tag-accent'
  if (status === 'pending_backtest') return 'tag-warning'
  return 'tag-muted'
}

function statusLabel(status) {
  if (status === 'pending_backtest') return 'Pending Backtest'
  if (!status) return 'Candidate'
  return status.charAt(0).toUpperCase() + status.slice(1)
}

function fmt(v, decimals = 2) {
  return v != null ? v.toFixed(decimals) : '—'
}
function fmtPct(v) {
  return v != null ? `${(v * 100).toFixed(1)}%` : '—'
}

// "2002-01-01" -> Date; null on bad input
function isoToDate(iso) {
  if (!iso) return null
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? null : d
}

// Backtest window in fractional years; null if either bound missing/bad
export function periodInYears(startIso, endIso) {
  const a = isoToDate(startIso), b = isoToDate(endIso)
  if (!a || !b) return null
  const days = (b - a) / 86_400_000
  return days > 0 ? days / 365.25 : null
}

// $1k -> $X over `years` at compound `cagr`. Returns null if either missing.
export function projectedEndValue(principal, cagr, years) {
  if (cagr == null || years == null) return null
  return principal * Math.pow(1 + cagr, years)
}

export function fmtUsd(n, fractionDigits = 0) {
  if (n == null) return '—'
  return n.toLocaleString('en-US', {
    style: 'currency', currency: 'USD',
    minimumFractionDigits: fractionDigits, maximumFractionDigits: fractionDigits,
  })
}

function WeightBar({ weight, accent }) {
  return (
    <div style={{ background: 'rgba(255,255,255,0.06)', borderRadius: 4, height: 8, overflow: 'hidden' }}>
      <div style={{ width: `${(weight * 100).toFixed(1)}%`, height: '100%', background: accent || 'var(--accent)' }} />
    </div>
  )
}


// ── Strategy Grid Card ────────────────────────────────────────

// Inline period + projected-value line. Shows backtest window, the $1k -> $X
// it would have produced over the same period, and an explicit *backward-looking*
// disclaimer. Hidden entirely if we don't know the period (CAGR alone isn't
// enough to make a defensible projection).
export function BacktestHorizon({ s, principal = 1000 }) {
  const years = periodInYears(s.backtest_start, s.backtest_end)
  const endValue = projectedEndValue(principal, s.cagr, years)
  if (years == null) return null
  // Backend ships ISO datetimes; strip the T... for display so it reads as a date.
  const startStr = (s.backtest_start || '').slice(0, 10)
  const endStr = (s.backtest_end || '').slice(0, 10)
  return (
    <div className="caption" style={{ marginBottom: 8, color: 'var(--text-3)', lineHeight: 1.5 }}>
      Backtested <span className="mono">{startStr}</span> → <span className="mono">{endStr}</span>
      {' '}({years.toFixed(1)} yrs) ·{' '}
      <strong>{fmtUsd(principal)}</strong> →{' '}
      <strong style={{ color: 'var(--positive)' }}>{fmtUsd(endValue)}</strong>
      {' '}<span style={{ opacity: 0.7 }}>over the backtest window (historical, not a forecast)</span>
    </div>
  )
}


// ── Strategy Architect form ───────────────────────────────────

// Weighted-sum of per-strategy backtest metrics across the architect's
// selected portfolio. Honest about partial coverage — if any selected
// strategy is missing a metric, we return null for that metric rather
// than silently undercounting.
function aggregateMetrics(selected, strategies) {
  if (!selected?.length || !strategies?.length) return null
  const byId = Object.fromEntries(strategies.map(s => [s.id, s]))
  const metrics = ['sharpe_ratio', 'cagr', 'max_drawdown']
  const out = {}
  let totalWeight = 0
  let anyMissing = false
  for (const sel of selected) {
    const row = byId[sel.strategy_id]
    if (!row) { anyMissing = true; continue }
    totalWeight += sel.weight
    for (const m of metrics) {
      if (row[m] == null) { anyMissing = true; continue }
      out[m] = (out[m] || 0) + row[m] * sel.weight
    }
  }
  if (totalWeight === 0) return null
  return {
    sharpe_ratio: out.sharpe_ratio ?? null,
    cagr: out.cagr ?? null,
    max_drawdown: out.max_drawdown ?? null,
    coverage_weight: totalWeight,
    partial: anyMissing,
  }
}

export function StrategyArchitect({ strategies }) {
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
    <div className="max-w-[700px] mx-auto">
      <h2 className="font-serif text-[1.6rem] mb-2">Strategy Architect</h2>
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
        <div className="form-row flex gap-4 flex-wrap mt-3">
          <div className="form-group">
            <label className="label">Risk profile</label>
            <CustomSelect
              value={riskProfile}
              onChange={setRiskProfile}
              options={RISK_PROFILES.map(r => ({ value: r.id, label: r.label }))}
            />
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
          <div className="form-group flex items-end">
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
            <div className="info-box warning mb-4">
              <span className="i-lucide-alert-triangle w-3.5 h-3.5 mr-1.5" />
              Offline fallback (no <code>ANTHROPIC_API_KEY</code>) — equal-weighted,
              not model reasoning. The guardrail + trace are still real.
            </div>
          )}
          <h3>Proposed portfolio</h3>
          <p className="hint mt-1">
            {result.risk_profile} · {Number(result.capital_usdc).toLocaleString()} USDC ·
            model: <span className="mono">{result.model_id}</span>
          </p>
          {(() => {
            const agg = aggregateMetrics(result.selected, strategies)
            // Pick the shortest backtest window across selected strategies as the
            // honest period — projected $→$ can't extrapolate beyond the data we
            // actually have for the longest-tested strategy.
            const periods = (result.selected || [])
              .map(sel => strategies.find(x => x.id === sel.strategy_id))
              .filter(Boolean)
              .map(s => periodInYears(s.backtest_start, s.backtest_end))
              .filter(y => y != null)
            const minYears = periods.length ? Math.min(...periods) : null
            const principal = Number(result.capital_usdc) || 0
            const projEnd = (agg && agg.cagr != null && minYears != null)
              ? projectedEndValue(principal, agg.cagr, minYears)
              : null
            if (!agg) return null
            return (
              <div className="card-flat mt-3.5" style={{ padding: 14, background: 'rgba(255,255,255,0.03)' }}>
                <div className="label mb-2">Expected portfolio profile (weighted from per-strategy backtests)</div>
                <div className="grid grid-cols-3 gap-3 mb-2">
                  <div><div className="caption">Blended Sharpe</div><div style={{ fontWeight: 700, fontSize: '1.1rem' }}>{fmt(agg.sharpe_ratio)}</div></div>
                  <div><div className="caption">Blended CAGR</div><div className="positive" style={{ fontWeight: 700, fontSize: '1.1rem' }}>{fmtPct(agg.cagr)}</div></div>
                  <div><div className="caption">Blended Max DD</div><div className="negative" style={{ fontWeight: 700, fontSize: '1.1rem' }}>{agg.max_drawdown != null ? `−${fmtPct(agg.max_drawdown)}` : '—'}</div></div>
                </div>
                {minYears != null && projEnd != null && (
                  <div className="caption" style={{ lineHeight: 1.5 }}>
                    Over a shared backtest window of <strong>{minYears.toFixed(1)} yrs</strong>,{' '}
                    <strong>{fmtUsd(principal)}</strong> would have grown to{' '}
                    <strong style={{ color: 'var(--positive)' }}>{fmtUsd(projEnd)}</strong>{' '}
                    at the blended CAGR <span style={{ opacity: 0.7 }}>(historical, not a forecast — assumes no rebalance friction)</span>.
                  </div>
                )}
                {agg.partial && (
                  <div className="caption" style={{ marginTop: 6, color: 'var(--text-4)' }}>
                    * One or more selected strategies have missing backtest fields; aggregate weighted by available data only.
                  </div>
                )}
              </div>
            )
          })()}
          {result.overall_reasoning && (
            <p className="mt-3 leading-relaxed">{result.overall_reasoning}</p>
          )}
          <div className="mt-4 flex flex-col gap-3.5">
            {result.selected.map((s) => (
              <div key={s.strategy_id}>
                <div className="flex justify-between mb-1">
                  <strong>{s.paper_title || s.strategy_id}</strong>
                  <span className="mono">{(s.weight * 100).toFixed(1)}%</span>
                </div>
                <WeightBar weight={s.weight} />
                {s.rationale && <p className="hint mt-1.5">{s.rationale}</p>}
                {s.paper_citation && (
                  <p className="caption mt-0.5 flex items-center gap-1">
                    <span className="i-lucide-file-text" style={{width:12,height:12,flexShrink:0}} />
                    {s.paper_citation}
                  </p>
                )}
              </div>
            ))}
            <div>
              <div className="flex justify-between mb-1">
                <strong>USYC (cash-yield sleeve)</strong>
                <span className="mono">{(result.usyc_weight * 100).toFixed(1)}%</span>
              </div>
              <WeightBar weight={result.usyc_weight} accent="#10B981" />
            </div>
          </div>
          {result.risk_notes && (
            <p className="hint mt-4"><strong>Risk notes:</strong> {result.risk_notes}</p>
          )}
          {result.guardrail_notes?.length > 0 && (
            <div className="mt-4">
              <div className="label">Guardrail adjustments</div>
              <ul className="hint mt-1.5 pl-[18px]">
                {result.guardrail_notes.map((n, i) => <li key={i}>{n}</li>)}
              </ul>
            </div>
          )}
          {result.trace && (
            <div className="trace-card flex-col gap-1.5" style={{ marginTop: 18 }}>
              <div className="flex items-center justify-between gap-2">
                <span className="trace-id" style={{ fontSize: '0.85rem' }}>
                  {result.trace.decision_type} · {result.trace.trigger}
                </span>
                <span className={`badge ${result.trace.is_anchored ? 'tier-1' : ''}`} style={{ marginLeft: 0, flexShrink: 0 }}>
                  {result.trace.is_anchored ? 'anchored on-chain' : 'pending anchor'}
                </span>
              </div>
              <code className="mono" style={{ wordBreak: 'break-all', fontSize: '0.72rem', color: 'var(--text-3)', display: 'block' }}>
                {result.trace.trace_hash}
              </code>
              <p className="caption" style={{ margin: 0 }}>
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

// ── Library Table ─────────────────────────────────────────────

// Compact tabular view — replaces the old big-card grid. Dense, scannable,
// sortable. Click a row → expand inline detail (period + paper-claim delta
// + rigor metrics). One row per strategy; no visual hierarchy by status (the
// STATUS column does that job).

function StrategyRow({ s, isHighlighted, onOpenRigorExplainer, onOpenPassport }) {
  const [open, setOpen] = useState(isHighlighted)
  const rowRef = useRef(null)
  const years = periodInYears(s.backtest_start, s.backtest_end)
  const endValue = projectedEndValue(1000, s.cagr, years)
  const startStr = (s.backtest_start || '').slice(0, 10)
  const endStr = (s.backtest_end || '').slice(0, 10)
  const paperCite = [
    s.paper_authors?.[0]?.split(' ').pop(),
    s.paper_year && `(${s.paper_year})`,
  ].filter(Boolean).join(' ')

  const sharpeCI = s.sharpe_ci_95 != null ? s.sharpe_ci_95 : null
  const driftFlag = s.drift_detected === true

  useEffect(() => {
    if (isHighlighted && rowRef.current) {
      rowRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }, [isHighlighted])

  const rowStyle = {
    cursor: 'pointer',
    ...(isHighlighted ? { background: 'rgba(255,209,102,0.10)', outline: '1px solid var(--accent)' } : {}),
  }

  return (
    <>
      <tr ref={rowRef} className="lib-row cursor-pointer" onClick={() => setOpen(o => !o)} style={rowStyle}>
        <td className="font-semibold">
          <span className={`${open ? 'i-lucide-chevron-down' : 'i-lucide-chevron-right'} w-3 h-3 mr-1.5 text-[var(--text-4)] flex-shrink-0 inline-block`} />
          {s.paper_title}
        </td>
        <td className="caption">{paperCite || (s.paper_year ? `(${s.paper_year})` : '—')}</td>
        <td>
          <div className="flex items-center gap-1.5 flex-wrap">
            <span
              className={`tag ${statusTag(s.status)}`}
              title={s.status === 'pending_backtest' ? 'Generated but the rigor gate has not scored real metrics yet — DSR / PBO / OOS Sharpe pending a backtest run.' : undefined}
            >
              {statusLabel(s.status)}
            </span>
            {s.passes_rigor_gate === true && (
              <span className="i-lucide-check w-3.5 h-3.5 text-[var(--positive)]" title="Passes rigor gate" />
            )}
            {s.passes_rigor_gate === false && (
              <span className="i-lucide-x w-3.5 h-3.5 text-[var(--text-4)]" title="Does not pass rigor gate" />
            )}
            {driftFlag && (
              <span className="i-lucide-alert-triangle w-3.5 h-3.5 text-[#f59e0b]" title="Drift detected" />
            )}
          </div>
        </td>
        <td className="mono" style={{ textAlign: 'right' }}>
          {fmt(s.sharpe_ratio)}
          {sharpeCI && (
            <div style={{ fontSize: '0.68rem', color: 'var(--text-4)' }}>
              [{fmt(sharpeCI[0])}, {fmt(sharpeCI[1])}]
            </div>
          )}
          {s.dsr_p_value != null && (
            <div style={{ fontSize: '0.68rem', color: 'var(--text-4)' }}>
              (DSR p={s.dsr_p_value.toFixed(2)})
            </div>
          )}
        </td>
        <td className="mono positive" style={{ textAlign: 'right' }}>{fmtPct(s.cagr)}</td>
        <td className="mono negative" style={{ textAlign: 'right' }}>
          {s.max_drawdown != null ? `−${fmtPct(s.max_drawdown)}` : '—'}
          {s.pbo_score != null && (
            <div style={{ fontSize: '0.68rem', color: s.pbo_score > 0.5 ? 'var(--negative)' : 'var(--text-4)' }}>
              (PBO {s.pbo_score.toFixed(2)})
            </div>
          )}
        </td>
        <td className="mono" style={{ textAlign: 'right', color: 'var(--positive)' }}>{fmtUsd(endValue)}</td>
        <td className="caption" style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{years != null ? `${years.toFixed(1)} yrs` : '—'}</td>
      </tr>
      {open && (
        <tr className="lib-row-detail">
          <td colSpan={8} style={{ padding: '12px 18px', background: 'rgba(255,255,255,0.02)' }}>
            <div className="text-[0.82rem]" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 18 }}>
              <div>
                <div className="label mb-2">Methodology</div>
                <div className="body">{s.methodology_summary || '—'}</div>
              </div>
              <div>
                <div className="label mb-2">Source paper</div>
                <div className="body">"{s.paper_title}"</div>
                <div className="caption mt-2">
                  {s.paper_authors?.slice(0, 3).join(', ')}{s.paper_authors?.length > 3 ? ' et al.' : ''}
                  {s.paper_year ? ` (${s.paper_year})` : ''}
                  {s.paper_venue ? ` · ${s.paper_venue}` : ''}
                </div>
                {s.paper_arxiv_id && (
                  <a
                    href={`https://arxiv.org/abs/${s.paper_arxiv_id}`}
                    target="_blank" rel="noreferrer"
                    style={{ color: 'var(--accent)', fontSize: '0.78rem', marginTop: 6, display: 'inline-block' }}
                    onClick={(e) => e.stopPropagation()}
                  >
                    arxiv:{s.paper_arxiv_id} ↗
                  </a>
                )}
              </div>
              <div>
                <div className="label mb-2 flex items-center gap-2">
                  Rigor metrics
                  {onOpenRigorExplainer && (
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); onOpenRigorExplainer() }}
                      className="rigor-help-btn"
                      aria-label="What is the rigor gate?"
                      title="What is the rigor gate?"
                    >
                      ?
                    </button>
                  )}
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10 }}>
                  <div><div className="caption">DSR</div><div className="mono" style={{ fontWeight: 700 }}>{fmt(s.deflated_sharpe_ratio)}</div></div>
                  <div><div className="caption">PBO</div><div className="mono" style={{ fontWeight: 700 }}>{fmtPct(s.pbo_score)}</div></div>
                  <div><div className="caption">OOS Sharpe</div><div className="mono" style={{ fontWeight: 700 }}>{fmt(s.out_of_sample_sharpe)}</div></div>
                </div>
                {s.paper_claimed_sharpe != null && (
                  <div className="caption mt-2">
                    Paper claim: <strong>{fmt(s.paper_claimed_sharpe)}</strong> · Backtest: <strong>{fmt(s.sharpe_ratio)}</strong>
                    {s.sharpe_ratio != null && (
                      <span className={s.sharpe_ratio / s.paper_claimed_sharpe >= 0.5 ? 'positive' : 'negative'} style={{ marginLeft: 6 }}>
                        ({((s.sharpe_ratio / s.paper_claimed_sharpe) * 100).toFixed(0)}%)
                      </span>
                    )}
                  </div>
                )}
                {years != null && (
                  <div className="caption mt-1.5">
                    Window: <span className="mono">{startStr} → {endStr}</span>
                  </div>
                )}
              </div>
            </div>
            <div style={{ marginTop: 14, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {onOpenPassport && (
                <button
                  className="btn btn-primary btn-sm"
                  onClick={(e) => { e.stopPropagation(); onOpenPassport(s.id) }}
                  title="Open the full strategy passport"
                >
                  Open Passport →
                </button>
              )}
              <button
                className="btn btn-outline btn-sm"
                onClick={(e) => { e.stopPropagation(); downloadStrategy(s, 'json') }}
                title="Download this strategy as JSON"
              >
                Export JSON
              </button>
              <button
                className="btn btn-outline btn-sm"
                onClick={(e) => { e.stopPropagation(); downloadStrategy(s, 'csv') }}
                title="Download this strategy as CSV"
              >
                Export CSV
              </button>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

function StrategyTable({ strategies, emptyState, highlightStrategyId, onOpenRigorExplainer, onOpenPassport }) {
  if (!strategies.length) return emptyState
  return (
    <>
      {/* Table — renders on every screen size; horizontal-scrolls on narrow
          viewports. We previously kept a separate mobile card list with
          `md:hidden`, but UnoCSS doesn't generate the `hidden` utility in
          this build, so both views rendered on desktop and the page showed
          every strategy twice. One table beats one duplicate. */}
      <div className="overflow-x-auto rounded-lg border border-[var(--glass-border)]">
        <table className="lib-table" style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
          <thead>
            <tr style={{ background: 'rgba(255,255,255,0.03)', textAlign: 'left', borderBottom: '1px solid var(--glass-border)' }}>
              <th style={{ padding: '10px 14px' }}>Strategy</th>
              <th style={{ padding: '10px 14px' }}>Paper</th>
              <th style={{ padding: '10px 14px' }}>Status</th>
              <th style={{ padding: '10px 14px', textAlign: 'right' }}>Sharpe</th>
              <th style={{ padding: '10px 14px', textAlign: 'right' }}>CAGR</th>
              <th style={{ padding: '10px 14px', textAlign: 'right' }}>Max DD</th>
              <th style={{ padding: '10px 14px', textAlign: 'right' }}>$1k →</th>
              <th style={{ padding: '10px 14px', textAlign: 'right' }}>Period</th>
            </tr>
          </thead>
          <tbody>
            {strategies.map(s => (
              <StrategyRow
                key={s.id}
                s={s}
                isHighlighted={highlightStrategyId && s.id === highlightStrategyId}
                onOpenRigorExplainer={onOpenRigorExplainer}
                onOpenPassport={onOpenPassport}
              />
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

// ── Main export ───────────────────────────────────────────────

// Map a strategy_store row (fusion/architect output) into the same shape
// StrategyRow expects. Most metric fields are null on a pre-backtest
// hypothesis — the row will render those columns as "—", which is the honest
// signal that fusion-to-backtest hasn't run yet.
function coerceGenerated(row) {
  const sourcePapers = Array.isArray(row.source_papers) ? row.source_papers : []
  const firstPaper = sourcePapers[0]?.arxiv_id || ''
  const year = row.created_at ? new Date(row.created_at).getFullYear() : null
  // Honest status mapping: the generation pipeline persists status="rejected"
  // when its synthesis-time signal didn't pass, but for these rows no real
  // backtest has run yet (every metric column is null). Calling that
  // "Rejected" is misleading — surface it as "pending_backtest" so users
  // know what's actually happening: candidate generated, real metrics not
  // computed yet. The rigor gate verdict + DSR/PBO numbers still render
  // honestly on the strategy passport.
  const hasRealMetrics = row.dsr_value != null || row.sharpe_ratio != null
  const honestStatus = (!hasRealMetrics && row.status === 'rejected')
    ? 'pending_backtest'
    : (row.status || 'candidate')
  return {
    id: row.id,
    paper_title: row.strategy_name || '(unnamed)',
    paper_arxiv_id: firstPaper,
    paper_authors: [],
    paper_year: year,
    paper_venue: row.generation_method,
    methodology_summary: row.thesis || '',
    status: honestStatus,
    asset_universe: row.asset_universe || [],
    sharpe_ratio: null,
    cagr: null,
    max_drawdown: null,
    correlation_to_spy: null,
    deflated_sharpe_ratio: null,
    pbo_score: null,
    out_of_sample_sharpe: null,
    paper_claimed_sharpe: null,
    backtest_start: null,
    backtest_end: null,
    is_backtest_placeholder: true,
    passes_rigor_gate: null,
    dsr_p_value: null,
    sharpe_ci_95: null,
    drift_detected: null,
  }
}

export default function Strategies({ highlightStrategyId, defaultTab, onNavigate }) {
  const [examples, setExamples] = useState([])
  const [generated, setGenerated] = useState([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  // 'generated' is the first-class tab per product feedback — pushes user
  // toward Generate when empty.
  const [activeTab, setActiveTab] = useState(() => defaultTab || 'generated')
  // Page-level rigor explainer modal, opened from any row expansion's "?"
  // affordance. Single modal instance per page keeps state simple.
  const [rigorModalOpen, setRigorModalOpen] = useState(false)
  const openRigorExplainer = useCallback(() => setRigorModalOpen(true), [])

  // Deep-link to the strategy passport route — added in Phase 4.
  const openPassport = useCallback(
    (strategyId) => { if (onNavigate) onNavigate('strategy', { strategyId }) },
    [onNavigate]
  )

  // If we arrived via ?highlight=<id> and the strategy is only in Examples,
  // auto-switch to the Examples tab so the scrollIntoView lands a real row.
  useEffect(() => {
    if (!highlightStrategyId) return
    const inGenerated = generated.some(s => s.id === highlightStrategyId)
    const inExamples = examples.some(s => s.id === highlightStrategyId)
    if (!inGenerated && inExamples) setActiveTab('examples')
  }, [highlightStrategyId, generated, examples])

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError('')
    try {
      const [seedRes, genRes] = await Promise.allSettled([
        apiGet('/api/strategies/'),
        apiGet('/api/strategies/generated'),
      ])
      if (seedRes.status === 'fulfilled') {
        const sorted = [...(seedRes.value.strategies || [])].sort(
          (a, b) => STATUS_ORDER.indexOf(a.status) - STATUS_ORDER.indexOf(b.status)
        )
        setExamples(sorted)
      } else {
        setLoadError(seedRes.reason?.message || 'Failed to load examples')
      }
      if (genRes.status === 'fulfilled') {
        setGenerated((genRes.value.strategies || []).map(coerceGenerated))
      }
      // Generated tab failing is non-fatal — empty state is the honest fallback.
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  return (
    <div>
      <div className="mb-[18px]">
        <h2 className="serif text-[2rem] mb-2.5">Your Strategies</h2>
        <p className="body mb-1.5">
          Your strategies, plus a clearly-separated set of example strategies
          drawn from published research so you can learn the metric format.
        </p>
      </div>

      <div className="strat-filter-bar mb-4">
        <span
          className={`tag ${activeTab === 'generated' ? 'tag-accent' : 'tag-muted'}`}
          onClick={() => setActiveTab('generated')}
        >
          Generated ({generated.length})
        </span>
        <span
          className={`tag ${activeTab === 'examples' ? 'tag-accent' : 'tag-muted'}`}
          onClick={() => setActiveTab('examples')}
        >
          Examples ({examples.length})
        </span>
      </div>

      {loadError && (
        <div className="info-box warning mb-4">
          Couldn't load library: {loadError}
        </div>
      )}

      {activeTab === 'generated' && (() => {
        // Split generated strategies by rigor verdict so the main table only
        // shows what passed (the wedge: "the Library is a quality filter, not
        // a junk drawer"). Rejected candidates stay accessible in a collapsed
        // section below so the user can inspect *why* they failed — honest
        // rather than hidden, but visually de-prioritised.
        const passing = generated.filter(s => s.passes_rigor_gate === true)
        const rejected = generated.filter(s => s.passes_rigor_gate === false)
        const pending = generated.filter(s => s.passes_rigor_gate == null)
        const mainTableStrategies = [...passing, ...pending]
        return (
          <>
            <StrategyTable
              strategies={mainTableStrategies}
              highlightStrategyId={highlightStrategyId}
              onOpenRigorExplainer={openRigorExplainer}
              onOpenPassport={openPassport}
              emptyState={
                rejected.length > 0 ? (
                  <div className="card" style={{ padding: 22 }}>
                    <div className="label mb-2">No strategies have passed the rigor gate yet</div>
                    <p className="body" style={{ marginBottom: 10 }}>
                      You've generated {rejected.length} {rejected.length === 1 ? 'candidate' : 'candidates'}, but
                      none have cleared the rigor gate yet. Expand the <strong>Rejected</strong> section below
                      to see the rigor verdicts (most are <code>"return series too short"</code> — a longer
                      backtest window is needed for DSR / PBO to score them).
                    </p>
                    <p className="caption" style={{ color: 'var(--text-3)' }}>
                      The Library is a quality filter — only strategies that pass DSR + PBO + walk-forward OOS
                      + look-ahead audit are surfaced here. That's the wedge.
                    </p>
                  </div>
                ) : (
                  <div className="card" style={{ padding: 22 }}>
                    <div className="label mb-2">No generated strategies yet</div>
                    <p className="body" style={{ marginBottom: 10 }}>
                      Multi-paper fusion strategies you create from the{' '}
                      <a href="/generate" style={{ color: 'var(--accent)' }}>Generate</a> page will
                      appear here once they've been backtested + cleared the rigor gate.
                    </p>
                    <p className="caption" style={{ color: 'var(--text-3)' }}>
                      Generations in flight show in the agent activity feed on Portfolio and
                      Reasoning. They land in this table once the rigor gate clears.
                    </p>
                  </div>
                )
              }
            />
            {rejected.length > 0 && (
              <details className="mt-5">
                <summary
                  className="caption cursor-pointer select-none"
                  style={{
                    color: 'var(--text-3)',
                    padding: '10px 14px',
                    background: 'var(--bg-2)',
                    border: '1px solid var(--glass-border)',
                    borderRadius: 6,
                    listStyle: 'none',
                  }}
                >
                  Rejected ({rejected.length}) — did not pass the rigor gate. Click to inspect.
                </summary>
                <div style={{ marginTop: 12 }}>
                  <p className="caption mb-3" style={{ color: 'var(--text-3)', fontSize: '0.82rem' }}>
                    These candidates were generated but failed at least one rigor check (DSR, PBO,
                    walk-forward OOS, or look-ahead audit). Most rejections at this stage are
                    "return series too short" — the agent generated a strategy but there isn't
                    enough backtest history yet to compute DSR / PBO with statistical confidence.
                    A longer backtest window typically unlocks them.
                  </p>
                  <StrategyTable
                    strategies={rejected}
                    highlightStrategyId={highlightStrategyId}
                    onOpenRigorExplainer={openRigorExplainer}
                    onOpenPassport={openPassport}
                    emptyState={<p className="caption">No rejected strategies.</p>}
                  />
                </div>
              </details>
            )}
          </>
        )
      })()}

      {activeTab === 'examples' && (
        <>
          <div className="caption mb-3 text-[var(--text-3)] leading-relaxed">
            <strong>Example strategies</strong> — hand-curated single-paper implementations
            from published research. <em>Not</em> outputs of the fusion engine. Included
            so you can read a strategy card, understand the metrics, and see what a
            rigor-gate verdict looks like. They're also the candidate pool the curated-library
            path of Generate picks and weights from.
          </div>
          {loading && <div className="caption mb-4">Loading…</div>}
          {!loading && (
            <StrategyTable
              strategies={examples}
              highlightStrategyId={highlightStrategyId}
              onOpenRigorExplainer={openRigorExplainer}
              onOpenPassport={openPassport}
              emptyState={<p className="caption">No example strategies loaded.</p>}
            />
          )}
        </>
      )}

      {examples.some(s => s.is_backtest_placeholder) && (
        <div className="caption mt-4 text-[var(--text-4)]">
          * Pre-backtest hypothesis — empirical metrics pending evaluation. Real
          numbers replace the placeholder once the analytics engine runs.
        </div>
      )}

      {/* Page-level analytics panels — moved from Reasoning per page-roles-spec. */}
      <div className="mt-8 flex flex-col gap-4">
        <EfficientFrontier />
      </div>

      {/* Rigor Explainer modal (portal-rendered, page-level) */}
      {rigorModalOpen && createPortal(
        <div
          className="modal-overlay"
          onClick={() => setRigorModalOpen(false)}
          style={{ zIndex: 1000 }}
        >
          <div
            className="modal"
            onClick={e => e.stopPropagation()}
            style={{ maxWidth: 820, maxHeight: '85vh', overflowY: 'auto', width: '90vw' }}
          >
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 8 }}>
              <button
                type="button"
                onClick={() => setRigorModalOpen(false)}
                style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-4)' }}
                aria-label="Close"
              >
                <span className="i-lucide-x" style={{ width: 20, height: 20 }} />
              </button>
            </div>
            <RigorExplainer />
          </div>
        </div>,
        document.body,
      )}
    </div>
  )
}

// StrategyArchitect is intentionally NOT rendered here anymore. It moved to the
// standalone Generate page (/generate), where it belongs per the spine in
// docs/user-stories.md. It's named-exported so Generate.jsx can import it.
