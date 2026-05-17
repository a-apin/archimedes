import { useState, useEffect, useCallback } from 'react'
import {
  publicClient, getWalletClient, getAddress,
  USDC, TOKEN_ABI, VAULT_ABI,
  ASSETS, NEW_CONTRACTS,
} from '../config'
import VaultChat from './VaultChat'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

async function apiGet(path) {
  const res = await fetch(`${API_BASE}${path}`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

function timeAgo(ts) {
  if (!ts) return '—'
  const d = new Date(ts)
  if (isNaN(d.getTime())) return ts
  const secs = Math.floor((Date.now() - d.getTime()) / 1000)
  if (secs < 60) return `${secs}s ago`
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  return `${Math.floor(secs / 86400)}d ago`
}

function shortAddr(addr) {
  if (!addr) return '—'
  return `${addr.slice(0, 6)}...${addr.slice(-4)}`
}

export default function VaultDetail({ address, onBack }) {
  const [detail, setDetail] = useState(null)
  const [onChainData, setOnChainData] = useState(null)
  const [depositAmt, setDepositAmt] = useState('')
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)
  const [chatOpen, setChatOpen] = useState(true)
  const wallet = getAddress()

  // Fetch backend data
  useEffect(() => {
    if (!address) return
    let cancelled = false
    apiGet(`/api/vaults/${address}`)
      .then(data => { if (!cancelled) setDetail(data) })
      .catch(() => {})
    // Also fetch off-chain metadata (name, symbol, strategies)
    apiGet(`/api/vaults/${address}/metadata`)
      .then(meta => {
        if (!cancelled && meta) {
          setDetail(prev => prev ? { ...prev, ...meta } : prev)
        }
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [address])

  // Fetch on-chain data
  const loadOnChain = useCallback(async () => {
    if (!address) return
    try {
      const [totalAssets, totalSupply, creator, tier, paused, asset] = await Promise.all([
        publicClient.readContract({ address, abi: VAULT_ABI, functionName: 'totalAssets' }),
        publicClient.readContract({ address, abi: VAULT_ABI, functionName: 'totalSupply' }),
        publicClient.readContract({ address, abi: VAULT_ABI, functionName: 'creator' }),
        publicClient.readContract({ address, abi: VAULT_ABI, functionName: 'tier' }),
        publicClient.readContract({ address, abi: VAULT_ABI, functionName: 'paused' }),
        publicClient.readContract({ address, abi: VAULT_ABI, functionName: 'asset' }),
      ])
      setOnChainData({
        totalAssets: Number(totalAssets) / 1e6,
        totalSupply: Number(totalSupply),
        sharePrice: Number(totalSupply) > 0 ? Number(totalAssets) / Number(totalSupply) / 1e6 : 1,
        creator, tier: Number(tier), paused, asset,
      })
    } catch {}
  }, [address])

  useEffect(() => { loadOnChain() }, [loadOnChain])

  const deposit = async () => {
    if (!address || !depositAmt) return
    setBusy(true); setStatus('')
    try {
      const w = await getWalletClient()
      const amount = BigInt(Math.round(parseFloat(depositAmt) * 1e6))
      setStatus('Approving USDC…')
      await w.writeContract({ address: USDC, abi: TOKEN_ABI, functionName: 'approve', args: [address, amount] })
      setStatus('Depositing…')
      const hash = await w.writeContract({ address, abi: VAULT_ABI, functionName: 'deposit', args: [amount, getAddress()] })
      setStatus(`Deposited! TX: ${hash}`)
      setDepositAmt('')
      loadOnChain()
    } catch (err) { setStatus(err.shortMessage || err.message) }
    setBusy(false)
  }

  const name = detail?.name || `Vault ${shortAddr(address)}`
  const symbol = detail?.symbol || 'vAULT'
  const tier = onChainData?.tier ?? detail?.tier ?? 1
  const aum = onChainData?.totalAssets ?? detail?.aum_usdc ?? 0
  const sharePrice = onChainData?.sharePrice ?? detail?.share_price ?? 1

  return (
    <div className="vault-detail-page">
      {/* Back button */}
      <button className="back-btn" onClick={onBack}>
        ← Back to Vaults
      </button>

      {/* Vault Header */}
      <div className="vault-detail-header">
        <div className="vault-detail-title-row">
          <h2 className="vault-detail-name">{name}</h2>
          <span className={`vault-tier-badge tier-${tier}`}>
            {tier === 1 ? '🏆 Verified' : '👥 Community'}
          </span>
        </div>
        <div className="vault-detail-meta">
          <code>{address}</code>
        </div>
      </div>

      {/* Stats Grid */}
      <div className="vault-stats-grid">
        <div className="vault-stat-card">
          <div className="vault-stat-label">AUM</div>
          <div className="vault-stat-value">${aum.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
        </div>
        <div className="vault-stat-card">
          <div className="vault-stat-label">Share Price</div>
          <div className="vault-stat-value">${sharePrice.toFixed(6)}</div>
        </div>
        <div className="vault-stat-card">
          <div className="vault-stat-label">Tier</div>
          <div className="vault-stat-value">{tier === 1 ? 'Paper-grounded' : 'Community'}</div>
        </div>
        <div className="vault-stat-card">
          <div className="vault-stat-label">Creator</div>
          <div className="vault-stat-value"><code>{shortAddr(onChainData?.creator || detail?.creator)}</code></div>
        </div>
      </div>

      {/* Holdings */}
      {detail?.holdings && detail.holdings.length > 0 && (
        <div className="vault-section">
          <h3>Holdings</h3>
          <div className="vault-holdings-list">
            {detail.holdings.map((h, i) => (
              <div key={i} className="vault-holding-row">
                <span className="vault-holding-symbol">{h.symbol}</span>
                <span className="vault-holding-amount">{h.amount?.toFixed(6) ?? '—'}</span>
                <span className="vault-holding-value">${h.value_usdc?.toFixed(2) ?? '—'}</span>
                <span className="vault-holding-weight">{h.weight_pct?.toFixed(1) ?? '—'}%</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Associated Strategies */}
      {detail?.strategy_ids && detail.strategy_ids.length > 0 && (
        <div className="vault-section">
          <h3>Associated Strategies</h3>
          <div className="vault-holdings-list">
            {detail.strategy_ids.map((sid, i) => (
              <div key={i} className="vault-holding-row">
                <span className="vault-holding-symbol">{(detail.strategy_names?.[i]) || sid.slice(0, 12)}…</span>
                <span className="badge tier-1">paper-grounded</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Recent Traces */}
      {detail?.recent_traces && detail.recent_traces.length > 0 && (
        <div className="vault-section">
          <h3>Recent Reasoning Traces</h3>
          <div className="vault-traces-list">
            {detail.recent_traces.map((t, i) => (
              <div key={i} className="vault-trace-row">
                <span className="vault-trace-type">{t.decision_type}</span>
                <code className="vault-trace-hash">{t.trace_hash?.slice(0, 16)}…</code>
                <span className="vault-trace-time">{timeAgo(t.timestamp)}</span>
                {t.is_verified && <span className="vault-trace-verified">✓</span>}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Deposit */}
      <div className="vault-section">
        <h3>Deposit</h3>
        <div className="vault-deposit-row">
          <input
            type="number"
            value={depositAmt}
            onChange={e => setDepositAmt(e.target.value)}
            placeholder="USDC amount"
            className="vault-deposit-input"
          />
          <button
            className="btn btn-primary"
            onClick={deposit}
            disabled={busy || !wallet}
          >
            {busy ? 'Waiting…' : 'Deposit'}
          </button>
        </div>
        {status && <div className="status-msg">{status}</div>}
      </div>

      {/* Chat */}
      <div className="vault-section">
        <VaultChat
          vaultAddress={address}
          isOpen={chatOpen}
          onToggle={() => setChatOpen(o => !o)}
        />
      </div>
    </div>
  )
}
