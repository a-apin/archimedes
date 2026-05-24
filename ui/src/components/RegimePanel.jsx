import { useState, useEffect } from 'react'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

async function apiGet(path) {
  const res = await fetch(`${API_BASE}${path}`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

function fmtPct(v) {
  return v != null ? `${(v * 100).toFixed(1)}%` : '—'
}

function fmt(v, d = 1) {
  return v != null ? v.toFixed(d) : '—'
}

function regimeColor(regime) {
  if (regime === 'risk_on') return 'var(--positive)'
  if (regime === 'crisis') return 'var(--negative)'
  if (regime === 'risk_off') return 'var(--negative)'
  return '#f59e0b'
}

function regimeBg(regime) {
  if (regime === 'risk_on') return 'rgba(16,185,129,0.08)'
  if (regime === 'crisis' || regime === 'risk_off') return 'rgba(239,68,68,0.08)'
  return 'rgba(245,158,11,0.08)'
}

function regimeBorder(regime) {
  if (regime === 'risk_on') return 'rgba(16,185,129,0.25)'
  if (regime === 'crisis' || regime === 'risk_off') return 'rgba(239,68,68,0.25)'
  return 'rgba(245,158,11,0.25)'
}

function MiniBar({ value, max, color }) {
  const pct = Math.max(0, Math.min((value / (max || 1)) * 100, 100))
  return (
    <div style={{ background: 'rgba(255,255,255,0.06)', borderRadius: 3, height: 6, overflow: 'hidden', flex: 1 }}>
      <div style={{ width: `${pct.toFixed(1)}%`, height: '100%', background: color || 'var(--accent)' }} />
    </div>
  )
}

// Compact pill — page-header context, not a full panel. Used on /portfolio
// where the user cares about their funds; the full educational view lives
// on /learnings.
function CompactRegimePill({ regime }) {
  const r = regime.regime
  const rColor = regimeColor(r)
  const rLabel = r.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
  const conf = regime.confidence ?? 0
  return (
    <span
      title={
        `The agent's current read of market conditions. Drives which library ` +
        `strategies it leans into for new generates + rebalances. ` +
        `risk_on → momentum/TSMOM. transition → vol-managed. ` +
        `risk_off → t-bill alternatives. crisis → capital preservation.`
      }
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 8,
        padding: '4px 10px',
        borderRadius: 999,
        background: regimeBg(r),
        border: `1px solid ${regimeBorder(r)}`,
        fontSize: '0.78rem',
        cursor: 'help',
      }}
    >
      <span style={{ width: 8, height: 8, borderRadius: '50%', background: rColor, display: 'inline-block' }} />
      <span style={{ fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em', color: rColor }}>
        {rLabel}
      </span>
      <span className="caption" style={{ color: 'var(--text-3)' }}>
        {fmtPct(conf)} conf
      </span>
    </span>
  )
}

function TransitionRow({ from, to, prob }) {
  const arrow = from === to ? 'stay' : `→ ${to.replace(/_/g, ' ')}`
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
      <span className="caption" style={{ width: 100, color: 'var(--text-3)', fontSize: '0.75rem' }}>
        {from === to ? `Stay ${from.replace(/_/g, ' ')}` : arrow}
      </span>
      <MiniBar value={prob} max={1} color={from === to ? 'var(--accent)' : 'rgba(255,255,255,0.25)'} />
      <span className="mono" style={{ fontSize: '0.75rem', width: 36, textAlign: 'right' }}>
        {fmtPct(prob)}
      </span>
    </div>
  )
}

export default function RegimePanel({ regime: regimeProp = null, compact = false }) {
  const [fetchedRegime, setFetchedRegime] = useState(null)
  const [loading, setLoading] = useState(regimeProp == null)

  useEffect(() => {
    if (regimeProp != null) return
    apiGet('/api/regime/current')
      .then(setFetchedRegime)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [regimeProp])

  const regime = regimeProp ?? fetchedRegime

  if (loading) {
    if (compact) return <span className="caption" style={{ color: 'var(--text-4)' }}>Loading regime…</span>
    return (
      <div className="card-flat" style={{ padding: 20, marginBottom: 24 }}>
        <div className="caption">Loading regime data…</div>
      </div>
    )
  }

  if (!regime || regime.regime === 'unknown') {
    if (compact) {
      return (
        <span
          className="caption"
          title="Agent not running or Redis not connected — the regime classifier writes state to Redis on each agent tick."
          style={{ color: 'var(--text-4)' }}
        >
          Regime: unavailable
        </span>
      )
    }
    return (
      <div className="card-flat" style={{ padding: 20, marginBottom: 24 }}>
        <div className="label mb-2">Current Market Regime</div>
        <div className="caption" style={{ color: 'var(--text-4)' }}>
          Regime data unavailable — agent not running or Redis not connected.
        </div>
      </div>
    )
  }

  if (compact) {
    return <CompactRegimePill regime={regime} />
  }

  const r = regime.regime
  const rColor = regimeColor(r)
  const rLabel = r.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
  const confidencePct = regime.confidence ?? 0
  const signals = regime.signals || {}
  const transitions = regime.transition_probabilities
  const currentTransitions = transitions?.[r]
  const recommended = regime.recommended_strategies || []

  // VIX score: rough scale — VIX 10=calm, VIX 40=crisis
  const vixScore = signals.vix_score ?? Math.min((signals.vix_level - 10) / 30, 1)
  const vixBarColor = vixScore > 0.7 ? 'var(--negative)' : vixScore > 0.4 ? '#f59e0b' : 'var(--positive)'

  return (
    <div
      className="card-flat"
      style={{
        padding: 20,
        marginBottom: 24,
        background: regimeBg(r),
        border: `1px solid ${regimeBorder(r)}`,
        borderRadius: 10,
      }}
    >
      <div className="label mb-3">Current Market Regime</div>

      {/* Regime header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14, flexWrap: 'wrap' }}>
        <span style={{ fontWeight: 800, fontSize: '1.4rem', color: rColor, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
          {rLabel}
        </span>
        <span className="caption">confidence {fmtPct(confidencePct)}</span>
        {regime.regime_changed && (
          <span style={{ fontSize: '0.72rem', fontWeight: 600, padding: '2px 7px', borderRadius: 4,
            background: 'rgba(245,158,11,0.2)', color: '#f59e0b', border: '1px solid rgba(245,158,11,0.4)' }}>
            REGIME CHANGE
          </span>
        )}
      </div>

      {/* Confidence bar */}
      <div style={{ marginBottom: 18 }}>
        <div style={{ background: 'rgba(255,255,255,0.08)', borderRadius: 4, height: 8, overflow: 'hidden' }}>
          <div style={{
            width: `${(confidencePct * 100).toFixed(1)}%`,
            height: '100%',
            background: rColor,
            transition: 'width 0.4s ease',
          }} />
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
        {/* Signal breakdown */}
        <div>
          <div className="label mb-3" style={{ fontSize: '0.72rem' }}>Signal Breakdown</div>

          {signals.vix_level != null && (
            <div style={{ marginBottom: 10 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
                <span className="caption">VIX Level</span>
                <span className="mono" style={{ fontSize: '0.8rem' }}>{fmt(signals.vix_level, 1)}</span>
              </div>
              <MiniBar value={vixScore} max={1} color={vixBarColor} />
            </div>
          )}

          {signals.vix_rate_of_change != null && (
            <div style={{ marginBottom: 10 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
                <span className="caption">VIX Momentum</span>
                <span className="mono" style={{ fontSize: '0.8rem', color: signals.vix_rate_of_change > 0 ? 'var(--negative)' : 'var(--positive)' }}>
                  {signals.vix_rate_of_change > 0 ? '+' : ''}{fmtPct(signals.vix_rate_of_change)}
                </span>
              </div>
              <MiniBar value={Math.abs(signals.vix_rate_of_change)} max={0.5} color={signals.vix_rate_of_change > 0 ? 'var(--negative)' : 'var(--positive)'} />
            </div>
          )}

          {signals.composite_score != null && (
            <div style={{ marginBottom: 10 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
                <span className="caption">Composite Score</span>
                <span className="mono" style={{ fontSize: '0.8rem' }}>{fmt(signals.composite_score, 2)}</span>
              </div>
              <MiniBar value={signals.composite_score} max={1} color={rColor} />
            </div>
          )}

          <div className="flex gap-2.5 flex-wrap mt-2.5">
            <div className="flex items-center gap-1">
              <span className={`w-3 h-3 flex-shrink-0 ${signals.sp500_above_ma200 ? 'i-lucide-check text-[var(--positive)]' : 'i-lucide-x text-[var(--negative)]'}`} />
              <span className="caption text-[0.72rem]">above MA200</span>
            </div>
            <div className="flex items-center gap-1">
              <span className={`w-3 h-3 flex-shrink-0 ${signals.sp500_above_ma50 ? 'i-lucide-check text-[var(--positive)]' : 'i-lucide-x text-[var(--negative)]'}`} />
              <span className="caption text-[0.72rem]">above MA50</span>
            </div>
          </div>
        </div>

        {/* Transition probabilities */}
        <div>
          <div className="label mb-3" style={{ fontSize: '0.72rem' }}>Regime Persistence (Dirichlet priors)</div>
          {currentTransitions ? (
            Object.entries(currentTransitions)
              .sort(([, a], [, b]) => b - a)
              .map(([to, prob]) => (
                <TransitionRow key={to} from={r} to={to} prob={prob} />
              ))
          ) : (
            <div className="caption" style={{ color: 'var(--text-4)' }}>No transition data</div>
          )}
        </div>
      </div>

      {/* Recommended strategies */}
      {recommended.length > 0 && (
        <div style={{ marginTop: 16, paddingTop: 14, borderTop: '1px solid rgba(255,255,255,0.08)' }}>
          <span className="caption" style={{ color: 'var(--text-3)', marginRight: 10 }}>Best strategies for this regime:</span>
          {recommended.map(id => (
            <span key={id} style={{
              display: 'inline-block', marginRight: 6, padding: '2px 9px', borderRadius: 4,
              fontSize: '0.75rem', fontWeight: 600,
              background: 'rgba(255,255,255,0.07)', color: 'var(--text-2)',
              border: '1px solid var(--glass-border)',
            }}>
              {id.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
