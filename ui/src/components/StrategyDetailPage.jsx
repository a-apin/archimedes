import { useEffect, useState } from 'react'
import { apiGet, apiPost } from '../api'
import {
  getAddress,
} from '../config'

function shortAddr(a) {
  return a ? `${a.slice(0, 6)}…${a.slice(-4)}` : '—'
}

// Generate a client-side 0x-prefixed 32-random-byte hex string.
// The backend validates: startswith("0x"), len==66, valid hex, non-zero.
function makeSubId() {
  const b = new Uint8Array(32)
  crypto.getRandomValues(b)
  return '0x' + [...b].map(x => x.toString(16).padStart(2, '0')).join('')
}

export default function StrategyDetailPage({ strategyId, onNavigate }) {
  const [strategy, setStrategy] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // Subscribe flow state
  const [subError, setSubError] = useState('')
  const [subSuccess, setSubSuccess] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!strategyId) return
    let cancelled = false
    const load = async () => {
      try {
        const data = await apiGet(`/api/marketplace/published/${encodeURIComponent(strategyId)}`)
        if (!cancelled) setStrategy(data)
      } catch (err) {
        if (!cancelled) setError(err.message || 'Failed to load strategy')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [strategyId])

  const walletAddr = getAddress()

  const handleSubscribe = async () => {
    setSubError('')
    setSubSuccess('')
    setBusy(true)
    try {
      const subId = makeSubId()
      await apiPost('/api/marketplace/subscribe', {
        strategy_id: strategyId,
        sub_id: subId,
      })
      setSubSuccess(`Successfully subscribed to "${strategyId}". Sub ID: ${shortAddr(subId)}`)
    } catch (err) {
      setSubError(err.message || 'Subscribe failed')
    } finally {
      setBusy(false)
    }
  }

  if (loading) {
    return (
      <div className="page-panel">
        <div className="max-w-[720px] mx-auto">
          <p className="text-[var(--color-muted)]">Loading strategy…</p>
        </div>
      </div>
    )
  }

  if (error || !strategy) {
    return (
      <div className="page-panel">
        <div className="max-w-[720px] mx-auto">
          <h2 className="serif text-[2rem] mb-2">Strategy</h2>
          <p className="text-[var(--color-danger)]">{error || 'Strategy not found'}</p>
          <button className="btn mt-4" onClick={() => onNavigate('marketplace')}>Back to Marketplace</button>
        </div>
      </div>
    )
  }

  return (
    <div className="page-panel">
      <div className="max-w-[720px] mx-auto">
        <button className="btn btn-ghost text-sm mb-4" onClick={() => onNavigate('marketplace')}>
          ← Back to Marketplace
        </button>

        <h2 className="serif text-[2rem] mb-1">{strategy.strategy_id}</h2>
        <p className="body text-[var(--color-muted)] mb-4">
          Creator: {shortAddr(strategy.creator_wallet)}
        </p>

        {/* Info cards */}
        <div className="grid grid-cols-2 gap-3 mb-6">
          <div className="card p-3">
            <div className="text-xs text-[var(--color-muted)]">Pool ID</div>
            <div className="text-sm font-mono truncate">{shortAddr(strategy.pool_id)}</div>
          </div>
          <div className="card p-3">
            <div className="text-xs text-[var(--color-muted)]">Vault</div>
            <div className="text-sm font-mono truncate">{shortAddr(strategy.vault_address)}</div>
          </div>
          <div className="card p-3">
            <div className="text-xs text-[var(--color-muted)]">Subscribers</div>
            <div className="text-sm">{strategy.subscriber_count}</div>
          </div>
          <div className="card p-3">
            <div className="text-xs text-[var(--color-muted)]">Status</div>
            <div className="text-sm">{strategy.is_running ? 'Active' : 'Stopped'}</div>
          </div>
        </div>

        {/* Subscribers */}
        {strategy.subscribers && strategy.subscribers.length > 0 && (
          <div className="mb-6">
            <h3 className="font-semibold mb-2">Subscribers</h3>
            <div className="space-y-1">
              {strategy.subscribers.map((s) => (
                <div key={s.sub_id} className="text-xs text-[var(--color-muted)] flex gap-2">
                  <span className="font-mono">{shortAddr(s.subscriber_wallet)}</span>
                  <span className={`${s.status === 'running' ? 'text-green-500' : 'text-[var(--color-muted)]'}`}>
                    {s.status}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Events */}
        {strategy.events && strategy.events.length > 0 && (
          <div className="mb-6">
            <h3 className="font-semibold mb-2">Recent Events</h3>
            <div className="space-y-1 text-xs text-[var(--color-muted)] max-h-[200px] overflow-y-auto">
              {strategy.events.map((e, i) => (
                <div key={i} className="font-mono">
                  {e.type}{e.action_count ? ` (${e.action_count} actions)` : ''}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Subscribe flow */}
        {strategy.strategy_id && (
          <div className="card p-4 mt-6">
            <h3 className="font-semibold mb-3">Subscribe to this Strategy</h3>

            {!walletAddr && (
              <p className="text-sm text-[var(--color-muted)]">
                Connect your wallet to subscribe.
              </p>
            )}

            {walletAddr && !subSuccess && (
              <>
                <button
                  className="btn btn-primary"
                  onClick={handleSubscribe}
                  disabled={busy}
                >
                  {busy ? 'Processing…' : 'Subscribe'}
                </button>

                {subError && (
                  <p className="text-sm text-[var(--color-danger)] mt-2">{subError}</p>
                )}
              </>
            )}

            {subSuccess && (
              <div>
                <p className="text-sm text-green-500 mb-2">{subSuccess}</p>
                <div className="flex gap-2">
                  <button className="btn" onClick={() => onNavigate('subscriptions')}>
                    View My Subscriptions
                  </button>
                  <button className="btn btn-ghost" onClick={() => onNavigate('marketplace')}>
                    Back to Marketplace
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
