import { useState, useEffect, useMemo } from 'react'
import { publicClient, ASSETS, ORACLE_ABI, VAULT_FACTORY_ABI, VAULT_ABI, NEW_CONTRACTS } from './config'

// Use relative /api paths — Vite proxy handles dev, nginx handles prod
const API = '/api'

// Ecosystem stats — static for now, can be derived from backend later
const ECOSYSTEM_STATS = {
  totalAum: '$2.4M',
  aumChange: '+12.3%',
  activeVaults: 18,
  verifiedVaults: 12,
  communityVaults: 6,
  totalTraces: 247,
  avgSharpe: 1.82,
  sharpeDelta: '+0.14',
}

// Mock vault leaderboard (on-chain data would come from VaultFactory)
const MOCK_VAULTS = [
  {
    id: '0x1a2b3c4d5e6f7890abcdef1234567890abcdef12',
    name: 'Momentum Alpha',
    symbol: 'vMOMENTUM',
    tier: 1,
    aum: 524200,
    return30d: 12.3,
    sharpe: 1.84,
    maxDrawdown: -8.2,
    allocations: [
      { symbol: 'sTSLA', color: '#3B82F6', weight: 28 },
      { symbol: 'sSPY',  color: '#6366F1', weight: 25 },
      { symbol: 'sGLD',  color: '#D4A853', weight: 12 },
      { symbol: 'sBTC',  color: '#F97316', weight: 5  },
      { symbol: 'USYC',  color: '#22C55E', weight: 30 },
    ],
    managementFee: 1.5,
    performanceFee: 20,
  },
  {
    id: '0x2b3c4d5e6f7890abcdef1234567890abcdef1234',
    name: 'Yield Optimizer',
    symbol: 'vYIELD',
    tier: 1,
    aum: 312800,
    return30d: 8.1,
    sharpe: 2.12,
    maxDrawdown: -4.1,
    allocations: [
      { symbol: 'USYC', color: '#22C55E', weight: 55 },
      { symbol: 'sSPY', color: '#6366F1', weight: 20 },
      { symbol: 'sGLD', color: '#D4A853', weight: 15 },
      { symbol: 'sTSLA',color: '#3B82F6', weight: 10 },
    ],
    managementFee: 1.0,
    performanceFee: 15,
  },
  {
    id: '0x3c4d5e6f7890abcdef1234567890abcdef12345',
    name: 'DeFi Degen',
    symbol: 'vDEGEN',
    tier: 2,
    aum: 87400,
    return30d: 18.7,
    sharpe: 0.94,
    maxDrawdown: -22.1,
    allocations: [
      { symbol: 'sBTC', color: '#F97316', weight: 60 },
      { symbol: 'sTSLA',color: '#3B82F6', weight: 25 },
      { symbol: 'USYC', color: '#22C55E', weight: 15 },
    ],
    managementFee: 2.0,
    performanceFee: 25,
  },
  {
    id: '0x4d5e6f7890abcdef1234567890abcdef123456',
    name: 'Safe Haven',
    symbol: 'vSAFE',
    tier: 2,
    aum: 145600,
    return30d: 5.2,
    sharpe: 1.54,
    maxDrawdown: -3.8,
    allocations: [
      { symbol: 'USYC', color: '#22C55E', weight: 60 },
      { symbol: 'sGLD', color: '#D4A853', weight: 25 },
      { symbol: 'sSPY', color: '#6366F1', weight: 15 },
    ],
    managementFee: 0.5,
    performanceFee: 10,
  },
  {
    id: '0x5e6f7890abcdef1234567890abcdef1234567',
    name: 'Multi-Factor Quant',
    symbol: 'vMFQ',
    tier: 1,
    aum: 298100,
    return30d: 9.8,
    sharpe: 1.67,
    maxDrawdown: -11.4,
    allocations: [
      { symbol: 'sTSLA', color: '#3B82F6', weight: 35 },
      { symbol: 'sSPY',  color: '#6366F1', weight: 25 },
      { symbol: 'sGLD',  color: '#D4A853', weight: 20 },
      { symbol: 'USYC',  color: '#22C55E', weight: 20 },
    ],
    managementFee: 1.5,
    performanceFee: 20,
  },
]

