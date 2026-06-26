import { useEffect, useState, useMemo } from 'react'
import CustomSelect from './CustomSelect'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

// /marketplace — Public browse surface for every vault deployed on Archimedes.
// No wallet gate; anyone can land here, filter, sort, and click into a vault.
//
// Lives split off from Portfolio (which is now strictly "your stuff") so the
// six anonymous deploy-seed vaults don't pollute the personal dashboard.

function shortAddr(a) {
  return a ? `${a.slice(0, 6)}…${a.slice(-4)}` : '—'
}

function fmtUsd(v) {
  const n = Number(v) || 0
  return `$${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function fmtPct(v) {
  if (v == null || Number.isNaN(v)) return '—'
  const sign = v >= 0 ? '+' : ''
  return `${sign}${Number(v).toFixed(2)}%`
}

const SORT_OPTIONS = [
  { value: 'aum',         label: 'AUM (highest first)' },
  { value: 'return_24h',  label: '24h return' },
  { value: 'return_7d',   label: '7d return' },
  { value: 'sharpe',      label: 'Sharpe ratio' },
  { value: 'created_at',  label: 'Created (newest first)' },
]

export default function Marketplace({ onNavigate }) {
  const [vaults, setVaults] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [tierFilter, setTierFilter] = useState('all') // 'all' | 1 | 2
  const [sortBy, setSortBy] = useState('aum')
  // T1.2 nanopayment marketplace — true when the backend has the x402 paywall
  // armed (ARCHIMEDES_X402_ENABLED=1). Read via the single `is_paywalled` boolean
  // the strategies API plumbs through; drives the "Pay per run" disclosure chip.
  const [paywallActive, setPaywallActive] = useState(false)

  // Probe the backend once for the marketplace paywall state. A no-op (chip
  // stays hidden) unless the operator has armed the x402 flag.
  useEffect(() => {
    let cancelled = false
    const probe = async () => {
      try {
        const r = await fetch(`${API_BASE}/api/strategies/?limit=1`)
        if (!r.ok) return
        const data = await r.json()
        const first = (data.strategies || [])[0]
        if (!cancelled && first) setPaywallActive(Boolean(first.is_paywalled))
      } catch {
        // Paywall probe is best-effort; the chip simply stays hidden on failure.
      }
    }
    probe()
    return () => { cancelled = true }
  }, [])

  // Load vaults from the backend marketplace endpoint.
  // We fetch with sort hint, but also re-sort client-side so the dropdown
  // reorder feels instant (no roundtrip).
  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const r = await fetch(`${API_BASE}/api/vaults/?sort_by=aum&order=desc&limit=100`)
        if (!r.ok) throw new Error(await r.text())
        const data = await r.json()
        if (!cancelled) {
          setVaults(data.vaults || [])
          setLoading(false)
        }
      } catch (e) {
        if (!cancelled) {
          setError(e.message || 'Failed to load vaults')
          setLoading(false)
        }
      }
    }
    load()
    // Refresh every 30s — AUM + returns drift with on-chain activity.
    const t = setInterval(load, 30_000)
    return () => { cancelled = true; clearInterval(t) }
  }, [])

  const tier1Count = vaults.filter(v => v.tier === 1).length
  const tier2Count = vaults.filter(v => v.tier === 2).length

  // Filter + sort client-side; the endpoint already returned a useful set.
  const visible = useMemo(() => {
    const filtered = tierFilter === 'all' ? vaults : vaults.filter(v => v.tier === tierFilter)
    const sorted = [...filtered].sort((a, b) => {
      switch (sortBy) {
        case 'aum':         return (b.aum_usdc || 0) - (a.aum_usdc || 0)
        case 'return_24h':  return (b.return_24h || 0) - (a.return_24h || 0)
        case 'return_7d':   return (b.return_7d  || 0) - (a.return_7d  || 0)
        case 'sharpe':      return (b.sharpe_ratio ?? -Infinity) - (a.sharpe_ratio ?? -Infinity)
        case 'created_at':  return String(b.created_at || '').localeCompare(String(a.created_at || ''))
        default:            return 0
      }
    })
    return sorted
  }, [vaults, tierFilter, sortBy])

  const handleCardClick = (addr) => {
    if (onNavigate) onNavigate('vault-detail', { vaultAddress: addr })
  }

  return (
    <div>
      {/* Header + framing */}
      <div style={{ maxWidth: 760, marginBottom: 28 }}>
        <h2 className="serif" style={{ fontSize: '2rem', marginBottom: 10 }}>Vault Marketplace</h2>
        <p className="body" style={{ marginBottom: 6 }}>
          Browse every vault deployed on Archimedes. Pick one to inspect, or
          generate a strategy and deploy your own.
        </p>
        <p className="caption" style={{ color: 'var(--text-4)' }}>
          Tier 1 vaults (<span className="i-lucide-trophy w-3.5 h-3.5" /> Verified) are paper-grounded and selection-bias-corrected.
          Tier 2 vaults (<span className="i-lucide-users w-3.5 h-3.5" /> Community) are permissionless and opt-in to agent features.
        </p>
      </div>

      {/* Controls — filter chips + sort dropdown */}
      <div className="flex items-center justify-between gap-3 flex-wrap mb-5">
        <div className="strat-filter-bar">
          <span
            className={`tag ${tierFilter === 'all' ? 'tag-accent' : 'tag-muted'}`}
            onClick={() => setTierFilter('all')}
            style={{ cursor: 'pointer' }}
          >
            All ({vaults.length})
          </span>
          {tier1Count > 0 && (
            <span
              className={`tag ${tierFilter === 1 ? 'tag-accent' : 'tag-muted'}`}
              onClick={() => setTierFilter(1)}
              style={{ cursor: 'pointer' }}
            >
              <span className="i-lucide-trophy w-3.5 h-3.5" /> Verified ({tier1Count})
            </span>
          )}
          {tier2Count > 0 && (
            <span
              className={`tag ${tierFilter === 2 ? 'tag-accent' : 'tag-muted'}`}
              onClick={() => setTierFilter(2)}
              style={{ cursor: 'pointer' }}
            >
              <span className="i-lucide-users w-3.5 h-3.5" /> Community ({tier2Count})
            </span>
          )}
        </div>

        <div className="flex items-center gap-2">
          <span className="caption" style={{ color: 'var(--text-4)' }}>Sort by</span>
          <div style={{ minWidth: 200 }}>
            <CustomSelect
              value={sortBy}
              onChange={setSortBy}
              options={SORT_OPTIONS}
            />
          </div>
        </div>
      </div>

      {/* States: loading / error / empty / populated */}
      {loading && (
        <div className="card" style={{ padding: 18 }}>
          <p className="caption">Loading vaults…</p>
        </div>
      )}

      {!loading && error && (
        <div className="info-box warning" style={{ padding: 14 }}>
          Failed to load marketplace: {error}
        </div>
      )}

      {!loading && !error && vaults.length === 0 && (
        <div className="card" style={{ padding: 18 }}>
          <p className="body" style={{ marginBottom: 6 }}>No vaults have been deployed yet.</p>
          <p className="caption">
            Be the first —{' '}
            <a
              onClick={() => onNavigate?.('generate')}
              style={{ color: 'var(--accent)', cursor: 'pointer', textDecoration: 'underline' }}
            >
              generate a strategy
            </a>
            {' '}and deploy your own vault.
          </p>
        </div>
      )}

      {!loading && !error && visible.length === 0 && vaults.length > 0 && (
        <div className="card" style={{ padding: 18 }}>
          <p className="caption">No vaults match this filter. Try “All”.</p>
        </div>
      )}

      {!loading && !error && visible.length > 0 && (
        <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))' }}>
          {visible.map(v => (
            <div
              key={v.address}
              className="card vault-card-clickable"
              onClick={() => handleCardClick(v.address)}
            >
              <div className="flex justify-between items-center mb-2">
                <span className="font-semibold text-sm" style={{ color: 'var(--text-1)' }}>
                  {v.name || `Vault ${shortAddr(v.address)}`}
                </span>
                <div className="flex items-center gap-1.5">
                  {/* T1.2: "Pay per run" disclosure — rendered only when the x402
                      paywall is armed on the backend and the vault is agent-assisted
                      (its strategy is the paywalled construct verb). One-line chip
                      only; the full payment flow is a follow-up issue. */}
                  {paywallActive && v.is_agent_assisted && (
                    <span className="tag tag-muted" title="Constructing this strategy charges a sub-cent USDC nanopayment via x402">
                      <span className="i-lucide-coins w-3.5 h-3.5" /> Pay per run
                    </span>
                  )}
                  <span className={`tag ${v.tier === 1 ? 'tag-accent' : 'tag-muted'}`}>
                    {v.tier === 1
                      ? <><span className="i-lucide-trophy w-3.5 h-3.5" /> Verified</>
                      : <><span className="i-lucide-users w-3.5 h-3.5" /> Community</>}
                  </span>
                </div>
              </div>
              <div className="flex items-baseline gap-2 mt-3 mb-1">
                <span className="text-[1.4rem] font-bold">{fmtUsd(v.aum_usdc)}</span>
                <span className="caption">AUM</span>
              </div>
              <div className="caption flex gap-3 text-[var(--text-3)] mt-1">
                <code>{shortAddr(v.address)}</code>
                {v.is_agent_assisted && <span style={{ color: 'var(--accent)' }}>AI-managed</span>}
              </div>
              {(v.return_24h != null || v.return_7d != null) && (
                <div className="caption flex gap-3 mt-1.5">
                  <span>
                    24h{' '}
                    <span className={v.return_24h >= 0 ? 'positive' : 'negative'}>
                      {fmtPct(v.return_24h)}
                    </span>
                  </span>
                  <span>
                    7d{' '}
                    <span className={v.return_7d >= 0 ? 'positive' : 'negative'}>
                      {fmtPct(v.return_7d)}
                    </span>
                  </span>
                </div>
              )}
              {v.management_fee_pct != null && (
                <div className="caption text-[var(--text-4)] mt-1">
                  {v.management_fee_pct}% mgmt · {v.performance_fee_pct}% perf
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
