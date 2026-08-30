import { useState, useEffect, useCallback, useRef } from 'react'
import { createPortal } from 'react-dom'
// EfficientFrontier + CorrelationMatrix deleted (Issue #383) — synthetic RNG data
import RigorExplainer from './RigorExplainer'
import RigorStrictnessControl, { levelLabel } from './RigorStrictnessControl'
import { useRigorStrictness, BADGE_LEVEL } from '../hooks/useRigorStrictness'
import useDialogFocus from '../hooks/useDialogFocus'
import { ROADMAP_SURFACES_ENABLED } from '../featureFlags.js'

import { apiGet, apiPost, apiDelete } from '../api'
import { compactCostCell } from '../generationCost.js'
import { signClass } from '../signClass.js'
import { strategies as ROADMAP_COPY } from '../roadmapCopyApp.js'

// A compact "deployable at your level" chip for a library row, driven by the
// strategy's min_passing_level (from the live gate) and the user's strictness.
function DeployabilityChip({ deploy, level }) {
  if (!deploy) return null
  if (deploy.blocked_by_floor) {
    return <span className="tag tag-negative" style={{ fontSize: '0.66rem' }} title="Fails an always-on correctness floor — cannot deploy at any level">blocked</span>
  }
  const min = deploy.min_passing_level
  if (min == null) {
    return <span className="tag tag-muted" style={{ fontSize: '0.66rem' }} title="Does not pass the rigor gate even at the most permissive level">not deployable</span>
  }
  if (min <= level) {
    const label = min > BADGE_LEVEL ? `deployable @ ${levelLabel(null, min)}+` : 'deployable'
    return <span className="tag tag-positive" style={{ fontSize: '0.66rem' }} title={`Passes at your strictness (level ${level})`}>{label}</span>
  }
  return <span className="tag tag-accent" style={{ fontSize: '0.66rem' }} title={`Raise your strictness to level ${min} to deploy`}>needs {levelLabel(null, min)}</span>
}

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

function statusTag(status, passesRigor) {
  if (status === 'live' && passesRigor === false) return 'tag-muted'
  if (status === 'live') return 'tag-positive'
  if (status === 'validated') return 'tag-accent'
  if (status === 'pending_backtest') return 'tag-warning'
  return 'tag-muted'
}

