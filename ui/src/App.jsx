import { useState, useEffect, useCallback } from 'react'
import {
  publicClient, getWalletClient, getAddress, disconnectWallet,
  reconnectWallet,
  USDC,
  ORACLE_ABI, TOKEN_ABI, SYNTH_VAULT_ABI,
  TRACE_REGISTRY_ABI, VAULT_ABI, VAULT_FACTORY_ABI,
  ASSETS, NEW_CONTRACTS,
} from './config'
import Layout from './components/Layout'
import Trade from './components/Trade'
import Strategies from './components/Strategies'
import CreateVault from './components/CreateVault'
import VaultDetail from './components/VaultDetail'
import VaultChat from './components/VaultChat'
import Reasoning from './components/Reasoning'
import RiskAnalysis from './components/RiskAnalysis'
import FinancialAnalysis from './components/FinancialAnalysis'
import Marketplace from './Marketplace'
import './App.css'

const PRICE_DECIMALS = 6
const TOKEN_DECIMALS = 18

const PAGE_TO_PATH = {
  explore: '/markets/explore',
  strategies: '/markets/strategies',
  trade: '/markets/trade',
  dashboard: '/portfolio/dashboard',
  mint: '/portfolio/mint-burn',
  liquidity: '/portfolio/liquidity',
  vaults: '/portfolio/vaults',
  'create-vault': '/portfolio/create-vault',
  financial: '/portfolio/financial',
  reasoning: '/intelligence/reasoning',
  risk: '/intelligence/risk',
  about: '/about',
  imprint: '/imprint',
}

const PATH_TO_PAGE = {
  '/': 'explore',
  ...Object.fromEntries(Object.entries(PAGE_TO_PATH).map(([page, path]) => [path, page])),
}

function resolveRoute(pathname = '/', search = '') {
  if (PATH_TO_PAGE[pathname]) {
    return { page: PATH_TO_PAGE[pathname], vaultAddress: null, matched: true }
  }

  if (pathname.startsWith('/portfolio/vaults/')) {
    const rawAddress = pathname.replace('/portfolio/vaults/', '')
    if (rawAddress) {
      return { page: 'vault-detail', vaultAddress: rawAddress, matched: true }
    }
  }

  const params = new URLSearchParams(search)
  const vaultAddress = params.get('vault')
  if (pathname === '/portfolio/vaults' && vaultAddress) {
    return { page: 'vault-detail', vaultAddress, matched: true }
  }

  return { page: 'trade', vaultAddress: null, matched: false }
}

function pageToPath(page, selectedVault = null) {
  if (page === 'vault-detail' && selectedVault) return `/portfolio/vaults/${selectedVault}`
  return PAGE_TO_PATH[page] ?? '/markets/trade'
}

// ─── Shared helpers ──────────────────────────────────────────

function timeAgo(ts) {
  const secs = Math.floor(Date.now() / 1000) - ts
  if (secs < 60) return `${secs}s ago`
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  return `${Math.floor(secs / 3600)}h ago`
}

function formatPrice(id, price) {
  if (id === 'BTC') return price.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })
  return price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

async function fetchAssetData(asset) {
  const [price, lastUpdated, isFresh, supply] = await Promise.all([
    publicClient.readContract({ address: asset.oracle, abi: ORACLE_ABI, functionName: 'price' }),
    publicClient.readContract({ address: asset.oracle, abi: ORACLE_ABI, functionName: 'lastUpdated' }),
    publicClient.readContract({ address: asset.oracle, abi: ORACLE_ABI, functionName: 'isFresh' }),
    publicClient.readContract({ address: asset.token, abi: TOKEN_ABI, functionName: 'totalSupply' }),
  ])
  return {
    price: Number(price) / 10 ** PRICE_DECIMALS,
    lastUpdated: Number(lastUpdated),
    isFresh,
    supply: Number(supply) / 10 ** TOKEN_DECIMALS,
  }
}

// ─── Dashboard Panel ─────────────────────────────────────────