// Mock agent activity
const MOCK_ACTIVITY = [
  {
    type: 'rebalance',
    vault: 'Momentum Alpha',
    time: 3,
    message: 'Reduced sTSLA 33% → 28%, shifted to USYC. Drift threshold exceeded. Cost-benefit: +$294 net.',
    traceId: 247,
  },
  {
    type: 'regime',
    vault: 'Global Detection',
    time: 60,
    message: 'Regime confirmed Risk-On. VIX 14.2, positive equity momentum, tight credit spreads. No action.',
    traceId: null,
  },
  {
    type: 'rotation',
    vault: 'Multi-Factor Quant',
    time: 240,
    message: 'Rotated out "Mean Reversion Small Cap" (Sharpe 0.38) → "Cross-Sectional Momentum" (Sharpe 1.42, correlation 0.12).',
    traceId: 246,
  },
]

// Asset prices — populated from on-chain oracles on mount
const ASSET_PRICES_INIT = {
  TSLA:   { price: '—', change: 0 },
  NVDA:   { price: '—', change: 0 },
  SPY:    { price: '—', change: 0 },
  BTC:    { price: '—', change: 0 },
  GOLD:   { price: '—', change: 0 },
  OIL:    { price: '—', change: 0 },
  NIKKEI: { price: '—', change: 0 },
}

// ─── Adapter: backend StrategyCard → UI card shape ──────────

function riskLabel(apiLevel) {
  switch (apiLevel) {
    case 'low':       return 'Low'
    case 'medium':    return 'Medium'
    case 'high':      return 'High'
    case 'very_high': return 'High'
    default:          return 'Medium'
  }
}

function adaptCard(card) {
  return {
    id:          card.id,
    name:        card.name,
    description: card.description,
    author:      card.author?.name || card.author?.address || 'Unknown',
    authorAddr:  card.author?.address || '',
    risk:        riskLabel(card.risk_level),
    category:    card.category,
    tags:        card.tags || [],
    tvl:         card.tvl_usdc || 0,
    apy:         card.apy_pct || 0,
    sharpe:      card.sharpe_ratio || null,
    maxDrawdown: null,
    users:       card.users_count || 0,
    rating:      card.rating || null,
    status:      card.verified ? 'verified' : 'community',
    featured:    card.featured || false,
    trending:    card.trending || false,
    created:     card.created_at ? card.created_at.slice(0, 10) : '',
  }
}

// ─── Helpers ─────────────────────────────────────────────────

function formatNumber(num) {
  if (num >= 1_000_000) return (num / 1_000_000).toFixed(1) + 'M'
  if (num >= 1_000)     return (num / 1_000).toFixed(1) + 'K'
  return num.toString()
}

function getRiskColor(risk) {
  switch (risk) {
    case 'Low':    return { bg: 'rgba(34, 197, 94, 0.12)',  color: '#22C55E' }
    case 'Medium': return { bg: 'rgba(212, 168, 83, 0.12)', color: '#D4A853' }
    case 'High':   return { bg: 'rgba(239, 68, 68, 0.12)',  color: '#EF4444' }
    default:       return { bg: 'var(--surface-3)',          color: 'var(--text-3)' }
  }
}

// ─── Strategy Detail Modal ───────────────────────────────────