function statusLabel(status, passesRigor) {
  if (status === 'live' && passesRigor === false) return 'Reference only — gate failed'
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

// ── Library Table ─────────────────────────────────────────────
//
// (The grid-card view's `BacktestHorizon` helper was deleted in #1361: it was
// never mounted anywhere in src/ or test/, and it carried the identical
// hard-coded `var(--positive)` bug this issue fixes below. Dead code that
// mis-colours a loss is worse than no code — resurrect it wired to
// `signClass` if the grid-card view comes back.)

// Compact tabular view — replaces the old big-card grid. Dense, scannable,
// sortable. Click a row → expand inline detail (period + paper-claim delta
// + rigor metrics). One row per strategy; no visual hierarchy by status (the
// STATUS column does that job).

function StrategyRow({ s, isHighlighted, onOpenRigorExplainer, onOpenPassport, deploy, level, extraActions }) {
  const [open, setOpen] = useState(isHighlighted)
  const rowRef = useRef(null)
  const years = periodInYears(s.backtest_start, s.backtest_end)
  const principal = 1000
  const endValue = projectedEndValue(principal, s.cagr, years)
  const startStr = (s.backtest_start || '').slice(0, 10)
  const endStr = (s.backtest_end || '').slice(0, 10)
  const paperCite = [
    s.paper_authors?.[0]?.split(' ').pop(),
    s.paper_year && `(${s.paper_year})`,
  ].filter(Boolean).join(' ')

  // Real API fields (backend/archimedes/api/schemas.py) — the singular-CI
  // and drift-boolean fields this used to read never existed in any API
  // response (#1361).
  const sharpeCI = s.sharpe_ci_lower != null && s.sharpe_ci_upper != null ? [s.sharpe_ci_lower, s.sharpe_ci_upper] : null
  const detailId = `lib-detail-${s.id}`
  // Absence is the point: a strategy nothing measured gets an em-dash and a
  // tooltip that says so, never a zero (#1326).
  const genCost = compactCostCell(s.generation_cost)

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
          {/* The disclosure is a real <button> in the first cell rather than a
              bare onClick on the <tr>: App.css hides the keyboard-accessible
              card list above 768px, so on desktop a keyboard-only user could
              open no strategy's detail panel at all — and with it none of
              "Open Passport", the exports, the source links or the DSR/PBO
              numbers, which live only in the expanded row (2.1.1 / 4.1.2).
              The row keeps its onClick as a mouse convenience; the button
              stops propagation so one activation is one toggle.
              aria-controls is conditional on `open` (same pattern as
              CustomSelect's listbox): the <tr id={detailId}> below only
              exists in the DOM while open, so an unconditional aria-controls
              pointed at a nonexistent id on every collapsed row — a #1318
              residual of the #1311/#1319 pass. */}
          <button
            type="button"
            className="lib-row-toggle"
            aria-expanded={open}
            aria-controls={open ? detailId : undefined}
            onClick={(e) => { e.stopPropagation(); setOpen(o => !o) }}
          >
            <span aria-hidden="true" className={`${open ? 'i-lucide-chevron-down' : 'i-lucide-chevron-right'} w-3 h-3 mr-1.5 text-[var(--text-4)] flex-shrink-0 inline-block`} />
            {s.paper_title}
          </button>
          {(s.papers || []).length > 1 && (
            <span
              className="tag tag-accent"
              style={{ fontSize: '0.68rem', marginLeft: 6, verticalAlign: 'middle', padding: '1px 5px' }}
              title={`Fused from ${s.papers.length} papers`}
            >
              {s.papers.length} papers
            </span>
          )}
        </td>
        <td className="caption">{paperCite || (s.paper_year ? `(${s.paper_year})` : '—')}</td>
        <td>
          <div className="flex items-center gap-1.5 flex-wrap">
            <span
              className={`tag ${statusTag(s.status, s.passes_rigor_gate)}`}
              title={s.status === 'pending_backtest' ? 'Generated but the rigor gate has not scored real metrics yet — DSR / PBO / OOS Sharpe pending a backtest run.' : undefined}
            >
              {statusLabel(s.status, s.passes_rigor_gate)}
            </span>
            {/* These UnoCSS icons render as a CSS mask on an empty <span>, so
                without role/aria-label they contribute nothing to the
                accessible name tree and `title` on a bare span is not
                reliably exposed — the rigor-gate verdict, the fact that
                decides whether a strategy may be deployed, was sighted-only
                (1.1.1). */}
            {s.passes_rigor_gate === true && (
              <span role="img" aria-label="Passes rigor gate" className="i-lucide-check w-3.5 h-3.5 text-[var(--positive)]" title="Passes rigor gate" />
            )}
            {s.passes_rigor_gate === false && (
              <span role="img" aria-label="Does not pass rigor gate" className="i-lucide-x w-3.5 h-3.5 text-[var(--text-4)]" title="Does not pass rigor gate" />
            )}
            <DeployabilityChip deploy={deploy} level={level} />
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
        <td className={`mono ${signClass(s.cagr)}`} style={{ textAlign: 'right' }}>{fmtPct(s.cagr)}</td>
        <td className="mono negative" style={{ textAlign: 'right' }}>
          {s.max_drawdown != null ? `−${fmtPct(s.max_drawdown)}` : '—'}
          {/* Same defect: crossing the 0.5 overfitting threshold was signalled
              only by the colour swap to --negative (1.4.1). */}
          {s.pbo_score != null && (
            <div style={{ fontSize: '0.68rem', color: s.pbo_score > 0.5 ? 'var(--negative)' : 'var(--text-4)' }}>
              (PBO {s.pbo_score.toFixed(2)}{s.pbo_score > 0.5 && <span aria-hidden="true"> ⚠</span>})
              {s.pbo_score > 0.5 && (
                <span className="sr-only"> — above the 0.50 overfitting threshold</span>
              )}
            </div>
          )}
        </td>
        <td className={`mono ${signClass(endValue != null ? endValue - principal : null)}`} style={{ textAlign: 'right' }}>
          {fmtUsd(endValue)}
          {/* fmtPct prepends '-' for a losing CAGR, so colour alone is never the
              only signal there; fmtUsd never emits a sign (endValue can't go
              below 0), so this cell needs its own text alternative (1.4.1). */}
          {endValue != null && endValue < principal && (
            <span className="sr-only"> — below the {fmtUsd(principal)} starting principal</span>
          )}
        </td>
        <td className="caption" style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{years != null ? `${years.toFixed(1)} yrs` : '—'}</td>
        <td
          className={genCost.measured ? 'mono' : 'caption'}
          style={{ textAlign: 'right', whiteSpace: 'nowrap', color: genCost.measured ? undefined : 'var(--text-4)' }}
          title={genCost.title}
        >{genCost.label}</td>
      </tr>
      {open && (
        <tr className="lib-row-detail" id={detailId}>
          <td colSpan={9} style={{ padding: '12px 18px', background: 'var(--glass)' }}>
            <StrategyDetailContent
              s={s}
              onOpenRigorExplainer={onOpenRigorExplainer}
              onOpenPassport={onOpenPassport}
              extraActions={extraActions}
              years={years}
              startStr={startStr}
              endStr={endStr}
            />
          </td>
        </tr>
      )}
    </>
  )
}

