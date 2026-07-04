import { useEffect, useRef, useState } from 'react'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

// Recent Generations job table for the Generate page.
//
// Polls /api/generate/jobs every 5 s so the table stays live while the user
// stays on the page. Clicking a row calls `onDrillIn(job_id)` to open that
// job's SSE stream as a drill-down view.
//
// 401-GUARD (issue #872): the endpoint is owner-scoped and requires a SIWE
// session. Polling while unauthenticated spammed nginx with ~2,319 401s in 6h.
// Guard: only start the poll when `walletAddr` is truthy (wallet connected →
// user is authenticated or will be on first generate). On any 401 response,
// stop the interval immediately and mark the poll as blocked — no retry until
// `walletAddr` changes. This is defense-in-depth; the server enforces auth too.

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
  // epoch-0 sentinel means absent timestamp — render like a missing value
  if (d.getTime() <= 0) return '—'
  const secs = Math.floor((Date.now() - d.getTime()) / 1000)
  if (secs < 60) return `${secs}s ago`
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  return `${Math.floor(secs / 86400)}d ago`
}

export default function GenerationStatus({ walletAddr, activeJobId, onDrillIn }) {
  const [jobs, setJobs] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  // When the endpoint returns 401, block further polling until the user's
  // wallet/session changes. Prevents unauthenticated spam.
  const [blocked, setBlocked] = useState(false)
  const intervalRef = useRef(null)

  const stopPoll = () => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current)
      intervalRef.current = null
    }
  }

  // Reset block state whenever walletAddr changes (e.g. user signs in).
  useEffect(() => {
    if (walletAddr) setBlocked(false)
  }, [walletAddr])

  useEffect(() => {
    // Do not poll when unauthenticated or after a 401.
    if (!walletAddr || blocked) {
      setLoading(false)
      stopPoll()
      return
    }

    let cancelled = false

    const load = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/generate/jobs?limit=20`, {
          credentials: 'include',
        })
        if (res.status === 401) {
          // Stop polling immediately.  Clear stale rows so the user does not
          // see outdated data while unauthenticated, and surface a sign-in
          // prompt with a manual retry path (in case SIWE happened without
          // changing the connected wallet address).
          if (!cancelled) {
            stopPoll()
            setBlocked(true)
            setJobs([])
            setError('Session expired — sign in again to view your generations.')
          }
          return
        }
        if (!res.ok) throw new Error(`Backend returned ${res.status}`)
        const data = await res.json()
        if (!cancelled) {
          setJobs(data.jobs || [])
          setError('')
        }
      } catch (e) {
        const msg = e?.message && e.message.length < 120 ? e.message : 'Failed to load jobs'
        if (!cancelled) setError(msg)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    load()
    intervalRef.current = setInterval(load, 5000)

    return () => {
      cancelled = true
      stopPoll()
    }
  }, [walletAddr, blocked])

  if (loading && !jobs.length) return null
  if (!walletAddr) return null

  return (
    <div className="card" style={{ padding: 16 }}>
      <div className="label mb-2">Recent generations</div>
      {error && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span className="caption" style={{ color: 'var(--negative)' }}>{error}</span>
          {blocked && (
            <button
              className="btn btn-outline btn-sm"
              style={{ fontSize: '0.75rem', padding: '2px 8px' }}
              onClick={() => setBlocked(false)}
            >
              Retry
            </button>
          )}
        </div>
      )}
      {jobs.length === 0 && !error && (
        <div className="caption" style={{ color: 'var(--text-3)' }}>
          No generations yet — submit your first brief above.
        </div>
      )}
      {jobs.length > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <table
            style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}
          >
            <thead>
              <tr
                style={{ textAlign: 'left', borderBottom: '1px solid var(--glass-border)' }}
              >
                <th style={{ padding: '6px 8px' }}>Brief</th>
                <th style={{ padding: '6px 8px' }}>State</th>
                <th style={{ padding: '6px 8px' }}>N</th>
                <th style={{ padding: '6px 8px' }}>Updated</th>
                <th style={{ padding: '6px 8px' }} />
              </tr>
            </thead>
            <tbody>
              {jobs.map(j => {
                const tag = STATE_TAGS[j.state] || STATE_TAGS.queued
                const isActive = j.job_id === activeJobId
                return (
                  <tr
                    key={j.job_id}
                    onClick={() => onDrillIn?.(j.job_id)}
                    role="button"
                    tabIndex={0}
                    aria-label={`Open generation: ${j.brief_intent || j.job_id}`}
                    onKeyDown={e => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        onDrillIn?.(j.job_id)
                      }
                    }}
                    style={{
                      borderBottom: '1px solid rgba(255,255,255,0.04)',
                      background: isActive
                        ? 'rgba(255,209,102,0.07)'
                        : 'transparent',
                      cursor: 'pointer',
                      transition: 'background 0.12s',
                    }}
                    onMouseEnter={e => {
                      if (!isActive) e.currentTarget.style.background = 'rgba(255,255,255,0.03)'
                    }}
                    onMouseLeave={e => {
                      if (!isActive) e.currentTarget.style.background = 'transparent'
                    }}
                  >
                    <td
                      style={{
                        padding: '8px 8px',
                        maxWidth: 300,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {j.brief_intent || '—'}
                    </td>
                    <td style={{ padding: '8px 8px' }}>
                      <span className={`tag ${tag.cls}`}>{tag.label}</span>
                    </td>
                    <td style={{ padding: '8px 8px' }}>{j.n_candidates ?? '—'}</td>
                    <td
                      style={{ padding: '8px 8px', whiteSpace: 'nowrap' }}
                      className="caption"
                    >
                      {timeAgo(j.updated_at)}
                    </td>
                    <td style={{ padding: '8px 8px', textAlign: 'right' }}>
                      <span
                        className="caption"
                        style={{ color: 'var(--accent)', whiteSpace: 'nowrap' }}
                      >
                        {j.state === 'running' || j.state === 'queued' ? 'resume →' : 'view →'}
                      </span>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
