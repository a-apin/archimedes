import { useState, useEffect, useCallback, useMemo } from 'react'
import {
  publicClient, getWalletClient, getAddress,
  TRACE_REGISTRY_ABI, NEW_CONTRACTS,
} from '../config'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

async function apiGet(path) {
  const res = await fetch(`${API_BASE}${path}`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

function timeAgo(ts) {
  if (!ts) return '—'
  const d = new Date(ts)
  if (isNaN(d.getTime())) {
    // Unix timestamp
    const secs = Math.floor(Date.now() / 1000) - Number(ts)
    if (secs < 60) return `${secs}s ago`
    if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
    if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
    return `${Math.floor(secs / 86400)}d ago`
  }
  const secs = Math.floor((Date.now() - d.getTime()) / 1000)
  if (secs < 60) return `${secs}s ago`
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  return `${Math.floor(secs / 3600)}h ago`
}

function shortAddr(addr) {
  if (!addr) return '—'
  return `${addr.slice(0, 8)}…${addr.slice(-4)}`
}

function shortHash(hash) {
  if (!hash) return '—'
  return `${hash.slice(0, 12)}…${hash.slice(-6)}`
}

// ─── Reasoning Strategy Card ────────────────────────────────

function StrategyReasoningCard({ strategy, isSelected, onClick }) {
  const hasBacktest = strategy.sharpe_ratio != null

  return (
    <div
      className={`card fade-up${isSelected ? ' card-accent' : ''}`}
      style={{ cursor: 'pointer', padding: 16 }}
      onClick={onClick}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
        <strong style={{ fontSize: '0.9rem', lineHeight: 1.3, flex: 1 }}>{strategy.paper_title}</strong>
        <span className={`tag ${strategy.status === 'live' ? 'tag-positive' : 'tag-muted'}`} style={{ marginLeft: 8 }}>
          {strategy.status}
        </span>
      </div>
      <div className="caption" style={{ marginBottom: 8 }}>
        {strategy.paper_authors?.slice(0, 2).join(', ')}{strategy.paper_authors?.length > 2 ? ' et al.' : ''}
        {strategy.paper_year ? ` (${strategy.paper_year})` : ''}
      </div>
      <p className="hint" style={{ marginBottom: 8, lineHeight: 1.4 }}>
        {strategy.methodology_summary?.slice(0, 120)}{strategy.methodology_summary?.length > 120 ? '…' : ''}
      </p>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
        {strategy.asset_universe?.slice(0, 4).map(a => (
          <span key={a} className="tag tag-muted">{a}</span>
        ))}
      </div>
      {hasBacktest && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8 }}>
          <div>
            <div className="caption">Sharpe</div>
            <div style={{ fontWeight: 700 }}>{strategy.sharpe_ratio?.toFixed(2)}</div>
          </div>
          <div>
            <div className="caption">CAGR</div>
            <div className="positive" style={{ fontWeight: 700 }}>{strategy.cagr ? `${(strategy.cagr * 100).toFixed(1)}%` : '—'}</div>
          </div>
          <div>
            <div className="caption">Max DD</div>
            <div className="negative" style={{ fontWeight: 700 }}>{strategy.max_drawdown ? `−${(strategy.max_drawdown * 100).toFixed(1)}%` : '—'}</div>
          </div>
        </div>
      )}
    </div>
  )
}

// ─── Strategy Detail View ────────────────────────────────────