// Shared "expanded" detail content — methodology / source paper(s) / rigor
// metrics / export actions. Used by both the desktop table row's expanded
// <tr> and the mobile card list's expanded panel, so the two layouts never
// drift out of sync with each other.
function StrategyDetailContent({ s, onOpenRigorExplainer, onOpenPassport, extraActions, years, startStr, endStr }) {
  return (
    <>
      <div className="text-[0.82rem]" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 18 }}>
        <div>
          <div className="label mb-2">Methodology</div>
          <div className="body">{s.methodology_summary || '—'}</div>
        </div>
        <div>
          {(s.papers || []).length > 1 ? (
            <>
              <div className="label mb-2">
                Fused from {s.papers.length} papers
              </div>
              <div className="flex flex-col gap-2">
                {s.papers.map((p, idx) => (
                  <div key={p.arxiv_id || idx}>
                    <div className="body" style={{ fontStyle: 'italic' }}>
                      "{p.title || p.arxiv_id || '—'}"
                    </div>
                    {p.arxiv_id && (
                      <a
                        href={`https://arxiv.org/abs/${p.arxiv_id}`}
                        target="_blank" rel="noreferrer"
                        style={{ color: 'var(--accent)', fontSize: '0.75rem', marginTop: 3, display: 'inline-block' }}
                        onClick={(e) => e.stopPropagation()}
                      >
                        arxiv:{p.arxiv_id} ↗
                      </a>
                    )}
                  </div>
                ))}
              </div>
            </>
          ) : (
            <>
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
            </>
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
              {/* The pass/fail judgement against the 50% replication threshold
                  used to live in the green/red class alone — "(43%)" and
                  "(97%)" rendered identically to a colourblind reader (1.4.1).
                  The ✓/✗ glyph carries it now, with the threshold spelled out
                  for assistive tech; colour stays as reinforcement. */}
              {s.sharpe_ratio != null && (() => {
                const ratio = s.paper_claimed_sharpe > 0.01 ? s.sharpe_ratio / s.paper_claimed_sharpe : null
                const replicated = ratio != null && ratio >= 0.5
                return (
                  <span className={replicated ? 'positive' : 'negative'} style={{ marginLeft: 6 }}>
                    <span aria-hidden="true">{replicated ? '✓' : '✗'}</span>{' '}
                    ({ratio != null ? `${(ratio * 100).toFixed(0)}%` : '—'})
                    <span className="sr-only">
                      {' '}
                      {ratio == null
                        ? 'replication ratio unavailable'
                        : replicated
                          ? 'of the paper claim — at or above the 50% replication threshold'
                          : 'of the paper claim — below the 50% replication threshold'}
                    </span>
                  </span>
                )
              })()}
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
        {extraActions?.(s)}
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
    </>
  )
}

