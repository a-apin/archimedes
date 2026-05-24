import { useState, useEffect, useCallback } from 'react'
import { createPortal } from 'react-dom'
import {
  getWalletClient,
  getAddress,
  publicClient,
  USDC,
  TOKEN_ABI,
  VAULT_ABI,
  ASSETS,
} from '../config'

const ARCSCAN_TX = 'https://testnet.arcscan.app/tx'
const STORAGE_PREFIX = 'archimedes_deposit_'

// Step states
const PENDING = 'pending'
const WAITING = 'waiting'      // wallet prompt shown
const CONFIRMING = 'confirming' // tx submitted, awaiting confirmation
const DONE = 'done'
const FAILED = 'failed'

const STEPS = [
  { key: 'approve', label: 'Approve USDC', desc: 'Grant the vault permission to receive your USDC' },
  { key: 'deposit', label: 'Deposit', desc: 'Deposit USDC into the ERC-4626 vault' },
  { key: 'allocate', label: 'Set Allocations', desc: 'Configure the target portfolio allocation' },
]

function loadProgress(vaultAddress) {
  try {
    const raw = localStorage.getItem(`${STORAGE_PREFIX}${vaultAddress}`)
    return raw ? JSON.parse(raw) : null
  } catch { return null }
}

function saveProgress(vaultAddress, stepIndex, txHashes) {
  try {
    localStorage.setItem(`${STORAGE_PREFIX}${vaultAddress}`, JSON.stringify({ stepIndex, txHashes }))
  } catch { /* storage unavailable */ }
}

function clearProgress(vaultAddress) {
  try { localStorage.removeItem(`${STORAGE_PREFIX}${vaultAddress}`) } catch { /* */ }
}

function shortHash(hash) {
  if (!hash) return ''
  return `${hash.slice(0, 10)}…${hash.slice(-6)}`
}

function StatusIcon({ status }) {
  if (status === DONE) return <span style={{ color: 'var(--positive, #22c55e)', fontSize: '1.1rem' }}>✓</span>
  if (status === FAILED) return <span style={{ color: 'var(--negative, #ef4444)', fontSize: '1.1rem' }}>✗</span>
  if (status === CONFIRMING) return <span className="spin" style={{ display: 'inline-block', width: 16, height: 16, border: '2px solid var(--text-4)', borderTopColor: 'var(--accent)', borderRadius: '50%', animation: 'spin 0.8s linear infinite' }} />
  if (status === WAITING) return <span style={{ color: 'var(--accent)' }}>⏳</span>
  return <span style={{ color: 'var(--text-4)' }}>○</span>
}

// Default allocation: equal weight across first 4 synthetics + remainder USDC
function defaultAllocations() {
  const tokens = ASSETS.slice(0, 4)
  const weightPer = Math.floor(7500 / tokens.length) // 75% total across synthetics
  return {
    tokens: tokens.map(a => a.token),
    weights: tokens.map(() => BigInt(weightPer)),
    labels: tokens.map(a => `${a.sym} (${(weightPer / 100).toFixed(0)}%)`),
  }
}

