import { useState, useEffect, useCallback } from 'react'
import {
  publicClient,
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

// ─── On-chain Traces Panel ───────────────────────────────────
// Reasoning is now the dedicated trace browser per page-roles-spec.md.
// Strategy detail (export, paper-claim delta, EfficientFrontier,
// CorrelationMatrix, RigorExplainer) lives on Library where the strategy
// itself does — open via ?highlight=<id> deep-link from any trace card.

function OnChainTraces({ onNavigate, highlightTraceId }) {
  const [traces, setTraces] = useState([])
  const [totalCount, setTotalCount] = useState(0)
  const [loading, setLoading] = useState(true)
  const [verifying, setVerifying] = useState({})
  const [verifyResults, setVerifyResults] = useState({})
  const [filter, setFilter] = useState('all') // 'all' | 'rebalance' | 'construction' | 'skip'

  const loadTraces = useCallback(async () => {
    setLoading(true)
    try {
      // Load from backend API (enriched with off-chain metadata)
      const data = await apiGet('/api/traces/?limit=50')
      setTraces(data.traces || [])
      setTotalCount(data.total || 0)
    } catch {
      // Fallback: read directly from on-chain
      const regAddr = NEW_CONTRACTS.traceRegistry
      if (!regAddr) return
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
            loaded.push({ id: i, agent, vault, trace_hash: traceHash, timestamp: Number(timestamp), decision_type: 'on-chain' })
          } catch {
            // Individual trace read failure — skip; the loop keeps going so a single bad row doesn't break the list.
          }
        }
        setTraces(loaded)
      } catch {
        // Initial registry-count read failure — leave traces empty; the user sees the "no traces" empty state.
      }
    }
    setLoading(false)
  }, [])

  useEffect(() => { loadTraces() }, [loadTraces])

  // Scroll + highlight the trace specified by ?trace_id=<id>
  useEffect(() => {
    if (!highlightTraceId || traces.length === 0) return
    // Small delay to ensure DOM is painted
    const timer = setTimeout(() => {
      const el = document.getElementById(`trace-${highlightTraceId}`)
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' })
        el.classList.add('trace-highlighted')
        setTimeout(() => el.classList.remove('trace-highlighted'), 3000)
      }
    }, 150)
    return () => clearTimeout(timer)
  }, [highlightTraceId, traces])

  const verifyTrace = async (traceId) => {
    setVerifying(prev => ({ ...prev, [traceId]: true }))
    try {
      const data = await apiGet(`/api/traces/${encodeURIComponent(traceId)}/verify`)
      setVerifyResults(prev => ({
        ...prev,
        [traceId]: data,
      }))
    } catch (err) {
      setVerifyResults(prev => ({
        ...prev,
        [traceId]: { is_verified: false, details: err.message },
      }))
    }
    setVerifying(prev => ({ ...prev, [traceId]: false }))
  }

  return (
    <div>
      <div className="label mb-3">Reasoning Trace Registry ({totalCount} total)</div>
      <p className="caption mb-4 max-w-[640px] leading-relaxed">
        Every trace below is a real agent decision: an autonomous rebalance, a
        regime change, or a strategy construction from the Generate page. The hash
        is computed deterministically off-chain and anchored on Arc via the
        <code style={{ marginLeft: 4 }}>ReasoningTraceRegistry</code> contract.
        Click <strong>Verify on-chain</strong> on any trace to recompute and check
        against the on-chain anchor; click <strong>→ Strategy in Library</strong>
        to jump to the source strategy and its full passport.
      </p>

      {/* Filter chips */}
      <div className="flex gap-2 flex-wrap mb-3">
        {['all', 'rebalance', 'construction', 'skip'].map(f => (
          <button
            key={f}
            className={`tag cursor-pointer ${filter === f ? 'tag-accent' : 'tag-muted'}`}
            onClick={() => setFilter(f)}
            style={{ border: 'none', padding: '4px 12px' }}
          >
            {f === 'all' ? 'All' : f === 'rebalance' ? '✅ Rebalances' : f === 'construction' ? '🏛️ Constructions' : '⏭ Skips'}
          </button>
        ))}
      </div>

      {/* Trace list */}
      {loading ? (
        <div className="caption">Loading traces…</div>
      ) : traces.length === 0 ? (
        <div className="card" style={{ padding: 18 }}>
          <p className="body" style={{ marginBottom: 6 }}>No reasoning traces yet.</p>
          <p className="caption">
            Traces accumulate when the autonomous agent rebalances vaults, or when you
            use the <a href="/generate" style={{ color: 'var(--accent)' }}>Generate</a> page to construct a portfolio (each
            construction emits a trace). If the page stays empty after generating, the
            agent runner may not be running locally — check <code>docker compose logs oracle</code>.
          </p>
        </div>
      ) : (
        <div className="flex flex-col gap-2.5">
          {traces
          .sort((a, b) => {
            // Most recent first
            const ta = a.timestamp ? new Date(typeof a.timestamp === 'number' && a.timestamp > 1e12 ? a.timestamp : a.timestamp * 1000).getTime() : 0
            const tb = b.timestamp ? new Date(typeof b.timestamp === 'number' && b.timestamp > 1e12 ? b.timestamp : b.timestamp * 1000).getTime() : 0
            return tb - ta
          })
          .filter(t => filter === 'all' || t.decision_type === filter)
          .map((t, i) => {
            const vResult = verifyResults[t.id]
            return (
              <div key={i} id={`trace-${t.id}`} className="card" style={{ padding: 14 }}>
                <div className="flex justify-between items-start mb-2">
                  <div className="flex gap-2 items-center flex-wrap">
                    <span style={{ fontWeight: 700, color: 'var(--accent)' }}>#{typeof t.id === 'string' ? t.id.slice(0, 8) : t.id}</span>
                    <span className={`tag ${t.decision_type === 'rebalance' ? 'tag-accent' : t.decision_type === 'construction' ? 'tag-positive' : 'tag-muted'}`}>
                      {t.decision_type}
                    </span>
                    {t.is_verified && <span className="flex items-center gap-1 text-xs text-[var(--positive)]"><span className="i-lucide-check w-3 h-3" /> verified</span>}
                    {t.arc_tx_hash && <span className="flex items-center gap-1 text-xs"><span className="i-lucide-anchor w-3 h-3" /> on-chain</span>}
                  </div>
                  <div className="caption">{timeAgo(t.timestamp)}</div>
                </div>

                <div className="grid grid-cols-2 gap-2 mb-2">
                  <div>
                    <div className="caption">Vault</div>
                    <code style={{ fontSize: '0.75rem' }}>{shortAddr(t.vault_address || t.vault)}</code>
                  </div>
                  <div>
                    <div className="caption">Trace Hash</div>
                    <code style={{ fontSize: '0.75rem' }}>{shortHash(t.trace_hash || t.traceHash)}</code>
                  </div>
                </div>

                {t.reasoning && (
                  <div className="mb-2">
                    <div className="caption">Reasoning</div>
                    <p className="body" style={{ fontSize: '0.85rem', lineHeight: 1.4 }}>{t.reasoning.slice(0, 200)}{t.reasoning.length > 200 ? '…' : ''}</p>
                  </div>
                )}

                {t.regime_at_decision && (
                  <div style={{ marginBottom: 8 }}>
                    <span className="caption">Regime: </span>
                    <span className={`tag ${t.regime_at_decision === 'risk_on' ? 'tag-positive' : t.regime_at_decision === 'risk_off' ? 'tag-muted' : 'tag-accent'}`}>
                      {t.regime_at_decision}
                    </span>
                  </div>
                )}

                {t.confidence > 0 && (
                  <div className="mb-2">
                    <div className="caption">Confidence: {(t.confidence * 100).toFixed(0)}%</div>
                    <div className="rounded h-1 w-full" style={{ background: 'var(--bg-2)' }}>
                      <div className="rounded h-1 bg-[var(--accent)]" style={{ width: `${t.confidence * 100}%` }} />
                    </div>
                  </div>
                )}

                {/* On-chain link — shown upfront whenever the trace has an
                    arc_tx_hash, regardless of whether the user has clicked
                    Verify yet. The trace already knows its tx + blocks from
                    the API response; we don't need a verify roundtrip to
                    expose the arcscan link. The Verify button below still
                    re-fetches the on-chain receipt and confirms hash match,
                    but the link doesn't gate on it. */}
                {t.arc_tx_hash && (
                  <div className="flex items-center gap-3 flex-wrap text-xs text-[var(--text-3)] mb-2">
                    <span className="flex items-center gap-1">
                      <span className="i-lucide-file-text w-3 h-3" />
                      Tx: <a
                        href={`https://testnet.arcscan.app/tx/${t.arc_tx_hash}`}
                        target="_blank"
                        rel="noreferrer"
                        className="mono underline decoration-dotted underline-offset-2 hover:text-[var(--accent)] transition-colors"
                      >
                        {shortHash(t.arc_tx_hash)}
                      </a>
                      <span className="i-lucide-external-link w-2.5 h-2.5" />
                    </span>
                    {t.commit_block_number != null && (
                      <span className="flex items-center gap-1">
                        <span className="i-lucide-box w-3 h-3" />
                        Block #{t.commit_block_number.toLocaleString()}
                      </span>
                    )}
                  </div>
                )}
                {!t.arc_tx_hash && (
                  <div className="caption text-[var(--text-4)] flex items-center gap-1 mb-2">
                    <span className="i-lucide-clock w-3 h-3" />
                    Not yet anchored on-chain
                  </div>
                )}

                {/* Verify button + strategy back-link */}
                <div className="flex gap-2 items-center flex-wrap">
                  <button
                    className="btn btn-outline btn-sm flex items-center gap-1.5"
                    onClick={() => verifyTrace(t.id)}
                    disabled={verifying[t.id]}
                    title="Re-fetch the on-chain receipt and confirm the trace hash matches"
                  >
                    {verifying[t.id] ? (
                      'Verifying…'
                    ) : vResult?.is_verified ? (
                      <><span className="i-lucide-check w-3.5 h-3.5 positive" /> Hash verified ✓</>
                    ) : (
                      <><span className="i-lucide-search w-3.5 h-3.5" /> Verify hash on-chain</>
                    )}
                  </button>
                  {t.strategy_id && onNavigate && (
                    <button
                      className="btn btn-outline btn-sm"
                      onClick={() => onNavigate('library', { highlight: t.strategy_id })}
                      title="Open this trace's strategy in the Library"
                    >
                      → Strategy in Library
                    </button>
                  )}
                  {vResult && !vResult.is_verified && (
                    <span className="caption flex items-center gap-1 negative">
                      <span className="i-lucide-x w-3 h-3" />
                      {vResult.details}
                    </span>
                  )}

                  {/* Why does this matter? disclosure — always available,
                      not gated behind clicking Verify. */}
                  <details className="mt-1.5 w-full">
                    <summary className="caption text-[var(--text-4)] cursor-pointer hover:text-[var(--text-2)] transition-colors select-none">
                      Why does this matter?
                    </summary>
                    <div className="caption text-[var(--text-3)] mt-1.5 max-w-[480px] leading-relaxed">
                      The hash is computed deterministically from the agent's reasoning, allocations, and regime context. By anchoring it on Arc's <code>ReasoningTraceRegistry</code>, anyone can independently recompute the hash and confirm the agent's decision existed at the recorded block — proving the reasoning preceded the trade, not the other way around.
                    </div>
                  </details>
                </div>

                {/* Temporal binding verification */}
                {t.temporal_binding_valid != null && (
                  <div className="mt-2 rounded-md px-3 py-2" style={{ background: t.temporal_binding_valid ? 'rgba(34,197,94,0.1)' : 'rgba(239,68,68,0.1)' }}>
                    <div className="flex items-center gap-1.5 mb-1">
                      <span className={`w-4 h-4 flex-shrink-0 ${t.temporal_binding_valid ? 'i-lucide-check-circle text-[var(--positive)]' : 'i-lucide-x-circle text-[var(--negative)]'}`} />
                      <strong className="text-[0.85rem]">Temporal Binding</strong>
                      {t.temporal_binding_valid && <span className="tag tag-positive text-[0.7rem]">VERIFIED</span>}
                    </div>
                    <div className="text-xs text-[var(--text-3)] leading-relaxed">
                      {t.commit_block_number != null && <div>Commit block: <strong>#{t.commit_block_number}</strong></div>}
                      {t.trade_block_number != null && <div>Trade block: <strong>#{t.trade_block_number}</strong></div>}
                      {t.reveal_block_number != null && <div>Reveal block: <strong>#{t.reveal_block_number}</strong></div>}
                      {t.temporal_binding_valid && (
                        <div style={{ marginTop: 4, fontStyle: 'italic' }}>
                          Trace committed before trade executed (commit &lt; trade &lt; reveal)
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ─── Main Export ─────────────────────────────────────────────

export default function Reasoning({ onNavigate }) {
  // Read ?trace_id= from URL for deep-link navigation from Portfolio
  const highlightTraceId = typeof window !== 'undefined'
    ? new URLSearchParams(window.location.search).get('trace_id')
    : null

  return (
    <div>
      <div className="max-w-[720px] mb-7">
        <h2 className="font-serif text-[2rem] mb-2.5">Reasoning</h2>
        <p className="body">
          Every autonomous agent decision is anchored on-chain by hash. Browse the
          trace timeline below, verify any hash against the on-chain registry, and
          follow each trace back to the source strategy in the Library.
        </p>
      </div>
      <OnChainTraces onNavigate={onNavigate} highlightTraceId={highlightTraceId} />
    </div>
  )
}
