// Renders the result of a fusion-mode generation job per
// docs/specs/phase8-9-landing-and-fusion-spec.md § Phase 9.
//
// Two shapes to handle:
//   1. Pre-backtest hypothesis — result has prose fields (thesis, fusion_reasoning,
//      novelty_rationale, risk_notes) but no backtest / rigor. We render the prose
//      and surface an honest "pre-backtest hypothesis" note.
//   2. Backtested + rigor-gated — result also has backtest { sharpe_ratio, cagr, ... }
//      and rigor { passing, dsr, dsr_p_value, pbo_score, oos_sharpe, look_ahead_clean }.
//      We render metrics + verdict + deploy CTA (gated on rigor.passing).

export default function FusionResult({ result, onNavigate }) {
  if (!result) return null

  const {
    strategy_name,
    thesis,
    source_arxiv_ids = [],
    fusion_reasoning,
    novelty_rationale,
    risk_notes,
    backtest,
    rigor,
    strategy_id,
    market_context_used,
    message,        // present when status !== 'ok' (rejected / failed)
    status,
  } = result

  const rejected = status && status !== 'ok' && !strategy_name
  if (rejected) {
    return (
      <div className="card p-5">
        <div className="label mb-2 text-[var(--negative)]">Fusion rejected</div>
        <p className="body">{message || 'The fusion engine did not produce an actionable strategy for this brief.'}</p>
        <p className="caption mt-3">Try a different brief, broader asset classes, or a different risk profile.</p>
      </div>
    )
  }

  const hasBacktest = backtest && typeof backtest.sharpe_ratio === 'number'
  const hasRigor = rigor && typeof rigor.passing === 'boolean'
  const rigorPassing = hasRigor && rigor.passing === true

  return (
    <div className="flex flex-col gap-4">
      {/* Headline — strategy name + thesis */}
      <div className="card p-5">
        <div className="caption mb-1 uppercase tracking-wider text-[var(--text-4)]">Fusion proposal</div>
        <h3 className="font-serif text-[1.4rem] mb-2 text-[var(--text-1)]">{strategy_name || '(unnamed)'}</h3>
        {thesis && (
          <p className="body leading-relaxed">{thesis}</p>
        )}

        {source_arxiv_ids.length > 0 && (
          <div className="mt-4 flex gap-2 flex-wrap">
            <span className="caption text-[var(--text-4)] mr-1">Source papers:</span>
            {source_arxiv_ids.map(aid => (
              <a
                key={aid}
                href={`https://arxiv.org/abs/${aid}`}
                target="_blank"
                rel="noopener noreferrer"
                className="tag tag-muted"
                style={{ fontFamily: 'var(--mono, monospace)', fontSize: '0.75rem' }}
                title={`arxiv.org/abs/${aid}`}
              >
                {aid}
              </a>
            ))}
          </div>
        )}

        {market_context_used?.regime && (
          <div className="caption mt-3 text-[var(--text-3)]">
            Generated under regime: <strong className="capitalize">{String(market_context_used.regime).replace('_', ' ')}</strong>
            {market_context_used.confidence != null && (
              <> ({Math.round(market_context_used.confidence * 100)}% confidence)</>
            )}
          </div>
        )}
      </div>

      {/* Backtest metrics (when present) */}
      {hasBacktest && (
        <div className="card p-5">
          <div className="label mb-3">Backtest</div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Metric label="Sharpe" value={fmtNumber(backtest.sharpe_ratio, 2)} />
            <Metric label="CAGR" value={fmtPct(backtest.cagr)} />
            <Metric label="Max DD" value={fmtPct(backtest.max_drawdown)} />
            <Metric label="Calmar" value={fmtNumber(backtest.calmar_ratio, 2)} />
            <Metric label="Sortino" value={fmtNumber(backtest.sortino_ratio, 2)} />
            <Metric label="Win rate" value={fmtPct(backtest.win_rate)} />
            <Metric label="Trades" value={backtest.total_trades != null ? backtest.total_trades : '—'} />
          </div>
        </div>
      )}

      {/* Rigor verdict (when present) */}
      {hasRigor && (
        <div className="card p-5">
          <div className="flex items-center justify-between flex-wrap gap-2 mb-3">
            <div className="label">Rigor verdict</div>
            <span className={`tag ${rigorPassing ? 'tag-positive' : 'tag-muted'}`}>
              {rigorPassing
                ? <><span className="i-lucide-check w-3 h-3 mr-1" /> rigor gate passed</>
                : <><span className="i-lucide-x w-3 h-3 mr-1" /> rigor gate failed</>}
            </span>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Metric label="DSR" value={fmtNumber(rigor.dsr, 2)} hint={rigor.dsr_p_value != null ? `confidence = ${fmtNumber(rigor.dsr_p_value, 3)}` : null} />
            <Metric label="PBO" value={fmtNumber(rigor.pbo_score, 2)} hint="lower = less overfit" />
            <Metric label="OOS Sharpe" value={fmtNumber(rigor.oos_sharpe, 2)} />
            <Metric label="Look-ahead" value={rigor.look_ahead_clean ? 'clean' : 'flagged'} />
          </div>
        </div>
      )}

      {/* Pre-backtest hypothesis honesty note (when no backtest) */}
      {!hasBacktest && (
        <div className="info-box">
          <strong>Pre-backtest hypothesis.</strong>{' '}
          Strategy specification was not produced or could not be evaluated.
          Rerun for a backtested result, or inspect the reasoning below.
        </div>
      )}

      {/* Fusion reasoning + novelty + risk — expandable cards */}
      {fusion_reasoning && (
        <details className="card p-5">
          <summary className="label cursor-pointer">How it fuses</summary>
          <p className="body mt-3 leading-relaxed">{fusion_reasoning}</p>
        </details>
      )}
      {novelty_rationale && (
        <details className="card p-5">
          <summary className="label cursor-pointer">Why novel</summary>
          <p className="body mt-3 leading-relaxed">{novelty_rationale}</p>
        </details>
      )}
      {risk_notes && (
        <details className="card p-5">
          <summary className="label cursor-pointer">Risk notes</summary>
          <p className="body mt-3 leading-relaxed">{risk_notes}</p>
        </details>
      )}

      {/* CTAs — open in Library / Deploy (Phase 4) */}
      <div className="flex gap-3 flex-wrap">
        {strategy_id && onNavigate && (
          <button
            className="btn btn-outline"
            onClick={() => onNavigate('library', { highlight: strategy_id })}
          >
            Open in Library →
          </button>
        )}
        <button
          className="btn btn-primary"
          disabled
          title={rigorPassing
            ? 'Deploy as vault — coming in Phase 4 (time-bound vaults + on-chain agent)'
            : 'Deploy disabled — strategy did not pass the rigor gate'}
        >
          Deploy as Vault — coming in Phase 4
        </button>
      </div>
    </div>
  )
}

function Metric({ label, value, hint }) {
  return (
    <div>
      <div className="caption text-[var(--text-4)]">{label}</div>
      <div className="text-[1.1rem] font-semibold tabular-nums">{value}</div>
      {hint && <div className="caption text-[var(--text-4)]">{hint}</div>}
    </div>
  )
}

function fmtNumber(v, digits = 2) {
  if (v == null || !Number.isFinite(v)) return '—'
  return Number(v).toFixed(digits)
}

function fmtPct(v) {
  if (v == null || !Number.isFinite(v)) return '—'
  return `${(Number(v) * 100).toFixed(1)}%`
}