export default function DepositFlow({ vaultAddress, depositAmount = '100', strategy, onClose, onComplete }) {
  // Resume from localStorage if we have prior progress
  const saved = loadProgress(vaultAddress)
  const [currentStep, setCurrentStep] = useState(saved?.stepIndex ?? 0)
  const [txHashes, setTxHashes] = useState(saved?.txHashes ?? [])
  const [statuses, setStatuses] = useState(() => {
    const s = [PENDING, PENDING, PENDING]
    if (saved?.stepIndex > 0) { for (let i = 0; i < saved.stepIndex; i++) s[i] = DONE }
    if (saved?.stepIndex < 3) s[saved.stepIndex] = PENDING
    return s
  })
  const [errors, setErrors] = useState([null, null, null])
  const [amount, setAmount] = useState(depositAmount)

  // Persist progress on every state change
  useEffect(() => {
    saveProgress(vaultAddress, currentStep, txHashes)
  }, [currentStep, txHashes, vaultAddress])

  const updateStep = useCallback((index, field, value) => {
    if (field === 'status') {
      setStatuses(prev => { const n = [...prev]; n[index] = value; return n })
    }
    if (field === 'error') {
      setErrors(prev => { const n = [...prev]; n[index] = value; return n })
    }
    if (field === 'txHash') {
      setTxHashes(prev => { const n = [...prev]; n[index] = value; return n })
    }
  }, [])

  // ── Step 1: USDC.approve(vault, amount) ──────────────────
  const runApprove = useCallback(async () => {
    updateStep(0, 'status', WAITING)
    updateStep(0, 'error', null)
    try {
      const walletClient = await getWalletClient()
      const parsedAmount = BigInt(Math.round(parseFloat(amount) * 1e6))
      if (parsedAmount <= 0n) throw new Error('Amount must be greater than 0')

      const hash = await walletClient.writeContract({
        address: USDC,
        abi: TOKEN_ABI,
        functionName: 'approve',
        args: [vaultAddress, parsedAmount],
      })
      updateStep(0, 'txHash', hash)
      updateStep(0, 'status', CONFIRMING)

      await publicClient.waitForTransactionReceipt({ hash })

      updateStep(0, 'status', DONE)
      setCurrentStep(1)
    } catch (err) {
      updateStep(0, 'status', FAILED)
      updateStep(0, 'error', err.shortMessage || err.message || 'Approval failed')
    }
  }, [amount, vaultAddress, updateStep])

  // ── Step 2: vault.deposit(amount, receiver) ──────────────
  const runDeposit = useCallback(async () => {
    updateStep(1, 'status', WAITING)
    updateStep(1, 'error', null)
    try {
      const walletClient = await getWalletClient()
      const userAddr = getAddress()
      if (!userAddr) throw new Error('Wallet address not available')
      const parsedAmount = BigInt(Math.round(parseFloat(amount) * 1e6))

      const hash = await walletClient.writeContract({
        address: vaultAddress,
        abi: VAULT_ABI,
        functionName: 'deposit',
        args: [parsedAmount, userAddr],
      })
      updateStep(1, 'txHash', hash)
      updateStep(1, 'status', CONFIRMING)

      await publicClient.waitForTransactionReceipt({ hash })

      updateStep(1, 'status', DONE)
      setCurrentStep(2)
    } catch (err) {
      updateStep(1, 'status', FAILED)
      updateStep(1, 'error', err.shortMessage || err.message || 'Deposit failed')
    }
  }, [amount, vaultAddress, updateStep])

  // ── Step 3: vault.setTargetAllocations(tokens, weights) ──
  const runAllocate = useCallback(async () => {
    updateStep(2, 'status', WAITING)
    updateStep(2, 'error', null)
    try {
      const walletClient = await getWalletClient()
      const { tokens, weights } = defaultAllocations()

      const hash = await walletClient.writeContract({
        address: vaultAddress,
        abi: VAULT_ABI,
        functionName: 'setTargetAllocations',
        args: [tokens, weights],
      })
      updateStep(2, 'txHash', hash)
      updateStep(2, 'status', CONFIRMING)

      await publicClient.waitForTransactionReceipt({ hash })

      updateStep(2, 'status', DONE)
      clearProgress(vaultAddress)
      // All done
      setCurrentStep(3)
    } catch (err) {
      updateStep(2, 'status', FAILED)
      updateStep(2, 'error', err.shortMessage || err.message || 'Allocation failed')
    }
  }, [vaultAddress, updateStep])

  const runFns = [runApprove, runDeposit, runAllocate]
  const allDone = statuses.every(s => s === DONE) || currentStep >= 3

  const handleClose = () => {
    // Don't clear progress on close — allows resume
    if (onClose) onClose()
  }

  const handleFinish = () => {
    clearProgress(vaultAddress)
    if (onComplete) onComplete()
  }

  return createPortal(
    <div
      className="fixed inset-0 flex items-center justify-center z-[1000]"
      style={{ background: 'rgba(0,0,0,0.78)', backdropFilter: 'blur(6px)' }}
      onClick={handleClose}
      role="dialog"
      aria-modal="true"
      aria-labelledby="deposit-flow-title"
    >
      <div
        className="card-elevated p-6 max-w-[580px] w-[92vw]"
        onClick={e => e.stopPropagation()}
        style={{ background: 'var(--surface-1)', maxHeight: '90vh', overflowY: 'auto' }}
      >
        {/* Header */}
        <div className="caption mb-2 uppercase tracking-wider text-[var(--text-4)]">
          {allDone ? 'Deposit complete' : 'Fund your vault'}
        </div>
        <h3 id="deposit-flow-title" className="font-serif text-[1.5rem] mb-1">
          {strategy?.paper_title || 'Strategy Vault'}
        </h3>
        <p className="caption mb-1 mono text-[var(--text-4)]">
          Vault: {vaultAddress?.slice(0, 10)}…{vaultAddress?.slice(-6)}
        </p>
        <p className="caption mb-4 leading-relaxed">
          {allDone
            ? 'Your vault is funded and the target allocation is set. The autonomous agent will begin managing it.'
            : 'Three on-chain transactions to fund and configure your vault. Each is signed by your wallet.'}
        </p>

        {/* Amount input */}
        {!allDone && (
          <div className="mb-4">
            <label className="block">
              <span className="caption block mb-1">Deposit amount (USDC)</span>
              <input
                type="number"
                min="0.01"
                step="0.01"
                value={amount}
                onChange={e => setAmount(e.target.value)}
                className="chat-input w-full p-2.5 mono"
                disabled={statuses[0] === DONE} // lock after approve
              />
            </label>
          </div>
        )}

        {/* Stepper */}
        <div className="flex flex-col gap-0">
          {STEPS.map((step, i) => {
            const status = statuses[i]
            const isActive = i === currentStep && !allDone
            const isPast = statuses[i] === DONE
            const isFailed = statuses[i] === FAILED
            const txHash = txHashes[i]

            return (
              <div key={step.key} style={{ opacity: isActive || isPast || isFailed ? 1 : 0.45 }}>
                {/* Step row */}
                <div className="flex items-start gap-3 py-3" style={{
                  borderBottom: i < 2 ? '1px solid var(--glass-border, rgba(255,255,255,0.08))' : 'none',
                }}>
                  <div className="flex flex-col items-center pt-0.5" style={{ minWidth: 24 }}>
                    <StatusIcon status={status} />
                  </div>
                  <div className="flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <span className="body" style={{ fontWeight: isActive ? 600 : 400 }}>
                        {i + 1}. {step.label}
                      </span>
                      {txHash && (
                        <a
                          href={`${ARCSCAN_TX}/${txHash}`}
                          target="_blank"
                          rel="noreferrer"
                          className="mono caption"
                          style={{ color: 'var(--accent)', fontSize: '0.7rem' }}
                        >
                          {shortHash(txHash)} ↗
                        </a>
                      )}
                    </div>
                    <p className="caption mt-0.5 text-[var(--text-3)]">{step.desc}</p>

                    {/* Status details */}
                    {status === CONFIRMING && (
                      <p className="caption mt-1" style={{ color: 'var(--accent)' }}>
                        Confirming on Arc…
                      </p>
                    )}
                    {status === FAILED && errors[i] && (
                      <div className="mt-2">
                        <p className="caption" style={{ color: 'var(--negative, #ef4444)' }}>
                          {errors[i]}
                        </p>
                        <button
                          className="btn btn-outline btn-sm mt-1.5"
                          onClick={() => runFns[i]()}
                          style={{ fontSize: '0.75rem' }}
                        >
                          Retry
                        </button>
                      </div>
                    )}
                    {isActive && status === PENDING && (
                      <button
                        className="btn btn-primary btn-sm mt-2"
                        onClick={() => runFns[i]()}
                      >
                        {i === 0 ? 'Approve USDC' : i === 1 ? 'Deposit' : 'Set Allocations'}
                      </button>
                    )}
                    {status === DONE && (
                      <p className="caption mt-1" style={{ color: 'var(--positive, #22c55e)' }}>
                        Confirmed {txHash ? `· ${shortHash(txHash)}` : ''}
                      </p>
                    )}
                  </div>
                </div>
              </div>
            )
          })}
        </div>

        {/* Allocation detail (collapsible) */}
        {!allDone && currentStep >= 1 && (
          <div className="mt-3 card-flat p-3">
            <div className="caption mb-1 text-[var(--text-4)]">Target allocation (equal weight)</div>
            <div className="flex flex-wrap gap-2">
              {defaultAllocations().labels.map((label, i) => (
                <span key={i} className="tag tag-muted" style={{ fontSize: '0.7rem' }}>{label}</span>
              ))}
              <span className="tag tag-muted" style={{ fontSize: '0.7rem' }}>USDC (25%)</span>
            </div>
          </div>
        )}

        {/* Completion CTA */}
        {allDone && (
          <div className="mt-5 flex justify-end">
            <button className="btn btn-primary" onClick={handleFinish}>
              Done — View Portfolio →
            </button>
          </div>
        )}

        {/* Close / cancel */}
        {!allDone && (
          <div className="flex justify-end gap-2 mt-5">
            <button className="btn btn-outline" onClick={handleClose}>
              {statuses.some(s => s === DONE) ? 'Continue Later' : 'Cancel'}
            </button>
          </div>
        )}
      </div>

      {/* Spinner keyframes (injected once) */}
      <style>{`
        @keyframes spin { to { transform: rotate(360deg) } }
        .spin { animation: spin 0.8s linear infinite; }
      `}</style>
    </div>,
    document.body,
  )
}
