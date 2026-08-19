import { useState, useEffect, useCallback } from 'react'
import { apiGet } from '../api'

// Insights — the public conversion + traction dashboard (#787, #830, #854).
//
// Renders the live conversion instruments as a human-readable dashboard instead
// of raw JSON. Public-only, PII-free by design — the cost/ops dashboard is a
// SEPARATE, account + admin-linked-wallet-gated surface
// (backend/archimedes/api/metrics_private_routes.py, GET
// /api/metrics/private/cost) and is intentionally NOT rendered anywhere in this
// component:
//   - Traction: human vs agent REQUEST counts (honestly labelled — these are
//     cumulative request tallies, bot-inflated, NOT unique users) + the honest
//     canonical Better Auth account count alongside them. "Real users (accounts)"
//     is an all-time count; it is a DIFFERENT population from
//     the funnel's "Connected Wallet" below (distinct anonymous visitor
//     SESSIONS that reached that step) — the two numbers are not expected to
//     match, and the copy says so explicitly rather than leaving readers to
//     guess.
//   - Conversion funnel: distinct visitors through landed → wallet_connected →
//     generation_started → vault_deployed, with step-conversion %.
//   - Visitor insights: distinct visitors by country + device, drawn from the
//     SAME JS-gated `landed` beacon population as the funnel, attributed once
//     per visitor (issue #854 finding #6: a Redis SADD first-seen gate in
//     services/visitor_insights_store.py). This is INTENDED to sum to the
//     funnel's `landed` count going forward, but the underlying HyperLogLog
//     counters are append-only — visits recorded before the attribution gate
//     shipped (2026-07-03) remain baked into the all-time totals and can't be
//     retroactively de-duplicated without an operator resetting the Redis
//     keys. The UI states this as a directional relationship, not an exact
//     equality, until that reset happens. Geography is `ZZ` until Dan's
//     CloudFront terraform apply lands (#795).

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
  background: 'var(--surface-1)',
  border: '1px solid var(--glass-border)',
  borderRadius: 12,
  padding: '20px 22px',
}

function Bar({ pct, color = '#5b9dff' }) {
  return (
    <div style={{ background: 'var(--glass-hover)', borderRadius: 6, height: 10, overflow: 'hidden' }}>
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

  // null (not 0) when the funnel hasn't loaded / failed — so "Distinct visitors"
  // renders an honest "—" (unknown) rather than a misleading "0".
  const landed = funnel ? (funnel.stages?.find(s => s.stage === 'landed')?.distinct_visitors ?? 0) : null
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
      <p style={{ color: 'var(--text-2)', marginTop: 0, fontSize: 14 }}>
        Live conversion instruments for our (un-promoted) traffic. Read-only, PII-free.
      </p>

      {error && (
        <div style={{ ...card, borderColor: 'var(--negative-bd)', color: 'var(--negative)', marginBottom: 16 }}>
          Couldn’t load metrics: {error}
        </div>
      )}

      {/* ── Real people (the honest headline) — distinct people first; raw request
          volume is demoted to a footnote because it's bot-inflated server hits, not people. */}
      <section style={{ ...card, marginBottom: 16 }}>
        <h2 style={{ marginTop: 0, fontSize: 16 }}>Real people</h2>
        <div style={{ display: 'flex', gap: 28, flexWrap: 'wrap' }}>
          <Stat label="Distinct visitors" value={landed} accent="#5b9dff" />
          <Stat label="Real users (accounts)" value={metrics?.real_users} accent="#3fb56b" />
        </div>
        <p style={{ color: 'var(--text-2)', fontSize: 12.5, marginBottom: 0, marginTop: 14 }}>
          The honest “how many people” numbers. <strong>Distinct visitors</strong> = unique people who loaded
          the app (JS-gated, so crawlers and bots drop out — this is the funnel’s <em>Landed</em> count below).
          <strong> Real users</strong> = canonical Better Auth accounts, all-time. This differs from
          “Connected Wallet”, which counts visitor sessions reaching optional wallet-link step.
        </p>
        <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--glass-border)', fontSize: 12, color: 'var(--text-3)' }}>
          <strong style={{ color: 'var(--text-2)' }}>Raw request volume</strong> — server hits, <em>not</em> people
          (cumulative all-time, heavily bot-inflated; kept only as an infra signal):{' '}
          {metrics?.total_requests?.toLocaleString() ?? '—'} total ·{' '}
          {metrics?.human_count?.toLocaleString() ?? '—'} browser-UA ·{' '}
          {metrics?.agent_count?.toLocaleString() ?? '—'} agent/bot.
        </div>
      </section>

      {/* ── Conversion funnel ── */}
      <section style={{ ...card, marginBottom: 16 }}>
        <h2 style={{ marginTop: 0, fontSize: 16 }}>Conversion funnel — distinct visitors</h2>
        {loading && !funnel ? (
          <Empty>Loading…</Empty>
        ) : !funnel ? (
          <Empty>Couldn’t load the funnel{error ? `: ${error}` : '.'}</Empty>
        ) : landed === 0 ? (
          <Empty>No visitors recorded yet. The funnel started collecting when it deployed today.</Empty>
        ) : (
          <div style={{ display: 'grid', gap: 14 }}>
            {funnel.stages.map((s, i) => (
              <div key={s.stage}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, marginBottom: 5 }}>
                  <span>{FUNNEL_LABELS[s.stage] || s.stage}</span>
                  <span style={{ color: 'var(--text-2)' }}>
                    <strong style={{ color: 'var(--text-1)' }}>{s.distinct_visitors}</strong>
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
        <p style={{ color: 'var(--text-2)', fontSize: 12.5, marginTop: 0, marginBottom: 14 }}>
          Same JS-gated <strong>landed</strong> population as the funnel, attributed once per visitor so a
          repeat visit doesn’t get double-counted. These sums are designed to track the funnel’s{' '}
          <em>Landed</em> number above, but may not match it exactly — both are HyperLogLog estimates, and
          visits recorded before once-per-visitor attribution shipped can still show up in the all-time
          totals. Treat this as directional, not a precise reconciliation. Country shows{' '}
          <code>ZZ</code> (unknown / not provided) until the visitor's country is available.
        </p>
        {loading && !visitors && !visitorsError ? (
          <Empty>Loading visitor insights…</Empty>
        ) : visitorsError ? (
          <Empty>Couldn’t load visitor insights: {visitorsError}</Empty>
        ) : !visitors || ((visitors.countries?.length ?? 0) === 0 && totalDevices === 0) ? (
          <Empty>No visitors recorded yet.</Empty>
        ) : (
          <div className="insights-two-col">
            <div>
              <h3 style={{ fontSize: 13, color: 'var(--text-2)', margin: '0 0 10px' }}>Top countries</h3>
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
              <h3 style={{ fontSize: 13, color: 'var(--text-2)', margin: '0 0 10px' }}>Device</h3>
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
    </div>
  )
}

function Stat({ label, value, accent }) {
  const display = value == null ? '—' : (typeof value === 'number' ? value.toLocaleString() : value)
  return (
    <div>
      <div style={{ fontSize: 26, fontWeight: 700, color: accent || 'var(--text-1)' }}>{display}</div>
      <div style={{ fontSize: 12.5, color: 'var(--text-2)' }}>{label}</div>
    </div>
  )
}

function Empty({ children }) {
  return <p style={{ color: 'var(--text-2)', fontSize: 13.5, margin: 0 }}>{children}</p>
}
