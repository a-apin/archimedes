import { useState, useEffect, useCallback } from 'react'
import { apiGet } from '../api'
import { checkSession } from '../siwe'

// Insights — the public conversion + traction dashboard (#787, #830).
//
// Renders the live conversion instruments as a human-readable dashboard instead
// of raw JSON. Everything on the PUBLIC tab is PII-free by design:
//   - Traction: human vs agent REQUEST counts (honestly labelled — these are
//     cumulative request tallies, bot-inflated, NOT unique users) + the honest
//     distinct real-users (wallet) count alongside them.
//   - Conversion funnel: distinct visitors through landed → generation_started →
//     wallet_connected → vault_deployed, with step-conversion %.
//   - Visitor insights: distinct visitors by country + device, drawn from the
//     SAME JS-gated `landed` beacon population as the funnel (#830) — geo/device
//     count agrees with the funnel `landed` count by construction. Geography is
//     `ZZ` until Dan's CloudFront terraform apply lands (#795).
//
// The INTERNAL tab (cost / ops) is SIWE-gated: it only renders the private
// dashboard when the viewer holds a valid session, and its cards read the
// SIWE-gated /api/metrics/private/* endpoints (401 when anonymous).

const FUNNEL_LABELS = {
  landed: 'Landed',
  generation_started: 'Tried Generate',
  wallet_connected: 'Connected Wallet',
  vault_deployed: 'Deployed Vault',
}

const DEVICE_LABELS = { mobile: 'Mobile', tablet: 'Tablet', desktop: 'Desktop', tv: 'TV', unknown: 'Unknown' }

const COUNTRY_NAMES = {
  US: 'United States', GB: 'United Kingdom', DE: 'Germany', BR: 'Brazil', TR: 'Türkiye',
  CA: 'Canada', FR: 'France', IN: 'India', NL: 'Netherlands', SG: 'Singapore',
  ZZ: 'Unknown / not provided',
}

const card = {
  background: 'var(--surface, #14161c)',
  border: '1px solid var(--border, #262a34)',
  borderRadius: 12,
  padding: '20px 22px',
}

function Bar({ pct, color = '#5b9dff' }) {
  return (
    <div style={{ background: 'rgba(255,255,255,0.06)', borderRadius: 6, height: 10, overflow: 'hidden' }}>
      <div style={{ width: `${Math.max(0, Math.min(100, pct))}%`, background: color, height: '100%', transition: 'width .3s' }} />
    </div>
  )
}

