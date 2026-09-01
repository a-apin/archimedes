import { useEffect, useRef, useState } from 'react'
import GenerationUnavailable from './GenerationUnavailable'
import RejectedCandidates from './RejectedCandidates'
import { apiPost } from '../api'

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

// Small regime marker: trending-up (green) for bull, trending-down (red) for
// bear. `fallbackBear` renders the bear icon for any non-bull regime — matches
// the old green/red two-state behaviour on the failure/persist log lines.
function RegimeIcon({ regime, fallbackBear = false }) {
  if (regime === 'bull') {
    return <span className="i-lucide-trending-up w-3.5 h-3.5" style={{ color: 'var(--positive, #22c55e)' }} />
  }
  if (regime === 'bear' || fallbackBear) {
    return <span className="i-lucide-trending-down w-3.5 h-3.5" style={{ color: 'var(--negative, #ef4444)' }} />
  }
  return null
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
      // No papers count here — paper retrieval happens inside each candidate's
      // debate run, so no honest count exists yet. The real citations arrive
      // per candidate on candidate_drafted below.
      return `Considering ${data?.candidate_count || '?'} candidates`
    case 'agent_iteration':
      return `Iteration ${data?.iteration_n}/${data?.max_iterations} (${data?.candidate_id || ''})`
    case 'tool_called':
      return `${data?.tool_name}(${data?.args_summary || ''})`
    case 'tool_result':
      return `${data?.tool_name} → ${data?.result_summary || 'ok'}`
    case 'candidate_drafted': {
      // Papers count comes from the candidate's ACTUAL, provenance-checked
      // citations (source_arxiv_ids on the event) — omitted when absent,
      // never invented.
      const nPapers = data?.source_arxiv_ids?.length || 0
      return (
        <>
          <RegimeIcon regime={data?.regime} /> {data?.strategy_name || '?'} ({data?.candidate_id})
          {nPapers > 0 ? ` — grounded in ${nPapers} paper${nPapers === 1 ? '' : 's'}` : ''}
        </>
      )
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
      return (
        <>
          <RegimeIcon regime={data?.regime} fallbackBear /> {data?.message || 'No candidate'}
        </>
      )
    case 'persisted':
      return (
        <>
          <RegimeIcon regime={data?.regime} /> {data?.redirect_url || ''}
        </>
      )
    case 'done':
      return `→ ${data?.strategy_id || ''}`
    case 'error':
      return data?.message || 'Unknown error'
    default:
      return ''
  }
}

