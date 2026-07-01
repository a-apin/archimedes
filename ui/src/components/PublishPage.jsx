import { useState } from 'react'
import { apiPost } from '../api'
import { getAddress } from '../config'

export default function PublishPage({ onNavigate }) {
  const walletAddr = getAddress()
  const [strategyId, setStrategyId] = useState('')
  const [vaultAddress, setVaultAddress] = useState('')
  const [platformWallet, setPlatformWallet] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)

  const handlePublish = async (e) => {
    e.preventDefault()
    if (!strategyId.trim()) return
    setError('')
    setResult(null)
    setBusy(true)
    try {
      const body = { strategy_id: strategyId.trim() }
      if (vaultAddress.trim()) body.vault_address = vaultAddress.trim()
      if (platformWallet.trim()) body.platform_wallet = platformWallet.trim()

      const res = await apiPost('/api/marketplace/publish', body)
      setResult(res)
    } catch (err) {
      if (err.message?.includes('409')) {
        setError(`Strategy "${strategyId}" is already published.`)
      } else {
        setError(err.message || 'Publish failed')
      }
    } finally {
      setBusy(false)
    }
  }

  if (!walletAddr) {
    return (
      <div className="page-panel">
        <div className="max-w-[540px] mx-auto">
          <h2 className="serif text-[2rem] mb-2">Publish a Strategy</h2>
          <p className="body text-[var(--color-muted)]">
            Connect your wallet to publish a strategy to the marketplace.
            Your strategy will be visible to all users who can subscribe and copy-trade.
          </p>
        </div>
      </div>
    )
  }

  if (result) {
    return (
      <div className="page-panel">
        <div className="max-w-[540px] mx-auto">
          <h2 className="serif text-[2rem] mb-2">Published!</h2>
          <div className="card p-4 space-y-2 text-sm">
            <div><span className="text-[var(--color-muted)]">Strategy:</span> {result.strategy_id}</div>
            <div><span className="text-[var(--color-muted)]">Pool ID:</span> <span className="font-mono">{result.pool_id}</span></div>
            <div><span className="text-[var(--color-muted)]">Vault:</span> <span className="font-mono">{result.vault_address}</span></div>
            <div><span className="text-[var(--color-muted)]">Status:</span> {result.status}</div>
          </div>
          <div className="flex gap-2 mt-4">
            <button className="btn" onClick={() => onNavigate('marketplace')}>
              View Marketplace
            </button>
            <button className="btn btn-ghost" onClick={() => { setResult(null); setStrategyId('') }}>
              Publish Another
            </button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="page-panel">
      <div className="max-w-[540px] mx-auto">
        <h2 className="serif text-[2rem] mb-2">Publish a Strategy</h2>
        <p className="body text-[var(--color-muted)] mb-6">
          Publish a strategy to the marketplace. The backend derives the pool ID
          from your strategy ID and wallet address. Subscribers will copy-trade
          this strategy through their own vaults.
        </p>

        <form onSubmit={handlePublish} className="space-y-4">
          <div>
            <label className="text-xs text-[var(--color-muted)] block mb-1">Strategy ID *</label>
            <input
              type="text"
              className="input w-full"
              value={strategyId}
              onChange={(e) => setStrategyId(e.target.value)}
              placeholder="e.g. mom_2014"
              required
              disabled={busy}
            />
            <p className="text-xs text-[var(--color-muted)] mt-1">
              The strategy ID must exist in the strategy provider.
            </p>
          </div>

          <div>
            <label className="text-xs text-[var(--color-muted)] block mb-1">Vault Address (optional)</label>
            <input
              type="text"
              className="input w-full font-mono text-sm"
              value={vaultAddress}
              onChange={(e) => setVaultAddress(e.target.value)}
              placeholder="0x... (leave empty to auto-create)"
              disabled={busy}
            />
          </div>

          <div>
            <label className="text-xs text-[var(--color-muted)] block mb-1">Platform Wallet (optional)</label>
            <input
              type="text"
              className="input w-full font-mono text-sm"
              value={platformWallet}
              onChange={(e) => setPlatformWallet(e.target.value)}
              placeholder="0x... (defaults to your wallet)"
              disabled={busy}
            />
            <p className="text-xs text-[var(--color-muted)] mt-1">
              Receives the platform fee share (10%) from subscription charges.
            </p>
          </div>

          {error && (
            <p className="text-sm text-[var(--color-danger)]">{error}</p>
          )}

          <button type="submit" className="btn btn-primary" disabled={busy || !strategyId.trim()}>
            {busy ? 'Publishing…' : 'Publish'}
          </button>
        </form>
      </div>
    </div>
  )
}