export default function Insights() {
  const [metrics, setMetrics] = useState(null)
  const [funnel, setFunnel] = useState(null)
  const [visitors, setVisitors] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [visitorsError, setVisitorsError] = useState(null)

  // SIWE session drives the private (cost/ops) tab. Anonymous viewers only ever
  // see the public dashboard; the internal cards read SIWE-gated endpoints.
  const [session, setSession] = useState({ authenticated: false, wallet: null })
  const [tab, setTab] = useState('public')

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    setVisitorsError(null)
    // Core dashboard = metrics + funnel; visitor-insights is loaded independently
    // so a transient visitors failure (500/network) degrades to a partial render
    // (the visitors card shows an error/empty state) instead of blanking the whole
    // dashboard — per-endpoint resilience (#830 review).
    try {
      const [m, f] = await Promise.all([
        apiGet('/api/metrics'),
        apiGet('/api/metrics/funnel'),
      ])
      setMetrics(m)
      setFunnel(f)
    } catch (e) {
      setError(String(e.message || e))
    }
    try {
      const v = await apiGet('/api/metrics/visitors')
      setVisitors(v)
    } catch (e) {
      setVisitors(null)
      setVisitorsError(String(e.message || e))
    }
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])
  useEffect(() => { checkSession().then(setSession).catch(() => {}) }, [])

  const landed = funnel?.stages?.find(s => s.stage === 'landed')?.distinct_visitors ?? 0
  const totalDevices = visitors ? Object.values(visitors.devices || {}).reduce((a, b) => a + b, 0) : 0
  const maxCountry = visitors?.countries?.[0]?.distinct_visitors ?? 0

  return (
    <div style={{ maxWidth: 880, margin: '0 auto', padding: '8px 4px 48px' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12 }}>
        <h1 style={{ margin: '0 0 4px' }}>Insights</h1>
        <button onClick={load} disabled={loading} style={{ fontSize: 13, padding: '6px 12px', borderRadius: 8, cursor: 'pointer' }}>
          {loading ? 'Refreshing…' : '↻ Refresh'}
        </button>
      </div>
      <p style={{ color: 'var(--text-dim, #8b93a7)', marginTop: 0, fontSize: 14 }}>
        Live conversion instruments for our (un-promoted) traffic. Read-only, PII-free.
      </p>

      {/* Public / Internal tabs — Internal is SIWE-gated. */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        <TabButton active={tab === 'public'} onClick={() => setTab('public')}>Public</TabButton>
        <TabButton active={tab === 'internal'} onClick={() => setTab('internal')}>
          Internal {session.authenticated ? '' : '🔒'}
        </TabButton>
      </div>

      {error && (
        <div style={{ ...card, borderColor: '#a3434a', color: '#ff9aa2', marginBottom: 16 }}>
          Couldn’t load metrics: {error}
        </div>
      )}

      {tab === 'internal' ? (
        <InternalDashboard session={session} />
      ) : (
        <>
          {/* ── Traction ── */}
          <section style={{ ...card, marginBottom: 16 }}>
            <h2 style={{ marginTop: 0, fontSize: 16 }}>Traction — requests &amp; users</h2>
            <div style={{ display: 'flex', gap: 28, flexWrap: 'wrap' }}>
              <Stat label="Human-UA requests" value={metrics?.human_count} />
              <Stat label="Agent / bot requests" value={metrics?.agent_count} />
              <Stat label="Total requests" value={metrics?.total_requests} />
              <Stat label="Real users (wallets)" value={metrics?.real_users} accent="#3fb56b" />
            </div>
            <p style={{ color: 'var(--text-dim, #8b93a7)', fontSize: 12.5, marginBottom: 0, marginTop: 14 }}>
              ⚠️ The request counts are <strong>cumulative request counts</strong>, not unique users — and the
              “human” bucket is inflated by browser-UA bots. <strong>Real users</strong> is the honest distinct
              count (wallet rows). The funnel below (distinct visitors, JS-gated so crawlers drop out) is the
              clean visitor signal.
            </p>
          </section>

          {/* ── Conversion funnel ── */}
          <section style={{ ...card, marginBottom: 16 }}>
            <h2 style={{ marginTop: 0, fontSize: 16 }}>Conversion funnel — distinct visitors</h2>
            {!funnel ? (
              <Empty>Loading…</Empty>
            ) : landed === 0 ? (
              <Empty>No visitors recorded yet. The funnel started collecting when it deployed today.</Empty>
            ) : (
              <div style={{ display: 'grid', gap: 14 }}>
                {funnel.stages.map((s, i) => (
                  <div key={s.stage}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, marginBottom: 5 }}>
                      <span>{FUNNEL_LABELS[s.stage] || s.stage}</span>
                      <span style={{ color: 'var(--text-dim, #8b93a7)' }}>
                        <strong style={{ color: 'var(--text, #e6e9f0)' }}>{s.distinct_visitors}</strong>
                        {i > 0 && <> · {(s.step_conversion * 100).toFixed(0)}% of prev</>}
                      </span>
                    </div>
                    <Bar pct={s.pct_of_landed * 100} color={i === 0 ? '#5b9dff' : s.distinct_visitors > 0 ? '#3fb56b' : '#3a3f4b'} />
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* ── Visitor insights (geo + device) ── */}
          <section style={card}>
            <h2 style={{ marginTop: 0, fontSize: 16 }}>Who’s visiting — geography &amp; device</h2>
            <p style={{ color: 'var(--text-dim, #8b93a7)', fontSize: 12.5, marginTop: 0, marginBottom: 14 }}>
              Same JS-gated <strong>landed</strong> population as the funnel (#830) — distinct visitors, so these
              counts reconcile with the funnel’s <em>Landed</em> number. Country is <code>ZZ</code> until the
              CloudFront <code>terraform apply</code> (#795) forwards <code>Viewer-Country</code>.
            </p>
            {loading && !visitors && !visitorsError ? (
              <Empty>Loading visitor insights…</Empty>
            ) : visitorsError ? (
              <Empty>Couldn’t load visitor insights: {visitorsError}</Empty>
            ) : !visitors || ((visitors.countries?.length ?? 0) === 0 && totalDevices === 0) ? (
              <Empty>No visitors recorded yet.</Empty>
            ) : (
              <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) minmax(0,1fr)', gap: 24 }}>
                <div>
                  <h3 style={{ fontSize: 13, color: 'var(--text-dim,#8b93a7)', margin: '0 0 10px' }}>Top countries</h3>
                  <div style={{ display: 'grid', gap: 10 }}>
                    {(visitors.countries || []).slice(0, 8).map(c => (
                      <div key={c.code}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, marginBottom: 4 }}>
                          <span>{COUNTRY_NAMES[c.code] || c.code}</span>
                          <strong>{c.distinct_visitors}</strong>
                        </div>
                        <Bar pct={maxCountry ? (c.distinct_visitors / maxCountry) * 100 : 0} color="#7c6cff" />
                      </div>
                    ))}
                  </div>
                </div>
                <div>
                  <h3 style={{ fontSize: 13, color: 'var(--text-dim,#8b93a7)', margin: '0 0 10px' }}>Device</h3>
                  <div style={{ display: 'grid', gap: 10 }}>
                    {Object.entries(visitors.devices || {})
                      .filter(([, n]) => n > 0)
                      .sort((a, b) => b[1] - a[1])
                      .map(([dev, n]) => (
                        <div key={dev}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, marginBottom: 4 }}>
                            <span>{DEVICE_LABELS[dev] || dev}</span>
                            <strong>{n}</strong>
                          </div>
                          <Bar pct={totalDevices ? (n / totalDevices) * 100 : 0} color="#3fb56b" />
                        </div>
                      ))}
                  </div>
                </div>
              </div>
            )}
          </section>
        </>
      )}
    </div>
  )
}

