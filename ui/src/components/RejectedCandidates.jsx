import { useEffect, useState } from 'react'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

// Modal-ish overlay showing all candidates the agent produced for a job.
// The 'best' one was surfaced in the main flow; this view lets the user see
// the others and why they weren't picked.

export default function RejectedCandidates({ jobId, onClose }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    fetch(`${API_BASE}/api/generate/jobs/${encodeURIComponent(jobId)}/candidates`)
      .then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t) }))
      .then(d => { if (!cancelled) setData(d) })
      .catch(e => { if (!cancelled) setError(e.message || 'Failed to load candidates') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [jobId])

  return (
    <div
      onClick={onClose}
      style={{
        // Bumped backdrop opacity 0.6 → 0.85 so the modal body pops; the
        // inner card also gets a solid background + border + drop-shadow so
        // the page's glass-blur 'card' class doesn't render translucent
        // over the dimmed backdrop (which had been leaving text semi-readable).
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.85)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        zIndex: 1000, padding: 20,
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        className="card"
        style={{
          maxWidth: 720, width: '100%', maxHeight: '80vh', overflowY: 'auto', padding: 24,
          background: 'var(--bg-1)',
          border: '1px solid var(--glass-border)',
          boxShadow: '0 24px 48px rgba(0,0,0,0.5)',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12 }}>
          <div>
            <h3 style={{ marginBottom: 4 }}>Candidates considered</h3>
            <p className="caption" style={{ marginBottom: 0 }}>
              The agent generated multiple candidates and picked the best by rigor verdict.
              The others are shown here for inspection.
            </p>
          </div>
          <button className="btn btn-outline btn-sm" onClick={onClose}>Close</button>
        </div>

        {loading && <div className="caption">Loading…</div>}
        {error && <div className="info-box warning">{error}</div>}

        {data && data.candidates?.length === 0 && (
          <div className="caption">No candidate data on file for this job.</div>
        )}

        {data && data.candidates?.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {data.candidates.map(c => (
              <div
                key={c.candidate_id}
                className="card-flat"
                style={{
                  padding: 12,
                  border: c.selected ? '1px solid var(--accent)' : '1px solid var(--glass-border)',
                  background: c.selected ? 'rgba(255,209,102,0.06)' : 'transparent',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                  <div>
                    <strong>{c.strategy_name || c.candidate_id}</strong>
                    <span className="caption" style={{ marginLeft: 8 }}>({c.candidate_id})</span>
                  </div>
                  <div style={{ display: 'flex', gap: 6 }}>
                    {c.regime && c.regime !== 'neutral' && (
                      <span className="tag" style={{
                        background: c.regime === 'bull' ? 'rgba(34,197,94,0.15)' : 'rgba(239,68,68,0.15)',
                        color: c.regime === 'bull' ? 'var(--positive, #22c55e)' : 'var(--negative, #ef4444)',
                      }}>
                        {c.regime === 'bull' ? '🟢 Bull' : '🔴 Bear'}
                      </span>
                    )}
                    {c.selected && <span className="tag tag-accent">Selected</span>}
                    {c.passes_rigor
                      ? <span className="tag tag-positive">Passes rigor</span>
                      : <span className="tag tag-negative">Failed rigor</span>}
                  </div>
                </div>
                {c.rigor_verdict && (
                  <div className="caption" style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                    {c.rigor_verdict.dsr != null && <span>DSR: <strong>{c.rigor_verdict.dsr}</strong></span>}
                    {c.rigor_verdict.pbo != null && <span>PBO: <strong>{c.rigor_verdict.pbo}</strong></span>}
                    {c.rigor_verdict.oos_sharpe != null && <span>OOS Sharpe: <strong>{c.rigor_verdict.oos_sharpe}</strong></span>}
                    {c.rigor_verdict.lookahead_audit_passed != null && (
                      <span>Lookahead: <span className={`${c.rigor_verdict.lookahead_audit_passed ? 'i-lucide-check text-[var(--positive)]' : 'i-lucide-x text-[var(--negative)]'} w-3 h-3`} /></span>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
