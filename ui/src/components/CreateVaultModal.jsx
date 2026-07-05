import { useState, useEffect } from 'react'
import { createPortal } from 'react-dom'
import DepositFlow from './DepositFlow'
import {
  getWalletClient,
  publicClient,
  VAULT_ABI,
  VAULT_FACTORY_ABI,
  NEW_CONTRACTS,
} from '../config'
import { decodeEventLog } from 'viem'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

// Opens from StrategyPassport's "Deploy as Vault →" CTA.
// Client-side vault creation: user signs createVault() + setAgent() directly
// so vault.creator == user wallet (not the backend operator). After deploy,
// persists vault metadata (off-chain) via POST /api/vaults/metadata so the
// strategy↔vault link survives reloads. On success, hands off to DepositFlow
// for the 3-step approve→deposit→allocate.

function nowPlusDays(days) {
  const d = new Date()
  d.setDate(d.getDate() + days)
  // <input type="datetime-local"> expects "YYYY-MM-DDTHH:mm"
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export default function CreateVaultModal({ strategy, walletAddr, strictnessLevel = 1, onClose, onDeployed }) {
  // Esc closes modal (Issue #338)
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape' && onClose) onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const defaultName = strategy?.paper_title
    ? strategy.paper_title.slice(0, 48).replace(/\s+$/, '')
    : 'My Strategy Vault'
  // Derive readable symbol from strategy name: first letter of each word, s-prefixed (#389)
  const defaultSymbol = (() => {
    const title = strategy?.paper_title || ''
    if (!title) return strategy?.id ? `sV${String(strategy.id).slice(0, 6).toUpperCase()}` : 'sVAULT'
    const initials = title.replace(/[^a-zA-Z\s]/g, '').split(/\s+/).map(w => w[0]?.toUpperCase() || '').join('')
    return `s${initials}`.slice(0, 8) || 'sVAULT'
  })()

  const [name, setName] = useState(defaultName)
  const [symbol, setSymbol] = useState(defaultSymbol)
  const [windowStart, setWindowStart] = useState(() => nowPlusDays(0))
  const [windowEnd, setWindowEnd] = useState(() => nowPlusDays(30))
  const [initialDeposit, setInitialDeposit] = useState('100')
  const [agentAssisted, setAgentAssisted] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [deployedVault, setDeployedVault] = useState(null) // triggers DepositFlow

  const [deployPhase, setDeployPhase] = useState('') // '', 'creating', 'authorizing', 'metadata'

  const handleDeploy = async () => {
    setError('')
    if (!name.trim() || !symbol.trim()) {
      setError('Name and symbol are required.')
      return
    }
    if (new Date(windowEnd) <= new Date(windowStart)) {
      setError('Window end must be after window start.')
      return
    }
    setSubmitting(true)
    try {
      const walletClient = await getWalletClient()

      // Step 1: Client-side createVault — user signs, so creator == user wallet
      setDeployPhase('creating')
      const createHash = await walletClient.writeContract({
        address: NEW_CONTRACTS.vaultFactory,
        abi: VAULT_FACTORY_ABI,
        functionName: 'createVault',
        args: [name, symbol, 0, 0, agentAssisted],
      })

      // Wait for receipt and extract vault address from VaultCreated event
      const receipt = await publicClient.waitForTransactionReceipt({ hash: createHash })
      let vaultAddress = null
      for (const log of receipt.logs) {
        try {
          const decoded = decodeEventLog({
            abi: VAULT_FACTORY_ABI,
            data: log.data,
            topics: log.topics,
          })
          if (decoded.eventName === 'VaultCreated') {
            vaultAddress = decoded.args.vault
            break
          }
        } catch { /* not our event */ }
      }
      if (!vaultAddress) throw new Error('VaultCreated event not found in tx receipt')

      // Step 2: Authorize agent — user signs setAgent() so the autonomous
      // agent can rebalance on behalf of the vault
      if (agentAssisted) {
        setDeployPhase('authorizing')
        try {
          // Read the factory's configured agent address
          const agentAddr = await publicClient.readContract({
            address: NEW_CONTRACTS.vaultFactory,
            abi: VAULT_FACTORY_ABI,
            functionName: 'agentAddress',
          })
          if (agentAddr && agentAddr !== '0x' + '0'.repeat(40)) {
            const setAgentHash = await walletClient.writeContract({
              address: vaultAddress,
              abi: VAULT_ABI,
              functionName: 'setAgent',
              args: [agentAddr],
            })
            await publicClient.waitForTransactionReceipt({ hash: setAgentHash })
          }
        } catch (agentErr) {
          // Non-fatal — vault is created, agent auth can be retried
          console.warn('setAgent failed (non-fatal):', agentErr)
        }
      }

      // Step 3: Persist off-chain metadata (strategy↔vault link, creator wallet)
      setDeployPhase('metadata')
      try {
        await fetch(`${API_BASE}/api/vaults/metadata`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            vault_address: vaultAddress,
            name,
            symbol,
            creator_address: walletAddr || '',
            strategy_ids: strategy?.id ? [strategy.id] : [],
            // The server re-checks the strategy passes at this strictness before
            // persisting the link — the client-signed deploy path's rigor choke point.
            strictness_level: strictnessLevel,
          }),
        })
      } catch (_metaErr) {
        // Non-fatal — vault exists on-chain; metadata persistence is a UX hint.
      }

      setDeployPhase('')
      if (onDeployed) onDeployed(vaultAddress)
      // Open DepositFlow instead of closing
      setDeployedVault(vaultAddress)
    } catch (e) {
      setError(e.message || 'Vault deployment failed')
      setDeployPhase('')
    } finally {
      setSubmitting(false)
    }
  }

  // After successful vault deploy, show DepositFlow stepper instead of the form
  if (deployedVault) {
    return (
      <DepositFlow
        vaultAddress={deployedVault}
        depositAmount={initialDeposit}
        strategy={strategy}
        onClose={() => { setDeployedVault(null); onClose?.() }}
        onComplete={() => { setDeployedVault(null); onClose?.() }}
      />
    )
  }

  return createPortal(
    <div
      className="fixed inset-0 flex items-center justify-center z-[1000]"
      style={{ background: 'rgba(0,0,0,0.78)', backdropFilter: 'blur(6px)' }}
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-labelledby="deploy-modal-title"
    >
      <div
        className="card-elevated p-6 max-w-[560px] w-[92vw]"
        onClick={e => e.stopPropagation()}
        style={{ background: 'var(--surface-1)', maxHeight: '90vh', overflowY: 'auto' }}
      >
        <div className="caption mb-2 uppercase tracking-wider text-[var(--text-4)]">Deploy vault</div>
        <h3 id="deploy-modal-title" className="font-serif text-[1.5rem] mb-1">
          {strategy?.paper_title || 'Deploy strategy'}
        </h3>
        <p className="caption mb-4 leading-relaxed">
          Creates an ERC-4626 vault on Arc and links it to this strategy. Funds
          stay non-custodial — the agent has rebalance authority only, no withdraw.
        </p>

        <div className="grid grid-cols-1 gap-3">
          <label className="block">
            <span className="caption block mb-1">Vault name</span>
            <input
              type="text"
              value={name}
              onChange={e => setName(e.target.value)}
              maxLength={64}
              className="chat-input w-full p-2.5"
              disabled={submitting}
            />
          </label>

          <label className="block">
            <span className="caption block mb-1">Symbol</span>
            <input
              type="text"
              value={symbol}
              onChange={e => setSymbol(e.target.value.toUpperCase())}
              maxLength={16}
              className="chat-input w-full p-2.5 mono"
              disabled={submitting}
            />
          </label>

          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="caption block mb-1">Window start</span>
              <input
                type="datetime-local"
                value={windowStart}
                onChange={e => setWindowStart(e.target.value)}
                className="chat-input w-full p-2.5"
                disabled={submitting}
              />
            </label>
            <label className="block">
              <span className="caption block mb-1">Window end</span>
              <input
                type="datetime-local"
                value={windowEnd}
                onChange={e => setWindowEnd(e.target.value)}
                className="chat-input w-full p-2.5"
                disabled={submitting}
              />
            </label>
          </div>

          <label className="block">
            <span className="caption block mb-1">Initial deposit (USDC)</span>
            <input
              type="number"
              min="0"
              step="0.01"
              value={initialDeposit}
              onChange={e => setInitialDeposit(e.target.value)}
              className="chat-input w-full p-2.5 mono"
              disabled={submitting}
            />
            <p className="caption mt-1 text-[var(--text-4)]">
              Amount to deposit via the 3-step deposit flow after vault creation.
            </p>
          </label>

          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={agentAssisted}
              onChange={e => setAgentAssisted(e.target.checked)}
              disabled={submitting}
            />
            <span className="body">Agent-assisted (rebalance authority granted to the autonomous agent)</span>
          </label>
        </div>

        <div className="info-box mt-4" style={{ fontSize: '0.8rem' }}>
          <strong>You sign everything.</strong> Vault creation is a 2-step client-side flow:
          <code>createVault</code> → <code>setAgent</code> (authorize rebalancer).
          Then a 3-step deposit: <code>approve</code> → <code>deposit</code> → <code>setAllocations</code>.
          Your wallet is the vault creator — non-custodial by design.
        </div>

        {error && <div className="info-box warning mt-3">{error}</div>}

        <div className="flex justify-end gap-2 mt-5">
          <button className="btn btn-outline" onClick={onClose} disabled={submitting}>
            Cancel
          </button>
          <button
            className="btn btn-primary"
            onClick={handleDeploy}
            disabled={submitting || !walletAddr}
            title={!walletAddr ? 'Connect wallet to deploy' : ''}
          >
            {submitting
              ? (deployPhase === 'creating' ? 'Creating vault… (sign in wallet)'
                : deployPhase === 'authorizing' ? 'Authorizing agent… (sign in wallet)'
                : deployPhase === 'metadata' ? 'Saving metadata…'
                : 'Deploying…')
              : 'Create Vault'}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
