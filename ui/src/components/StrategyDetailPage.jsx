import { useEffect, useState } from 'react'
import { toEventSelector } from 'viem'
import { apiGet, apiPost } from '../api'
import {
  getAddress,
  getConnectedProvider,
  getWalletClient,
  publicClient,
  USDC,
  USDC_DECIMALS,
  USDC_ABI,
  NEW_CONTRACTS,
  SUBSCRIPTION_MANAGER_ABI,
} from '../config'
import { parseUnits } from 'viem'

function shortAddr(a) {
  return a ? `${a.slice(0, 6)}…${a.slice(-4)}` : '—'
}

function fmtTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

const SUBSCRIPTION_MANAGER = NEW_CONTRACTS.subscriptionManager

// Pre-compute event topic hashes for the SubscriptionManager contract.
const SUBSCRIBED_TOPIC = toEventSelector('Subscribed(bytes32,address,bytes32,string)')
const EWC_TOPIC = toEventSelector('EphemeralWalletCreated(bytes32,address,address)')

// Step indicator component
function Step({ num, label, status }) {
  const dot = status === 'done' ? '✓' : status === 'active' ? num : num
  const cls = status === 'done' ? 'bg-[var(--color-primary)] text-white'
    : status === 'active' ? 'bg-[var(--color-accent)] text-white'
    : 'bg-[var(--color-surface)] text-[var(--color-muted)]'
  return (
    <div className="flex items-center gap-2 text-sm">
      <span className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-medium ${cls}`}>
        {dot}
      </span>
      <span className={status === 'done' ? '' : 'text-[var(--color-muted)]'}>{label}</span>
    </div>
  )
}

export default function StrategyDetailPage({ strategyId, onNavigate }) {
  const [strategy, setStrategy] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // Subscribe flow state
  const [initialDeposit, setInitialDeposit] = useState('100')
  const [stepStatus, setStepStatus] = useState({ 1: 'idle', 2: 'idle', 3: 'idle' })
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
  const isCircle = getConnectedProvider() === 'circle-passkey'

  const handleSubscribe = async () => {
    setSubError('')
    setSubSuccess('')
    setBusy(true)

    // USDC on Arc uses 6 decimals (USDC_DECIMALS = 6)
    const depositAmount = parseUnits(initialDeposit, USDC_DECIMALS)

    try {
      // Step 1: USDC.approve(SUBSCRIPTION_MANAGER, amount)
      setStepStatus({ 1: 'active', 2: 'idle', 3: 'idle' })
      const walletClient = await getWalletClient()
      const approveHash = await walletClient.writeContract({
        address: USDC,
        abi: USDC_ABI,
        functionName: 'approve',
        args: [SUBSCRIPTION_MANAGER, depositAmount],
      })
      setStepStatus({ 1: 'verifying', 2: 'idle', 3: 'idle' })
      // No need to await receipt — the subscribe call will fail if not approved

      // Step 2: SubscriptionManager.subscribe(pool_id, webhookPlaceholder, initialDeposit)
      // webhookPlaceholder must be non-empty (contract requires it), but the monolith
      // doesn't use webhooks (D-FANOUT).
      const webhookPlaceholder = 'archimedes-monolith-v1'
      setStepStatus({ 1: 'done', 2: 'active', 3: 'idle' })
      const subscribeHash = await walletClient.writeContract({
        address: SUBSCRIPTION_MANAGER,
        abi: SUBSCRIPTION_MANAGER_ABI,
        functionName: 'subscribe',
        args: [strategy.pool_id, webhookPlaceholder, depositAmount],
      })

      // Wait for receipt and parse the Subscribed event
      const receipt = await publicClient.waitForTransactionReceipt({ hash: subscribeHash })

      // Parse Subscribed event for sub_id (indexed bytes32 → topics[1])
      const subscribedLog = receipt.logs.find(log => log.topics[0] === SUBSCRIBED_TOPIC)
      if (!subscribedLog) {
        throw new Error('Subscribe succeeded but could not find Subscribed event. Refresh the page and check your subscriptions.')
      }
      const subId = subscribedLog.topics[1]

      // Parse EphemeralWalletCreated event for ephemeral_wallet address
      // address is indexed and left-padded to 32 bytes in topics[2]
      const ewcLog = receipt.logs.find(log => log.topics[0] === EWC_TOPIC)
      const ephemeralWallet = ewcLog
        ? '0x' + ewcLog.topics[2].slice(26)
        : ''

      setStepStatus({ 1: 'done', 2: 'done', 3: 'active' })

      // Step 3: POST /api/marketplace/subscribe
      await apiPost('/api/marketplace/subscribe', {
        strategy_id: strategyId,
        pool_id: strategy.pool_id,
        sub_id: subId,
        ephemeral_wallet: ephemeralWallet,
        initial_deposit_usdc: initialDeposit,
      })

      setStepStatus({ 1: 'done', 2: 'done', 3: 'done' })
      setSubSuccess(`Successfully subscribed to "${strategyId}". Sub ID: ${shortAddr(subId)}`)
    } catch (err) {
      setSubError(err.message || 'Subscribe failed')
      // Mark only failed steps
      setStepStatus(prev => ({
        1: prev[1] === 'done' ? 'done' : 'idle',
        2: prev[2] === 'done' ? 'done' : (prev[2] === 'active' ? 'idle' : prev[2]),
        3: prev[3] === 'active' ? 'idle' : prev[3],
      }))
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
                Connect your wallet to subscribe. You will need USDC on Arc Testnet for the initial deposit.
              </p>
            )}

            {walletAddr && !subSuccess && (
              <>
                {isCircle && (
                  <p className="text-xs text-[var(--color-warning)] mb-2">
                    Passkey wallet detected. For the subscribe flow, please use an EOA wallet (MetaMask, Coinbase Wallet, etc.) or perform the contract calls manually.
                  </p>
                )}

                <div className="mb-3">
                  <label className="text-xs text-[var(--color-muted)] block mb-1">Initial Deposit (USDC)</label>
                  <input
                    type="number"
                    className="input w-full"
                    value={initialDeposit}
                    onChange={(e) => setInitialDeposit(e.target.value)}
                    min="0"
                    step="1"
                    disabled={busy}
                  />
                </div>

                {/* Step indicator */}
                <div className="flex items-center gap-4 mb-4">
                  <Step num={1} label="Approve USDC" status={
                    stepStatus[1] === 'done' ? 'done' : stepStatus[1] === 'verifying' || stepStatus[1] === 'active' ? 'active' : 'idle'
                  } />
                  <Step num={2} label="Subscribe on-chain" status={
                    stepStatus[2] === 'done' ? 'done' : stepStatus[2] === 'active' ? 'active' : 'idle'
                  } />
                  <Step num={3} label="Register with API" status={
                    stepStatus[3] === 'done' ? 'done' : stepStatus[3] === 'active' ? 'active' : 'idle'
                  } />
                </div>

                <button
                  className="btn btn-primary"
                  onClick={handleSubscribe}
                  disabled={busy || isCircle}
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
