import { useEffect, useRef, useState } from 'react'
import { StrategyArchitect } from './Strategies'
import GenerationStream from './GenerationStream'
import GenerationStatus from './GenerationStatus'
import FusionResult from './FusionResult'
import PortfolioAdvisor from './PortfolioAdvisor'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

// /generate spine page. Single input — backend _pick_pipeline() auto-routes
// between Fusion (novel synthesis), Architect (curated-library preview), and
// the streaming agent. The SSE stream emits a `pipeline_selected` event right
// after `brief_validated` so the frontend renders the correct result component.

const STORAGE_JOB_KEY = 'archimedes:currentJobId'
const STORAGE_FUSION_JOB_KEY = 'archimedes:currentFusionJobId'
const RISK_PROFILES = [
  { id: 'fixed_income', label: 'Fixed income' },
  { id: 'conservative', label: 'Conservative' },
  { id: 'moderate', label: 'Moderate' },
  { id: 'aggressive', label: 'Aggressive' },
  { id: 'hyper_risky', label: 'Hyper-risky' },
]
const ASSET_CLASSES = ['equities', 'bonds', 'commodities', 'crypto', 'fx']

// Hand-picked briefs that route cleanly through the auto-router and produce
// a strategy a user can deploy without further coaching. Click fills the
// textarea so the user can edit before submitting.
const EXAMPLE_BRIEFS = [
  'A 13-week treasury alternative with low volatility and crypto upside on Fridays',
  'Trend-following momentum on liquid US equity ETFs, rebalanced monthly, regime-aware',
  'Defensive equity strategy that rotates into bonds when realized volatility spikes',
  'Long-only large-cap value with a quality screen and a max-drawdown circuit breaker',
]