export default function GenerationStream({ jobId, onDone, onReset, onPipelineSelected, onNavigate, onBroaden, onSurprise, hideReset = false }) {
  const [events, setEvents] = useState([])
  const [terminal, setTerminal] = useState(null)  // 'done' | 'error' | null
  const [strategyId, setStrategyId] = useState(null)
  const [servedModel, setServedModel] = useState(null)  // provenance: model that actually ran
  const [errorMsg, setErrorMsg] = useState('')
  // Full `error` payload, kept so the terminal card can render the honest
  // outcome (steer, measured candidate count, corpus-derived ways forward)
  // instead of only the one-line message.
  const [errorData, setErrorData] = useState(null)
  const [showRejected, setShowRejected] = useState(false)
  const [draftedCandidates, setDraftedCandidates] = useState([])  // {candidate_id, strategy_name, regime, strategy_id}
  const [failedRegimes, setFailedRegimes] = useState([])  // {regime, message}
  const [cancelling, setCancelling] = useState(false)
  const [cancelError, setCancelError] = useState('')
  const esRef = useRef(null)
  const scrollRef = useRef(null)
  // Mirror of `terminal` so the EventSource `onerror` handler reads the current
  // state. `onerror` closes over the effect's first render, where `terminal` is
  // null; without this ref it would never see 'done'/'error' and could keep
  // auto-reconnecting after the stream has legitimately ended.
  const terminalRef = useRef(null)

  useEffect(() => {
    if (!jobId) return
    setEvents([])
    setTerminal(null)
    terminalRef.current = null
    setStrategyId(null)
    setServedModel(null)
    setErrorMsg('')
    setErrorData(null)
    setCancelling(false)
    setCancelError('')

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
        terminalRef.current = 'done'
        if (data?.strategy_id) setStrategyId(data.strategy_id)
        if (data?.served_model) setServedModel(data.served_model)
        es.close()
        onDone?.({
          strategy_id: data?.strategy_id,
          all_strategy_ids: data?.all_strategy_ids,
          served_model: data?.served_model,
        })
      }
      if (name === 'error') {
        setTerminal('error')
        terminalRef.current = 'error'
        setErrorMsg(data?.message || 'Generation failed')
        setErrorData(data || null)
        es.close()
      }
    }

    Object.keys(EVENT_LABELS).forEach(name => es.addEventListener(name, handle(name)))

    es.onerror = () => {
      // EventSource will auto-reconnect; only treat as fatal once terminal.
      // Read the live state via the ref — the closed-over `terminal` is stale
      // (captured at first render), so relying on it would let the stream
      // reconnect-loop instead of settling after 'done'/'error'.
      if (terminalRef.current) es.close()
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

  // The actual Cancel action (#1355) — POST /api/generate/jobs/{id}/cancel.
  // Distinct from `onReset`/`hideReset` above: those control ONLY whether
  // this component renders its own "back to table" affordance (the page
  // that mounts us may already provide one, as Generate.jsx's drill-in
  // view does) and never called this endpoint. Best-effort: the SSE stream
  // is the source of truth for whether the job actually stopped — once the
  // server flips the job to `cancelled` it pushes a CANCELLED error event on
  // this same stream, which the existing `error` handler above already
  // renders as the terminal outcome.
  const handleCancel = async () => {
    if (cancelling || terminal) return
    setCancelling(true)
    setCancelError('')
    try {
      await apiPost(`/api/generate/jobs/${encodeURIComponent(jobId)}/cancel`, {})
    } catch (e) {
      setCancelError(e.message || 'Cancel request failed — the job may still be running.')
    } finally {
      setCancelling(false)
    }
  }

  return (
    <div className="card" style={{ padding: 20 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        {/* Generation runs for minutes and every terminal outcome landed in
            plain <div>s: a screen-reader user who submitted a brief had no way
            to know the run was progressing, had finished, or had errored
            (4.1.3). role="status" with aria-live toggled to "assertive" on failure
 (and "polite" otherwise) — the explicit aria-live overrides the role's
 implicit politeness, so an error is announced even when the user is not
 on the log below. */}
        <div>
          <div className="label">Generating — job {jobId.slice(0, 10)}…</div>
          {/* The live region holds ONLY the terminal outcome, and is mounted
              empty from the start so the message it later receives is actually
              announced. The running-state event counter below is deliberately
              OUTSIDE it: `events.length` increments on every SSE frame, and
              while it sat inside this region a screen reader re-read the whole
              block — job id and all — once per event, for the several minutes
              a generation runs. That buried the very outcome the region exists
              to convey, and it defeated the point of the role="log" feed
              further down, which already reports progress one entry at a
              time. */}
          <div
            role="status"
            aria-live={terminal === 'error' ? 'assertive' : 'polite'}
          >
            {terminal === 'done' && (
              <div className="positive caption" style={{ marginTop: 4 }}>
                <span aria-hidden="true" className="i-lucide-check w-3.5 h-3.5 mr-1" /> Strategy persisted{strategyId ? ` as ${strategyId}` : ''}
                {servedModel && servedModel !== 'fixture' && (
                  <span style={{ color: 'var(--text-3)' }}> · served by <strong>{servedModel}</strong></span>
                )}
              </div>
            )}
            {terminal === 'error' && (
              <div className="negative caption" style={{ marginTop: 4 }}>
                <span aria-hidden="true" className="i-lucide-x w-3.5 h-3.5 mr-1" /> {errorMsg}
              </div>
            )}
          </div>
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
          {/* Reachable for any running/queued job regardless of `hideReset` —
              the `hideReset` gate below only concerns this component's OWN
              "back to table" affordance, which is unrelated to actually
              stopping the job (#1355: the previous single button was labeled
              "Cancel" but its handler was `onReset`, which cancels nothing;
              it was also unreachable because the only mount always passes
              `hideReset`). */}
          {!terminal && (
            <button className="btn btn-outline btn-sm" onClick={handleCancel} disabled={cancelling}>
              {cancelling ? 'Cancelling…' : 'Cancel'}
            </button>
          )}
          {terminal && !hideReset && (
            <button className="btn btn-outline btn-sm" onClick={onReset}>
              New generation
            </button>
          )}
        </div>
      </div>
      {cancelError && (
        <div className="negative caption" style={{ marginTop: -6, marginBottom: 12 }}>
          {cancelError}
        </div>
      )}

      {/* A run that stopped before synthesis is a RESULT, not a dead end: the
          card names the steer, the count lexical retrieval actually returned,
          and the two ways forward. Renders only when the server sent the
          structured payload; any other error keeps the one-line treatment
          above. */}
      {terminal === 'error' && errorData?.reason_code && (
        <GenerationUnavailable data={errorData} onBroaden={onBroaden} onSurprise={onSurprise} />
      )}

      {/* role="log" is the right role for an append-only feed: it keeps
          announcements to newly added entries instead of re-reading the whole
          scroller on every event. */}
      <div
        ref={scrollRef}
        role="log"
        aria-live="polite"
        aria-relevant="additions text"
        aria-label="Generation events"
        style={{
          maxHeight: 320,
          overflowY: 'auto',
          background: 'var(--glass)',
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
      {terminal === 'done' && (draftedCandidates.length >= 1 || failedRegimes.length >= 1) && (
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
                  window.location.assign(`/app/library?highlight=${encodeURIComponent(c.strategy_id)}`)
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
                      background: c.regime === 'bull' ? 'rgba(34,197,94,0.15)' : c.regime === 'bear' ? 'rgba(239,68,68,0.15)' : 'var(--surface-2)',
                      color: c.regime === 'bull' ? 'var(--positive, #22c55e)' : c.regime === 'bear' ? 'var(--negative, #ef4444)' : 'var(--text-2)',
                    }}>
                      {c.regime === 'bull' ? <><RegimeIcon regime="bull" /> Bull</> : c.regime === 'bear' ? <><RegimeIcon regime="bear" /> Bear</> : 'Neutral'}
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
                  <RegimeIcon regime={f.regime} fallbackBear /> {f.message}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {showRejected && (
        <RejectedCandidates jobId={jobId} onClose={() => setShowRejected(false)} onNavigate={onNavigate} />
      )}
    </div>
  )
}