function StrategyDetailView({ strategy, traces }) {
  if (!strategy) return null

  const [exporting, setExporting] = useState(false)

  const handleExport = (format) => {
    setExporting(true)
    try {
      let content, filename, type
      if (format === 'json') {
        content = JSON.stringify(strategy, null, 2)
        filename = `strategy-${strategy.id.slice(0, 8)}.json`
        type = 'application/json'
      } else {
        // CSV
        const rows = [
          ['Field', 'Value'],
          ['Title', strategy.paper_title],
          ['Authors', strategy.paper_authors?.join(', ')],
          ['Year', strategy.paper_year],
          ['Status', strategy.status],
          ['Sharpe', strategy.sharpe_ratio],
          ['CAGR', strategy.cagr],
          ['Max Drawdown', strategy.max_drawdown],
          ['Methodology', strategy.methodology_summary],
          ['Assets', strategy.asset_universe?.join(', ')],
          ['Methodology Hash', strategy.methodology_hash],
        ]
        content = rows.map(r => r.map(c => `"${String(c ?? '').replace(/"/g, '""')}"`).join(',')).join('\n')
        filename = `strategy-${strategy.id.slice(0, 8)}.csv`
        type = 'text/csv'
      }
      const blob = new Blob([content], { type })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = filename; a.click()
      URL.revokeObjectURL(url)
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className="card-elevated" style={{ padding: 24 }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16 }}>
        <div>
          <h2 style={{ fontSize: '1.3rem', lineHeight: 1.3 }}>{strategy.paper_title}</h2>
          <div className="caption" style={{ marginTop: 4 }}>
            {strategy.paper_authors?.join(', ')} · {strategy.paper_year} · {strategy.paper_venue}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn-outline btn-sm" onClick={() => handleExport('json')} disabled={exporting}>
            Export JSON
          </button>
          <button className="btn btn-outline btn-sm" onClick={() => handleExport('csv')} disabled={exporting}>
            Export CSV
          </button>
        </div>
      </div>

      {/* Methodology */}
      <div style={{ marginBottom: 20 }}>
        <div className="label mb-2">Methodology</div>
        <p className="body" style={{ lineHeight: 1.6 }}>{strategy.methodology_summary}</p>
      </div>

      {/* Reasoning Trace */}
      <div style={{ marginBottom: 20 }}>
        <div className="label mb-2">Reasoning Trace</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div className="card-flat" style={{ padding: 12, display: 'flex', gap: 12, alignItems: 'center' }}>
            <span style={{ color: 'var(--accent)', fontWeight: 700, fontSize: '0.75rem' }}>STEP 1</span>
            <span className="body">Signal generation: {strategy.position_sizing} position sizing across {strategy.asset_universe?.join(', ')}</span>
          </div>
          <div className="card-flat" style={{ padding: 12, display: 'flex', gap: 12, alignItems: 'center' }}>
            <span style={{ color: 'var(--accent)', fontWeight: 700, fontSize: '0.75rem' }}>STEP 2</span>
            <span className="body">Rebalance frequency: {strategy.rebalance_frequency}</span>
          </div>
          <div className="card-flat" style={{ padding: 12, display: 'flex', gap: 12, alignItems: 'center' }}>
            <span style={{ color: 'var(--accent)', fontWeight: 700, fontSize: '0.75rem' }}>STEP 3</span>
            <span className="body">Risk guardrail: {strategy.status === 'live' ? 'validated + deployed' : 'pending validation'}</span>
          </div>
        </div>
      </div>

      {/* Performance Metrics */}
      <div style={{ marginBottom: 20 }}>
        <div className="label mb-2">Performance Metrics{strategy.is_backtest_placeholder ? ' (est.)' : ''}</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
          <div className="card-flat" style={{ padding: 12 }}>
            <div className="caption">Sharpe</div>
            <div style={{ fontSize: '1.2rem', fontWeight: 700 }}>{strategy.sharpe_ratio?.toFixed(2) ?? '—'}</div>
          </div>
          <div className="card-flat" style={{ padding: 12 }}>
            <div className="caption">CAGR</div>
            <div className="positive" style={{ fontSize: '1.2rem', fontWeight: 700 }}>
              {strategy.cagr ? `${(strategy.cagr * 100).toFixed(1)}%` : '—'}
            </div>
          </div>
          <div className="card-flat" style={{ padding: 12 }}>
            <div className="caption">Max DD</div>
            <div className="negative" style={{ fontSize: '1.2rem', fontWeight: 700 }}>
              {strategy.max_drawdown ? `−${(strategy.max_drawdown * 100).toFixed(1)}%` : '—'}
            </div>
          </div>
          <div className="card-flat" style={{ padding: 12 }}>
            <div className="caption">Win Rate</div>
            <div style={{ fontSize: '1.2rem', fontWeight: 700 }}>
              {strategy.win_rate ? `${(strategy.win_rate * 100).toFixed(1)}%` : '—'}
            </div>
          </div>
        </div>
      </div>

      {/* Paper-Claim Delta */}
      {strategy.paper_claimed_sharpe && strategy.sharpe_ratio && (
        <div style={{ marginBottom: 20 }}>
          <div className="label mb-2">Paper-Claim Delta</div>
          <div className="card-flat" style={{ padding: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span className="caption">Paper claimed Sharpe</span>
              <strong>{strategy.paper_claimed_sharpe.toFixed(2)}</strong>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span className="caption">Backtest Sharpe</span>
              <strong>{strategy.sharpe_ratio.toFixed(2)}</strong>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span className="caption">Delta</span>
              <strong className={strategy.sharpe_ratio / strategy.paper_claimed_sharpe >= 0.5 ? 'positive' : 'negative'}>
                {((strategy.sharpe_ratio / strategy.paper_claimed_sharpe) * 100).toFixed(0)}% of claim
              </strong>
            </div>
          </div>
        </div>
      )}

      {/* On-chain Provenance */}
      <div style={{ marginBottom: 20 }}>
        <div className="label mb-2">On-Chain Provenance</div>
        <div className="card-flat" style={{ padding: 12 }}>
          <div className="caption">Strategy ID: <code style={{ color: 'var(--info)' }}>{shortHash(strategy.id)}</code></div>
          {strategy.methodology_hash && (
            <div className="caption" style={{ marginTop: 4 }}>
              Methodology Hash: <code style={{ color: 'var(--text-2)' }}>{shortHash(strategy.methodology_hash)}</code>
            </div>
          )}
          {strategy.curator_note && (
            <div className="caption" style={{ marginTop: 8, fontStyle: 'italic', color: 'var(--text-3)' }}>
              Curator: "{strategy.curator_note?.slice(0, 150)}{strategy.curator_note?.length > 150 ? '…' : ''}"
            </div>
          )}
        </div>
      </div>

      {/* On-chain Traces for this strategy */}
      {traces.length > 0 && (
        <div>
          <div className="label mb-2">On-Chain Reasoning Traces ({traces.length})</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {traces.map((t, i) => (
              <div key={i} className="card-flat" style={{ padding: 10, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div>
                  <span className="tag tag-accent">#{t.id}</span>
                  <code style={{ marginLeft: 8, fontSize: '0.75rem' }}>{shortHash(t.trace_hash)}</code>
                </div>
                <div className="caption">{timeAgo(t.timestamp)}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Related PRs */}
      <div style={{ marginTop: 20 }}>
        <div className="label mb-2">Related</div>
        <div style={{ display: 'flex', gap: 8 }}>
          <a href="https://github.com/hackagora/archimedes-arcadia/pull/37" target="_blank" rel="noopener noreferrer" className="btn btn-outline btn-sm">
            PR #37 (corpus)
          </a>
          <a href="https://github.com/hackagora/archimedes-arcadia/pull/38" target="_blank" rel="noopener noreferrer" className="btn btn-outline btn-sm">
            PR #38 (fusion)
          </a>
        </div>
      </div>
    </div>
  )
}

// ─── On-chain Traces Panel ───────────────────────────────────

function OnChainTraces() {
  const [traces, setTraces] = useState([])
  const [totalCount, setTotalCount] = useState(0)
  const [loading, setLoading] = useState(true)
  const [publishVault, setPublishVault] = useState('')
  const [publishMsg, setPublishMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('')

  const regAddr = NEW_CONTRACTS.traceRegistry

  const loadTraces = useCallback(async () => {
    if (!regAddr) { setLoading(false); return }
    try {
      const count = await publicClient.readContract({
        address: regAddr, abi: TRACE_REGISTRY_ABI, functionName: 'traceCount'
      })
      setTotalCount(Number(count))
      const loaded = []
      const start = Math.max(1, Number(count) - 19)
      for (let i = Number(count); i >= start; i--) {
        try {
          const [agent, vault, traceHash, timestamp] = await publicClient.readContract({
            address: regAddr, abi: TRACE_REGISTRY_ABI, functionName: 'getTraceById', args: [BigInt(i)]
          })
          loaded.push({ id: i, agent, vault, traceHash, timestamp: Number(timestamp) })
        } catch {}
      }
      setTraces(loaded)
    } catch {}
    setLoading(false)
  }, [regAddr])

  useEffect(() => { loadTraces() }, [loadTraces])

  const publishTrace = async () => {
    if (!regAddr || !publishVault || !publishMsg) return
    setBusy(true); setStatus('')
    try {
      const wallet = await getWalletClient()
      const hash = '0x' + Array.from(new TextEncoder().encode(publishMsg))
        .map(b => b.toString(16).padStart(2, '0')).join('').padEnd(64, '0').slice(0, 64)
      setStatus('Publishing trace…')
      const txHash = await wallet.writeContract({
        address: regAddr, abi: TRACE_REGISTRY_ABI,
        functionName: 'publishTrace', args: [publishVault, hash, '0x'],
      })
      setStatus(`Published! TX: ${txHash}`)
      setPublishMsg('')
      loadTraces()
    } catch (err) { setStatus(err.shortMessage || err.message) }
    setBusy(false)
  }

  if (!regAddr) {
    return <div className="info-box warning">TraceRegistry not deployed.</div>
  }

  return (
    <div>
      <div className="label mb-3">On-Chain Trace Registry ({totalCount} total)</div>

      {/* Publish test trace */}
      <div className="card" style={{ marginBottom: 16, padding: 16 }}>
        <div className="label mb-2">Publish Test Trace</div>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <input
            type="text"
            value={publishVault}
            onChange={e => setPublishVault(e.target.value)}
            placeholder="Vault address (0x…)"
            className="chat-input"
            style={{ flex: 1, minWidth: 200 }}
          />
          <input
            type="text"
            value={publishMsg}
            onChange={e => setPublishMsg(e.target.value)}
            placeholder="Reasoning message"
            className="chat-input"
            style={{ flex: 2, minWidth: 200 }}
          />
          <button className="btn btn-primary" onClick={publishTrace} disabled={busy || !publishVault || !publishMsg}>
            {busy ? 'Publishing…' : 'Publish'}
          </button>
        </div>
        {status && <div className="caption" style={{ marginTop: 8 }}>{status}</div>}
      </div>

      {/* Trace list */}
      {loading ? (
        <div className="caption">Loading traces…</div>
      ) : traces.length === 0 ? (
        <div className="caption">No traces published yet.</div>
      ) : (
        <div className="table-container">
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>Vault</th>
                <th>Agent</th>
                <th>Trace Hash</th>
                <th className="text-right">Time</th>
              </tr>
            </thead>
            <tbody>
              {traces.map(t => (
                <tr key={t.id}>
                  <td style={{ fontWeight: 700, color: 'var(--accent)' }}>#{t.id}</td>
                  <td><code>{shortAddr(t.vault)}</code></td>
                  <td><code>{shortAddr(t.agent)}</code></td>
                  <td><code>{shortHash(t.traceHash)}</code></td>
                  <td className="text-right caption">{timeAgo(t.timestamp)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ─── Main Export ─────────────────────────────────────────────

export default function Reasoning() {
  const [strategies, setStrategies] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [selectedId, setSelectedId] = useState(null)
  const [filterStatus, setFilterStatus] = useState('all')
  const [filterAuthor, setFilterAuthor] = useState('all')
  const [searchQuery, setSearchQuery] = useState('')
  const [onChainTraces, setOnChainTraces] = useState([])
  const [tab, setTab] = useState('strategies')

  // Load strategies from API
  useEffect(() => {
    const load = async () => {
      try {
        const data = await apiGet('/api/strategies/')
        setStrategies(data.strategies || [])
      } catch (e) {
        setError(e.message)
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [])

  // Load on-chain traces for selected strategy
  useEffect(() => {
    const loadTraces = async () => {
      try {
        const data = await apiGet('/api/traces/')
        setOnChainTraces(data.traces || [])
      } catch {}
    }
    loadTraces()
  }, [])

  // Unique authors for filter
  const authors = useMemo(() => {
    const set = new Set()
    strategies.forEach(s => s.paper_authors?.forEach(a => set.add(a)))
    return [...set].sort()
  }, [strategies])

  // Filtered strategies
  const filtered = useMemo(() => {
    return strategies.filter(s => {
      if (filterStatus !== 'all' && s.status !== filterStatus) return false
      if (filterAuthor !== 'all' && !s.paper_authors?.includes(filterAuthor)) return false
      if (searchQuery) {
        const q = searchQuery.toLowerCase()
        return s.paper_title?.toLowerCase().includes(q) ||
               s.methodology_summary?.toLowerCase().includes(q) ||
               s.asset_universe?.some(a => a.toLowerCase().includes(q))
      }
      return true
    })
  }, [strategies, filterStatus, filterAuthor, searchQuery])

  const selected = selectedId ? strategies.find(s => s.id === selectedId) : filtered[0]
  const selectedTraces = selected ? onChainTraces.filter(t =>
    t.vault_address?.includes(selected.id.slice(0, 8))
  ) : []

  return (
    <div>
      <div className="fade-up fade-up-1" style={{ maxWidth: 640, marginBottom: 28 }}>
        <h2 style={{ fontFamily: 'var(--serif)', fontSize: '2rem', marginBottom: 10 }}>Intelligence — Reasoning</h2>
        <p className="body">
          Every strategy carries a verifiable reasoning trace. Methodology is extracted from
          published research, anchored on-chain, and auditable by anyone.
        </p>
      </div>

      {/* Tabs */}
      <div className="tabs fade-up fade-up-2" style={{ marginBottom: 24 }}>
        <div className={`tab${tab === 'strategies' ? ' active' : ''}`} onClick={() => setTab('strategies')}>Strategies</div>
        <div className={`tab${tab === 'traces' ? ' active' : ''}`} onClick={() => setTab('traces')}>On-Chain Traces</div>
      </div>

      {tab === 'strategies' && (
        <div className="trade-grid fade-up fade-up-2">
          {/* Left: Strategy list + filters */}
          <div>
            {/* Filters */}
            <div style={{ display: 'flex', gap: 8, marginBottom: 16, flexWrap: 'wrap' }}>
              <input
                type="text"
                placeholder="Search strategies…"
                value={searchQuery}
                onChange={e => setSearchQuery(e.target.value)}
                className="chat-input"
                style={{ flex: 1, minWidth: 140 }}
              />
              <select value={filterStatus} onChange={e => setFilterStatus(e.target.value)} className="chat-input" style={{ width: 'auto' }}>
                <option value="all">All status</option>
                <option value="live">Live</option>
                <option value="validated">Validated</option>
                <option value="candidate">Candidate</option>
              </select>
              <select value={filterAuthor} onChange={e => setFilterAuthor(e.target.value)} className="chat-input" style={{ width: 'auto' }}>
                <option value="all">All authors</option>
                {authors.map(a => <option key={a} value={a}>{a}</option>)}
              </select>
            </div>

            {loading && <div className="caption">Loading strategies…</div>}
            {error && <div className="info-box warning">API error: {error}</div>}
            {!loading && filtered.length === 0 && <div className="caption">No strategies match filters.</div>}

            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {filtered.map(s => (
                <StrategyReasoningCard
                  key={s.id}
                  strategy={s}
                  isSelected={selected?.id === s.id}
                  onClick={() => setSelectedId(s.id)}
                />
              ))}
            </div>
          </div>

          {/* Right: Detail view */}
          <div>
            {selected ? (
              <StrategyDetailView strategy={selected} traces={selectedTraces} />
            ) : (
              <div className="card" style={{ textAlign: 'center', padding: 40 }}>
                <div style={{ fontSize: '2rem', marginBottom: 10 }}>🧠</div>
                <strong>Select a strategy</strong>
                <p className="caption" style={{ marginTop: 8 }}>Click a strategy card to view its reasoning trace, metrics, and on-chain provenance.</p>
              </div>
            )}
          </div>
        </div>
      )}

      {tab === 'traces' && (
        <OnChainTraces />
      )}
    </div>
  )
}
