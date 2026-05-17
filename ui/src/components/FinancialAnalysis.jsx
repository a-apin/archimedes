import { useState, useEffect, useMemo } from 'react'
import {
  publicClient, getAddress,
  ASSETS, ORACLE_ABI, VAULT_ABI, VAULT_FACTORY_ABI,
  NEW_CONTRACTS, USDC, TOKEN_ABI,
} from '../config'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

async function apiGet(path) {
  const res = await fetch(`${API_BASE}${path}`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

function fmt(v, d = 2) { return v != null ? v.toFixed(d) : '—' }
function fmtUsd(v) { return v != null ? `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '—' }
function fmtPct(v) { return v != null ? `${(v * 100).toFixed(2)}%` : '—' }

// ─── Portfolio Value Over Time (chart placeholder) ───────────

function PortfolioChart({ vaults }) {
  // Generate a mock equity curve from vault AUM for demo
  const totalAum = vaults.reduce((s, v) => s + v.aum, 0)
  const baseValue = totalAum > 0 ? totalAum : 10000
  const days = 30
  const points = Array.from({ length: days }, (_, i) => {
    const trend = baseValue * (1 + 0.003 * i)
    const noise = trend * (Math.random() * 0.02 - 0.01)
    return trend + noise
  })
  const maxV = Math.max(...points)
  const minV = Math.min(...points)
  const range = maxV - minV || 1

  return (
    <div className="card" style={{ padding: 20 }}>
      <div className="label mb-3">Portfolio Value (30d simulated)</div>
      <div style={{ width: '100%', height: 160 }}>
        <svg width="100%" height="100%" viewBox="0 0 400 150" preserveAspectRatio="none">
          <defs>
            <linearGradient id="portfolioGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#D4A853" stopOpacity="0.2" />
              <stop offset="100%" stopColor="#D4A853" stopOpacity="0" />
            </linearGradient>
          </defs>
          {points.length > 1 && (
            <>
              <path
                d={points.map((v, i) => {
                  const x = (i / (days - 1)) * 400
                  const y = 140 - ((v - minV) / range) * 120
                  return `${i === 0 ? 'M' : 'L'}${x},${y}`
                }).join(' ')}
                stroke="#D4A853" fill="none" strokeWidth="2"
              />
              <path
                d={points.map((v, i) => {
                  const x = (i / (days - 1)) * 400
                  const y = 140 - ((v - minV) / range) * 120
                  return `${i === 0 ? 'M' : 'L'}${x},${y}`
                }).join(' ') + ' V150 H0 Z'}
                fill="url(#portfolioGrad)"
              />
            </>
          )}
        </svg>
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 8 }}>
        <span className="caption">30 days ago</span>
        <span className="caption">Today</span>
      </div>
    </div>
  )
}

// ─── Asset Allocation Breakdown ──────────────────────────────

function AllocationBreakdown({ holdings }) {
  if (!holdings.length) return null

  const colors = {
    USDC: '#22C55E', sTSLA: '#3B82F6', sNVDA: '#8B5CF6', sSPY: '#6366F1',
    sBTC: '#F97316', sGOLD: '#D4A853', sOIL: '#64748B', sNKY: '#EC4899',
  }

  const total = holdings.reduce((s, h) => s + h.value, 0)

  return (
    <div className="card" style={{ padding: 20 }}>
      <div className="label mb-3">Asset Allocation</div>
      {/* Allocation bar */}
      <div style={{ display: 'flex', height: 24, borderRadius: 6, overflow: 'hidden', marginBottom: 16 }}>
        {holdings.map((h, i) => (
          <div key={i} style={{
            width: `${(h.value / total) * 100}%`,
            background: colors[h.symbol] || `hsl(${i * 50}, 60%, 50%)`,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: '0.65rem', fontWeight: 600, color: 'white',
            minWidth: h.value / total > 0.05 ? 30 : 0,
          }}>
            {h.value / total > 0.08 ? h.symbol.replace('s', '') : ''}
          </div>
        ))}
      </div>
      {/* Legend */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {holdings.sort((a, b) => b.value - a.value).map((h, i) => (
          <div key={i} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <div style={{ width: 10, height: 10, borderRadius: 2, background: colors[h.symbol] || `hsl(${i * 50}, 60%, 50%)` }} />
              <span style={{ fontWeight: 500 }}>{h.symbol}</span>
            </div>
            <div style={{ display: 'flex', gap: 16 }}>
              <span className="mono">{fmtUsd(h.value)}</span>
              <span className="mono caption">{((h.value / total) * 100).toFixed(1)}%</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ─── Return Attribution ──────────────────────────────────────

function ReturnAttribution({ strategies }) {
  if (!strategies.length) return null

  return (
    <div className="card" style={{ padding: 20 }}>
      <div className="label mb-3">Return Attribution by Strategy</div>
      <div className="table-container">
        <table>
          <thead>
            <tr>
              <th>Strategy</th>
              <th>Status</th>
              <th className="text-right">CAGR</th>
              <th className="text-right">Sharpe</th>
              <th className="text-right">Max DD</th>
              <th className="text-right">Win Rate</th>
              <th className="text-right">Contribution</th>
            </tr>
          </thead>
          <tbody>
            {strategies.map(s => {
              const contribution = s.cagr ? (s.cagr * 100 / strategies.length).toFixed(2) : '0.00'
              return (
                <tr key={s.id}>
                  <td style={{ fontWeight: 500, maxWidth: 180 }}>{s.paper_title?.slice(0, 30)}…</td>
                  <td><span className={`tag ${s.status === 'live' ? 'tag-positive' : 'tag-muted'}`}>{s.status}</span></td>
                  <td className="text-right mono positive">{s.cagr ? fmtPct(s.cagr) : '—'}</td>
                  <td className="text-right mono">{fmt(s.sharpe_ratio)}</td>
                  <td className="text-right mono negative">{s.max_drawdown ? `−${fmtPct(s.max_drawdown)}` : '—'}</td>
                  <td className="text-right mono">{s.win_rate ? fmtPct(s.win_rate) : '—'}</td>
                  <td className="text-right mono" style={{ fontWeight: 700 }}>+{contribution}%</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ─── Wallet Token Holdings ───────────────────────────────────

function WalletHoldings() {
  const [holdings, setHoldings] = useState([])
  const [loading, setLoading] = useState(true)
  const wallet = getAddress()

  useEffect(() => {
    const load = async () => {
      if (!wallet) { setLoading(false); return }
      const items = [{ symbol: 'USDC', address: USDC, decimals: 6 }]
      ASSETS.forEach(a => items.push({ symbol: a.sym, address: a.token, decimals: 18, oracle: a.oracle }))

      const results = await Promise.all(items.map(async (item) => {
        try {
          const [rawBal, rawPrice] = await Promise.all([
            publicClient.readContract({
              address: item.address, abi: TOKEN_ABI, functionName: 'balanceOf', args: [wallet],
            }),
            item.oracle
              ? publicClient.readContract({ address: item.oracle, abi: ORACLE_ABI, functionName: 'price' })
              : Promise.resolve(BigInt(1e6)), // USDC = $1
          ])
          const amount = Number(rawBal) / 10 ** item.decimals
          const price = item.oracle ? Number(rawPrice) / 1e6 : 1
          const value = amount * price
          return { symbol: item.symbol, amount, price, value }
        } catch {
          return { symbol: item.symbol, amount: 0, price: 0, value: 0 }
        }
      }))
      setHoldings(results.filter(h => h.value > 0.01))
      setLoading(false)
    }
    load()
  }, [wallet])

  if (!wallet) return <div className="info-box warning">Connect wallet to view holdings.</div>
  if (loading) return <div className="caption">Loading holdings…</div>
  if (!holdings.length) return <div className="caption">No token holdings found.</div>

  const total = holdings.reduce((s, h) => s + h.value, 0)

  return (
    <div className="card" style={{ padding: 20 }}>
      <div className="label mb-3">Wallet Holdings</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {holdings.sort((a, b) => b.value - a.value).map(h => (
          <div key={h.symbol} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '4px 0' }}>
            <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
              <strong>{h.symbol}</strong>
              <span className="caption">{h.amount < 0.001 ? '<0.001' : h.amount.toFixed(h.symbol === 'USDC' ? 2 : 4)}</span>
            </div>
            <div style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
              <span className="mono">{fmtUsd(h.value)}</span>
              <span className="mono caption">{((h.value / total) * 100).toFixed(1)}%</span>
            </div>
          </div>
        ))}
        <div className="divider" style={{ margin: '8px 0' }} />
        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
          <strong>Total</strong>
          <strong className="mono">{fmtUsd(total)}</strong>
        </div>
      </div>
    </div>
  )
}

// ─── Performance Metrics ─────────────────────────────────────

function PerformanceMetrics({ strategies, vaultAum }) {
  const avgSharpe = strategies.length ? strategies.reduce((s, st) => s + (st.sharpe_ratio ?? 0), 0) / strategies.length : 0
  const avgCagr = strategies.length ? strategies.reduce((s, st) => s + (st.cagr ?? 0), 0) / strategies.length : 0
  const worstDD = strategies.length ? Math.max(...strategies.map(s => s.max_drawdown ?? 0)) : 0
  const bestSharpe = strategies.length ? Math.max(...strategies.map(s => s.sharpe_ratio ?? 0)) : 0

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16 }}>
      <div className="card-flat" style={{ padding: 16 }}>
        <div className="caption">Portfolio Value</div>
        <div style={{ fontSize: '1.6rem', fontWeight: 700 }}>{fmtUsd(vaultAum)}</div>
        <div className="caption positive" style={{ marginTop: 4 }}>Vaults AUM</div>
      </div>
      <div className="card-flat" style={{ padding: 16 }}>
        <div className="caption">Avg Strategy Sharpe</div>
        <div style={{ fontSize: '1.6rem', fontWeight: 700 }}>{fmt(avgSharpe)}</div>
        <div className="caption" style={{ marginTop: 4 }}>Best: {fmt(bestSharpe)}</div>
      </div>
      <div className="card-flat" style={{ padding: 16 }}>
        <div className="caption">Avg CAGR</div>
        <div className="positive" style={{ fontSize: '1.6rem', fontWeight: 700 }}>{fmtPct(avgCagr)}</div>
        <div className="caption" style={{ marginTop: 4 }}>{strategies.length} strategies</div>
      </div>
      <div className="card-flat" style={{ padding: 16 }}>
        <div className="caption">Worst Max Drawdown</div>
        <div className="negative" style={{ fontSize: '1.6rem', fontWeight: 700 }}>−{fmtPct(worstDD)}</div>
        <div className="caption" style={{ marginTop: 4 }}>Risk ceiling</div>
      </div>
    </div>
  )
}

// ─── Main Export ─────────────────────────────────────────────

export default function FinancialAnalysis() {
  const [strategies, setStrategies] = useState([])
  const [vaults, setVaults] = useState([])
  const [vaultAum, setVaultAum] = useState(0)
  const [holdings, setHoldings] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const load = async () => {
      try {
        const data = await apiGet('/api/strategies/')
        setStrategies(data.strategies || [])
      } catch {}

      // Load on-chain vaults
      const factoryAddr = NEW_CONTRACTS.vaultFactory
      if (factoryAddr) {
        try {
          const addrs = await publicClient.readContract({
            address: factoryAddr, abi: VAULT_FACTORY_ABI, functionName: 'getVaults',
          })
          let totalAum = 0
          const vaultData = await Promise.all((addrs || []).map(async (addr) => {
            try {
              const totalAssets = await publicClient.readContract({
                address: addr, abi: VAULT_ABI, functionName: 'totalAssets',
              })
              const aum = Number(totalAssets) / 1e6
              totalAum += aum
              return { address: addr, aum }
            } catch { return null }
          }))
          setVaults(vaultData.filter(Boolean))
          setVaultAum(totalAum)

          // Read holdings from first vault if exists
          if (addrs.length > 0) {
            try {
              const [tokenAddrs, amounts] = await publicClient.readContract({
                address: addrs[0], abi: VAULT_ABI, functionName: 'getHoldings',
              })
              const h = []
              for (let i = 0; i < tokenAddrs.length; i++) {
                if (amounts[i] === 0n) continue
                const symbol = tokenAddrs[i].toLowerCase() === USDC.toLowerCase() ? 'USDC' :
                  ASSETS.find(a => a.token.toLowerCase() === tokenAddrs[i].toLowerCase())?.sym || tokenAddrs[i].slice(0, 8)
                const decimals = symbol === 'USDC' ? 6 : 18
                const oracle = ASSETS.find(a => a.token.toLowerCase() === tokenAddrs[i].toLowerCase())?.oracle
                let price = 1
                if (oracle) {
                  try {
                    const rawPrice = await publicClient.readContract({ address: oracle, abi: ORACLE_ABI, functionName: 'price' })
                    price = Number(rawPrice) / 1e6
                  } catch {}
                }
                const amount = Number(amounts[i]) / 10 ** decimals
                h.push({ symbol, amount, value: amount * price })
              }
              setHoldings(h)
            } catch {}
          }
        } catch {}
      }
      setLoading(false)
    }
    load()
  }, [])

  return (
    <div>
      <div className="fade-up fade-up-1" style={{ maxWidth: 640, marginBottom: 28 }}>
        <h2 style={{ fontFamily: 'var(--serif)', fontSize: '2rem', marginBottom: 10 }}>Financial Analysis</h2>
        <p className="body">
          Portfolio performance, return attribution, and asset allocation. All metrics
          sourced from on-chain vault state and paper-grounded strategy backtests.
        </p>
      </div>

      {loading ? (
        <div className="caption">Loading financial data…</div>
      ) : (
        <>
          <div className="fade-up fade-up-2" style={{ marginBottom: 24 }}>
            <PerformanceMetrics strategies={strategies} vaultAum={vaultAum} />
          </div>

          <div className="trade-grid fade-up fade-up-3">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              <PortfolioChart vaults={vaults} />
              <ReturnAttribution strategies={strategies} />
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              <WalletHoldings />
              <AllocationBreakdown holdings={holdings} />
            </div>
          </div>
        </>
      )}
    </div>
  )
}
