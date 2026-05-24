import { useState, useEffect, useCallback } from 'react'
import {
  publicClient,
  VAULT_ABI, VAULT_FACTORY_ABI, NEW_CONTRACTS,
} from '../config'
import RegimePanel from './RegimePanel'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

function timeAgo(iso) {
  const d = typeof iso === 'string' ? new Date(iso) : new Date(iso * 1000)
  const secs = Math.floor((Date.now() - d.getTime()) / 1000)
  if (Number.isNaN(secs)) return '—'
  if (secs < 60) return `${secs}s ago`
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  return `${Math.floor(secs / 86400)}d ago`
}

function shortAddr(a) {
  return a ? `${a.slice(0, 6)}…${a.slice(-4)}` : '—'
}

export default function Portfolio({ walletAddr, onSelectVault, onSelectTrace }) {
  const [userVaults, setUserVaults] = useState([])
  const [allVaults, setAllVaults] = useState([])
  const [agentStatus, setAgentStatus] = useState(null)
  const [recentTraces, setRecentTraces] = useState([])
  const [tracesLoading, setTracesLoading] = useState(false)
  const [vaultsLoading, setVaultsLoading] = useState(false)

  // Load user's own vaults (wallet-gated, from on-chain)
  const loadVaults = useCallback(async () => {
    const factoryAddr = NEW_CONTRACTS.vaultFactory
    if (!factoryAddr || !walletAddr) { setUserVaults([]); return }
    setVaultsLoading(true)
    try {
      const creatorVaults = await publicClient.readContract({
        address: factoryAddr, abi: VAULT_FACTORY_ABI, functionName: 'getVaultsByCreator', args: [walletAddr],
      })
      const rows = await Promise.all((creatorVaults || []).map(async (addr) => {
        try {
          const [totalAssets, tier, name] = await Promise.all([
            publicClient.readContract({ address: addr, abi: VAULT_ABI, functionName: 'totalAssets' }),
            publicClient.readContract({ address: addr, abi: VAULT_ABI, functionName: 'tier' }),
            publicClient.readContract({ address: addr, abi: VAULT_ABI, functionName: 'name' }).catch(() => ''),
          ])
          return { address: addr, aum: Number(totalAssets) / 1e6, tier: Number(tier), name }
        } catch { return null }
      }))
      setUserVaults(rows.filter(Boolean))
    } catch {
      setUserVaults([])
    } finally {
      setVaultsLoading(false)
    }
  }, [walletAddr])

  // Load ALL vaults from backend API (not wallet-gated)
  const loadAllVaults = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/api/vaults/`)
      if (r.ok) {
        const data = await r.json()
        setAllVaults(data.vaults || [])
      }
    } catch {
      // Network blip — leave prior allVaults intact; next 30s poll retries.
    }
  }, [])

  const loadAgentAndRegime = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/api/agent/status`)
      if (r.ok) setAgentStatus(await r.json())
    } catch {
      // Network blip — leave prior agentStatus intact; next poll retries.
    }
    // Regime is loaded + rendered by <RegimePanel /> above, not duplicated here.
  }, [])

  const loadTraces = useCallback(async () => {
    setTracesLoading(true)
    try {
      const r = await fetch(`${API_BASE}/api/traces/?limit=20`)
      if (r.ok) {
        const data = await r.json()
        setRecentTraces(data.traces || [])
      }
    } catch {
      // Network blip — leave prior recentTraces intact; next poll retries.
    }
    setTracesLoading(false)
  }, [])

  useEffect(() => { loadVaults() }, [loadVaults])
  useEffect(() => { loadAllVaults(); loadAgentAndRegime(); loadTraces() }, [loadAllVaults, loadAgentAndRegime, loadTraces])
  useEffect(() => {
    const t = setInterval(() => { loadAllVaults(); loadAgentAndRegime(); loadTraces() }, 30_000)
    return () => clearInterval(t)
  }, [loadAllVaults, loadAgentAndRegime, loadTraces])

  // YOUR AUM — sum across vaults the connected wallet created.
  // Wallet-disconnected users see 0; wallet-connected users see real $ at risk.
  const yourAum = userVaults.reduce((s, v) => s + v.aum, 0)

  return (
    <div>
      <div className="max-w-[720px] mb-6 flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h2 className="serif text-[2rem] mb-2.5">Portfolio</h2>
          <p className="body">
            Browse vaults, monitor the agent, and inspect every rebalance decision.
            Every action has a reasoning trace anchored on Arc — click to inspect.
          </p>
        </div>
        {/* Regime context — small pill; full breakdown lives on /learnings. */}
        <RegimePanel compact />
      </div>

      {/* Status strip — Your AUM (wallet-scoped) + marketplace vault count + agent. */}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-4 mb-6">
        <div className="card-flat p-4">
          <div className="label mb-2">Your AUM</div>
          <div className="text-[1.8rem] font-bold">
            ${yourAum.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </div>
          <div className="caption mt-1.5">
            Across {userVaults.length} {userVaults.length === 1 ? 'vault' : 'vaults'} you created
          </div>
        </div>
        <div className="card-flat p-4">
          <div className="label mb-2">Marketplace</div>
          <div className="text-[1.8rem] font-bold">{allVaults.length}</div>
          <div className="caption mt-1.5">
            {allVaults.filter(v => v.tier === 1).length} Tier 1 · {allVaults.filter(v => v.tier === 2).length} Tier 2
          </div>
        </div>
        <div className="card-flat p-4">
          <div className="label mb-2">Agent</div>
          <div className="flex items-center gap-2 font-bold text-[1.8rem]">
            <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${agentStatus?.alive ? 'bg-[var(--positive)] shadow-[0_0_6px_var(--positive)]' : 'bg-[var(--negative)]'}`} />
            {agentStatus?.alive ? 'Alive' : 'Offline'}
          </div>
          <div className="caption mt-1.5">
            Last heartbeat: {agentStatus?.last_heartbeat ? timeAgo(agentStatus.last_heartbeat) : '—'}
          </div>
        </div>
      </div>

      {/* Vault marketplace — every deployed vault (yours and others). */}
      <div className="mb-7">
        <div className="label mb-3">Vault Marketplace</div>
        {allVaults.length === 0 && (
          <div className="card" style={{ padding: 18 }}>
            <p className="body">No vaults deployed yet.</p>
          </div>
        )}
        {allVaults.length > 0 && (
          <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))' }}>
            {allVaults.map(v => (
              <div key={v.address} className="card vault-card-clickable" onClick={() => onSelectVault?.(v.address)}>
                <div className="flex justify-between items-center mb-2">
                  <span className="font-semibold text-sm" style={{ color: 'var(--text-1)' }}>
                    {v.name || `Vault ${shortAddr(v.address)}`}
                  </span>
                  <span className={`tag ${v.tier === 1 ? 'tag-accent' : 'tag-muted'}`}>
                    {v.tier === 1 ? '🏆 Verified' : '👥 Community'}
                  </span>
                </div>
                <div className="flex items-baseline gap-2 mt-3 mb-1">
                  <span className="text-[1.4rem] font-bold">
                    ${(v.aum_usdc || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                  </span>
                  <span className="caption">AUM</span>
                </div>
                <div className="caption flex gap-3 text-[var(--text-3)] mt-1">
                  <code>{shortAddr(v.address)}</code>
                  {v.is_agent_assisted && <span style={{ color: 'var(--accent)' }}>AI-managed</span>}
                </div>
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

      {/* User's own vaults (wallet-gated) */}
      {walletAddr && userVaults.length > 0 && (
        <div className="mb-7">
          <div className="label mb-3">Your Vault Positions</div>
          {vaultsLoading && <div className="caption">Loading vaults…</div>}
          <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))' }}>
            {userVaults.map(v => (
              <div key={v.address} className="card vault-card-clickable" onClick={() => onSelectVault?.(v.address)}>
                <div className="flex justify-between mb-2">
                  <code style={{ fontSize: '0.8rem' }}>{shortAddr(v.address)}</code>
                  <span className={`tag ${v.tier === 1 ? 'tag-accent' : 'tag-muted'}`}>T{v.tier}</span>
                </div>
                {v.name && <div className="caption" style={{ marginBottom: 4 }}>{v.name}</div>}
                <div className="text-[1.2rem] font-bold">${v.aum.toFixed(2)}</div>
                <div className="caption">AUM</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Agent activity feed — real traces from /api/traces */}
      <div>
        <div className="label mb-3">Your Traces</div>
        {tracesLoading && <div className="caption">Loading traces…</div>}
        {!tracesLoading && recentTraces.length === 0 && (
          <div className="card" style={{ padding: 18 }}>
            <p className="body" style={{ marginBottom: 6 }}>No agent activity yet.</p>
            <p className="caption">
              Deploy a vault to see agent decisions here, or{' '}
              <a onClick={() => onSelectTrace?.(undefined)} style={{ color: 'var(--accent)', cursor: 'pointer', textDecoration: 'underline' }}>
                browse all traces on the Reasoning page
              </a>.
            </p>
          </div>
        )}
        {recentTraces.length > 0 && (
          <div className="flex flex-col gap-2">
            {recentTraces.map(t => (
              <div
                key={t.id}
                className="trace-card vault-card-clickable"
                onClick={() => onSelectTrace?.(t.id)}
                style={{ cursor: 'pointer' }}
                title={`Click to view trace ${t.id}`}
              >
                <div className="flex justify-between items-center gap-3 flex-wrap">
                  <div>
                    <span className="tag tag-muted mr-2 capitalize">{t.decision_type}</span>
                    <strong style={{ fontSize: '0.9rem' }}>{t.trigger}</strong>
                  </div>
                  <span className="caption">{t.timestamp ? timeAgo(t.timestamp) : ''}</span>
                </div>
                {t.reasoning && (
                  <div className="caption mt-1.5 leading-relaxed">
                    {t.reasoning.slice(0, 180)}{t.reasoning.length > 180 ? '…' : ''}
                  </div>
                )}
                <div className="caption mt-1.5 flex gap-3 text-[var(--text-3)]">
                  {t.vault_address && <span>vault {shortAddr(t.vault_address)}</span>}
                  {t.trace_hash && <span className="mono">{t.trace_hash.slice(0, 10)}…</span>}
                  {t.is_verified
                    ? <span className="flex items-center gap-1 text-[var(--positive)]"><span className="i-lucide-check-circle w-3.5 h-3.5" /> anchored on Arc</span>
                    : <span className="flex items-center gap-1 text-[var(--text-3)]" title="Trace hashed + persisted off-chain; on-chain anchor pending (registry write didn't complete yet — usually transient).">
                        <span className="i-lucide-clock w-3.5 h-3.5" /> anchor pending
                      </span>
                  }
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