// ── Library Cards (mobile) ──────────────────────────────────────
//
// Same data as StrategyRow, collapsed into a stacked label:value card.
// Visibility is toggled with a plain CSS media query (.lib-table-wrap /
// .lib-cards in App.css) rather than a UnoCSS `hidden md:block` utility —
// the prior attempt at a card layout used UnoCSS's `hidden` utility, which
// this build doesn't generate, so both views rendered simultaneously on
// desktop and every strategy showed twice. A plain media query has no such
// build-tool dependency.
function StrategyCard({ s, isHighlighted, onOpenRigorExplainer, onOpenPassport, deploy, level, extraActions }) {
  const [open, setOpen] = useState(isHighlighted)
  const cardRef = useRef(null)
  const years = periodInYears(s.backtest_start, s.backtest_end)
  const startStr = (s.backtest_start || '').slice(0, 10)
  const endStr = (s.backtest_end || '').slice(0, 10)
  const genCost = compactCostCell(s.generation_cost)

  useEffect(() => {
    if (isHighlighted && cardRef.current) {
      cardRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }, [isHighlighted])

  return (
    <div
      ref={cardRef}
      className="lib-card"
      style={isHighlighted ? { background: 'rgba(255,209,102,0.10)', outline: '1px solid var(--accent)' } : undefined}
      onClick={() => setOpen(o => !o)}
      role="button"
      tabIndex={0}
      aria-expanded={open}
      onKeyDown={e => {
        if (e.key === 'Enter' || e.key === ' ') {
          if (e.key === ' ') e.preventDefault()
          setOpen(o => !o)
        }
      }}
    >
      <div className="lib-card-header">
        <span className={`${open ? 'i-lucide-chevron-down' : 'i-lucide-chevron-right'} w-3.5 h-3.5 text-[var(--text-4)] flex-shrink-0`} />
        <div className="lib-card-title">
          {s.paper_title}
          {(s.papers || []).length > 1 && (
            <span className="tag tag-accent" style={{ fontSize: '0.66rem', marginLeft: 6 }} title={`Fused from ${s.papers.length} papers`}>
              {s.papers.length} papers
            </span>
          )}
        </div>
      </div>
      <div className="lib-card-badges">
        <span
          className={`tag ${statusTag(s.status, s.passes_rigor_gate)}`}
          title={s.status === 'pending_backtest' ? 'Generated but the rigor gate has not scored real metrics yet — DSR / PBO / OOS Sharpe pending a backtest run.' : undefined}
        >
          {statusLabel(s.status, s.passes_rigor_gate)}
        </span>
        <DeployabilityChip deploy={deploy} level={level} />
      </div>
      <div className="lib-card-stats">
        <div><div className="caption">Sharpe</div><div className="mono">{fmt(s.sharpe_ratio)}</div></div>
        <div><div className="caption">CAGR</div><div className={`mono ${signClass(s.cagr)}`}>{fmtPct(s.cagr)}</div></div>
        <div><div className="caption">Max DD</div><div className="mono negative">{s.max_drawdown != null ? `−${fmtPct(s.max_drawdown)}` : '—'}</div></div>
        <div title={genCost.title}><div className="caption">Gen tokens</div><div className={genCost.measured ? 'mono' : 'caption'}>{genCost.label}</div></div>
      </div>
      {open && (
        <div className="lib-card-detail" onClick={(e) => e.stopPropagation()}>
          <StrategyDetailContent
            s={s}
            onOpenRigorExplainer={onOpenRigorExplainer}
            onOpenPassport={onOpenPassport}
            extraActions={extraActions}
            years={years}
            startStr={startStr}
            endStr={endStr}
          />
        </div>
      )}
    </div>
  )
}

function StrategyTable({ strategies, emptyState, highlightStrategyId, onOpenRigorExplainer, onOpenPassport, deployMap, level, extraActions }) {
  if (!strategies.length) return emptyState
  return (
    <>
      {/* Table — visible ≥769px, horizontal-scrolls if it still doesn't fit.
          Card list below is the mobile-native replacement (≤768px). Visibility
          is toggled by a plain CSS media query (App.css .lib-table-wrap /
          .lib-cards) — NOT a UnoCSS `hidden` utility, which this build
          doesn't generate (both views rendered on desktop in the prior
          attempt and every strategy showed twice). */}
      <div className="lib-table-wrap overflow-x-auto rounded-lg border border-[var(--glass-border)]">
        <table className="lib-table" style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
          <thead>
            <tr style={{ background: 'var(--glass)', textAlign: 'left', borderBottom: '1px solid var(--glass-border)' }}>
              <th style={{ padding: '10px 14px' }}>Strategy</th>
              <th style={{ padding: '10px 14px' }}>Paper</th>
              <th style={{ padding: '10px 14px' }}>Status</th>
              <th style={{ padding: '10px 14px', textAlign: 'right' }}>Sharpe</th>
              <th style={{ padding: '10px 14px', textAlign: 'right' }}>CAGR</th>
              <th style={{ padding: '10px 14px', textAlign: 'right' }}>Max DD</th>
              <th style={{ padding: '10px 14px', textAlign: 'right' }}>$1k →</th>
              <th style={{ padding: '10px 14px', textAlign: 'right' }}>Period</th>
              {/* Generation cost (#1326) — total tokens is the design call: it's
                  the term that scales with the model and the one #1217 exists to
                  pin down, while wall time is dominated by backtests and moves
                  with whatever else the worker is doing. Wall time + dominant
                  stage ride in the cell's tooltip. */}
              <th
                style={{ padding: '10px 14px', textAlign: 'right' }}
                title="Tokens consumed by the generation run that produced this strategy. A raw measurement — never converted to dollars."
              >Gen tokens</th>
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
                deploy={deployMap?.[s.id]}
                level={level}
                extraActions={extraActions}
              />
            ))}
          </tbody>
        </table>
      </div>

      <div className="lib-cards">
        {strategies.map(s => (
          <StrategyCard
            key={s.id}
            s={s}
            isHighlighted={highlightStrategyId && s.id === highlightStrategyId}
            onOpenRigorExplainer={onOpenRigorExplainer}
            onOpenPassport={onOpenPassport}
            deploy={deployMap?.[s.id]}
            level={level}
            extraActions={extraActions}
          />
        ))}
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
  // rigor_verdict is the real shape the backend persists (see
  // StrategyRecord.to_dict() in backend/archimedes/models/strategy_store.py
  // and generation_pipeline.py ~line 1272): {dsr, dsr_p_value, pbo,
  // oos_sharpe, passing}. Read from there rather than nonexistent top-level
  // fields — fall back to null only when rigor_verdict itself is absent.
  const verdict = row.rigor_verdict || null
  const hasRealMetrics = verdict?.dsr != null || row.sharpe_ratio != null
  // Honest status mapping: the generation pipeline persists status="rejected"
  // when its synthesis-time signal didn't pass, but for these rows no real
  // backtest has run yet (every metric column is null). Calling that
  // "Rejected" is misleading — surface it as "pending_backtest" so users
  // know what's actually happening: candidate generated, real metrics not
  // computed yet. The rigor gate verdict + DSR/PBO numbers still render
  // honestly on the strategy passport.
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
    deflated_sharpe_ratio: verdict?.dsr ?? null,
    pbo_score: verdict?.pbo ?? null,
    out_of_sample_sharpe: verdict?.oos_sharpe ?? null,
    paper_claimed_sharpe: null,
    backtest_start: null,
    backtest_end: null,
    is_backtest_placeholder: true,
    passes_rigor_gate: verdict ? Boolean(verdict.passing) : null,
    dsr_p_value: verdict?.dsr_p_value ?? null,
    // No real backtest has run yet on a pre-backtest hypothesis, so there is
    // no CI to report — honestly null, on the real field names (#1361).
    sharpe_ci_lower: null,
    sharpe_ci_upper: null,
    // Durable generation-cost record (#1326) served by /api/strategies/generated.
    // Absent for anything generated before the meter — passed through as null so
    // the cost column renders "not measured" rather than a fabricated zero.
    generation_cost: row.generation_cost ?? null,
  }
}

