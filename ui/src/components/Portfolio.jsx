import { useState, useEffect, useCallback } from 'react'
import {
  publicClient,
  VAULT_ABI, VAULT_FACTORY_ABI, NEW_CONTRACTS,
  getUsdcBalance,
} from '../config'
import RegimePanel from './RegimePanel'
import StressScenarioPanel from './StressScenarioPanel'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

// /portfolio — Personal dashboard. YOUR AUM, YOUR vaults, YOUR traces.
// The vault marketplace (every vault ever deployed) used to live here too, which
// dragged the surface down with anonymous deploy-seed vaults. That moved to
// /marketplace on 2026-05-25; this page is now strictly personal.

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

export default function Portfolio({ walletAddr, onSelectVault, onSelectTrace, onNavigate }) {
  const [userVaults, setUserVaults] = useState([])
  const [walletUsdc, setWalletUsdc] = useState(null)  // null = not loaded yet; show "—"
  const [agentStatus, setAgentStatus] = useState(null)
  const [recentTraces, setRecentTraces] = useState([])
  const [tracesLoading, setTracesLoading] = useState(false)
  const [vaultsLoading, setVaultsLoading] = useState(false)

  // Load user's own vaults (wallet-gated, from on-chain via VaultFactory.getVaultsByCreator).
  // This is the personal surface; we deliberately do NOT pull /api/vaults/ here —
  // the marketplace listing lives on /marketplace now.
  //
  // Per vault we read totalAssets (current USDC value), totalSupply (cumulative
  // shares minted), and balanceOf(walletAddr) (the connected wallet's share
  // count). Together they give us a price-per-share and an unrealized PnL for
  // the user's position relative to the 1:1 deposit basis ERC-4626 vaults
  // start at. PnL is approximate vs deposit-time basis — accurate while shares
  // were minted at PPS≈1.0 (true for new vaults; degrades as PPS drifts and
  // additional deposits land at the drifted PPS).
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
          const [totalAssets, totalSupply, userShares, tier, name] = await Promise.all([
            publicClient.readContract({ address: addr, abi: VAULT_ABI, functionName: 'totalAssets' }),
            publicClient.readContract({ address: addr, abi: VAULT_ABI, functionName: 'totalSupply' }),
            publicClient.readContract({ address: addr, abi: VAULT_ABI, functionName: 'balanceOf', args: [walletAddr] }),
            publicClient.readContract({ address: addr, abi: VAULT_ABI, functionName: 'tier' }),
            publicClient.readContract({ address: addr, abi: VAULT_ABI, functionName: 'name' }).catch(() => ''),
          ])
          const aum = Number(totalAssets) / 1e6
          const supply = Number(totalSupply) / 1e6
          const shares = Number(userShares) / 1e6
          // Price-per-share: 1.0 baseline, drifts as the vault gains/loses value.
          const pps = supply > 0 ? aum / supply : 1
          // User's current position value + unrealized PnL vs 1:1 basis.
          const userValue = shares * pps
          const userPnlUsdc = userValue - shares           // $ above (or below) basis
          const userPnlPct = shares > 0 ? (pps - 1) * 100 : null
          return {
            address: addr, aum, tier: Number(tier), name,
            shares, userValue, userPnlUsdc, userPnlPct,
          }
        } catch { return null }
      }))
      setUserVaults(rows.filter(Boolean))
    } catch {
      setUserVaults([])
    } finally {
      setVaultsLoading(false)
    }
  }, [walletAddr])

  // Wallet USDC balance — idle funds, "ready to deploy". Polled with the
  // vault list because the two numbers compose the user's total position.
  const loadWalletUsdc = useCallback(async () => {
    if (!walletAddr) { setWalletUsdc(null); return }
    const v = await getUsdcBalance(walletAddr)
    setWalletUsdc(v)
  }, [walletAddr])

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

  useEffect(() => { loadVaults(); loadWalletUsdc() }, [loadVaults, loadWalletUsdc])
  useEffect(() => { loadAgentAndRegime(); loadTraces() }, [loadAgentAndRegime, loadTraces])
  useEffect(() => {
    const t = setInterval(() => {
      loadAgentAndRegime(); loadTraces(); loadWalletUsdc(); loadVaults()
    }, 30_000)
    return () => clearInterval(t)
  }, [loadAgentAndRegime, loadTraces, loadWalletUsdc, loadVaults])

  // YOUR AUM — sum across vaults the connected wallet created.
  // Wallet-disconnected users see 0; wallet-connected users see real $ at risk.
  const yourAum = userVaults.reduce((s, v) => s + v.aum, 0)
  const hasVaults = userVaults.length > 0

  // Aggregate unrealized PnL across the user's vault positions (positions they
  // hold shares in, not just vaults they created). Honest "—" when there are
  // no shares to compute against.
  const totalShares = userVaults.reduce((s, v) => s + (v.shares || 0), 0)
  const totalPositionValue = userVaults.reduce((s, v) => s + (v.userValue || 0), 0)
  const aggregatePnlUsdc = totalShares > 0 ? totalPositionValue - totalShares : null
  const aggregatePnlPct = totalShares > 0 ? (totalPositionValue / totalShares - 1) * 100 : null

  // Format helpers — "—" everywhere a number isn't computable so we never
  // show a misleading 0.
  const fmtUsd = (n) => n == null ? '—' : `$${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
  const fmtPct = (n) => n == null ? '—' : `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`
  const pnlColor = (n) => n == null ? 'var(--text-3)' : n > 0 ? 'var(--positive)' : n < 0 ? 'var(--negative)' : 'var(--text-1)'

  return (
    <div>
      <div className="max-w-[720px] mb-6 flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h2 className="serif text-[2rem] mb-2.5">Portfolio</h2>
          <p className="body">
            Your AUM, your vaults, your agent's rebalance traces — all in one place.
            Every action has a reasoning trace anchored on Arc; click any trace to inspect.
          </p>
        </div>
        {/* Regime context — small pill; full breakdown lives on /learnings. */}
        <RegimePanel compact />
      </div>

      {/* Account header — the four numbers a wallet-connected user wants to
          see at a glance. Wallet USDC = idle, ready to deploy. Vault AUM =
          deployed at risk. Unrealized PnL = since-deposit drift (approximate;
          tooltip explains). Agent = system status, since live execution
          depends on it. */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        <div className="card-flat p-4">
          <div className="label mb-2">Wallet USDC</div>
          <div className="text-[1.6rem] font-bold">{fmtUsd(walletUsdc)}</div>
          <div className="caption mt-1.5">Idle — ready to deploy</div>
        </div>
        <div className="card-flat p-4">
          <div className="label mb-2">Vault AUM</div>
          <div className="text-[1.6rem] font-bold">{fmtUsd(yourAum)}</div>
          <div className="caption mt-1.5">
            Across {userVaults.length} {userVaults.length === 1 ? 'vault' : 'vaults'}
          </div>
        </div>
        <div
          className="card-flat p-4"
          title="Unrealized PnL is the current value of your vault shares minus the 1:1 USDC basis they were minted at. Approximate while shares were minted at PPS≈1.0 (true for new vaults; drifts as PPS does)."
        >
          <div className="label mb-2">Unrealized PnL</div>
          <div className="text-[1.6rem] font-bold" style={{ color: pnlColor(aggregatePnlUsdc) }}>
            {fmtUsd(aggregatePnlUsdc)}
          </div>
          <div className="caption mt-1.5" style={{ color: pnlColor(aggregatePnlPct) }}>
            {fmtPct(aggregatePnlPct)} since deposit
          </div>
        </div>
        <div className="card-flat p-4">
          <div className="label mb-2">Agent</div>
          <div className="flex items-center gap-2 font-bold text-[1.6rem]">
            <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${agentStatus?.alive ? 'bg-[var(--positive)] shadow-[0_0_6px_var(--positive)]' : 'bg-[var(--negative)]'}`} />
            {agentStatus?.alive ? 'Alive' : 'Offline'}
          </div>
          <div className="caption mt-1.5">
            {agentStatus?.last_heartbeat ? `Heartbeat ${timeAgo(agentStatus.last_heartbeat)}` : 'No heartbeat yet'}
          </div>
        </div>
      </div>

      {/* Your Vaults — the hero. */}
      {!vaultsLoading && !hasVaults && (
        <div className="card mb-7" style={{ padding: 24, textAlign: 'center' }}>
          <h3 className="serif text-[1.4rem] mb-2">You haven't deployed a vault yet</h3>
          <p className="body mb-4" style={{ color: 'var(--text-3)' }}>
            A vault is the non-custodial container that holds your USDC and runs your strategy.
            Generate one in minutes — or browse what others have built.
          </p>
          <div className="flex justify-center gap-3 flex-wrap">
            <button
              className="btn btn-primary"
              onClick={() => onNavigate?.('generate')}
            >
              Generate a Strategy
            </button>
            <a
              onClick={() => onNavigate?.('marketplace')}
              style={{ color: 'var(--accent)', cursor: 'pointer', textDecoration: 'underline', alignSelf: 'center' }}
            >
              Browse Marketplace
            </a>
          </div>
        </div>
      )}

      {vaultsLoading && (
        <div className="mb-7">
          <div className="label mb-3">Your Vault Positions</div>
          <div className="caption">Loading vaults…</div>
        </div>
      )}

      {hasVaults && (
        <div className="mb-7">
          <div className="label mb-3">Your Vault Positions</div>
          <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))' }}>
            {userVaults.map(v => (
              <div
                key={v.address}
                className="card vault-card-clickable"
                onClick={() => onSelectVault?.(v.address)}
                style={{ padding: 18 }}
              >
                <div className="flex justify-between items-center mb-2">
                  <span className="font-semibold" style={{ fontSize: '0.95rem' }}>
                    {v.name || `Vault ${shortAddr(v.address)}`}
                  </span>
                  <span className={`tag ${v.tier === 1 ? 'tag-accent' : 'tag-muted'}`}>
                    {v.tier === 1 ? '🏆 Verified' : '👥 Community'}
                  </span>
                </div>
                <div className="flex items-baseline gap-2 mt-3 mb-1">
                  <span className="text-[1.5rem] font-bold">
                    ${v.aum.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                  </span>
                  <span className="caption">AUM</span>
                </div>
                <div className="flex justify-between items-center mt-2 flex-wrap gap-2">
                  <code className="caption" style={{ color: 'var(--text-3)' }}>{shortAddr(v.address)}</code>
                  {v.shares > 0 ? (
                    <span
                      className="caption font-medium"
                      style={{ color: pnlColor(v.userPnlUsdc) }}
                      title={`Your shares: ${v.shares.toFixed(2)} • PPS: ${(v.userValue / v.shares).toFixed(4)}`}
                    >
                      {fmtPct(v.userPnlPct)}
                    </span>
                  ) : (
                    <span className="caption" style={{ color: 'var(--text-3)' }} title="You don't hold shares in this vault">
                      —
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Stress scenarios — collapsed by default, only when user has vaults */}
      {hasVaults && (
        <div className="mb-7">
          <details className="card" style={{ padding: 0 }}>
            <summary className="label cursor-pointer" style={{ padding: '14px 18px' }}>
              🔥 Stress Scenarios
            </summary>
            <div style={{ padding: '0 18px 18px' }}>
              <StressScenarioPanel
                allocations={[]}
                usdcWeight={0}
                portfolioValue={yourAum || 10000}
              />
            </div>
          </details>
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
                  {t.arc_tx_hash ? (
                    <a
                      href={`https://testnet.arcscan.app/tx/${t.arc_tx_hash}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="mono underline decoration-dotted underline-offset-2 hover:text-[var(--accent)] transition-colors"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {t.arc_tx_hash.slice(0, 10)}… ↗
                    </a>
                  ) : t.trace_hash ? (
                    <span className="mono">{t.trace_hash.slice(0, 10)}…</span>
                  ) : null}
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
