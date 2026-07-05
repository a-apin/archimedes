import { useEffect, useState } from 'react'
import { apiGet } from '../api'

function shortAddr(a) {
  return a ? `${a.slice(0, 6)}…${a.slice(-4)}` : '—'
}

function fmtTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export default function MarketplacePage({ onNavigate }) {
  const [strategies, setStrategies] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const data = await apiGet('/api/marketplace/published')
        if (!cancelled) setStrategies(data)
      } catch (err) {
        if (!cancelled) setError(err.message || 'Failed to load marketplace')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [])

  if (loading) {
    return (
      <div className="page-panel">
        <div className="max-w-[960px] mx-auto">
          <h2 className="serif text-[2rem] mb-2">Marketplace</h2>
          <p className="body text-[var(--color-muted)] mb-8">
            Browse published copy-trading strategies.
          </p>
          <div className="text-[var(--color-muted)]">Loading…</div>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="page-panel">
        <div className="max-w-[960px] mx-auto">
          <h2 className="serif text-[2rem] mb-2">Marketplace</h2>
          <p className="body text-[var(--color-danger)]">{error}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="page-panel">
      <div className="max-w-[960px] mx-auto">
        <h2 className="serif text-[2rem] mb-2">Marketplace</h2>
        <p className="body text-[var(--color-muted)] mb-6">
          Published copy-trading strategies. Subscribe to mirror trades into your own vault.
        </p>

        {strategies.length === 0 && (
          <p className="text-[var(--color-muted)]">No strategies published yet.</p>
        )}

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {strategies.map((s) => (
            <div
              key={s.strategy_id}
              className="card cursor-pointer"
              onClick={() => onNavigate('market-strategy', { strategyId: s.strategy_id })}
              onKeyDown={(e) => { if (e.key === 'Enter') onNavigate('market-strategy', { strategyId: s.strategy_id }) }}
              role="button"
              tabIndex={0}
            >
              <div className="flex items-center justify-between mb-2">
                <h3 className="font-semibold text-[1.05rem]">{s.strategy_id}</h3>
                <span className="text-xs text-[var(--color-muted)] bg-[var(--color-surface)] px-2 py-0.5 rounded-full">
                  {s.subscriber_count} subscriber{s.subscriber_count !== 1 ? 's' : ''}
                </span>
              </div>
              <div className="text-xs text-[var(--color-muted)] space-y-1">
                <div>Creator: {shortAddr(s.creator_wallet)}</div>
                <div>Pool: {shortAddr(s.pool_id)}</div>
                <div>Published: {fmtTime(s.created_at)}</div>
              </div>
              {s.events && s.events.length > 0 && (
                <div className="mt-2 text-xs text-[var(--color-muted)]">
                  Last event: {s.events[0].type}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
