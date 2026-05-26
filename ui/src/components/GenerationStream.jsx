import { useEffect, useRef, useState } from 'react'
import RejectedCandidates from './RejectedCandidates'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

// Per docs/specs/generation-streaming-spec.md. The EventSource auto-reconnects
// on network blips; Last-Event-ID is honoured server-side so we resume from
// where we left off.

const EVENT_LABELS = {
  job_queued: 'Job queued',
  brief_validated: 'Brief validated',
  pipeline_selected: 'Pipeline selected',
  candidates_selected: 'Candidates selected',
  agent_iteration: 'Agent iteration',
  tool_called: 'Tool called',
  tool_result: 'Tool result',
  candidate_drafted: 'Candidate drafted',
  candidate_failed: 'No candidate',
  candidate_evaluated: 'Candidate evaluated',
  best_selected: 'Best selected',
  trace_hashed: 'Trace hashed',
  persisted: 'Persisted',
  done: 'Done',
  error: 'Error',
}

function summarizeEvent(name, data) {
  switch (name) {
    case 'job_queued':
      return data?.brief?.intent
        ? `"${data.brief.intent.slice(0, 90)}${data.brief.intent.length > 90 ? '…' : ''}"`
        : ''
    case 'brief_validated':
      return `Risk: ${data?.risk_appetite || '—'}`
    case 'pipeline_selected':
      return `${data?.pipeline || '?'} — ${data?.reason || ''}`
    case 'candidates_selected':
      return `Considering ${data?.candidate_count || '?'} candidates; ${(data?.source_arxiv_ids?.length || 0)} papers`
    case 'agent_iteration':
      return `Iteration ${data?.iteration_n}/${data?.max_iterations} (${data?.candidate_id || ''})`
    case 'tool_called':
      return `${data?.tool_name}(${data?.args_summary || ''})`
    case 'tool_result':
      return `${data?.tool_name} → ${data?.result_summary || 'ok'}`
    case 'candidate_drafted': {
      const rTag = data?.regime === 'bull' ? '🟢' : data?.regime === 'bear' ? '🔴' : ''
      return `${rTag} ${data?.strategy_name || '?'} (${data?.candidate_id})`
    }
    case 'candidate_evaluated': {
      const v = data?.rigor_verdict || {}
      const bits = []
      if (v.dsr != null) bits.push(`DSR ${v.dsr}`)
      if (v.pbo != null) bits.push(`PBO ${v.pbo}`)
      if (v.oos_sharpe != null) bits.push(`OOS ${v.oos_sharpe}`)
      return `${data?.candidate_id}${bits.length ? ' — ' + bits.join(' · ') : ''}`
    }
    case 'best_selected':
      return `Picked ${data?.best_candidate_id} from ${data?.considered_count}`
    case 'trace_hashed':
      return `${(data?.trace_hash || '').slice(0, 14)}…`
    case 'candidate_failed':
      return `${data?.regime === 'bull' ? '🟢' : '🔴'} ${data?.message || 'No candidate'}`
    case 'persisted':
      return `${data?.regime === 'bull' ? '🟢' : data?.regime === 'bear' ? '🔴' : ''} ${data?.redirect_url || ''}`
    case 'done':
      return `→ ${data?.strategy_id || ''}`
    case 'error':
      return data?.message || 'Unknown error'
    default:
      return ''
  }
}