export default function Generate({ onNavigate }) {
  // ── Unified form state ──
  const [intent, setIntent] = useState('')
  const [helpOpen, setHelpOpen] = useState(false)
  const [riskAppetite, setRiskAppetite] = useState('moderate')
  const [selectedAssets, setSelectedAssets] = useState([])
  const [depth, setDepth] = useState(5)       // replaces max_papers UI
  const [starting, setStarting] = useState(false)
  const [startError, setStartError] = useState('')

  // ── Agent / SSE path ──
  const [jobId, setJobId] = useState(() => localStorage.getItem(STORAGE_JOB_KEY) || null)

  // ── Architect path (pre-fetched library) ──
  const [strategies, setStrategies] = useState([])
  const [libLoading, setLibLoading] = useState(true)
  const [libError, setLibError] = useState('')

  // ── Fusion path (GET-poll, no SSE) — fusionJobId is set by the agent
  //    pipeline when it routes to fusion internally; no direct user entry
  //    point anymore (T2.2 consolidated the mode picker). ──
  const [fusionJobId, setFusionJobId] = useState(() => localStorage.getItem(STORAGE_FUSION_JOB_KEY) || null)
  const [fusionStatus, setFusionStatus] = useState(null)
  const [fusionResult, setFusionResult] = useState(null)
  const [fusionError, setFusionError] = useState('')
  const fusionPollRef = useRef(null)

  // ── Pipeline resolved from SSE event (listened via GenerationStream) ──
  const [pipelineFromEvent, setPipelineFromEvent] = useState(null)

  // ── Pre-fetch library for architect path ──
  useEffect(() => {
    let cancelled = false
    fetch(`${API_BASE}/api/strategies/`)
      .then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t) }))
      .then(data => { if (!cancelled) setStrategies(data.strategies || []) })
      .catch(e => { if (!cancelled) setLibError(e.message || 'Failed to load library') })
      .finally(() => { if (!cancelled) setLibLoading(false) })
    return () => { cancelled = true }
  }, [])

  // ── Start unified generation job ──
  const startJob = async () => {
    setStartError('')
    setPipelineFromEvent(null)
    if (!intent.trim()) {
      setStartError('Describe what you want in a sentence or two.')
      return
    }
    setStarting(true)
    try {
      const res = await fetch(`${API_BASE}/api/generate/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          brief: {
            intent,
            risk_appetite: riskAppetite,
            asset_classes: selectedAssets.length > 0 ? selectedAssets : undefined,
            max_papers: depth,
          },
          // Mode is intentionally omitted — the backend's _pick_pipeline()
          // auto-routes between Fusion / Architect / Agent based on corpus
          // state and LLM availability. The selected route is surfaced in
          // the SSE stream's "Pipeline selected" event so the user can see
          // it transparently without us forcing a tab choice.
        }),
      })
      if (!res.ok) throw new Error(await res.text())
      const data = await res.json()
      localStorage.setItem(STORAGE_JOB_KEY, data.job_id)
      setJobId(data.job_id)
    } catch (e) {
      setStartError(e.message || 'Failed to start generation')
    } finally {
      setStarting(false)
    }
  }


  const onJobDone = (_result) => {
    // Don't auto-navigate — let user see both bull/bear candidates first.
    // They can click "View in Library" on either card.
    // GenerationStream stays mounted showing the dual-regime result cards.
  }

  const resetJob = () => {
    localStorage.removeItem(STORAGE_JOB_KEY)
    setJobId(null)
    setPipelineFromEvent(null)
  }

  // ── Fusion poll ──
  const stopFusionPoll = () => {
    if (fusionPollRef.current) {
      clearInterval(fusionPollRef.current)
      fusionPollRef.current = null
    }
  }

  const pollFusion = async (id) => {
    try {
      const res = await fetch(`${API_BASE}/api/strategies/generate/${encodeURIComponent(id)}`)
      if (!res.ok) {
        if (res.status === 404) {
          setFusionError('Fusion job not found — backend may have restarted.')
          setFusionStatus('failed')
          stopFusionPoll()
          localStorage.removeItem(STORAGE_FUSION_JOB_KEY)
        }
        return
      }
      const data = await res.json()
      setFusionStatus(data.status)
      if (data.status === 'done') {
        setFusionResult(data.result || null)
        stopFusionPoll()
        localStorage.removeItem(STORAGE_FUSION_JOB_KEY)
      } else if (data.status === 'failed') {
        setFusionError(data.error || 'Fusion job failed.')
        stopFusionPoll()
        localStorage.removeItem(STORAGE_FUSION_JOB_KEY)
      }
    } catch (_e) {
      // Network blip — keep polling silently
    }
  }

  useEffect(() => {
    if (!fusionJobId) {
      stopFusionPoll()
      return
    }
    pollFusion(fusionJobId)
    fusionPollRef.current = setInterval(() => pollFusion(fusionJobId), 2000)
    return stopFusionPoll
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fusionJobId])

  const resetFusion = () => {
    stopFusionPoll()
    localStorage.removeItem(STORAGE_FUSION_JOB_KEY)
    setFusionJobId(null)
    setFusionStatus(null)
    setFusionResult(null)
    setFusionError('')
  }

  const toggleAsset = (a) => {
    setSelectedAssets(prev => prev.includes(a) ? prev.filter(x => x !== a) : [...prev, a])
  }

  // ── Callback: GenerationStream tells us which pipeline was selected ──
  const handlePipelineEvent = (pipelineName) => {
    setPipelineFromEvent(pipelineName)
    // If pipeline is fusion, start the fusion poll path
    if (pipelineName === 'fusion' && jobId) {
      // The agent pipeline handles fusion internally for the unified endpoint;
      // we just track which result component to render.
    }
  }

  // ── Which result to render — handled inline below by jobId / fusionJobId /
  //    pipelineFromEvent gating. Earlier scaffolding kept an
  //    `isSSEStreamActive` boolean here that no JSX read; deleted with the
  //    rest of the fusion-job orphans T2.2 left behind.

  return (
    <div>
      <div className="max-w-[720px] mb-5">
        <h2 className="serif text-[2rem] mb-2.5">Generate a Strategy</h2>
        <p className="body mb-2">
          Describe what you want in plain English. A multi-agent pipeline retrieves
          relevant q-fin papers, fuses them with live market context, sizes positions
          with Kelly + risk parity, and anchors every decision on Arc.
        </p>
      </div>

      {/* ── COLLAPSIBLE: How this works + Tips + Example briefs ──
          Closed by default so the Generate box keeps the focus. Open it to
          read the architecture, get prompt-writing tips, and click an example
          to auto-fill the textarea. */}
      {!jobId && !fusionJobId && !fusionResult && (
        <div className="card mb-4" style={{ padding: 0, overflow: 'hidden' }}>
          <button
            type="button"
            onClick={() => setHelpOpen(o => !o)}
            aria-expanded={helpOpen}
            style={{
              display: 'flex',
              width: '100%',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 12,
              padding: '12px 18px',
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
              textAlign: 'left',
              color: 'inherit',
            }}
          >
            <div>
              <div className="label" style={{ marginBottom: 2 }}>How this works · tips · examples</div>
              <div className="caption" style={{ color: 'var(--text-3)' }}>
                Three agents + a 5-layer memory turn your brief into a deployable vault.
                Click for the architecture and example briefs.
              </div>
            </div>
            <span style={{ fontSize: '1rem', color: 'var(--text-3)', flexShrink: 0 }}>
              {helpOpen ? '▾' : '▸'}
            </span>
          </button>

          {helpOpen && (
            <div style={{ padding: '6px 18px 18px', borderTop: '1px solid var(--glass-border)' }}>
              <div className="label mb-2 mt-3">Architecture</div>
              <p className="body mb-3">
                <strong>Strategy Generation Agent</strong> retrieves relevant papers from a
                9,873-paper q-fin corpus (SPECTER2 embeddings + clusters), reads current market
                context, and synthesizes a candidate strategy. <strong>Portfolio Construction
                Agent</strong> picks assets, sizes them with Kelly + risk parity, and stress-tests
                across six scenarios. After you sign to deploy, the <strong>Live Execution
                Agent</strong> runs the rebalance loop on-chain — every decision anchored on Arc
                via <code>ReasoningTraceRegistry</code>.
              </p>
              <p className="caption mb-3" style={{ color: 'var(--text-3)' }}>
                We don't make you pick a mode. The backend routes between <strong>Fusion</strong> (novel
                paper synthesis), <strong>Architect</strong> (curated library), or <strong>Agent</strong>
                (LLM portfolio) based on corpus state and your brief — and tells you which it picked in
                the live stream.
              </p>
              <p className="caption mb-3">
                <a
                  onClick={() => onNavigate?.('architecture')}
                  style={{ color: 'var(--accent)', cursor: 'pointer', textDecoration: 'underline' }}
                >
                  Read the full architecture →
                </a>
              </p>

              <div className="label mb-2">Tips for writing your brief</div>
              <ul className="body mb-4" style={{ paddingLeft: '1.2rem', margin: 0 }}>
                <li>Be concrete about asset class, time horizon, and risk tolerance.</li>
                <li>Describe the behavior you want — "low drawdown", "trend-following", "defensive in vol spikes".</li>
                <li>Reference recognizable patterns if it helps — "Kelly-sized momentum", "60/40 with regime overlay".</li>
              </ul>

              <div className="label mb-2">Try an example (click to fill the box)</div>
              <div className="flex flex-col gap-2">
                {EXAMPLE_BRIEFS.map((p) => (
                  <button
                    key={p}
                    type="button"
                    onClick={() => { setIntent(p); setHelpOpen(false) }}
                    className="text-left"
                    style={{
                      padding: '8px 12px',
                      background: 'var(--bg-2)',
                      border: '1px solid var(--glass-border)',
                      borderRadius: 6,
                      cursor: 'pointer',
                      fontSize: '0.88rem',
                      color: 'var(--text-1)',
                      lineHeight: 1.4,
                    }}
                  >
                    <span style={{ color: 'var(--accent)', marginRight: 8 }}>→</span>
                    {p}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── SINGLE UNIFIED FORM ── */}
      {!jobId && !fusionJobId && !fusionResult && (
        <div className="card p-5 mb-4">
          <div className="label mb-2">What would you like?</div>
          <textarea
            value={intent}
            onChange={e => setIntent(e.target.value)}
            placeholder="e.g. A 13-week treasury alternative with low volatility and crypto upside on Fridays"
            rows={4}
            className="chat-input w-full mb-3 p-2.5 leading-relaxed"
            disabled={starting}
          />

          <div className="label mb-2">Asset classes (optional)</div>
          <div className="flex gap-2 flex-wrap mb-4">
            {ASSET_CLASSES.map(a => (
              <span
                key={a}
                className={`tag ${selectedAssets.includes(a) ? 'tag-accent' : 'tag-muted'} cursor-pointer capitalize`}
                onClick={() => toggleAsset(a)}
              >
                {a}
              </span>
            ))}
          </div>

          <div className="flex gap-3 items-center flex-wrap">
            <label className="caption flex items-center gap-1.5">
              Risk
              <select
                value={riskAppetite}
                onChange={e => setRiskAppetite(e.target.value)}
                className="chat-input w-auto px-2 py-1"
                disabled={starting}
              >
                {RISK_PROFILES.map(r => (
                  <option key={r.id} value={r.id}>{r.label}</option>
                ))}
              </select>
            </label>
            <label className="caption flex items-center gap-1.5">
              Depth
              <select
                value={depth}
                onChange={e => setDepth(Number(e.target.value))}
                className="chat-input w-auto px-2 py-1"
                disabled={starting}
                title="How many papers / strategies the engine considers"
              >
                {[2, 3, 4, 5, 6, 8, 10].map(n => <option key={n} value={n}>{n}</option>)}
              </select>
            </label>
            <button
              className="btn btn-primary ml-auto"
              onClick={startJob}
              disabled={starting || !intent.trim()}
            >
              {starting ? 'Starting…' : 'Generate →'}
            </button>
          </div>

          {startError && (
            <div className="info-box warning mt-3">{startError}</div>
          )}
        </div>
      )}

      {/* ── SSE STREAM (agent + architect paths) ── */}
      {jobId && (
        <GenerationStream
          jobId={jobId}
          onDone={onJobDone}
          onReset={resetJob}
          onPipelineSelected={handlePipelineEvent}
          onNavigate={onNavigate}
        />
      )}

      {/* ── Result components based on pipeline_selected ── */}
      {pipelineFromEvent === 'architect' && !jobId && (
        <div>
          <div className="info-box mb-4">
            <strong>Architect path</strong> — selected from the curated library based on your brief.
          </div>
          {libLoading && <div className="caption">Loading strategy library…</div>}
          {libError && (
            <div className="info-box warning mb-4">
              Couldn't load library: {libError}
            </div>
          )}
          {!libLoading && !libError && <StrategyArchitect strategies={strategies} />}
          {/* Preview-before-deploy: same advisor on the architect path so
              users get the rigor-gate + Kelly sizing + stress + correlation
              view before any commit. Issue #210. */}
          {!libLoading && !libError && (
            <div className="mt-6">
              <PortfolioAdvisor initialRiskProfile={riskAppetite} />
            </div>
          )}
        </div>
      )}

      {pipelineFromEvent === 'fusion' && fusionResult && (
        <>
          <FusionResult result={fusionResult} onNavigate={onNavigate} />
          {/* Preview-before-deploy: show the user what the Portfolio
              Construction Agent would allocate for the same brief BEFORE
              they commit funds. Same risk profile they just selected;
              they can flip profiles in the advisor's own selector if
              they want to compare. Issue #210. */}
          <div className="mt-6">
            <PortfolioAdvisor initialRiskProfile={riskAppetite} />
          </div>
          <div className="mt-4">
            <button className="btn btn-outline btn-sm" onClick={resetFusion}>
              Fuse another →
            </button>
          </div>
        </>
      )}

      {/* ── Fusion polling overlay (when pipeline=fusion uses separate endpoint) ── */}
      {fusionJobId && !fusionResult && (
        <div className="card p-5 mb-4">
          <div className="label mb-2">
            Status: <span className="capitalize">{fusionStatus || 'queued'}</span>
          </div>
          <div className="caption mb-3">
            Fusion engine running — synthesizing papers, backtesting, computing rigor.
            Typically 30–90 seconds.
          </div>
          <div className="flex gap-2">
            <div
              className="h-1 flex-1 rounded"
              style={{
                background: 'var(--bg-2)',
                overflow: 'hidden',
                position: 'relative',
              }}
            >
              <div
                style={{
                  position: 'absolute',
                  inset: 0,
                  width: '30%',
                  background: 'var(--accent)',
                  animation: 'fusion-progress 1.6s ease-in-out infinite',
                }}
              />
            </div>
            <button className="btn btn-outline btn-sm" onClick={resetFusion}>Cancel</button>
          </div>
          {fusionError && <div className="info-box warning mt-3">{fusionError}</div>}
        </div>
      )}

      {/* ── Recent jobs sidebar ── */}
      <div className="mt-6">
        <GenerationStatus
          activeJobId={jobId}
          onSelect={(id) => { localStorage.setItem(STORAGE_JOB_KEY, id); setJobId(id) }}
        />
      </div>
    </div>
  )
}
