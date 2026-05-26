import { useEffect, useState } from 'react'
import CreateVaultModal from './CreateVaultModal'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

// /strategy/:id deep-link route. Renders the full passport per
// docs/specs/strategy-passport-spec.md — bigger, scrollable version of the row
// expansion that lives on /library. Includes the Deploy CTA that opens
// CreateVaultModal (Phase 4 scaffold).
//
// Reachable from: Library row title click (deep-link), Reasoning trace
// "→ Strategy in Library" follow-back, FusionResult "Open in Library →" CTA.

function fmt(v, digits = 2) {
  if (v == null || !Number.isFinite(v)) return '—'
  return Number(v).toFixed(digits)
}

function fmtPct(v) {
  if (v == null || !Number.isFinite(v)) return '—'
  return `${(Number(v) * 100).toFixed(1)}%`
}

function statusTag(status) {
  if (status === 'validated' || status === 'live') return 'tag-positive'
  if (status === 'rejected' || status === 'retired') return 'tag-muted'
  return 'tag-accent'
}

// Derive a brief-specific display title. The unified passport table doesn't
// persist `strategy_name`, but Pi's #336 fix ensures methodology_summary
// always starts with "For brief 'XXX': YYY" — extract XXX as the title so
// each generated strategy reads differently. Falls back to paper title for
// legacy strategies and curated ones (whose methodology_summary doesn't
// follow that template).
function deriveDisplayTitle(s) {
  const m = (s.methodology_summary || '').match(/^For brief ['"](.+?)['"]\s*:/i)
  if (m && m[1]) return m[1].trim()
  return s.paper_title || s.id
}

function regimeChip(tag) {
  if (tag === 'bull') {
    return {
      label: <><span className="i-lucide-trending-up w-3.5 h-3.5" /> Bull regime</>,
      cls: 'tag-positive',
    }
  }
  if (tag === 'bear') {
    return {
      label: <><span className="i-lucide-trending-down w-3.5 h-3.5" /> Bear regime</>,
      cls: 'tag-negative',
    }
  }
  return null
}

export default function StrategyPassport({ strategyId, onNavigate, walletAddr }) {
  const [strategy, setStrategy] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [deployOpen, setDeployOpen] = useState(false)

  useEffect(() => {
    let cancelled = false
    if (!strategyId) {
      setError('No strategy id in URL.')
      setLoading(false)
      return
    }
    fetch(`${API_BASE}/api/strategies/${encodeURIComponent(strategyId)}`)
      .then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.statusText) }))
      .then(data => { if (!cancelled) setStrategy(data) })
      .catch(e => { if (!cancelled) setError(e.message || 'Failed to load strategy') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [strategyId])

  if (loading) return <div className="caption">Loading strategy passport…</div>

  if (error || !strategy) {
    return (
      <div className="max-w-[640px]">
        <button className="btn btn-outline btn-sm mb-3" onClick={() => onNavigate('library')}>
          ← Back to Library
        </button>
        <div className="info-box warning">
          Could not load strategy: {error || 'unknown error'}
        </div>
      </div>
    )
  }

  const s = strategy
  const passingRigor = s.passes_rigor_gate === true
  const paperCite = [
    s.paper_authors?.[0]?.split(' ').pop(),
    s.paper_year && `(${s.paper_year})`,
  ].filter(Boolean).join(' ')
  const displayTitle = deriveDisplayTitle(s)
  const regime = regimeChip(s.regime_tag)
  // Show the anchor-paper title as a sub-line only when the derived title is
  // brief-specific (i.e. we did extract it from methodology_summary) AND a
  // paper title exists. Otherwise the sub-line would duplicate the heading.
  const paperAnchorLine = (displayTitle !== s.paper_title && s.paper_title)
    ? `Anchored on: ${s.paper_title}`
    : null

  return (
    <div className="max-w-[920px]">
      <button className="btn btn-outline btn-sm mb-4" onClick={() => onNavigate('library')}>
        ← Back to Library
      </button>

      {/* Header */}
      <div className="fade-up fade-up-1 mb-6">
        <div className="caption mb-1 uppercase tracking-wider text-[var(--text-4)]">Strategy Passport</div>
        <h2 className="font-serif text-[2rem] mb-2 leading-tight">{displayTitle}</h2>
        {paperAnchorLine && (
          <div className="caption mb-1" style={{ color: 'var(--text-3)' }}>{paperAnchorLine}</div>
        )}
        <div className="caption mb-3">
          {paperCite || (s.paper_year ? `(${s.paper_year})` : '')}
          {s.paper_venue && <> · {s.paper_venue}</>}
        </div>
        <div className="flex gap-2 items-center flex-wrap">
          {regime && <span className={`tag ${regime.cls}`}>{regime.label}</span>}
          <span className={`tag ${statusTag(s.status)}`} style={{ textTransform: 'capitalize' }}>{s.status || 'candidate'}</span>
          {s.passes_rigor_gate === true && (
            <span className="tag tag-positive inline-flex items-center gap-1">
              <span className="i-lucide-check w-3.5 h-3.5" /> rigor gate passed
            </span>
          )}
          {s.passes_rigor_gate === false && (
            <span className="tag tag-muted">rigor gate not passed</span>
          )}
          {s.paper_arxiv_id && (
            <a
              href={`https://arxiv.org/abs/${s.paper_arxiv_id}`}
              target="_blank" rel="noreferrer"
              className="tag tag-muted"
              style={{ fontFamily: 'var(--mono, monospace)', fontSize: '0.75rem' }}
            >
              arxiv:{s.paper_arxiv_id} ↗
            </a>
          )}
        </div>
      </div>

      {/* Deploy CTA — top, gated on rigor + wallet */}
      <div className="card p-5 mb-6 fade-up fade-up-2 flex items-start justify-between gap-4 flex-wrap">
        <div className="flex-1 min-w-[240px]">
          <div className="label mb-1">Deploy as a vault</div>
          <p className="caption leading-relaxed">
            Time-bound, non-custodial execution. Funds stay in an ERC-4626 vault
            you control; the agent has rebalance authority only, no withdraw.
            {!walletAddr && <> Connect a wallet (top right) to enable deployment.</>}
            {s.passes_rigor_gate === false && <> This strategy did not pass the rigor gate — deploy at your own risk.</>}
          </p>
        </div>
        <button
          className="btn btn-primary"
          onClick={() => setDeployOpen(true)}
          disabled={!walletAddr}
          style={
            !walletAddr
              ? { opacity: 0.45, cursor: 'not-allowed', filter: 'grayscale(0.6)' }
              : undefined
          }
          title={
            !walletAddr ? 'Connect wallet to deploy' :
            'Open deploy modal'
          }
        >
          Deploy as Vault →
        </button>
      </div>

      {/* Methodology + source paper */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6 fade-up fade-up-3">
        <div className="card p-5">
          <div className="label mb-3">Methodology</div>
          <p className="body leading-relaxed">{s.methodology_summary || '—'}</p>
          <div className="mt-4 grid grid-cols-2 gap-3">
            <div>
              <div className="caption text-[var(--text-4)]">Position sizing</div>
              <div className="body capitalize">{s.position_sizing || '—'}</div>
            </div>
            <div>
              <div className="caption text-[var(--text-4)]">Rebalance</div>
              <div className="body capitalize">{s.rebalance_frequency || '—'}</div>
            </div>
            <div>
              <div className="caption text-[var(--text-4)]">Asset universe</div>
              <div className="body">{(s.asset_universe || []).join(', ') || '—'}</div>
            </div>
            {s.kelly_fraction != null && (
              <div>
                <div className="caption text-[var(--text-4)]">Kelly fraction</div>
                <div className="body mono">{fmt(s.kelly_fraction)}</div>
              </div>
            )}
          </div>
        </div>

        <div className="card p-5">
          <div className="label mb-3">Source paper</div>
          <p className="body leading-snug" style={{ fontStyle: 'italic' }}>"{s.paper_title}"</p>
          <p className="caption mt-2 leading-relaxed">
            {s.paper_authors?.slice(0, 4).join(', ')}{s.paper_authors?.length > 4 ? ' et al.' : ''}
            {s.paper_year ? ` (${s.paper_year})` : ''}
            {s.paper_venue && <> · {s.paper_venue}</>}
          </p>
          {s.paper_doi && (
            <div className="caption mt-2">DOI: <span className="mono">{s.paper_doi}</span></div>
          )}
          {s.paper_citation_count != null && (
            <div className="caption">Cited by {s.paper_citation_count} other works</div>
          )}
          {s.methodology_hash && (
            <div className="caption mt-3 mono text-[var(--text-4)]">
              hash: {s.methodology_hash.slice(0, 24)}…
            </div>
          )}
        </div>
      </div>

      {/* Backtest metrics */}
      <div className="card p-5 mb-6 fade-up fade-up-4">
        <div className="label mb-3">Backtest</div>
        {s.is_backtest_placeholder && (
          <div className="info-box mb-3" style={{ fontSize: '0.85rem' }}>
            Pre-backtest hypothesis — empirical metrics pending evaluation.
          </div>
        )}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <Metric label="Sharpe" value={fmt(s.sharpe_ratio)} hint={
            s.sharpe_ci_lower != null && s.sharpe_ci_upper != null
              ? `[${fmt(s.sharpe_ci_lower)}, ${fmt(s.sharpe_ci_upper)}]`
              : null
          } />
          <Metric label="CAGR" value={fmtPct(s.cagr)} />
          <Metric label="Max DD" value={s.max_drawdown != null ? `−${fmtPct(s.max_drawdown)}` : '—'} />
          <Metric label="Calmar" value={fmt(s.calmar_ratio)} />
          <Metric label="Sortino" value={fmt(s.sortino_ratio)} />
          <Metric label="Win rate" value={fmtPct(s.win_rate)} />
          <Metric label="Trades" value={s.total_trades != null ? s.total_trades : '—'} />
          <Metric label="ρ to SPY" value={fmt(s.correlation_to_spy)} />
        </div>
        {(s.backtest_start || s.backtest_end) && (
          <div className="caption mt-3 text-[var(--text-3)]">
            Window: <span className="mono">{(s.backtest_start || '').slice(0, 10)} → {(s.backtest_end || '').slice(0, 10)}</span>
          </div>
        )}
        {s.paper_claimed_sharpe != null && (
          <div className="caption mt-2">
            Paper-claimed Sharpe: <strong>{fmt(s.paper_claimed_sharpe)}</strong>{' '}
            · realized: <strong>{fmt(s.sharpe_ratio)}</strong>{' '}
            {s.sharpe_ratio != null && (
              <span className={s.sharpe_ratio / s.paper_claimed_sharpe >= 0.5 ? 'positive' : 'negative'}>
                ({((s.sharpe_ratio / s.paper_claimed_sharpe) * 100).toFixed(0)}% of paper claim)
              </span>
            )}
          </div>
        )}
      </div>

      {/* Rigor gate */}
      <div className="card p-5 mb-6 fade-up fade-up-5">
        <div className="flex items-center justify-between flex-wrap gap-2 mb-3">
          <div className="label">Rigor verdict — selection-bias controls</div>
          <span className={`tag inline-flex items-center gap-1 ${passingRigor ? 'tag-positive' : 'tag-muted'}`}>
            {passingRigor ? <><span className="i-lucide-check w-3.5 h-3.5" /> passed</> : 'not passed'}
          </span>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <Metric
            label="DSR"
            value={fmt(s.deflated_sharpe_ratio)}
            hint={s.dsr_p_value != null ? `p = ${fmt(s.dsr_p_value, 3)}` : null}
          />
          {/* PBO of exactly 0 paired with missing DSR/OOS is almost always a
              placeholder, not a real measurement. Render "—" in that case so
              we don't show a fake 0.0% next to honest unknowns elsewhere. */}
          <Metric
            label="PBO"
            value={
              s.pbo_score == null ||
              (s.pbo_score === 0 && s.deflated_sharpe_ratio == null && s.out_of_sample_sharpe == null)
                ? '—'
                : fmtPct(s.pbo_score)
            }
            hint="lower = less overfit"
          />
          <Metric label="OOS Sharpe" value={fmt(s.out_of_sample_sharpe)} />
          <Metric label="Trades" value={s.total_trades != null ? s.total_trades : '—'} hint="executed in backtest" />
        </div>
        <p className="caption mt-3 leading-relaxed text-[var(--text-3)]">
          The Deflated Sharpe Ratio corrects the realized Sharpe for multiple-testing
          inflation (Bailey & López de Prado 2014). PBO estimates how much of the
          in-sample Sharpe is overfit (Bailey et al. 2014). OOS Sharpe is the
          walk-forward out-of-sample number. A strategy passes the rigor gate only
          when all three signals align.
        </p>
      </div>

      {/* Provenance */}
      {(s.curator_wallet || s.on_chain_registration_tx || s.extraction_llm) && (
        <div className="card p-5 mb-6">
          <div className="label mb-3">Provenance</div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-[0.85rem]">
            {s.extraction_llm && (
              <div>
                <div className="caption text-[var(--text-4)]">Extracted by</div>
                <div className="mono">{s.extraction_llm}</div>
              </div>
            )}
            {s.curator_wallet && (
              <div>
                <div className="caption text-[var(--text-4)]">Curator</div>
                <div className="mono">{s.curator_wallet.slice(0, 12)}…{s.curator_wallet.slice(-6)}</div>
              </div>
            )}
            {s.on_chain_registration_tx && (
              <div>
                <div className="caption text-[var(--text-4)]">Registration tx</div>
                <a
                  href={`https://testnet.arcscan.app/tx/${s.on_chain_registration_tx}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mono underline decoration-dotted underline-offset-2 hover:text-[var(--accent)] transition-colors"
                >
                  {s.on_chain_registration_tx.slice(0, 14)}… ↗
                </a>
              </div>
            )}
            {s.curator_note && (
              <div style={{ gridColumn: '1 / -1' }}>
                <div className="caption text-[var(--text-4)]">Curator note</div>
                <div className="body">{s.curator_note}</div>
              </div>
            )}
          </div>
        </div>
      )}

      {deployOpen && (
        <CreateVaultModal
          strategy={s}
          walletAddr={walletAddr}
          onClose={() => setDeployOpen(false)}
          onDeployed={(vaultAddress) => {
            setDeployOpen(false)
            if (onNavigate) {
              onNavigate('portfolio', { vaultAddress })
            }
          }}
        />
      )}
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
