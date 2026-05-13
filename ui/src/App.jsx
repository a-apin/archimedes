import { useState, useEffect, useCallback } from 'react'
import { client, ORACLE_ABI, VAULT_ABI, TOKEN_ABI, ASSETS } from './config'

const PRICE_DECIMALS = 6
const TOKEN_DECIMALS = 18

async function fetchAssetData(asset) {
  const [price, lastUpdated, isFresh, supply] = await Promise.all([
    client.readContract({ address: asset.oracle, abi: ORACLE_ABI, functionName: 'price' }),
    client.readContract({ address: asset.oracle, abi: ORACLE_ABI, functionName: 'lastUpdated' }),
    client.readContract({ address: asset.oracle, abi: ORACLE_ABI, functionName: 'isFresh' }),
    client.readContract({ address: asset.token,  abi: TOKEN_ABI,  functionName: 'totalSupply' }),
  ])
  return {
    price: Number(price) / 10 ** PRICE_DECIMALS,
    lastUpdated: Number(lastUpdated),
    isFresh,
    supply: Number(supply) / 10 ** TOKEN_DECIMALS,
  }
}

function timeAgo(ts) {
  const secs = Math.floor(Date.now() / 1000) - ts
  if (secs < 60)   return `${secs}s ago`
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  return `${Math.floor(secs / 3600)}h ago`
}

function formatPrice(id, price) {
  if (id === 'BTC') return price.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })
  return price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function AssetCard({ asset, data, prevPrice }) {
  const change = prevPrice != null ? ((data.price - prevPrice) / prevPrice) * 100 : null
  const isUp    = change !== null && change > 0
  const isDown  = change !== null && change < 0

  return (
    <div className={`card ${!data.isFresh ? 'stale' : ''}`}>
      <div className="card-header">
        <span className="emoji">{asset.emoji}</span>
        <div>
          <div className="asset-name">{asset.name}</div>
          <div className="asset-sym">{asset.sym}</div>
        </div>
        <span className={`badge ${data.isFresh ? 'fresh' : 'stale'}`}>
          {data.isFresh ? 'LIVE' : 'STALE'}
        </span>
      </div>

      <div className="price-row">
        <span className="price">${formatPrice(asset.id, data.price)}</span>
        {change !== null && (
          <span className={`change ${isUp ? 'up' : isDown ? 'down' : ''}`}>
            {isUp ? '▲' : isDown ? '▼' : '─'}
            {Math.abs(change).toFixed(3)}%
          </span>
        )}
      </div>

      <div className="meta">
        <span>Updated {timeAgo(data.lastUpdated)}</span>
        <span>{data.supply.toFixed(4)} {asset.sym} supply</span>
      </div>
    </div>
  )
}

function LoadingCard({ asset }) {
  return (
    <div className="card loading">
      <div className="card-header">
        <span className="emoji">{asset.emoji}</span>
        <div>
          <div className="asset-name">{asset.name}</div>
          <div className="asset-sym">{asset.sym}</div>
        </div>
      </div>
      <div className="price-row"><span className="price skeleton">$———</span></div>
      <div className="meta"><span>Loading…</span></div>
    </div>
  )
}

function ErrorCard({ asset, error }) {
  return (
    <div className="card error">
      <div className="card-header">
        <span className="emoji">{asset.emoji}</span>
        <div>
          <div className="asset-name">{asset.name}</div>
          <div className="asset-sym">{asset.sym}</div>
        </div>
        <span className="badge stale">ERR</span>
      </div>
      <div className="price-row"><span className="price">—</span></div>
      <div className="meta"><span className="err-msg">{error}</span></div>
    </div>
  )
}

export default function App() {
  const [data,      setData]      = useState({})   // id → { price, lastUpdated, isFresh, supply }
  const [prevData,  setPrevData]  = useState({})   // id → price (previous fetch)
  const [errors,    setErrors]    = useState({})   // id → error string
  const [loading,   setLoading]   = useState(true)
  const [lastFetch, setLastFetch] = useState(null)
  const [countdown, setCountdown] = useState(30)

  const fetchAll = useCallback(async () => {
    const results = await Promise.allSettled(ASSETS.map(a => fetchAssetData(a)))
    const newData   = {}
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
    const interval = setInterval(() => {
      setCountdown(c => {
        if (c <= 1) { fetchAll(); return 30 }
        return c - 1
      })
    }, 1000)
    return () => clearInterval(interval)
  }, [fetchAll])

  const liveCount = Object.values(data).filter(d => d?.isFresh).length

  return (
    <div className="app">
      <header>
        <div className="logo">⚖️ Archimedes</div>
        <div className="subtitle">Synthetic Asset Prices · Arc Testnet</div>
        <div className="status-bar">
          {lastFetch && (
            <>
              <span className="dot" />
              <span>{liveCount}/{ASSETS.length} live · refreshes in {countdown}s</span>
              <button onClick={fetchAll}>↺ Refresh</button>
            </>
          )}
        </div>
      </header>

      <main>
        <div className="grid">
          {ASSETS.map(asset => {
            if (loading && !data[asset.id]) return <LoadingCard key={asset.id} asset={asset} />
            if (errors[asset.id])           return <ErrorCard   key={asset.id} asset={asset} error={errors[asset.id]} />
            return (
              <AssetCard
                key={asset.id}
                asset={asset}
                data={data[asset.id]}
                prevPrice={prevData[asset.id]}
              />
            )
          })}
        </div>
      </main>

      <footer>
        <span>Prices from onchain oracles · 6-decimal USDC · 120% collateral ratio</span>
      </footer>
    </div>
  )
}
