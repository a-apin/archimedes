import { useEffect, useState } from 'react'
import { apiGet, apiDelete } from '../api'
import { getAddress } from '../config'

function shortAddr(a) {
  return a ? `${a.slice(0, 6)}…${a.slice(-4)}` : '—'
}

function fmtTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export default function SubscriptionsPage({ onNavigate }) {
  const walletAddr = getAddress()
  const [subs, setSubs] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [unsubscribing, setUnsubscribing] = useState(null)

  const load = async () => {
    try {
      const data = await apiGet('/api/marketplace/my-subscriptions')
      setSubs(data)
    } catch (err) {
      setError(err.message || 'Failed to load subscriptions')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (walletAddr) load()
    else setLoading(false)
  }, [walletAddr])

  const handleUnsubscribe = async (strategyId) => {
    if (!window.confirm(`Unsubscribe from "${strategyId}"? Remaining balance will be refunded to your wallet.`)) return
    setUnsubscribing(strategyId)
    setError('')
    try {
      await apiDelete(`/api/marketplace/subscribe/${encodeURIComponent(strategyId)}`)
      await load()
    } catch (err) {
      setError(err.message || 'Unsubscribe failed')
    } finally {
      setUnsubscribing(null)
    }
  }

  if (!walletAddr) {
    return (
      <div className="page-panel">
        <div className="max-w-[720px] mx-auto">
          <h2 className="serif text-[2rem] mb-2">My Subscriptions</h2>
          <p className="body text-[var(--color-muted)]">
            Connect your wallet to view your subscriptions.
          </p>
        </div>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="page-panel">
        <div className="max-w-[720px] mx-auto">
          <h2 className="serif text-[2rem] mb-2">My Subscriptions</h2>
          <p className="text-[var(--color-muted)]">Loading…</p>
        </div>
      </div>
    )
  }

  return (
    <div className="page-panel">
      <div className="max-w-[720px] mx-auto">
        <h2 className="serif text-[2rem] mb-2">My Subscriptions</h2>

        {error && (
          <p className="text-sm text-[var(--color-danger)] mb-4">{error}</p>
        )}

        {subs.length === 0 && (
          <div className="card p-4">
            <p className="text-[var(--color-muted)] mb-2">No subscriptions yet.</p>
            <button className="btn" onClick={() => onNavigate('marketplace')}>
              Browse Marketplace
            </button>
          </div>
        )}

        <div className="space-y-3">
          {subs.map((s) => (
            <div key={s.sub_id} className="card p-4">
              <div className="flex items-start justify-between">
                <div className="space-y-1">
                  <h3 className="font-semibold">{s.strategy_id}</h3>
                  <div className="text-xs text-[var(--color-muted)] space-y-0.5">
                    <div>Sub ID: <span className="font-mono">{shortAddr(s.sub_id)}</span></div>
                    <div>Pool: <span className="font-mono">{shortAddr(s.pool_id)}</span></div>
                    <div>Wallet: <span className="font-mono">{shortAddr(s.subscriber_wallet)}</span></div>
                    <div>Created: {fmtTime(s.created_at)}</div>
                  </div>
                </div>
                <div className="flex flex-col items-end gap-2">
                  <span className={`text-xs px-2 py-0.5 rounded-full ${
                    s.status === 'running' ? 'bg-green-500/10 text-green-500' : 'text-[var(--color-muted)] bg-[var(--color-surface)]'
                  }`}>
                    {s.status}
                  </span>
                  {s.status === 'running' && (
                    <button
                      className="btn btn-ghost text-xs text-[var(--color-danger)]"
                      onClick={() => handleUnsubscribe(s.strategy_id)}
                      disabled={unsubscribing === s.strategy_id}
                    >
                      {unsubscribing === s.strategy_id ? '…' : 'Unsubscribe'}
                    </button>
                  )}
                </div>
              </div>
              <p className="text-xs text-[var(--color-muted)] mt-2 border-t border-[var(--color-border)] pt-2">
                To top up, call <code className="bg-[var(--color-surface)] px-1">SubscriptionManager.renewEphemeralWallet(sub_id, amount)</code> from your wallet.
              </p>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
