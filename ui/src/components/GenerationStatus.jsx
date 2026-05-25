import { useEffect, useState } from 'react'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

// Compact status table at the bottom of the Generate page. Polls
// /api/generate/jobs every 5s so navigating back to /generate after starting
// a job in another tab still surfaces it. Per generation-streaming-spec.md.

const STATE_TAGS = {
  queued:    { label: 'queued',    cls: 'tag-muted' },
  running:   { label: 'running',   cls: 'tag-accent' },
  done:      { label: 'done',      cls: 'tag-positive' },
  error:     { label: 'error',     cls: 'tag-negative' },
  cancelled: { label: 'cancelled', cls: 'tag-muted' },
}

function timeAgo(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  const secs = Math.floor((Date.now() - d.getTime()) / 1000)
  if (secs < 60) return `${secs}s ago`
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  return `${Math.floor(secs / 86400)}d ago`
}

export default function GenerationStatus({ activeJobId, onSelect }) {
  const [jobs, setJobs] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/generate/jobs?limit=10`)
        if (!res.ok) throw new Error(`Backend returned ${res.status}`)
        const data = await res.json()
        if (!cancelled) {
          setJobs(data.jobs || [])
          setError('')
        }
      } catch (e) {
        // Network failures bubble up as TypeError with no useful message; nginx
        // upstream errors return HTML bodies we never want to render raw.
        const msg = e?.message && e.message.length < 120 ? e.message : 'Failed to load jobs'
        if (!cancelled) setError(msg)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    const interval = setInterval(load, 5000)
    return () => { cancelled = true; clearInterval(interval) }
  }, [])

  if (loading && !jobs.length) return null
  if (!jobs.length) return null

  return (
    <div className="card" style={{ padding: 16 }}>
      <div className="label mb-2">Recent generations</div>
      {error && <div className="caption negative">{error}</div>}
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
          <thead>
            <tr style={{ textAlign: 'left', borderBottom: '1px solid var(--glass-border)' }}>
              <th style={{ padding: '6px 8px' }}>Intent</th>
              <th style={{ padding: '6px 8px' }}>State</th>
              <th style={{ padding: '6px 8px' }}>N</th>
              <th style={{ padding: '6px 8px' }}>Updated</th>
              <th style={{ padding: '6px 8px' }}></th>
            </tr>
          </thead>
          <tbody>
            {jobs.map(j => {
              const tag = STATE_TAGS[j.state] || STATE_TAGS.queued
              const isActive = j.job_id === activeJobId
              return (
                <tr
                  key={j.job_id}
                  style={{
                    borderBottom: '1px solid rgba(255,255,255,0.04)',
                    background: isActive ? 'rgba(255,209,102,0.07)' : 'transparent',
                  }}
                >
                  <td style={{ padding: '6px 8px', maxWidth: 320, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {j.brief_intent || '—'}
                  </td>
                  <td style={{ padding: '6px 8px' }}>
                    <span className={`tag ${tag.cls}`}>{tag.label}</span>
                  </td>
                  <td style={{ padding: '6px 8px' }}>{j.n_candidates}</td>
                  <td style={{ padding: '6px 8px', whiteSpace: 'nowrap' }} className="caption">
                    {timeAgo(j.updated_at)}
                  </td>
                  <td style={{ padding: '6px 8px', textAlign: 'right' }}>
                    {!isActive && (j.state === 'running' || j.state === 'queued') && (
                      <button
                        className="btn btn-outline btn-sm"
                        onClick={() => onSelect?.(j.job_id)}
                      >
                        Resume →
                      </button>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