export default function GenerationStream({ jobId, onDone, onReset, onPipelineSelected, onNavigate }) {
  const [events, setEvents] = useState([])
  const [terminal, setTerminal] = useState(null)  // 'done' | 'error' | null
  const [strategyId, setStrategyId] = useState(null)
  const [errorMsg, setErrorMsg] = useState('')
  const [showRejected, setShowRejected] = useState(false)
  const [draftedCandidates, setDraftedCandidates] = useState([])  // {candidate_id, strategy_name, regime, strategy_id}
  const [failedRegimes, setFailedRegimes] = useState([])  // {regime, message}
  const esRef = useRef(null)
  const scrollRef = useRef(null)

  useEffect(() => {
    if (!jobId) return
    setEvents([])
    setTerminal(null)
    setStrategyId(null)
    setErrorMsg('')

    const url = `${API_BASE}/api/generate/stream/${encodeURIComponent(jobId)}`
    const es = new EventSource(url)
    esRef.current = es

    const handle = (name) => (e) => {
      let data = {}
      try { data = JSON.parse(e.data) } catch { /* keep empty */ }
      setEvents(prev => [...prev, { id: Number(e.lastEventId) || prev.length + 1, name, data }])
      if (name === 'pipeline_selected' && data?.pipeline) {
        onPipelineSelected?.(data.pipeline)
      }
      if (name === 'candidate_drafted') {
        setDraftedCandidates(prev => [...prev, {
          candidate_id: data?.candidate_id,
          strategy_name: data?.strategy_name,
          regime: data?.regime,
          weights_preview: data?.weights_preview,
        }])
      }
      if (name === 'candidate_failed') {
        setFailedRegimes(prev => [...prev, {
          regime: data?.regime,
          message: data?.message,
        }])
      }
      if (name === 'persisted' && data?.strategy_id) {
        setStrategyId(data.strategy_id)
        // Update the drafted candidate with its strategy_id
        setDraftedCandidates(prev => prev.map(c =>
          c.candidate_id === data.candidate_id ? { ...c, strategy_id: data.strategy_id } : c
        ))
      }
      if (name === 'done') {
        setTerminal('done')
        if (data?.strategy_id) setStrategyId(data.strategy_id)
        es.close()
        onDone?.({
          strategy_id: data?.strategy_id,
          all_strategy_ids: data?.all_strategy_ids,
        })
      }
      if (name === 'error') {
        setTerminal('error')
        setErrorMsg(data?.message || 'Generation failed')
        es.close()
      }
    }

    Object.keys(EVENT_LABELS).forEach(name => es.addEventListener(name, handle(name)))

    es.onerror = () => {
      // EventSource will auto-reconnect; only treat as fatal once terminal.
      if (terminal) es.close()
    }

    return () => { es.close() }
    // jobId is the only real dep — re-subscribe on job change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId])

  // Autoscroll the event list as it grows.
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [events])

  const consideredCount = events.find(e => e.name === 'best_selected')?.data?.considered_count || 0

  return (
    <div className="card" style={{ padding: 20 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <div>
          <div className="label">Generating — job {jobId.slice(0, 10)}…</div>
          {terminal === 'done' && (
            <div className="positive caption" style={{ marginTop: 4 }}>
              <span className="i-lucide-check w-3.5 h-3.5 mr-1" /> Strategy persisted{strategyId ? ` as ${strategyId}` : ''}
            </div>
          )}
          {terminal === 'error' && (
            <div className="negative caption" style={{ marginTop: 4 }}>
              <span className="i-lucide-x w-3.5 h-3.5 mr-1" /> {errorMsg}
            </div>
          )}
          {!terminal && (
            <div className="caption" style={{ marginTop: 4, color: 'var(--text-3)' }}>
              Streaming live · {events.length} event{events.length === 1 ? '' : 's'}
            </div>
          )}
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          {consideredCount > 1 && (
            <button
              className="btn btn-outline btn-sm"
              onClick={() => setShowRejected(true)}
              title="See all candidates the agent considered"
            >
              Considered {consideredCount} candidates
            </button>
          )}
          <button className="btn btn-outline btn-sm" onClick={onReset}>
            {terminal ? 'New generation' : 'Cancel'}
          </button>
        </div>
      </div>

      <div
        ref={scrollRef}
        style={{
          maxHeight: 320,
          overflowY: 'auto',
          background: 'rgba(255,255,255,0.02)',
          border: '1px solid var(--glass-border)',
          borderRadius: 6,
          padding: 12,
          fontSize: '0.82rem',
          fontFamily: 'var(--mono, monospace)',
        }}
      >
        {events.length === 0 && (
          <div className="caption">Waiting for first event…</div>
        )}
        {events.map(ev => (
          <div key={ev.id} style={{ marginBottom: 4, lineHeight: 1.4 }}>
            <span style={{ color: 'var(--text-4)', marginRight: 8 }}>#{ev.id}</span>
            <span style={{ color: 'var(--accent)', fontWeight: 600 }}>{EVENT_LABELS[ev.name] || ev.name}</span>
            {' — '}
            <span>{summarizeEvent(ev.name, ev.data)}</span>
          </div>
        ))}
      </div>

      {/* ── Dual regime result cards (Issue #163) ── */}
      {terminal === 'done' && draftedCandidates.length >= 2 && (
        <div style={{ marginTop: 16 }}>
          <div className="label" style={{ marginBottom: 8 }}>Strategy Candidates</div>
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
            gap: 12,
          }}>
            {draftedCandidates.map(c => {
              // Once the candidate has a strategy_id, the entire card becomes
              // a navigation affordance to its Library passport — the button
              // below is preserved as a redundant explicit CTA for users who
              // expect a labelled trigger. The button stops propagation so
              // its click isn't double-counted.
              const navigateToLibrary = () => {
                localStorage.removeItem('archimedes:currentJobId')
                if (onNavigate) {
                  onNavigate('library', { highlight: c.strategy_id, tab: 'generated' })
                } else {
                  window.location.hash = `#/library?highlight=${c.strategy_id}`
                }
              }
              const clickable = Boolean(c.strategy_id)
              return (
                <div
                  key={c.candidate_id}
                  className="card"
                  onClick={clickable ? navigateToLibrary : undefined}
                  onKeyDown={clickable ? (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); navigateToLibrary() } } : undefined}
                  role={clickable ? 'link' : undefined}
                  tabIndex={clickable ? 0 : undefined}
                  aria-label={clickable ? `Open ${c.strategy_name} in Library` : undefined}
                  style={{
                    padding: 16,
                    border: `2px solid ${c.regime === 'bull' ? 'var(--positive, #22c55e)' : c.regime === 'bear' ? 'var(--negative, #ef4444)' : 'var(--glass-border)'}`,
                    cursor: clickable ? 'pointer' : 'default',
                    transition: 'transform 0.12s ease-out, border-color 0.12s ease-out',
                  }}
                  onMouseEnter={clickable ? (e) => { e.currentTarget.style.transform = 'translateY(-1px)' } : undefined}
                  onMouseLeave={clickable ? (e) => { e.currentTarget.style.transform = 'translateY(0)' } : undefined}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                    <span style={{
                      display: 'inline-block',
                      padding: '2px 10px',
                      borderRadius: 999,
                      fontSize: '0.75rem',
                      fontWeight: 700,
                      background: c.regime === 'bull' ? 'rgba(34,197,94,0.15)' : c.regime === 'bear' ? 'rgba(239,68,68,0.15)' : 'var(--bg-2)',
                      color: c.regime === 'bull' ? 'var(--positive, #22c55e)' : c.regime === 'bear' ? 'var(--negative, #ef4444)' : 'var(--text-2)',
                    }}>
                      {c.regime === 'bull' ? '🟢 Bull' : c.regime === 'bear' ? '🔴 Bear' : 'Neutral'}
                    </span>
                    <span className="label" style={{ fontSize: '0.85rem' }}>{c.strategy_name}</span>
                  </div>
                  {c.weights_preview && (
                    <div className="caption" style={{ marginBottom: 8 }}>
                      {Object.entries(c.weights_preview)
                        .sort(([, a], [, b]) => b - a)
                        .map(([sym, w]) => `${sym} ${(w * 100).toFixed(0)}%`)
                        .join(' · ')}
                    </div>
                  )}
                  {c.strategy_id && (
                    <button
                      className="btn btn-primary btn-sm"
                      style={{ width: '100%', marginTop: 4 }}
                      onClick={(e) => { e.stopPropagation(); navigateToLibrary() }}
                    >
                      View in Library →
                    </button>
                  )}
                </div>
              )
            })}
          </div>
          {failedRegimes.length > 0 && (
            <div className="info-box warning" style={{ marginTop: 12 }}>
              {failedRegimes.map((f, i) => (
                <div key={i}>
                  {f.regime === 'bull' ? '🟢' : '🔴'} {f.message}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {showRejected && (
        <RejectedCandidates jobId={jobId} onClose={() => setShowRejected(false)} />
      )}
    </div>
  )
}