function StrategyDetailModal({ strategy, detail, onClose, onDeploy }) {
  if (!strategy) return null

  const riskStyle = getRiskColor(strategy.risk)

  const perfData = detail?.performance_history?.length
    ? detail.performance_history.map(p => p.return_pct)
    : [65, 68, 72, 70, 75, 78, 82, 85, 88, 92, 95, 100]

  const maxV = Math.max(...perfData)
  const minV = Math.min(...perfData)
  const range = maxV - minV || 1
  const pts = perfData.length

  const allocations = detail?.allocation_breakdown || []

  return (
    <div className="strategy-modal-overlay" onClick={onClose}>
      <div className="strategy-modal" onClick={e => e.stopPropagation()}>
        <div className="strategy-modal-header">
          <div className="flex items-center gap-3">
            <h2 style={{ fontSize: '1.3rem' }}>{strategy.name}</h2>
            <span className="tag" style={{ background: riskStyle.bg, color: riskStyle.color }}>
              {strategy.risk} Risk
            </span>
            {strategy.status === 'verified' && <span className="tag tag-accent">Verified 🏆</span>}
            {strategy.status === 'community' && <span className="tag tag-muted">Community 👥</span>}
          </div>
          <button className="strategy-modal-close" onClick={onClose}>×</button>
        </div>

        <div className="strategy-modal-body">
          <p className="body" style={{ marginBottom: 20 }}>
            {detail?.description_long || strategy.description}
          </p>

          <div className="strategy-modal-meta">
            <span>By <strong>{strategy.author}</strong></span>
            {strategy.users > 0 && <span>· {strategy.users} users</span>}
            {strategy.created && <span>· {strategy.created}</span>}
          </div>

          <div className="divider" style={{ margin: '20px 0' }} />

          {/* Performance Chart */}
          <div style={{ marginBottom: 24 }}>
            <div className="label mb-2">Performance ({perfData.length} months)</div>
            <div className="strategy-chart">
              <svg width="100%" height="100%" viewBox="0 0 400 100" preserveAspectRatio="none">
                <defs>
                  <linearGradient id="chartGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%"   stopColor="#D4A853" stopOpacity="0.2"/>
                    <stop offset="100%" stopColor="#D4A853" stopOpacity="0"/>
                  </linearGradient>
                </defs>
                <path
                  d={`M0,${100 - ((perfData[0] - minV) / range) * 80} ` +
                    perfData.slice(1).map((v, i) =>
                      `L${(i + 1) * (400 / (pts - 1))},${100 - ((v - minV) / range) * 80}`
                    ).join(' ')}
                  stroke="#D4A853" fill="none" strokeWidth="2"
                />
                <path
                  d={`M0,${100 - ((perfData[0] - minV) / range) * 80} ` +
                    perfData.slice(1).map((v, i) =>
                      `L${(i + 1) * (400 / (pts - 1))},${100 - ((v - minV) / range) * 80}`
                    ).join(' ') + ` V100 H0 Z`}
                  fill="url(#chartGrad)"
                />
              </svg>
            </div>
          </div>

          {/* Metrics Grid */}
          <div className="strategy-metrics-grid">
            <div className="strategy-metric-card">
              <div className="caption">APY</div>
              <div className="positive" style={{ fontSize: '1.4rem', fontWeight: 700 }}>{strategy.apy.toFixed(1)}%</div>
            </div>
            <div className="strategy-metric-card">
              <div className="caption">TVL</div>
              <div style={{ fontSize: '1.4rem', fontWeight: 700 }}>${formatNumber(strategy.tvl)}</div>
            </div>
            <div className="strategy-metric-card">
              <div className="caption">Sharpe</div>
              <div style={{ fontSize: '1.4rem', fontWeight: 700 }}>
                {detail?.risk_assessment?.max_drawdown != null
                  ? (Math.abs(strategy.apy / detail.risk_assessment.volatility) || 0).toFixed(2)
                  : '—'}
              </div>
            </div>
            <div className="strategy-metric-card">
              <div className="caption">Max Drawdown</div>
              <div className="negative" style={{ fontSize: '1.4rem', fontWeight: 700 }}>
                {detail?.risk_assessment?.max_drawdown != null
                  ? `${detail.risk_assessment.max_drawdown.toFixed(1)}%`
                  : '—'}
              </div>
            </div>
          </div>

          {/* Allocation Breakdown */}
          {allocations.length > 0 && (
            <div style={{ marginTop: 24 }}>
              <div className="label mb-3">Allocation Breakdown</div>
              <div className="strategy-allocation-list">
                {allocations.map(alloc => {
                  const color = alloc.asset.startsWith('s')
                    ? alloc.asset === 'sTSLA' ? '#3B82F6'
                    : alloc.asset === 'sSPY' ? '#6366F1'
                    : alloc.asset === 'sGLD' || alloc.asset === 'sGOLD' ? '#D4A853'
                    : alloc.asset === 'sBTC' ? '#F97316'
                    : '#8B5CF6'
                    : '#22C55E'
                  return (
                    <div key={alloc.asset} className="strategy-allocation-item">
                      <div className="flex items-center gap-3">
                        <div className="strategy-allocation-dot" style={{ background: color }} />
                        <span>{alloc.asset}</span>
                      </div>
                      <span className="mono">{alloc.weight_pct}%</span>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* Risk Assessment */}
          {detail?.risk_assessment && (
            <div style={{ marginTop: 24 }}>
              <div className="label mb-3">Risk Assessment</div>
              <div className="strategy-risk-assessment">
                {[
                  { label: 'Volatility', pct: Math.min(100, (detail.risk_assessment.volatility / 30) * 100) },
                  { label: 'Market Beta', pct: Math.min(100, Math.abs(detail.risk_assessment.beta || 0) * 100) },
                  { label: 'Liquidity',   pct: 85 },
                ].map(({ label, pct }) => (
                  <div key={label} className="strategy-risk-item">
                    <span className="caption" style={{ minWidth: 90 }}>{label}</span>
                    <div className="strategy-risk-bar">
                      <div
                        className="strategy-risk-fill"
                        style={{
                          width: `${pct}%`,
                          background: pct > 65 ? '#EF4444' : pct > 35 ? '#D4A853' : '#22C55E',
                        }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Tags */}
          <div style={{ marginTop: 24 }}>
            <div className="flex gap-2 flex-wrap">
              {strategy.tags.map(tag => (
                <span key={tag} className="tag tag-muted">{tag}</span>
              ))}
            </div>
          </div>
        </div>

        <div className="strategy-modal-footer">
          <button className="btn btn-outline" onClick={onClose}>Close</button>
          <button className="btn btn-primary" onClick={() => onDeploy(strategy)}>
            Deploy Strategy
          </button>
        </div>
      </div>
    </div>
  )
}

// ─── Strategy Card ───────────────────────────────────────────

function StrategyCard({ strategy, onClick }) {
  const riskStyle = getRiskColor(strategy.risk)
  return (
    <div className="card strategy-card" onClick={onClick} style={{ cursor: 'pointer' }}>
      <div className="flex items-center gap-3 mb-4">
        <h3 style={{ fontSize: '1rem', flex: 1, margin: 0 }}>{strategy.name}</h3>
        <span className="tag" style={{ background: riskStyle.bg, color: riskStyle.color }}>
          {strategy.risk}
        </span>
        {strategy.status === 'verified' && <span className="tag tag-accent">🏆</span>}
      </div>
      <div className="caption mb-4" style={{ lineHeight: 1.5, minHeight: 40 }}>
        {strategy.description}
      </div>
      <div className="caption mb-4" style={{ color: 'var(--text-3)' }}>
        By {strategy.author}
      </div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <div className="caption">TVL</div>
          <div style={{ fontWeight: 600 }}>${formatNumber(strategy.tvl)}</div>
        </div>
        <div>
          <div className="caption">APY</div>
          <div className="positive" style={{ fontWeight: 600 }}>{strategy.apy.toFixed(1)}%</div>
        </div>
        <div>
          <div className="caption">Users</div>
          <div style={{ fontWeight: 600 }}>{strategy.users}</div>
        </div>
      </div>
      <div className="flex gap-2 flex-wrap">
        {strategy.tags.slice(0, 4).map(tag => (
          <span key={tag} className="tag tag-muted">{tag}</span>
        ))}
      </div>
    </div>
  )
}

// ─── Main Component ──────────────────────────────────────────

export default function Marketplace() {
  const [regime, setRegime] = useState('Risk-On')
  const [vaultFilter, setVaultFilter] = useState('all')
  const [sortBy, setSortBy] = useState('tvl')
  const [searchQuery, setSearchQuery] = useState('')
  const [showSection, setShowSection] = useState('all')
  const [categoryFilter, setCategoryFilter] = useState('all')

  // Live oracle prices
  const [assetPrices, setAssetPrices] = useState(ASSET_PRICES_INIT)

  // On-chain vaults
  const [onChainVaults, setOnChainVaults] = useState([])
  const [vaultLoading, setVaultLoading] = useState(true)

  // API state
  const [allStrategies, setAllStrategies] = useState([])
  const [featuredStrategies, setFeaturedStrategies] = useState([])
  const [trendingStrategies, setTrendingStrategies] = useState([])
  const [categories, setCategories] = useState([])
  const [loadingStrategies, setLoadingStrategies] = useState(true)
  const [apiError, setApiError] = useState(null)

  // Modal state
  const [selectedStrategy, setSelectedStrategy] = useState(null)
  const [selectedDetail, setSelectedDetail] = useState(null)
  const [loadingDetail, setLoadingDetail] = useState(false)

  // Fetch live oracle prices
  useEffect(() => {
    const loadPrices = async () => {
      const prices = { ...ASSET_PRICES_INIT }
      await Promise.all(ASSETS.map(async (asset) => {
        try {
          const rawPrice = await publicClient.readContract({
            address: asset.oracle,
            abi: ORACLE_ABI,
            functionName: 'price',
          })
          // Oracle prices are in 6 decimals (USDC-precision)
          const priceNum = Number(rawPrice) / 1e6
          let formatted
          if (priceNum >= 10000) formatted = priceNum.toLocaleString('en-US', { maximumFractionDigits: 0 })
          else if (priceNum >= 100) formatted = priceNum.toFixed(2)
          else formatted = priceNum.toFixed(4)
          prices[asset.id] = { price: formatted, change: 0 }
        } catch {
          // keep placeholder
        }
      }))
      setAssetPrices(prices)
    }
    loadPrices()
    const interval = setInterval(loadPrices, 30000)
    return () => clearInterval(interval)
  }, [])

  // Fetch on-chain vaults from VaultFactory
  useEffect(() => {
    const loadVaults = async () => {
      const factoryAddr = NEW_CONTRACTS.vaultFactory
      if (!factoryAddr) { setVaultLoading(false); return }
      try {
        const addrs = await publicClient.readContract({
          address: factoryAddr,
          abi: VAULT_FACTORY_ABI,
          functionName: 'getVaults',
        })
        // Read basic metrics for each vault
        const vaultData = await Promise.all(addrs.map(async (addr) => {
          try {
            const [totalAssets, creator, tier, paused] = await Promise.all([
              publicClient.readContract({ address: addr, abi: VAULT_ABI, functionName: 'totalAssets' }),
              publicClient.readContract({ address: addr, abi: VAULT_ABI, functionName: 'creator' }),
              publicClient.readContract({ address: addr, abi: VAULT_ABI, functionName: 'tier' }),
              publicClient.readContract({ address: addr, abi: VAULT_ABI, functionName: 'paused' }),
            ])
            return {
              id: addr,
              name: `Vault ${addr.slice(0, 6)}...${addr.slice(-4)}`,
              symbol: `v${addr.slice(2, 6).toUpperCase()}`,
              tier: Number(tier),
              aum: Number(totalAssets) / 1e6,
              return30d: 0,
              sharpe: 0,
              maxDrawdown: 0,
              allocations: [],
              managementFee: 0,
              performanceFee: 0,
              creator,
              paused,
            }
          } catch {
            return null
          }
        }))
        setOnChainVaults(vaultData.filter(Boolean))
      } catch {
        // factory not available — keep mock
      }
      setVaultLoading(false)
    }
    loadVaults()
  }, [])

  // Fetch regime
  useEffect(() => {
    fetch(`${API}/regime/current`)
      .then(r => r.json())
      .then(d => {
        if (d.regime) {
          setRegime(d.regime === 'risk_on' ? 'Risk-On' : d.regime === 'risk_off' ? 'Risk-Off' : 'Transition')
        }
      })
      .catch(() => {})
  }, [])

  // Fetch marketplace data
  useEffect(() => {
    const load = async () => {
      setLoadingStrategies(true)
      setApiError(null)
      try {
        const [allRes, featRes, trendRes, catRes] = await Promise.all([
          fetch(`${API}/marketplace/strategies?limit=50`),
          fetch(`${API}/marketplace/featured`),
          fetch(`${API}/marketplace/trending`),
          fetch(`${API}/marketplace/categories`),
        ])

        if (!allRes.ok) throw new Error(`Strategies API ${allRes.status}`)

        const allData   = await allRes.json()
        const featData  = featRes.ok ? await featRes.json() : { strategies: [] }
        const trendData = trendRes.ok ? await trendRes.json() : { strategies: [] }
        const catData   = catRes.ok ? await catRes.json() : { categories: [] }

        setAllStrategies((allData.strategies || []).map(adaptCard))
        setFeaturedStrategies((featData.strategies || []).map(adaptCard))
        setTrendingStrategies((trendData.strategies || []).map(adaptCard))
        setCategories(catData.categories || [])
      } catch (err) {
        console.error('Marketplace API error:', err)
        setApiError(err.message)
      } finally {
        setLoadingStrategies(false)
      }
    }
    load()
  }, [])

  // Fetch strategy detail when selected
  useEffect(() => {
    if (!selectedStrategy) { setSelectedDetail(null); return }
    setLoadingDetail(true)
    fetch(`${API}/marketplace/strategies/${selectedStrategy.id}`)
      .then(r => r.ok ? r.json() : null)
      .then(d => setSelectedDetail(d))
      .catch(() => setSelectedDetail(null))
      .finally(() => setLoadingDetail(false))
  }, [selectedStrategy])

  // Filtered + sorted strategies
  const filteredStrategies = useMemo(() => {
    return allStrategies
      .filter(s => {
        if (showSection === 'verified'  && s.status !== 'verified')  return false
        if (showSection === 'community' && s.status !== 'community') return false
        if (categoryFilter !== 'all' && s.category !== categoryFilter) return false
        if (searchQuery) {
          const q = searchQuery.toLowerCase()
          return s.name.toLowerCase().includes(q) ||
                 s.author.toLowerCase().includes(q) ||
                 s.description.toLowerCase().includes(q) ||
                 s.tags.some(t => t.toLowerCase().includes(q))
        }
        return true
      })
      .sort((a, b) => {
        if (sortBy === 'apy')    return b.apy  - a.apy
        if (sortBy === 'sharpe') return (b.sharpe || 0) - (a.sharpe || 0)
        return b.tvl - a.tvl  // default: tvl
      })
  }, [allStrategies, showSection, categoryFilter, searchQuery, sortBy])

  // Use on-chain vaults if available, otherwise fall back to mock
  const displayVaults = onChainVaults.length > 0 ? onChainVaults : MOCK_VAULTS

  const filteredVaults = displayVaults
    .filter(v => {
      if (vaultFilter === 'verified')  return v.tier === 1
      if (vaultFilter === 'community') return v.tier === 2
      return true
    })

  const verifiedCount  = allStrategies.filter(s => s.status === 'verified').length
  const communityCount = allStrategies.filter(s => s.status === 'community').length

  const handleSelectStrategy = (strategy) => {
    setSelectedStrategy(strategy)
  }

  const handleDeployStrategy = (strategy) => {
    alert(`Deploying strategy: ${strategy.name}\n\nThis would open the vault creation flow with this strategy pre-selected.`)
    setSelectedStrategy(null)
  }

  return (
    <div className="fade-up fade-up-1">
      {/* Top bar */}
      <div className="flex items-center justify-between mb-6">
        <div><span className="label">Marketplace</span></div>
        <div className="flex items-center gap-5">
          <span className="caption">
            Regime <strong className={regime === 'Risk-On' ? 'positive' : regime === 'Risk-Off' ? 'negative' : 'accent'}>{regime}</strong>
          </span>
        </div>
      </div>

      {/* Ecosystem Stats */}
      <div className="grid g-4 mb-7" style={{ gridTemplateColumns: 'repeat(4, 1fr)', gap: 16 }}>
        {[
          { label: 'Ecosystem AUM',    value: onChainVaults.length > 0 ? `$${formatNumber(onChainVaults.reduce((s, v) => s + v.aum, 0))}` : ECOSYSTEM_STATS.totalAum, sub: `${ECOSYSTEM_STATS.aumChange} this week`, cls: 'positive' },
          { label: 'Active Vaults',    value: onChainVaults.length || ECOSYSTEM_STATS.activeVaults, sub: `${onChainVaults.filter(v => v.tier === 1).length || ECOSYSTEM_STATS.verifiedVaults} verified · ${onChainVaults.filter(v => v.tier === 2).length || ECOSYSTEM_STATS.communityVaults} community`, cls: '' },
          { label: 'On-Chain Traces',  value: ECOSYSTEM_STATS.totalTraces, sub: 'All verifiable', cls: 'accent' },
          { label: 'Avg Sharpe (T1)',  value: ECOSYSTEM_STATS.avgSharpe,   sub: `${ECOSYSTEM_STATS.sharpeDelta} vs benchmark`, cls: 'positive' },
        ].map(({ label, value, sub, cls }) => (
          <div key={label}>
            <div className="label mb-2">{label}</div>
            <div className="stat" style={{ fontSize: '1.8rem' }}>{value}</div>
            <div className={`caption ${cls}`} style={{ marginTop: 6 }}>{sub}</div>
          </div>
        ))}
      </div>

      <div className="divider" style={{ marginBottom: 24 }} />

      {/* Synthetic Asset Prices */}
      <div className="mb-7">
        <div className="flex items-center justify-between mb-5">
          <div className="label">Synthetic Assets</div>
          <span className="caption">Live oracle prices</span>
        </div>
        <div className="grid g-5" style={{ gridTemplateColumns: 'repeat(5, 1fr)', gap: 12 }}>
          {ASSETS.slice(0, 5).map(asset => {
            const info = assetPrices[asset.id] || { price: '—', change: 0 }
            const isPos = info.change >= 0
            return (
              <div key={asset.id} className="card-flat" style={{ padding: 16 }}>
                <div className="flex items-center gap-3 mb-3">
                  <div className="token-dot" style={{
                    width: 32, height: 32, fontSize: '0.75rem',
                    background: asset.id === 'TSLA'   ? '#3B82F6' :
                                asset.id === 'SPY'    ? '#6366F1' :
                                asset.id === 'GOLD'   ? '#D4A853' :
                                asset.id === 'BTC'    ? '#F97316' : '#22C55E',
                  }}>
                    {asset.emoji}
                  </div>
                  <div>
                    <div style={{ fontWeight: 600, fontSize: '0.85rem' }}>{asset.sym}</div>
                    <div className="caption">{asset.name}</div>
                  </div>
                </div>
                <div style={{ fontSize: '1.2rem', fontWeight: 700, letterSpacing: '-0.02em' }}>
                  ${info.price}
                </div>
                <div className={`caption ${isPos ? 'positive' : 'negative'}`}>
                  {isPos ? '+' : ''}{info.change.toFixed(2)}%
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* Vault Leaderboard */}
      <div className="mb-7">
        <div className="flex items-center justify-between mb-5">
          <div className="label">Vault Leaderboard</div>
          <div className="flex gap-2">
            {['all', 'verified', 'community'].map(f => (
              <span
                key={f}
                className={`tag ${vaultFilter === f ? 'tag-accent' : 'tag-muted'}`}
                style={{ cursor: 'pointer', textTransform: 'capitalize' }}
                onClick={() => setVaultFilter(f)}
              >
                {f.charAt(0).toUpperCase() + f.slice(1)}
              </span>
            ))}
          </div>
        </div>
        <div className="table-container">
          <table>
            <thead>
              <tr>
                <th style={{ width: 32 }}>#</th>
                <th>Vault</th>
                <th>Tier</th>
                <th className="text-right">AUM</th>
                <th className="text-right">30d Return</th>
                <th className="text-right">Sharpe</th>
                <th className="text-right">Max DD</th>
                <th>Allocation</th>
                <th className="text-right">Fees</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {filteredVaults.map((vault, idx) => (
                <tr key={vault.id} style={{ cursor: 'pointer' }}>
                  <td style={{ fontWeight: 700, color: idx === 0 ? 'var(--accent)' : 'var(--text-3)' }}>{idx + 1}</td>
                  <td>
                    <div><span style={{ fontWeight: 600 }}>{vault.name}</span></div>
                    <div className="mono caption">{vault.symbol}</div>
                  </td>
                  <td>
                    <span className={`tier ${vault.tier === 1 ? 'tier-verified' : 'tier-community'}`}>
                      {vault.tier === 1 ? 'Verified' : 'Community'}
                    </span>
                  </td>
                  <td className="text-right" style={{ fontWeight: 600 }}>${formatNumber(vault.aum)}</td>
                  <td className="text-right positive" style={{ fontWeight: 600 }}>+{vault.return30d}%</td>
                  <td className="text-right" style={{ fontWeight: 600 }}>{vault.sharpe}</td>
                  <td className="text-right negative">{vault.maxDrawdown}%</td>
                  <td>
                    <div className="alloc-bar" style={{ width: 80 }}>
                      {vault.allocations.map((a, i) => (
                        <div key={i} className="seg" style={{ width: a.weight + '%', background: a.color }} />
                      ))}
                    </div>
                  </td>
                  <td className="text-right caption">{vault.managementFee}% + {vault.performanceFee}%</td>
                  <td><button className="btn btn-primary btn-sm">Invest</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* API error banner */}
      {apiError && (
        <div style={{
          padding: '12px 16px', marginBottom: 24,
          background: 'rgba(245,158,11,0.06)', border: '1px solid rgba(245,158,11,0.2)',
          borderRadius: 8, fontSize: '0.82rem', color: '#fbbf24',
        }}>
          ⚠️ Strategy API unavailable — showing cached data. ({apiError})
        </div>
      )}

      {/* Featured Strategies */}
      {featuredStrategies.length > 0 && (
        <div className="mb-7">
          <div className="flex items-center justify-between mb-5">
            <div className="label flex items-center gap-2"><span>⭐</span> Featured Strategies</div>
            <span className="caption">Hand-picked by the Archimedes team</span>
          </div>
          <div className="strat-grid-3">
            {featuredStrategies.map(s => (
              <StrategyCard key={s.id} strategy={s} onClick={() => handleSelectStrategy(s)} />
            ))}
          </div>
        </div>
      )}

      {/* Trending Strategies */}
      {trendingStrategies.length > 0 && (
        <div className="mb-7">
          <div className="flex items-center justify-between mb-5">
            <div className="label flex items-center gap-2"><span>🔥</span> Trending This Week</div>
            <span className="caption">Highest user growth in the last 7 days</span>
          </div>
          <div className="strat-grid-3">
            {trendingStrategies.map(s => (
              <StrategyCard key={s.id} strategy={s} onClick={() => handleSelectStrategy(s)} />
            ))}
          </div>
        </div>
      )}

      {/* Loading state */}
      {loadingStrategies && !apiError && (
        <div style={{ textAlign: 'center', padding: '40px 0', color: 'var(--text-3)' }}>
          Loading strategies…
        </div>
      )}

      {/* All Strategies */}
      {!loadingStrategies && (
        <div className="mb-7">
          <div className="flex items-center justify-between mb-5">
            <div className="label">All Strategies</div>
            <div className="flex gap-2 items-center">
              <input
                type="text"
                placeholder="Search strategies…"
                value={searchQuery}
                onChange={e => setSearchQuery(e.target.value)}
                className="marketplace-search"
              />
              <select
                value={sortBy}
                onChange={e => setSortBy(e.target.value)}
                className="marketplace-select"
              >
                <option value="tvl">Sort by TVL</option>
                <option value="apy">Sort by APY</option>
                <option value="sharpe">Sort by Sharpe</option>
              </select>
            </div>
          </div>

          {/* Category tabs from API */}
          {categories.length > 0 && (
            <div className="flex gap-2 mb-4 flex-wrap">
              <span
                className={`tag ${categoryFilter === 'all' ? 'tag-accent' : 'tag-muted'}`}
                style={{ cursor: 'pointer' }}
                onClick={() => setCategoryFilter('all')}
              >
                All ({allStrategies.length})
              </span>
              {categories.map(cat => (
                <span
                  key={cat.id}
                  className={`tag ${categoryFilter === cat.id ? 'tag-accent' : 'tag-muted'}`}
                  style={{ cursor: 'pointer' }}
                  onClick={() => setCategoryFilter(cat.id)}
                >
                  {cat.icon} {cat.name} ({cat.strategies_count})
                </span>
              ))}
            </div>
          )}

          {/* Tier filter */}
          <div className="flex gap-2 mb-6">
            <span
              className={`tag ${showSection === 'all' ? 'tag-accent' : 'tag-muted'}`}
              style={{ cursor: 'pointer' }}
              onClick={() => setShowSection('all')}
            >All ({allStrategies.length})</span>
            <span
              className={`tag ${showSection === 'verified' ? 'tag-accent' : 'tag-muted'}`}
              style={{ cursor: 'pointer' }}
              onClick={() => setShowSection('verified')}
            >Verified ({verifiedCount}) 🏆</span>
            <span
              className={`tag ${showSection === 'community' ? 'tag-accent' : 'tag-muted'}`}
              style={{ cursor: 'pointer' }}
              onClick={() => setShowSection('community')}
            >Community ({communityCount}) 👥</span>
          </div>

          {filteredStrategies.length === 0 ? (
            <div className="positions-empty">
              <div>No strategies match your filters</div>
              <div className="caption" style={{ marginTop: 8 }}>Try clearing search or filters</div>
            </div>
          ) : (
            <div className="strat-grid-3">
              {filteredStrategies.map(s => (
                <StrategyCard key={s.id} strategy={s} onClick={() => handleSelectStrategy(s)} />
              ))}
            </div>
          )}
        </div>
      )}

      {/* Agent Activity */}
      <div>
        <div className="flex items-center justify-between mb-5">
          <div className="label">Agent Activity</div>
        </div>
        <div className="timeline">
          {MOCK_ACTIVITY.map((item, idx) => (
            <div key={idx} className={`tl-item ${idx === 0 ? 'active' : ''}`}>
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-3">
                  <span className={`tag ${item.type === 'rebalance' ? 'tag-positive' : item.type === 'regime' ? 'tag-muted' : 'tag-accent'}`}>
                    {item.type === 'rebalance' ? 'Rebalance' : item.type === 'regime' ? 'Regime' : 'Rotation'}
                  </span>
                  <span style={{ fontWeight: 600, fontSize: '0.88rem' }}>{item.vault}</span>
                </div>
                <span className="caption">
                  {item.time < 60 ? `${item.time} min ago` : `${Math.floor(item.time / 60)} hr ago`}
                </span>
              </div>
              <div className="body">
                {item.message}
                {item.traceId && <span className="accent"> Trace #{item.traceId} →</span>}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Strategy Detail Modal */}
      <StrategyDetailModal
        strategy={selectedStrategy}
        detail={loadingDetail ? null : selectedDetail}
        onClose={() => setSelectedStrategy(null)}
        onDeploy={handleDeployStrategy}
      />
    </div>
  )
}