// ── Internal (SIWE-gated) cost/ops dashboard ────────────────────────────────

function InternalDashboard({ session }) {
  const [cost, setCost] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    if (!session.authenticated) return
    apiGet('/api/metrics/private/cost').then(setCost).catch(e => setErr(String(e.message || e)))
  }, [session.authenticated])

  if (!session.authenticated) {
    return (
      <section style={card}>
        <h2 style={{ marginTop: 0, fontSize: 16 }}>Internal — cost &amp; ops 🔒</h2>
        <Empty>
          Sign in with your wallet to view the internal cost / ops dashboard. Bedrock spend, infra
          cost, and ops health are SIWE-gated — the public tab stays PII- and cost-free by design.
        </Empty>
      </section>
    )
  }

  return (
    <section style={card}>
      <h2 style={{ marginTop: 0, fontSize: 16 }}>Internal — cost &amp; ops</h2>
      {err && <div style={{ color: '#ff9aa2', fontSize: 13, marginBottom: 12 }}>Couldn’t load: {err}</div>}
      <div style={{ display: 'flex', gap: 28, flexWrap: 'wrap' }}>
        <Stat label="Real users (wallets)" value={cost?.real_users} accent="#3fb56b" />
        <Stat label="Bedrock / mo (USD)" value={cost?.bedrock_monthly_usd} />
        <Stat label="Infra / mo (USD)" value={cost?.infra_monthly_usd} />
        <Stat label="Cost / user (USD)" value={cost?.cost_per_user_usd} />
      </div>
      <p style={{ color: 'var(--text-dim, #8b93a7)', fontSize: 12.5, marginBottom: 0, marginTop: 14 }}>
        {cost?.source === 'draft'
          ? 'Draft placeholders — live Bedrock/infra billing wiring is roadmap work. Any $/user or $/gen figure is derived from real users (wallets) or generations, never the request tallies (#830).'
          : 'Per-user / per-generation figures are derived from real users or generations, never the request tallies (#830).'}
      </p>
    </section>
  )
}

function TabButton({ active, onClick, children }) {
  return (
    <button
      onClick={onClick}
      style={{
        fontSize: 13,
        padding: '6px 14px',
        borderRadius: 8,
        cursor: 'pointer',
        border: '1px solid var(--border, #262a34)',
        background: active ? 'var(--surface, #14161c)' : 'transparent',
        color: active ? 'var(--text, #e6e9f0)' : 'var(--text-dim, #8b93a7)',
        fontWeight: active ? 700 : 400,
      }}
    >
      {children}
    </button>
  )
}

function Stat({ label, value, accent }) {
  const display = value == null ? '—' : (typeof value === 'number' ? value.toLocaleString() : value)
  return (
    <div>
      <div style={{ fontSize: 26, fontWeight: 700, color: accent || 'var(--text, #e6e9f0)' }}>{display}</div>
      <div style={{ fontSize: 12.5, color: 'var(--text-dim, #8b93a7)' }}>{label}</div>
    </div>
  )
}

function Empty({ children }) {
  return <p style={{ color: 'var(--text-dim, #8b93a7)', fontSize: 13.5, margin: 0 }}>{children}</p>
}
