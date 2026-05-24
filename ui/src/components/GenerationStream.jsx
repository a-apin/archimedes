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
    case 'candidate_drafted':
      return `${data?.strategy_name || '?'} (${data?.candidate_id})`
    case 'candidate_evaluated': {
      const v = data?.rigor_verdict || {}
      const bits = []
      if (v.dsr != null) bits.push(`DSR ${v.dsr}`)
      if (v.pbo != null) bits.push(`PBO ${v.pbo}`)
      if (v.oos_sharpe != null) bits.push(`OOS ${v.oos_sharpe}`)
      return `${data?.candidate_id} — ${bits.join(' · ') || 'no metrics'}`
    }
    case 'best_selected':
      return `Picked ${data?.best_candidate_id} from ${data?.considered_count}`
    case 'trace_hashed':
      return `${(data?.trace_hash || '').slice(0, 14)}…`
    case 'persisted':
      return data?.redirect_url || ''
    case 'done':
      return `→ ${data?.strategy_id || ''}`
    case 'error':
      return data?.message || 'Unknown error'
    default:
      return ''
  }
}

export default function GenerationStream({ jobId, onDone, onReset, onPipelineSelected }) {
  const [events, setEvents] = useState([])
  const [terminal, setTerminal] = useState(null)  // 'done' | 'error' | null
  const [strategyId, setStrategyId] = useState(null)
  const [errorMsg, setErrorMsg] = useState('')
  const [showRejected, setShowRejected] = useState(false)
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
      if (name === 'persisted' && data?.strategy_id) setStrategyId(data.strategy_id)
      if (name === 'done') {
        setTerminal('done')
        if (data?.strategy_id) setStrategyId(data.strategy_id)
        es.close()
        onDone?.({ strategy_id: data?.strategy_id })
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

      {showRejected && (
        <RejectedCandidates jobId={jobId} onClose={() => setShowRejected(false)} />
      )}
    </div>
  )
}
