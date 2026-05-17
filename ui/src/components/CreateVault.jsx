import { useState, useEffect, useCallback } from 'react'
import {
  publicClient, getWalletClient, getAddress,
  VAULT_FACTORY_ABI, VAULT_ABI,
  NEW_CONTRACTS,
} from '../config'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

async function apiGet(path) {
  const res = await fetch(`${API_BASE}${path}`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

async function apiPost(path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export default function CreateVault({ onVaultCreated }) {
  const [strategies, setStrategies] = useState([])
  const [loadError, setLoadError] = useState('')
  const [loading, setLoading] = useState(true)

  const [name, setName] = useState('')
  const [symbol, setSymbol] = useState('')
  const [mgmtFeeBps, setMgmtFeeBps] = useState(50)
  const [perfFeeBps, setPerfFeeBps] = useState(1000)
  const [agentAssisted, setAgentAssisted] = useState(true)
  const [selectedIds, setSelectedIds] = useState([])

  const [deploying, setDeploying] = useState(false)
  const [deployError, setDeployError] = useState('')
  const [deployStep, setDeployStep] = useState('')
  const [vaultAddress, setVaultAddress] = useState(null)
  const [txHash, setTxHash] = useState(null)

  const wallet = getAddress()

  const loadStrategies = useCallback(async () => {
    setLoading(true)
    setLoadError('')
    try {
      const data = await apiGet('/api/strategies/')
      setStrategies(data.strategies || [])
    } catch (e) {
      setLoadError(e.message || 'Failed to load strategies')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadStrategies() }, [loadStrategies])

  const toggleStrategy = (id) => {
    setSelectedIds(prev =>
      prev.includes(id) ? prev.filter(s => s !== id) : [...prev, id]
    )
  }

  const deploy = async () => {
    if (!wallet) {
      setDeployError('Connect your wallet first.')
      return
    }

    // Validate
    if (!name.trim()) { setDeployError('Vault name is required.'); return }
    if (!symbol.trim()) { setDeployError('Symbol is required.'); return }
    const mgmt = Number(mgmtFeeBps)
    const perf = Number(perfFeeBps)
    if (mgmt < 0 || mgmt > 1000) { setDeployError('Management fee must be 0–1000 BPS.'); return }
    if (perf < 0 || perf > 3000) { setDeployError('Performance fee must be 0–3000 BPS.'); return }

    setDeploying(true)
    setDeployError('')
    setVaultAddress(null)
    setTxHash(null)

    const factoryAddress = NEW_CONTRACTS.vaultFactory
    if (!factoryAddress) {
      setDeployError('VaultFactory address not configured. Check config.js NEW_CONTRACTS.')
      setDeploying(false)
      return
    }

    try {
      const w = await getWalletClient()

      setDeployStep('Sending transaction…')

      // Call VaultFactory.createVault(name, symbol, managementFeeBps, performanceFeeBps, agentAssisted)
      const hash = await w.writeContract({
        address: factoryAddress,
        abi: VAULT_FACTORY_ABI,
        functionName: 'createVault',
        args: [
          name.trim(),
          symbol.trim().toUpperCase(),
          mgmt,
          perf,
          agentAssisted,
        ],
      })

      setTxHash(hash)
      setDeployStep('Waiting for confirmation…')

      // Wait for receipt to parse VaultCreated event
      const receipt = await publicClient.waitForTransactionReceipt({ hash })

      // Decode VaultCreated event from logs
      let newVaultAddress = null
      for (const log of receipt.logs) {
        try {
          const decoded = publicClient.decodeEventLog({
            abi: VAULT_FACTORY_ABI,
            data: log.data,
            topics: log.topics,
          })
          if (decoded.eventName === 'VaultCreated' && decoded.args?.vault) {
            newVaultAddress = decoded.args.vault
            break
          }
        } catch {
          // skip undecodable logs (other contract events)
        }
      }

      // Fallback: get last vault from factory list
      if (!newVaultAddress) {
        const allVaults = await publicClient.readContract({
          address: factoryAddress,
          abi: VAULT_FACTORY_ABI,
          functionName: 'getVaults',
        })
        newVaultAddress = allVaults[allVaults.length - 1]
      }

      setVaultAddress(newVaultAddress)
      setDeployStep('')

      // Store strategy association off-chain
      if (newVaultAddress && selectedIds.length > 0) {
        try {
          await apiPost('/api/vaults/metadata', {
            vault_address: newVaultAddress,
            strategy_ids: selectedIds,
            name: name.trim(),
            symbol: symbol.trim().toUpperCase(),
          })
        } catch {
          // Non-fatal — vault is deployed on-chain, metadata is best-effort
        }
      }

      if (onVaultCreated) onVaultCreated(newVaultAddress)
    } catch (err) {
      setDeployError(err.shortMessage || err.message || 'Deployment failed')
      setDeployStep('')
    } finally {
      setDeploying(false)
    }
  }

  const canDeploy = name.trim() && symbol.trim() && !deploying && wallet

  return (
    <div className="panel">
      <h2>Create Vault</h2>
      <p className="hint" style={{ marginTop: 8 }}>
        Deploy a new managed vault on Arc. Select strategies to associate, set fee
        parameters, and optionally enable agent-assisted rebalancing.
      </p>

      {!wallet && (
        <div className="info-box warning" style={{ marginTop: 16 }}>
          Connect your wallet to deploy a vault. Your wallet will sign the on-chain transaction.
        </div>
      )}

      <div className="card" style={{ marginTop: 20 }}>
        <h3>Vault details</h3>

        <div className="form-row" style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginTop: 12 }}>
          <div className="form-group" style={{ flex: 1 }}>
            <label className="label">Vault name</label>
            <input
              className="chat-input"
              type="text"
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="Archimedes Moderate Growth"
            />
          </div>
          <div className="form-group">
            <label className="label">Symbol</label>
            <input
              className="chat-input"
              type="text"
              value={symbol}
              onChange={e => setSymbol(e.target.value.toUpperCase())}
              placeholder="AMG"
              maxLength={16}
              style={{ width: 110 }}
            />
          </div>
        </div>

        <div className="form-row" style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginTop: 12 }}>
          <div className="form-group">
            <label className="label">Management fee (bps)</label>
            <input
              className="chat-input"
              type="number"
              min="0"
              max="1000"
              value={mgmtFeeBps}
              onChange={e => setMgmtFeeBps(e.target.value)}
              style={{ width: 120 }}
            />
            <div className="caption" style={{ marginTop: 4 }}>
              {(Number(mgmtFeeBps) / 100).toFixed(2)}% / year
            </div>
          </div>

          <div className="form-group">
            <label className="label">Performance fee (bps)</label>
            <input
              className="chat-input"
              type="number"
              min="0"
              max="3000"
              value={perfFeeBps}
              onChange={e => setPerfFeeBps(e.target.value)}
              style={{ width: 120 }}
            />
            <div className="caption" style={{ marginTop: 4 }}>
              {(Number(perfFeeBps) / 100).toFixed(2)}% on profits
            </div>
          </div>

          <div className="form-group" style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
            <label className="label">Agent-assisted</label>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 6 }}>
              <button
                type="button"
                className={`btn${agentAssisted ? ' btn-primary' : ''}`}
                style={{ width: 'auto', padding: '4px 16px' }}
                onClick={() => setAgentAssisted(true)}
              >
                On
              </button>
              <button
                type="button"
                className={`btn${!agentAssisted ? ' btn-primary' : ''}`}
                style={{ width: 'auto', padding: '4px 16px' }}
                onClick={() => setAgentAssisted(false)}
              >
                Off
              </button>
            </div>
          </div>
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h3>Strategies</h3>
        <p className="hint" style={{ marginTop: 4 }}>
          Select strategies to associate with this vault.
          {selectedIds.length > 0 && ` ${selectedIds.length} selected.`}
        </p>

        {loading && <div className="loading" style={{ marginTop: 12 }}>Loading strategies…</div>}
        {loadError && (
          <div className="info-box warning" style={{ marginTop: 12 }}>
            Couldn't load strategies: {loadError}
          </div>
        )}

        <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {strategies.map(s => {
            const selected = selectedIds.includes(s.id)
            return (
              <div
                key={s.id}
                className="vault-card vault-card-clickable"
                style={selected ? { border: '1px solid var(--accent, #D4A853)', background: 'rgba(212,168,83,0.08)' } : {}}
                onClick={() => toggleStrategy(s.id)}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                  <strong>{s.paper_title}</strong>
                  <span className={`badge${selected ? ' tier-1' : ''}`}>
                    {selected ? 'selected' : s.status}
                  </span>
                </div>
                {s.paper_authors?.length > 0 && (
                  <p className="caption" style={{ marginTop: 2 }}>{s.paper_authors.join(', ')}</p>
                )}
                <p className="hint" style={{ marginTop: 6 }}>{s.methodology_summary}</p>
                <p className="caption" style={{ marginTop: 4 }}>
                  {s.asset_universe?.join(' · ')} · {s.position_sizing} · {s.rebalance_frequency}
                </p>
              </div>
            )
          })}
        </div>
      </div>

      {deployError && (
        <div className="info-box warning" style={{ marginTop: 16 }}>
          Deployment failed: {deployError}
        </div>
      )}

      {deployStep && (
        <div className="info-box" style={{ marginTop: 16 }}>
          {deployStep}
        </div>
      )}

      {txHash && !vaultAddress && (
        <div className="info-box" style={{ marginTop: 16 }}>
          TX submitted: <code>{txHash}</code>. Waiting for confirmation…
        </div>
      )}

      {vaultAddress && (
        <div className="info-box" style={{ marginTop: 16 }}>
          ✅ Vault deployed at <code>{vaultAddress}</code>
          {txHash && <><br/>TX: <code>{txHash}</code></>}
          <div className="caption" style={{ marginTop: 4 }}>
            {wallet?.toLowerCase() === '0x...agent'.toLowerCase()
              ? '🏆 Tier 1 — Verified (agent-deployed)'
              : '👥 Tier 2 — Community (user-deployed)'}
          </div>
        </div>
      )}

      <button
        className="btn btn-primary"
        style={{ marginTop: 20, width: '100%' }}
        onClick={deploy}
        disabled={!canDeploy}
      >
        {deploying ? deployStep || 'Deploying vault…' : wallet ? 'Deploy vault on Arc' : 'Connect wallet to deploy'}
      </button>
    </div>
  )
}