export default function Strategies({ highlightStrategyId, defaultTab, onNavigate }) {
  const [examples, setExamples] = useState([])
  const [generated, setGenerated] = useState([])
  const [published, setPublished] = useState([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  // Per-feed failure messages (#1356): genRes/gateRes/publishedRes failing
  // used to be silently swallowed — the generated panel painted "No
  // generated strategies yet" (a different, false claim from "the fetch
  // failed"), every deployability chip vanished with no signal, and
  // Published painted "Nothing published yet". Each gets its own visible,
  // near-the-panel error instead.
  const [genError, setGenError] = useState('')
  const [gateError, setGateError] = useState('')
  const [publishedError, setPublishedError] = useState('')
  // Per-user rigor strictness (shared with the Passport slider via localStorage).
  const [level, setLevel] = useRigorStrictness()
  // {strategy_id: {min_passing_level, blocked_by_floor}} from the live gate —
  // strictness-independent, so we fetch once and re-annotate rows client-side as
  // the slider moves. Curated strategies resolve here; generated ones fall back
  // to their badge boolean (no chip).
  const [deployMap, setDeployMap] = useState({})
  // 'generated' is the first-class tab per product feedback — pushes user
  // toward Generate when empty. Published is a hidden roadmap surface
  // (#1266/#1324) — a ?tab=published deep link must not land there with the
  // flag off, since the tab button that would normally set this is gone too.
  const [activeTab, setActiveTab] = useState(() => {
    if (defaultTab === 'published' && !ROADMAP_SURFACES_ENABLED) return 'generated'
    return defaultTab || 'generated'
  })
  // Page-level rigor explainer modal, opened from any row expansion's "?"
  // affordance. Single modal instance per page keeps state simple.
  const [rigorModalOpen, setRigorModalOpen] = useState(false)
  const openRigorExplainer = useCallback(() => setRigorModalOpen(true), [])
  const closeRigorExplainer = useCallback(() => setRigorModalOpen(false), [])
  const rigorModalRef = useDialogFocus(rigorModalOpen, { onEscape: closeRigorExplainer })

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
    setGenError('')
    setGateError('')
    setPublishedError('')
    try {
      // Published is a hidden roadmap surface (#1266/#1324) — its fetch must
      // not fire with the flag off, not just its tab stay unclickable.
      const [seedRes, genRes, gateRes, publishedRes] = await Promise.allSettled([
        // limit=100 (the backend's max): the endpoint defaults to 20 of the
        // 34-strategy curated library, alphabetically — which structurally hid
        // every currently-passing strategy (all sort past row 20). Found in
        // the 2026-08-30 external product review ("0 of 20 examples pass").
        apiGet('/api/strategies/?limit=100'),
        apiGet('/api/strategies/generated'),
        apiGet('/api/selection-bias/gate'),
        ROADMAP_SURFACES_ENABLED ? apiGet('/api/marketplace/my-published') : Promise.resolve([]),
      ])
      if (seedRes.status === 'fulfilled') {
        const sorted = [...(seedRes.value.strategies || [])].sort(
          (a, b) => STATUS_ORDER.indexOf(a.status) - STATUS_ORDER.indexOf(b.status)
        )
        setExamples(sorted)
        // A 2xx response can still mean the fetch failed: the backend swallows a
        // provider exception into `degraded: true` with an empty list rather than
        // a 500 (#1356's own fix, applied to this same route) so a fulfilled
        // promise is not proof of a real empty library — check the flag before
        // trusting the empty state.
        if (seedRes.value.degraded) {
          setLoadError(seedRes.value.degraded_reason || 'Failed to load examples')
        }
      } else {
        setLoadError(seedRes.reason?.message || 'Failed to load examples')
      }
      if (genRes.status === 'fulfilled') {
        setGenerated((genRes.value.strategies || []).map(coerceGenerated))
        // Same fulfilled-but-degraded shape as seedRes above (#1356 review
        // round 2): the backend swallows a store exception into a 200 with
        // `degraded: true` rather than a 500, so a fulfilled promise alone
        // is not proof the fetch actually succeeded.
        if (genRes.value.degraded) {
          setGenError(genRes.value.degraded_reason || 'Failed to load generated strategies')
        }
      } else {
        setGenError(genRes.reason?.message || 'Failed to load generated strategies')
      }
      if (gateRes.status === 'fulfilled') {
        const map = {}
        for (const r of gateRes.value.strategies || []) {
          map[r.strategy_id] = { min_passing_level: r.min_passing_level, blocked_by_floor: r.blocked_by_floor }
        }
        setDeployMap(map)
      } else {
        setGateError(gateRes.reason?.message || 'Failed to load deployability status')
      }
      if (publishedRes.status === 'fulfilled') {
        setPublished(Array.isArray(publishedRes.value) ? publishedRes.value : [])
      } else {
        setPublishedError(publishedRes.reason?.message || 'Failed to load published strategies')
      }
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  return (
    <div className="strategies-page">
      <header className="app-page-heading">
        <p className="app-eyebrow">Evidence library</p>
        <h1>Your strategies</h1>
        <p>
          Your strategies, plus a clearly-separated set of example strategies
          drawn from published research so you can learn the metric format.
        </p>
      </header>

      <div className="mb-5">
        <RigorStrictnessControl level={level} onChange={setLevel} />
      </div>

      {/* Real <button>s, not click-only <span>s: activeTab defaults to
          'generated', so a keyboard-only user was permanently pinned to that
          one view and could never reach Examples or Published, and nothing in
          the accessibility tree said which view was active (2.1.1 / 4.1.2).
          Same shape Leaderboard.jsx:239 already uses. */}
      <div className="strat-filter-bar mb-4" role="group" aria-label="Strategy view">
        <button
          type="button"
          className={`tag ${activeTab === 'generated' ? 'tag-accent' : 'tag-muted'}`}
          aria-pressed={activeTab === 'generated'}
          onClick={() => setActiveTab('generated')}
        >
          Generated ({genError ? '—' : generated.length})
        </button>
        <button
          type="button"
          className={`tag ${activeTab === 'examples' ? 'tag-accent' : 'tag-muted'}`}
          aria-pressed={activeTab === 'examples'}
          onClick={() => setActiveTab('examples')}
        >
          Examples ({loadError ? '—' : examples.length})
        </button>
        {/* Published leads into the marketplace surface #1266 hid — hides
            with it (#1324). Anti-goal: gating render alone without gating
            the fetch above would still hit the hidden API every load. */}
        {ROADMAP_SURFACES_ENABLED && (
          <button
            type="button"
            className={`tag ${activeTab === 'published' ? 'tag-accent' : 'tag-muted'}`}
            aria-pressed={activeTab === 'published'}
            onClick={() => setActiveTab('published')}
          >
            Published ({published.length})
          </button>
        )}
      </div>

      {loadError && (
        <div className="info-box warning mb-4">
          Couldn't load library: {loadError}{' '}
          <button type="button" className="btn btn-sm btn-outline" onClick={load} style={{ marginLeft: 4 }}>
            Retry
          </button>
        </div>
      )}

      {activeTab === 'generated' && (
        <>
          {/* The gate feed is independent of the generated-strategies feed: a
              gate failure alone used to leave deployMap at {} with no
              signal — DeployabilityChip short-circuits to `null` for every
              row (:14), so every chip silently vanished (#1356). This banner
              is the visible signal that replaces that silence, near the
              chips it describes. */}
          {gateError && (
            <div className="info-box warning mb-3">
              Deployability status unavailable: {gateError}. Chips below may not reflect the live gate.{' '}
              <button type="button" className="btn btn-sm btn-outline" onClick={load} style={{ marginLeft: 4 }}>
                Retry
              </button>
            </div>
          )}
          {loading ? (
            <div className="caption mb-4">Loading…</div>
          ) : genError ? (
            <div className="info-box warning mb-4">
              Couldn't load generated strategies: {genError}{' '}
              <button type="button" className="btn btn-sm btn-outline" onClick={load} style={{ marginLeft: 4 }}>
                Retry
              </button>
            </div>
          ) : (() => {
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
              deployMap={deployMap}
              level={level}
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
                      The Library is a quality filter — only strategies that pass DSR + PBO + chronological OOS
                      + look-ahead audit are surfaced here. That's the wedge.
                    </p>
                  </div>
                ) : (
                  <div className="card" style={{ padding: 22 }}>
                    <div className="label mb-2">No generated strategies yet</div>
                    <p className="body" style={{ marginBottom: 10 }}>
                      Multi-paper fusion strategies you create from the{' '}
                      <a href="/app/generate" style={{ color: 'var(--accent)' }}>Generate</a> page will
                      appear here once they've been backtested + cleared the rigor gate.
                    </p>
                    <p className="caption" style={{ color: 'var(--text-3)' }}>
                      {ROADMAP_SURFACES_ENABLED
                        ? ROADMAP_COPY.emptyLibraryNoteRoadmap
                        : 'Generations in flight show in the agent activity feed on Reasoning. They land in this table once the rigor gate clears.'}
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
                    background: 'var(--surface-2)',
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
                    chronological OOS, or look-ahead audit). Most rejections at this stage are
                    "return series too short" — the agent generated a strategy but there isn't
                    enough backtest history yet to compute DSR / PBO with statistical confidence.
                    A longer backtest window typically unlocks them.
                  </p>
                  <StrategyTable
                    strategies={rejected}
                    highlightStrategyId={highlightStrategyId}
                    onOpenRigorExplainer={openRigorExplainer}
                    onOpenPassport={openPassport}
                    deployMap={deployMap}
                    level={level}
                    emptyState={<p className="caption">No rejected strategies.</p>}
                  />
                </div>
              </details>
            )}
              </>
            )
          })()}
        </>
      )}

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
          {/* Gated on !loadError, matching the Published branch below (#1356
              review round 2): loadError is set from the seed route's own
              `degraded` flag on a *fulfilled* response (see load() above), so
              without this gate a degraded fetch painted the loadError banner
              at :815 AND the false "No example strategies loaded." empty
              state simultaneously — the exact claim #1356 was filed against. */}
          {!loading && !loadError && (
            <StrategyTable
              strategies={examples}
              highlightStrategyId={highlightStrategyId}
              onOpenRigorExplainer={openRigorExplainer}
              onOpenPassport={openPassport}
              deployMap={deployMap}
              level={level}
              emptyState={<p className="caption">No example strategies loaded.</p>}
            />
          )}
        </>
      )}

      {activeTab === 'published' && ROADMAP_SURFACES_ENABLED && (
        <>
          <div className="caption mb-3 text-[var(--text-3)] leading-relaxed">
            Strategies you have published to the on-chain marketplace. Subscribers
            you approve can mirror trades from your vault.
          </div>
          {loading && <div className="caption mb-4">Loading…</div>}
          {!loading && publishedError && (
            <div className="info-box warning mb-4">
              Couldn't load published strategies: {publishedError}{' '}
              <button type="button" className="btn btn-sm btn-outline" onClick={load} style={{ marginLeft: 4 }}>
                Retry
              </button>
            </div>
          )}
          {!loading && !publishedError && (
            <StrategyTable
              strategies={published}
              highlightStrategyId={highlightStrategyId}
              onOpenPassport={openPassport}
              extraActions={(row) =>
                row.status === 'running' ? (
                  <>
                    <button
                      type="button"
                      className="btn btn-sm btn-outline"
                      onClick={async () => {
                        const res = await apiPost(`/api/marketplace/publish/${row.strategy_id}/withdraw`, {})
                        if (res.status === 'withdrawn') {
                          alert(`Withdrew ${res.amount_raw / 1e6} USDC`)
                        } else if (res.status === 'nothing_to_withdraw') {
                          alert('Nothing to withdraw yet')
                        }
                        load()
                      }}
                      style={{ marginLeft: 8 }}
                    >
                      Withdraw
                    </button>
                    <button
                      type="button"
                      className="btn btn-sm btn-outline-danger"
                      onClick={async () => {
                        if (window.confirm(`Stop publishing "` + (row.strategy_name || row.strategy_id) + `"?`)) {
                          await apiDelete(`/api/marketplace/publish/${row.strategy_id}`)
                          load()
                        }
                      }}
                      style={{ marginLeft: 8 }}
                    >
                      Stop
                    </button>
                  </>
                ) : null
              }
              emptyState={
                <div className="card" style={{ padding: 22 }}>
                  <div className="label mb-2">Nothing published yet</div>
                  <p className="body" style={{ marginBottom: 10 }}>
                    Strategies you publish from the strategy passport page will
                    appear here. Publishing lets subscribers mirror your trades
                    on-chain.
                  </p>
                </div>
              }
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

      {/* EfficientFrontier + CorrelationMatrix removed (Issue #383) — synthetic RNG data */}

      {/* Rigor Explainer modal (portal-rendered, page-level).
          It had no dialog role, no accessible name, no Escape handler and no
          focus management — and because the portal appends after #root its
          Close button was the LAST focus stop in the document, so reaching it
          meant tabbing the whole Library page underneath a blurred overlay
          (2.4.3 / 4.1.2). */}
      {rigorModalOpen && createPortal(
        <div
          className="modal-overlay"
          onClick={() => setRigorModalOpen(false)}
          style={{ zIndex: 1000 }}
        >
          <div
            ref={rigorModalRef}
            tabIndex={-1}
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="rigor-explainer-title"
            onClick={e => e.stopPropagation()}
            style={{ maxWidth: 820, maxHeight: '85vh', overflowY: 'auto', width: '90vw' }}
          >
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 8 }}>
              <button
                type="button"
                onClick={closeRigorExplainer}
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