function Dashboard({ data, prevData, errors, loading, lastFetch, countdown, fetchAll }) {
  const liveCount = Object.values(data).filter(d => d?.isFresh).length
  const walletAddr = getAddress()
  const [userVaults, setUserVaults] = useState([])
  const [agentStatus, setAgentStatus] = useState(null)
  const [regime, setRegime] = useState(null)
  const [signals, setSignals] = useState(null)

  // Fetch user's vaults, agent status, regime, and signals
  useEffect(() => {
    const load = async () => {
      const factoryAddr = NEW_CONTRACTS.vaultFactory
      if (factoryAddr && walletAddr) {
        try {
          // Get vaults created by this user
          const creatorVaults = await publicClient.readContract({
            address: factoryAddr,
            abi: [{ name: 'getVaultsByCreator', type: 'function', stateMutability: 'view', inputs: [{ type: 'address' }], outputs: [{ type: 'address[]' }] }],
            functionName: 'getVaultsByCreator',
            args: [walletAddr],
          })
          // Read basic data for each vault
          const vaultData = await Promise.all((creatorVaults || []).map(async (addr) => {
            try {
              const [totalAssets, tier] = await Promise.all([
                publicClient.readContract({ address: addr, abi: VAULT_ABI, functionName: 'totalAssets' }),
                publicClient.readContract({ address: addr, abi: VAULT_ABI, functionName: 'tier' }),
              ])
              return { address: addr, aum: Number(totalAssets) / 1e6, tier: Number(tier) }
            } catch { return null }
          }))
          setUserVaults(vaultData.filter(Boolean))
        } catch { /* no vaults yet */ }
      }

      // Agent status
      try {
        const res = await fetch(`${API_BASE}/api/agent/status`)
        if (res.ok) setAgentStatus(await res.json())
      } catch {}

      // Regime
      try {
        const res = await fetch(`${API_BASE}/api/regime/current`)
        if (res.ok) setRegime(await res.json())
      } catch {}

      // Strategy signals
      try {
        const res = await fetch(`${API_BASE}/api/strategies/signals`)
        if (res.ok) setSignals(await res.json())
      } catch {}
    }
    load()
    const interval = setInterval(load, 30000)
    return () => clearInterval(interval)
  }, [walletAddr])

  const API_BASE = import.meta.env.VITE_API_BASE ?? ''
  const totalAum = userVaults.reduce((s, v) => s + v.aum, 0)

  return (
    <div>
      <div className="status-bar">
        {lastFetch && (
          <>
            <span className="dot" />
            <span>{liveCount}/{ASSETS.length} live · refreshes in {countdown}s</span>
            <button onClick={fetchAll}>↺</button>
          </>
        )}
      </div>

      {/* Portfolio Overview */}
      {walletAddr && (
        <div style={{ marginBottom: 24 }}>
          <div className="grid g-4" style={{ gridTemplateColumns: 'repeat(4, 1fr)', gap: 16, marginBottom: 24 }}>
            <div className="card-flat" style={{ padding: 16 }}>
              <div className="label mb-2">Your Vaults</div>
              <div style={{ fontSize: '1.8rem', fontWeight: 700 }}>{userVaults.length}</div>
              <div className="caption" style={{ marginTop: 6 }}>{userVaults.filter(v => v.tier === 1).length} Tier 1 · {userVaults.filter(v => v.tier === 2).length} Tier 2</div>
            </div>
            <div className="card-flat" style={{ padding: 16 }}>
              <div className="label mb-2">Total AUM</div>
              <div style={{ fontSize: '1.8rem', fontWeight: 700 }}>${totalAum.toLocaleString(undefined, { minimumFractionDigits: 2 })}</div>
              <div className="caption positive" style={{ marginTop: 6 }}>On Arc Testnet</div>
            </div>
            <div className="card-flat" style={{ padding: 16 }}>
              <div className="label mb-2">Agent Status</div>
              <div style={{ fontSize: '1.8rem', fontWeight: 700 }}>{agentStatus?.alive ? '🟢' : '🔴'} {agentStatus?.alive ? 'Alive' : 'Offline'}</div>
              <div className="caption" style={{ marginTop: 6 }}>Managing {agentStatus?.managed_vaults ?? 0} vaults</div>
            </div>
            <div className="card-flat" style={{ padding: 16 }}>
              <div className="label mb-2">Market Regime</div>
              <div style={{ fontSize: '1.8rem', fontWeight: 700 }}>{regime?.regime === 'risk_on' ? '📈' : regime?.regime === 'risk_off' ? '📉' : '🔄'} {regime?.regime?.replace('_', ' ') || '—'}</div>
              <div className="caption" style={{ marginTop: 6 }}>Confidence: {regime?.confidence ? (regime.confidence * 100).toFixed(0) + '%' : '—'}</div>
            </div>
          </div>

          {/* User Vaults */}
          {userVaults.length > 0 && (
            <div style={{ marginBottom: 24 }}>
              <div className="label mb-3">Your Vault Positions</div>
              <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 12 }}>
                {userVaults.map(v => (
                  <div key={v.address} className="card" style={{ cursor: 'pointer' }} onClick={() => window.location.hash = '#vaults'}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                      <code style={{ fontSize: '0.8rem' }}>{v.address.slice(0, 10)}...{v.address.slice(-6)}</code>
                      <span className={`tag ${v.tier === 1 ? 'tag-accent' : 'tag-muted'}`}>T{v.tier}</span>
                    </div>
                    <div style={{ fontSize: '1.2rem', fontWeight: 700 }}>${v.aum.toFixed(2)}</div>
                    <div className="caption">AUM</div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Strategy Signals Summary */}
      {signals && (
        <div style={{ marginBottom: 24 }}>
          <div className="label mb-3">Strategy Signals</div>
          <div className="table-container">
            <table>
              <thead>
                <tr>
                  <th>Strategy</th>
                  <th>Asset</th>
                  <th>Signal</th>
                  <th className="text-right">Weight</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {signals.strategies?.flatMap(s => s.signals?.map((sig, i) => (
                  <tr key={`${s.strategy_id}-${i}`}>
                    <td className="caption">{s.paper_title?.slice(0, 30)}…</td>
                    <td><span className="tag tag-muted">{sig.asset}</span></td>
                    <td><span className={`tag ${sig.signal === 'long' ? 'tag-positive' : sig.signal === 'short' ? 'tag-negative' : 'tag-muted'}`}>{sig.signal}</span></td>
                    <td className="text-right mono">{(sig.weight * 100).toFixed(1)}%</td>
                    <td className="caption" style={{ maxWidth: 200 }}>{sig.reason?.slice(0, 60)}…</td>
                  </tr>
                )))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Oracle Price Grid */}
      <div className="label mb-3">Oracle Prices</div>
      <div className="grid">
        {ASSETS.map(asset => {
          if (loading && !data[asset.id]) return <LoadingCard key={asset.id} asset={asset} />
          if (errors[asset.id]) return <ErrorCard key={asset.id} asset={asset} error={errors[asset.id]} />
          const change = prevData[asset.id] != null ? ((data[asset.id].price - prevData[asset.id]) / prevData[asset.id]) * 100 : null
          return (
            <div key={asset.id} className={`card ${!data[asset.id].isFresh ? 'stale' : ''}`}>
              <div className="card-header">
                <span className="emoji">{asset.emoji}</span>
                <div>
                  <div className="asset-name">{asset.name}</div>
                  <div className="asset-sym">{asset.sym}</div>
                </div>
                <span className={`badge ${data[asset.id].isFresh ? 'fresh' : 'stale'}`}>
                  {data[asset.id].isFresh ? 'LIVE' : 'STALE'}
                </span>
              </div>
              <div className="price-row">
                <span className="price">${formatPrice(asset.id, data[asset.id].price)}</span>
                {change !== null && (
                  <span className={`change ${change > 0 ? 'up' : change < 0 ? 'down' : ''}`}>
                    {change > 0 ? '▲' : change < 0 ? '▼' : '─'}{Math.abs(change).toFixed(3)}%
                  </span>
                )}
              </div>
              <div className="meta">
                <span>Updated {timeAgo(data[asset.id].lastUpdated)}</span>
                <span>{data[asset.id].supply.toFixed(4)} {asset.sym}</span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function LoadingCard({ asset }) {
  return (
    <div className="card loading">
      <div className="card-header">
        <span className="emoji">{asset.emoji}</span>
        <div><div className="asset-name">{asset.name}</div><div className="asset-sym">{asset.sym}</div></div>
      </div>
      <div className="price-row"><span className="price skeleton">$———</span></div>
    </div>
  )
}

function ErrorCard({ asset, error }) {
  return (
    <div className="card error">
      <div className="card-header">
        <span className="emoji">{asset.emoji}</span>
        <div><div className="asset-name">{asset.name}</div><div className="asset-sym">{asset.sym}</div></div>
        <span className="badge stale">ERR</span>
      </div>
      <div className="meta"><span className="err-msg">{error}</span></div>
    </div>
  )
}

// ─── Mint/Burn Panel ────────────────────────────────────────

function MintBurn() {
  const [selectedAsset, setSelectedAsset] = useState(ASSETS[0])
  const [amount, setAmount] = useState('')
  const [action, setAction] = useState('mint')
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)
  const [balances, setBalances] = useState({ usdc: null, synth: null, price: null })
  const [estimate, setEstimate] = useState(null)

  const loadBalances = useCallback(async () => {
    try {
      const addr = getAddress()
      if (!addr) return
      const [usdcBal, synthBal, price] = await Promise.all([
        publicClient.readContract({ address: USDC, abi: TOKEN_ABI, functionName: 'balanceOf', args: [addr] }),
        publicClient.readContract({ address: selectedAsset.token, abi: TOKEN_ABI, functionName: 'balanceOf', args: [addr] }),
        publicClient.readContract({ address: selectedAsset.oracle, abi: ORACLE_ABI, functionName: 'price' }),
      ])
      setBalances({
        usdc: Number(usdcBal) / 10 ** PRICE_DECIMALS,
        synth: Number(synthBal) / 10 ** TOKEN_DECIMALS,
        price: Number(price) / 10 ** PRICE_DECIMALS,
      })
    } catch { /* wallet not connected */ }
  }, [selectedAsset])

  useEffect(() => { loadBalances() }, [selectedAsset, loadBalances])
  useEffect(() => {
    const interval = setInterval(loadBalances, 15_000)
    return () => clearInterval(interval)
  }, [loadBalances])

  useEffect(() => {
    if (!amount || !balances.price) { setEstimate(null); return }
    const amt = parseFloat(amount)
    if (isNaN(amt) || amt <= 0) { setEstimate(null); return }
    if (action === 'mint') {
      const fee = amt * 0.005
      const netUsdc = amt - fee
      const synthOut = netUsdc / balances.price
      setEstimate({ fee: fee.toFixed(2), output: synthOut, outputLabel: `${selectedAsset.emoji} ${synthOut.toFixed(6)} ${selectedAsset.sym}` })
    } else {
      const usdcGross = amt * balances.price
      const fee = usdcGross * 0.005
      const usdcOut = usdcGross - fee
      setEstimate({ fee: fee.toFixed(2), output: usdcOut, outputLabel: `💵 ${usdcOut.toFixed(2)} USDC` })
    }
  }, [amount, action, balances.price, selectedAsset])

  const execute = async () => {
    setBusy(true); setStatus('')
    try {
      const wallet = await getWalletClient()
      const amountInt = BigInt(Math.round(parseFloat(amount) * 10 ** (action === 'mint' ? PRICE_DECIMALS : TOKEN_DECIMALS)))
      if (action === 'mint') {
        setStatus('Approving USDC…')
        await wallet.writeContract({ address: USDC, abi: TOKEN_ABI, functionName: 'approve', args: [selectedAsset.vault, amountInt] })
        setStatus('Minting…')
        const hash = await wallet.writeContract({ address: selectedAsset.vault, abi: SYNTH_VAULT_ABI, functionName: 'mint', args: [amountInt] })
        setStatus(`Minted! TX: ${hash}`)
      } else {
        setStatus('Burning…')
        const hash = await wallet.writeContract({ address: selectedAsset.vault, abi: SYNTH_VAULT_ABI, functionName: 'burn', args: [amountInt] })
        setStatus(`Burned! TX: ${hash}`)
      }
      loadBalances()
    } catch (err) { setStatus(err.shortMessage || err.message) }
    setBusy(false)
  }

  const setMax = () => {
    if (action === 'mint') setAmount(balances.usdc ? String(Math.floor(balances.usdc * 100) / 100) : '')
    else setAmount(balances.synth ? String(balances.synth) : '')
  }

  return (
    <div className="panel">
      <h2>Mint / Burn Synthetics</h2>
      <p className="hint">Deposit USDC to mint synthetic tokens, or burn them back to USDC.</p>

      <div className="balance-bar">
        <div className="balance-item">
          <span className="balance-label">💵 USDC</span>
          <span className="balance-value">{balances.usdc !== null ? balances.usdc.toFixed(2) : '—'}</span>
        </div>
        <div className="balance-item">
          <span className="balance-label">{selectedAsset.emoji} {selectedAsset.sym}</span>
          <span className="balance-value">{balances.synth !== null ? balances.synth.toFixed(6) : '—'}</span>
        </div>
        <div className="balance-item">
          <span className="balance-label">💲 Price</span>
          <span className="balance-value">{balances.price !== null ? `$${balances.price.toFixed(2)}` : '—'}</span>
        </div>
      </div>

      <div className="form-group">
        <label>Asset</label>
        <select value={selectedAsset.id} onChange={e => { setSelectedAsset(ASSETS.find(a => a.id === e.target.value)); setAmount(''); setEstimate(null) }}>
          {ASSETS.map(a => <option key={a.id} value={a.id}>{a.emoji} {a.sym} — {a.name}</option>)}
        </select>
      </div>

      <div className="form-group">
        <label>Action</label>
        <div className="btn-group">
          <button className={action === 'mint' ? 'active' : ''} onClick={() => { setAction('mint'); setAmount(''); setEstimate(null) }}>Mint</button>
          <button className={action === 'burn' ? 'active' : ''} onClick={() => { setAction('burn'); setAmount(''); setEstimate(null) }}>Burn</button>
        </div>
      </div>

      <div className="form-group">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <label>{action === 'mint' ? 'USDC Amount' : `${selectedAsset.sym} Amount`}</label>
          <button className="link-btn" onClick={setMax}>Max</button>
        </div>
        <input type="number" value={amount} onChange={e => setAmount(e.target.value)} placeholder={action === 'mint' ? '1000' : '0.5'} />
      </div>

      {estimate && (
        <div className="estimate-box">
          <div className="estimate-row"><span>Fee (0.5%)</span><span>${estimate.fee}</span></div>
          <div className="estimate-row estimate-output"><span>You receive ≈</span><span>{estimate.outputLabel}</span></div>
          <div className="estimate-note">Estimate based on current oracle price. Actual amount may differ slightly.</div>
        </div>
      )}

      <button className="primary" onClick={execute} disabled={busy || !amount}>
        {busy ? 'Waiting…' : action === 'mint' ? 'Mint' : 'Burn'}
      </button>
      {status && <div className="status">{status}</div>}
    </div>
  )
}

// ─── Liquidity Panel ─────────────────────────────────────────

function Liquidity() {
  const [selectedAsset, setSelectedAsset] = useState(ASSETS[0])
  const [usdcAmount, setUsdcAmount] = useState('')
  const [synthAmount, setSynthAmount] = useState('')
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)
  const [poolAddr, setPoolAddr] = useState(null)
  const [reserves, setReserves] = useState(null)
  const [balances, setBalances] = useState(null)

  const routerAddr = NEW_CONTRACTS.ammRouter
  const USDC_DECIMALS = 6
  const SYNTH_DECIMALS = 18
  const walletAddr = getAddress()

  const loadPoolInfo = async () => {
    if (!routerAddr) return
    try {
      const pool = await publicClient.readContract({ address: routerAddr, abi: [{name:'getPool',type:'function',stateMutability:'view',inputs:[{type:'address'},{type:'address'}],outputs:[{type:'address'}]}], functionName: 'getPool', args: [USDC, selectedAsset.token] })
      setPoolAddr(pool)
      if (pool && pool !== '0x0000000000000000000000000000000000000000') {
        const AMMPOOL_ABI = [
          { name: 'reserve0', type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'uint256' }] },
          { name: 'reserve1', type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'uint256' }] },
          { name: 'totalSupply', type: 'function', stateMutability: 'view', inputs: [], outputs: [{ type: 'uint256' }] },
        ]
        const [r0, r1, ts] = await Promise.all([
          publicClient.readContract({ address: pool, abi: AMMPOOL_ABI, functionName: 'reserve0' }),
          publicClient.readContract({ address: pool, abi: AMMPOOL_ABI, functionName: 'reserve1' }),
          publicClient.readContract({ address: pool, abi: AMMPOOL_ABI, functionName: 'totalSupply' }),
        ])
        setReserves({ reserve0: Number(r0), reserve1: Number(r1), totalSupply: Number(ts) })
      } else {
        setReserves(null)
      }
    } catch { setPoolAddr(null); setReserves(null) }

    if (walletAddr) {
      try {
        const [usdcBal, synthBal] = await Promise.all([
          publicClient.readContract({ address: USDC, abi: TOKEN_ABI, functionName: 'balanceOf', args: [walletAddr] }),
          publicClient.readContract({ address: selectedAsset.token, abi: TOKEN_ABI, functionName: 'balanceOf', args: [walletAddr] }),
        ])
        setBalances({ usdc: Number(usdcBal) / 10 ** USDC_DECIMALS, synth: Number(synthBal) / 10 ** SYNTH_DECIMALS })
      } catch { setBalances(null) }
    } else { setBalances(null) }
  }

  useEffect(() => { loadPoolInfo() }, [selectedAsset])

  const addLiquidity = async () => {
    if (!routerAddr || !usdcAmount || !synthAmount) return
    setBusy(true); setStatus('')
    try {
      const wallet = await getWalletClient()
      const usdcInt = BigInt(Math.round(parseFloat(usdcAmount) * 10 ** USDC_DECIMALS))
      const synthInt = BigInt(Math.round(parseFloat(synthAmount) * 10 ** SYNTH_DECIMALS))

      setStatus('Approving synth token…')
      await wallet.writeContract({ address: selectedAsset.token, abi: TOKEN_ABI, functionName: 'approve', args: [routerAddr, synthInt] })

      setStatus('Approving USDC…')
      await wallet.writeContract({ address: USDC, abi: TOKEN_ABI, functionName: 'approve', args: [routerAddr, usdcInt] })

      setStatus('Adding liquidity…')
      const hash = await wallet.writeContract({
        address: routerAddr,
        abi: [{ name: 'addLiquidity', type: 'function', stateMutability: 'nonpayable', inputs: [{ type: 'address' }, { type: 'address' }, { type: 'uint256' }, { type: 'uint256' }, { type: 'uint256' }], outputs: [{ type: 'uint256' }] }],
        functionName: 'addLiquidity',
        args: [USDC, selectedAsset.token, usdcInt, synthInt, BigInt(0)],
      })
      setStatus(`Liquidity added! TX: ${hash}`)
      loadPoolInfo()
    } catch (err) { setStatus(err.shortMessage || err.message) }
    setBusy(false)
  }

  return (
    <div className="panel">
      <h2>Add Liquidity</h2>
      {!routerAddr ? (
        <div className="info-box warning">AMM Router not deployed yet.</div>
      ) : (
        <>
          <div className="form-group">
            <label>Pool</label>
            <select value={selectedAsset.id} onChange={e => setSelectedAsset(ASSETS.find(a => a.id === e.target.value))}>
              {ASSETS.map(a => <option key={a.id} value={a.id}>{a.emoji} {a.sym}/USDC</option>)}
            </select>
          </div>

          {poolAddr && poolAddr !== '0x0000000000000000000000000000000000000000' ? (
            <div className="info-box">
              <strong>Pool:</strong> <code>{poolAddr}</code><br />
              {reserves && (
                <>
                  <strong>Reserves:</strong> {(reserves.reserve0 / 1e6).toFixed(2)} USDC / {(reserves.reserve1 / 1e18).toFixed(6)} {selectedAsset.sym}<br />
                  <strong>LP Supply:</strong> {reserves.totalSupply}
                </>
              )}
            </div>
          ) : (
            <div className="info-box warning">Pool not found for this pair.</div>
          )}

          {balances ? (
            <div className="balance-bar">
              <span>💵 USDC: <strong>{balances.usdc.toFixed(2)}</strong></span>
              <span>{selectedAsset.emoji} {selectedAsset.sym}: <strong>{balances.synth.toFixed(6)}</strong></span>
            </div>
          ) : walletAddr ? (
            <div className="balance-bar">Loading balances...</div>
          ) : (
            <div className="info-box warning">Connect wallet to see your balances.</div>
          )}

          <div className="form-group">
            <label>USDC Amount (6 dec)</label>
            <input type="number" value={usdcAmount} onChange={e => setUsdcAmount(e.target.value)} placeholder="10" />
          </div>

          <div className="form-group">
            <label>Synth Amount (18 dec)</label>
            <input type="number" value={synthAmount} onChange={e => setSynthAmount(e.target.value)} placeholder="50" />
          </div>

          <button className="primary" onClick={addLiquidity} disabled={busy || !usdcAmount || !synthAmount}>
            {busy ? 'Waiting…' : 'Add Liquidity'}
          </button>
          {status && <div className="status">{status}</div>}
          <p className="hint" style={{ marginTop: 16 }}>First mint synthetics via the Mint/Burn tab, then add them here paired with USDC.</p>
        </>
      )}
    </div>
  )
}

// ─── Vaults Panel ────────────────────────────────────────────

function Vaults({ onSelectVault }) {
  const [vaults, setVaults] = useState([])
  const [status, setStatus] = useState('')
  const [manualAddr, setManualAddr] = useState('')

  const factoryAddr = NEW_CONTRACTS.vaultFactory

  const loadVaults = async () => {
    if (!factoryAddr) return
    try {
      const addrs = await publicClient.readContract({ address: factoryAddr, abi: VAULT_FACTORY_ABI, functionName: 'getVaults' })
      setVaults(addrs)
    } catch (err) { setStatus(err.shortMessage || err.message) }
  }

  useEffect(() => { loadVaults() }, [])

  return (
    <div className="panel">
      <h2>Managed Vaults</h2>
      {!factoryAddr ? (
        <div className="info-box warning">VaultFactory not deployed. Run <code>node deploy-new.mjs</code>.</div>
      ) : (
        <>
          <p className="hint">{vaults.length} vault{vaults.length !== 1 ? 's' : ''} deployed — click to view details & chat</p>
          {vaults.length > 0 && (
            <div className="vault-list">
              {vaults.map((v, i) => (
                <div key={i} className="vault-card vault-card-clickable" onClick={() => onSelectVault(v)}>
                  <code>{v}</code>
                  <span className="vault-card-arrow">→</span>
                </div>
              ))}
            </div>
          )}

          <div style={{ marginTop: 20, paddingTop: 16, borderTop: '1px solid var(--glass-border)' }}>
            <label style={{ fontSize: '0.8rem', color: 'var(--text-3)', marginBottom: 6, display: 'block' }}>Enter vault address</label>
            <div className="form-row">
              <input
                type="text"
                value={manualAddr}
                onChange={e => setManualAddr(e.target.value)}
                placeholder="0x..."
                style={{ flex: 1, background: 'var(--surface-3)', border: '1px solid var(--glass-border)', borderRadius: 8, padding: '8px 12px', color: 'var(--text-1)', fontSize: '0.85rem', outline: 'none' }}
              />
              <button className="btn btn-primary" style={{ width: 'auto' }} onClick={() => manualAddr && onSelectVault(manualAddr)} disabled={!manualAddr}>
                View →
              </button>
            </div>
          </div>

          {status && <div className="status">{status}</div>}
        </>
      )}
    </div>
  )
}

// ─── Traces Panel ────────────────────────────────────────────

function Traces() {
  const [traces, setTraces] = useState([])
  const [totalCount, setTotalCount] = useState(0)
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)
  const [publishVault, setPublishVault] = useState(ASSETS[0].vault)
  const [publishMsg, setPublishMsg] = useState('Test trace from Archimedes UI')

  const regAddr = NEW_CONTRACTS.traceRegistry

  const loadTraces = async () => {
    if (!regAddr) return
    try {
      const count = await publicClient.readContract({ address: regAddr, abi: TRACE_REGISTRY_ABI, functionName: 'traceCount' })
      setTotalCount(Number(count))
      const loaded = []
      const start = Math.max(1, Number(count) - 9)
      for (let i = Number(count); i >= start; i--) {
        try {
          const [agent, vault, traceHash, timestamp] = await publicClient.readContract({ address: regAddr, abi: TRACE_REGISTRY_ABI, functionName: 'getTraceById', args: [BigInt(i)] })
          loaded.push({ id: i, agent, vault, traceHash, timestamp: Number(timestamp) })
        } catch {}
      }
      setTraces(loaded)
    } catch (err) { setStatus(err.shortMessage || err.message) }
  }

  const publishTestTrace = async () => {
    if (!regAddr) return
    setBusy(true); setStatus('')
    try {
      const wallet = await getWalletClient()
      const hash = '0x' + Array.from(new TextEncoder().encode(publishMsg)).map(b => b.toString(16).padStart(2, '0')).join('').padEnd(64, '0').slice(0, 64)
      const metadata = '0x'
      setStatus('Publishing trace…')
      const txHash = await wallet.writeContract({ address: regAddr, abi: TRACE_REGISTRY_ABI, functionName: 'publishTrace', args: [publishVault, hash, metadata] })
      setStatus(`Published! TX: ${txHash}`)
      loadTraces()
    } catch (err) { setStatus(err.shortMessage || err.message) }
    setBusy(false)
  }

  useEffect(() => { loadTraces() }, [])

  return (
    <div className="panel">
      <h2>Reasoning Traces</h2>
      {!regAddr ? (
        <div className="info-box warning">TraceRegistry not deployed. Run <code>node deploy-new.mjs</code>.</div>
      ) : (
        <>
          <p className="hint">Total traces: {totalCount}</p>

          <h3>Publish Test Trace</h3>
          <div className="form-group">
            <label>Vault Address</label>
            <select value={publishVault} onChange={e => setPublishVault(e.target.value)}>
              {ASSETS.map(a => <option key={a.id} value={a.vault}>{a.emoji} {a.sym} vault</option>)}
            </select>
          </div>
          <div className="form-group">
            <label>Message (hashed on-chain)</label>
            <input value={publishMsg} onChange={e => setPublishMsg(e.target.value)} />
          </div>
          <button className="primary" onClick={publishTestTrace} disabled={busy}>
            {busy ? 'Waiting…' : 'Publish Trace'}
          </button>
          {status && <div className="status">{status}</div>}

          <h3>Recent Traces</h3>
          {traces.length === 0 ? (
            <p className="hint">No traces yet. Publish one above!</p>
          ) : (
            <div className="trace-list">
              {traces.map(t => (
                <div key={t.id} className="trace-card">
                  <div className="trace-id">#{t.id}</div>
                  <div className="trace-detail">
                    <div><strong>Vault:</strong> <code>{t.vault.slice(0,10)}...{t.vault.slice(-6)}</code></div>
                    <div><strong>Agent:</strong> <code>{t.agent.slice(0,10)}...</code></div>
                    <div><strong>Hash:</strong> <code>{t.traceHash.slice(0,18)}...</code></div>
                    <div><strong>Time:</strong> {timeAgo(t.timestamp)}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}

// ─── Placeholder for pages not yet implemented ───────────────

function StaticPage({ title, content }) {
  return (
    <div className="panel" style={{ maxWidth: 640, margin: '0 auto', paddingTop: 40 }}>
      <h2 style={{ fontFamily: 'var(--serif)', fontSize: '2rem', marginBottom: 20 }}>{title}</h2>
      {content.split('\n\n').map((p, i) => (
        <p key={i} className="body" style={{ lineHeight: 1.7, marginBottom: 16 }}>{p}</p>
      ))}
    </div>
  )
}

function ComingSoon({ title }) {
  return (
    <div className="panel" style={{ textAlign: 'center', paddingTop: 80 }}>
      <h2>{title}</h2>
      <p className="hint" style={{ marginTop: 12 }}>This page is coming soon. Check the mockups in <code>ui-mockups/</code> for the design.</p>
    </div>
  )
}

// ─── Main App ────────────────────────────────────────────────

export default function App() {
  const initialRoute = resolveRoute(window.location.pathname, window.location.search)

  const [page, setPage] = useState(initialRoute.page)
  const [walletAddr, setWalletAddr] = useState(null)
  const [selectedVault, setSelectedVault] = useState(initialRoute.vaultAddress)
  const [data, setData] = useState({})
  const [prevData, setPrevData] = useState({})
  const [errors, setErrors] = useState({})
  const [loading, setLoading] = useState(true)
  const [lastFetch, setLastFetch] = useState(null)
  const [countdown, setCountdown] = useState(30)

  useEffect(() => {
    reconnectWallet().then(result => {
      if (result) setWalletAddr(result.address)
    })
  }, [])

  useEffect(() => {
    const handler = (e) => setWalletAddr(e.detail.address)
    window.addEventListener('wallet-changed', handler)
    return () => window.removeEventListener('wallet-changed', handler)
  }, [])

  const handleConnect = (addr) => setWalletAddr(addr)
  const handleDisconnect = () => { disconnectWallet(); setWalletAddr(null) }

  const navigateToPage = useCallback((nextPage, opts = {}) => {
    const nextVault = opts.vaultAddress ?? selectedVault
    const nextPath = pageToPath(nextPage, nextVault)
    const method = opts.replace ? 'replaceState' : 'pushState'

    if (window.location.pathname + window.location.search !== nextPath) {
      window.history[method]({}, '', nextPath)
    }

    setPage(nextPage)
    if (Object.prototype.hasOwnProperty.call(opts, 'vaultAddress')) {
      setSelectedVault(opts.vaultAddress)
    } else if (nextPage !== 'vault-detail') {
      setSelectedVault(null)
    }
  }, [selectedVault])

  const selectVault = (addr) => {
    navigateToPage('vault-detail', { vaultAddress: addr })
  }

  const backToVaults = () => {
    navigateToPage('vaults', { vaultAddress: null })
  }

  const fetchAll = useCallback(async () => {
    const results = await Promise.allSettled(ASSETS.map(a => fetchAssetData(a)))
    const newData = {}
    const newErrors = {}
    results.forEach((r, i) => {
      const id = ASSETS[i].id
      if (r.status === 'fulfilled') newData[id] = r.value
      else newErrors[id] = r.reason?.shortMessage || r.reason?.message || 'RPC error'
    })
    setPrevData(old => {
      const prev = {}
      for (const id of Object.keys(newData)) prev[id] = old[id]?.price ?? null
      return prev
    })
    setData(newData)
    setErrors(newErrors)
    setLoading(false)
    setLastFetch(new Date())
    setCountdown(30)
  }, [])

  useEffect(() => { fetchAll() }, [fetchAll])

  useEffect(() => {
    if (!initialRoute.matched) {
      window.history.replaceState({}, '', '/markets/trade')
    }

    const onPopState = () => {
      const route = resolveRoute(window.location.pathname, window.location.search)
      setPage(route.page)
      setSelectedVault(route.vaultAddress)
    }

    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [initialRoute.matched])
  useEffect(() => {
    const interval = setInterval(() => {
      setCountdown(c => { if (c <= 1) { fetchAll(); return 30 } return c - 1 })
    }, 1000)
    return () => clearInterval(interval)
  }, [fetchAll])

  const renderPage = () => {
    switch (page) {
      case 'explore':       return <Marketplace />
      case 'strategies':    return <Strategies />
      case 'trade':         return <Trade />
      case 'dashboard':     return <Dashboard data={data} prevData={prevData} errors={errors} loading={loading} lastFetch={lastFetch} countdown={countdown} fetchAll={fetchAll} />
      case 'mint':          return <MintBurn />
      case 'liquidity':     return <Liquidity />
      case 'vaults':        return <Vaults onSelectVault={selectVault} />
      case 'create-vault':  return <CreateVault onVaultCreated={selectVault} />
      case 'financial':     return <FinancialAnalysis />
      case 'vault-detail':  return <VaultDetail address={selectedVault} onBack={backToVaults} />
      case 'reasoning':     return <Reasoning />
      case 'risk':           return <RiskAnalysis />
      case 'about':          return <StaticPage title="About Archimedes" content="Archimedes is a fund-of-funds portfolio agent that turns published quant finance research into investable, backtested strategies, then constructs personalized portfolios of RWA tokens and yield instruments on Arc with USDC settlement.\n\nBuilt for the Agora Agents Hackathon — Canteen × Circle × Arc, May 2026.\n\nEvery position the agent takes, every rebalance it executes, every regime shift it responds to, is hashed and verifiable on-chain. The mathematician's name is fitting: he was the original empiricist working from first principles. We work from peer-reviewed first principles." />
      case 'imprint':        return <StaticPage title="Imprint" content="Archimedes Arcadia\nHackathon Team\n\nThis project was built for the Agora Agents Hackathon (May 11-25, 2026).\n\nTeam: Dan Browne, Marten Windler, Daniel Reis dos Santos, Chuan Bai, Önder Akkaya\n\nLicense: Unlicense — full public-domain dedication." />
      default:              return <Trade />
    }
  }

  return (
    <Layout page={page} setPage={navigateToPage} walletAddr={walletAddr} onConnect={handleConnect} onDisconnect={handleDisconnect}>
      {renderPage()}
    </Layout>
  )
}
