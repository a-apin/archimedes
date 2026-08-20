import { useEffect, useState, useCallback, useRef, useMemo } from 'react'
import { fetchHealth } from '../health'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

// Entity-type colors
const TYPE_COLORS = {
  paper: '#6366f1',
  author: '#10b981',
  category: '#f59e0b',
  topic: '#06b6d4',
  method: '#ec4899',
}

const TYPE_ICONS = {
  paper: 'i-lucide-file-text',
  author: 'i-lucide-user',
  category: 'i-lucide-tag',
  topic: 'i-lucide-lightbulb',
  method: 'i-lucide-settings',
}

// Matches the backend's `Query(..., min_length=2)` on /api/corpus/kg/entities —
// checked client-side so a too-short search shows validation feedback instead
// of firing a request that 422s.
const MIN_QUERY_LENGTH = 2

/**
 * Topic Clusters viewer. (Currently renders BERTopic-derived topic clusters
 * across the KB-processed paper subset. Promoted to a real Knowledge Graph
 * once #1090 produces the KB pipeline's first artifact AND #1092 backfills
 * kg_entities/kg_relations from it — /health's corpus_kg_built reflects the
 * latter (entities actually present), not artifact presence alone; see
 * corpus_artifact_present for that separate fact. The prior pointer here,
 * #293, closed 2026-05-25 with kg_entities/kg_relations still at 0/0 in
 * prod — see #1368.)
 *
 * Fetches from ``/api/corpus/kg/entities?q=<q>`` and renders entities
 * + relations as an SVG graph. Entity search filters the KG. Falls back
 * gracefully on 503 or empty data.
 */
export default function CorpusKG({ onOpenPaper }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [validationError, setValidationError] = useState('')
  const [hoverEntity, setHoverEntity] = useState(null)
  const svgRef = useRef(null)

  // The zero-state copy for `entities.length === 0` must not assert "pipeline
  // hasn't run" for a search that simply matched nothing once the pipeline
  // HAS run (#1368) — /health's corpus_kg_built is the live authority for
  // which of those two states we're in, so it's fetched here rather than
  // asserted. Tri-state (loading / error / value) — a failed fetch must
  // render an honest "can't tell right now", never a guessed corpus_kg_built
  // value. Fetched via the shared fetchHealth() TTL cache (../health.js),
  // deliberately NOT a raw direct call to the /health endpoint — Layout.jsx
  // already fetches /health on every in-app navigation (#1333), so an
  // uncached second read here would fire a second Arc RPC round-trip + DB
  // reads in the same render pass. See ui/test/chain-status.test.js for the
  // guard against reintroducing the direct call.
  const [health, setHealth] = useState(null)
  const [healthError, setHealthError] = useState(false)
  useEffect(() => {
    fetchHealth()
      .then(setHealth)
      .catch(() => setHealthError(true))
  }, [])
  const healthLoading = !health && !healthError

  // The exact term actually sent to the backend (defaults to 'topic' — see
  // fetchKG below) — distinct from `query`, the live input-box value, so the
  // zero-state can name what was searched even for the default on-mount load
  // where the user never typed anything.
  const [searchedTerm, setSearchedTerm] = useState('')

  // Zoom/pan state — scale + translation applied to a wrapping <g> so
  // node/edge/label rendering below is untouched. Wheel zooms around the
  // cursor position; drag pans. Kept intentionally simple (no d3-zoom dep)
  // since this is a plain SVG, not a canvas force-graph.
  const [viewTransform, setViewTransform] = useState({ scale: 1, x: 0, y: 0 })
  const dragState = useRef(null)

  const fetchKG = useCallback(async (q) => {
    setLoading(true)
    setError('')
    try {
      // Trim before the default-fallback check: whitespace-only input must
      // fall back to 'topic' (not become the literal query and 422), and
      // stray leading/trailing spaces shouldn't reach the backend (review).
      const searchTerm = (q || '').trim() || 'topic'
      setSearchedTerm(searchTerm)
      const res = await fetch(`${API_BASE}/api/corpus/kg/entities?q=${encodeURIComponent(searchTerm)}`)
      if (res.status === 503) throw new Error('KB pipeline still running — first artifact pending')
      if (res.status === 422) throw new Error(`422: search term must be at least ${MIN_QUERY_LENGTH} characters`)
      if (!res.ok) throw new Error(res.statusText)
      const raw = await res.json()
      // Normalize backend field names to frontend conventions (Issue #345)
      if (raw.entities) {
        raw.entities = raw.entities.map(e => ({ ...e, type: e.entity_type || e.type }))
      }
      if (raw.relations) {
        raw.relations = raw.relations.map(r => ({
          ...r,
          source: r.subject_id ?? r.source,
          target: r.object_id ?? r.target,
          predicate: r.relation ?? r.predicate,
        }))
      }
      setData(raw)
      setViewTransform({ scale: 1, x: 0, y: 0 })
    } catch (e) {
      setError(e.message || 'Failed to load topic clusters')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchKG('') }, [fetchKG])

  const handleSearch = (e) => {
    e.preventDefault()
    const trimmed = query.trim()
    if (trimmed.length > 0 && trimmed.length < MIN_QUERY_LENGTH) {
      setValidationError(`Enter at least ${MIN_QUERY_LENGTH} characters`)
      return
    }
    setValidationError('')
    // Pass the TRIMMED value — validation ran on `trimmed`, so the request
    // must use the same string or validation and request can disagree (review).
    fetchKG(trimmed)
  }

  // Layout: arrange entities in a radial layout by type
  const layout = useMemo(() => {
    if (!data?.entities) return { positions: {}, svgW: 800, svgH: 500 }
    const entities = data.entities
    const types = [...new Set(entities.map(e => e.type))]
    const typeAngles = {}
    types.forEach((t, i) => { typeAngles[t] = (2 * Math.PI * i) / types.length - Math.PI / 2 })

    const cx = 400, cy = 250
    const typeRadius = 180
    const positions = {}

    entities.forEach((e, i) => {
      const baseAngle = typeAngles[e.type] || 0
      // Spread entities of same type in a small arc
      const sameType = entities.filter(x => x.type === e.type)
      const idx = sameType.indexOf(e)
      const spread = sameType.length > 1 ? (idx / (sameType.length - 1) - 0.5) * 0.8 : 0
      const angle = baseAngle + spread
      const jitter = (i * 17 % 30) - 15  // deterministic scatter
      positions[e.id] = {
        x: cx + (typeRadius + jitter) * Math.cos(angle),
        y: cy + (typeRadius + jitter) * Math.sin(angle),
      }
    })

    return { positions, svgW: 800, svgH: 500 }
  }, [data])

  const clampScale = (s) => Math.min(8, Math.max(0.4, s))

  // Map a pointer/wheel event to viewBox coordinates via the SVG's actual
  // screen CTM. Proportional getBoundingClientRect math breaks whenever the
  // rendered element's aspect differs from the 800×500 viewBox (width:100% +
  // fixed height ⇒ preserveAspectRatio letterboxing on most viewports), which
  // made the zoom anchor drift away from the cursor and pan deltas run fast/
  // slow (#431 review). The CTM inverse maps through the letterboxing exactly.
  const clientToViewBox = useCallback((e) => {
    const svg = svgRef.current
    if (!svg) return null
    const ctm = svg.getScreenCTM?.()
    if (!ctm || typeof DOMPoint === 'undefined') return null
    return new DOMPoint(e.clientX, e.clientY).matrixTransform(ctm.inverse())
  }, [])

  const handleWheel = useCallback((e) => {
    // Only effective because the listener is attached natively with
    // passive:false (see setSvgRef) — React 19 registers the JSX onWheel
    // prop as a PASSIVE root listener, where preventDefault() is a silent
    // no-op and the page scrolls while the graph zooms (#431 review).
    e.preventDefault()
    const pt = clientToViewBox(e)
    if (!pt) return
    // Cursor position in viewBox units (before this zoom step).
    const px = pt.x
    const py = pt.y

    setViewTransform((prev) => {
      const factor = Math.exp(-e.deltaY * 0.0015)
      const nextScale = clampScale(prev.scale * factor)
      // Keep the point under the cursor fixed while scaling.
      const nx = px - ((px - prev.x) / prev.scale) * nextScale
      const ny = py - ((py - prev.y) / prev.scale) * nextScale
      return { scale: nextScale, x: nx, y: ny }
    })
  }, [clientToViewBox])

  // Native wheel attachment with passive:false. Two constraints force this
  // shape: (1) React 19 makes JSX onWheel passive, so preventDefault can't
  // work through the prop; (2) the <svg> mounts conditionally after data
  // loads, so a mount-only useEffect would attach to null — a callback ref
  // attaches/detaches with the element itself (same reasoning as
  // CorpusGraph's ResizeObserver callback ref).
  const wheelHandlerRef = useRef(handleWheel)
  useEffect(() => {
    wheelHandlerRef.current = handleWheel
  }, [handleWheel])
  const wheelCleanupRef = useRef(null)
  const setSvgRef = useCallback((el) => {
    if (wheelCleanupRef.current) {
      wheelCleanupRef.current()
      wheelCleanupRef.current = null
    }
    svgRef.current = el
    if (el) {
      const listener = (e) => wheelHandlerRef.current(e)
      el.addEventListener('wheel', listener, { passive: false })
      wheelCleanupRef.current = () => el.removeEventListener('wheel', listener)
    }
  }, [])

  const handlePointerDown = useCallback((e) => {
    const startPt = clientToViewBox(e)
    if (!startPt) return
    // Capture the pointer so the drag keeps tracking when the cursor leaves
    // the <svg> mid-pan (without capture, pointermove stops at the edge and
    // the pan "sticks" — #431 review).
    e.currentTarget.setPointerCapture?.(e.pointerId)
    dragState.current = { startX: startPt.x, startY: startPt.y, origin: viewTransform }
  }, [viewTransform, clientToViewBox])

  const handlePointerMove = useCallback((e) => {
    if (!dragState.current) return
    const pt = clientToViewBox(e)
    if (!pt) return
    // Deltas in viewBox units via the same CTM mapping as the zoom anchor —
    // no letterboxing drift on narrow viewports.
    const dx = pt.x - dragState.current.startX
    const dy = pt.y - dragState.current.startY
    const { origin } = dragState.current
    setViewTransform({ scale: origin.scale, x: origin.x + dx, y: origin.y + dy })
  }, [clientToViewBox])

  const handlePointerUp = useCallback((e) => {
    if (e?.currentTarget?.hasPointerCapture?.(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId)
    }
    dragState.current = null
  }, [])

  const resetView = useCallback(() => setViewTransform({ scale: 1, x: 0, y: 0 }), [])

  // Single-pointer alternatives to the drag-pan and wheel-zoom (2.5.7 Dragging
  // Movements). "Reset view" was the only non-drag control and it only ever
  // returns to the origin, so a head-pointer / eye-gaze / switch user — or
  // anyone who cannot hold a button while moving — saw only whatever fell
  // inside the default viewport. Drag and wheel are untouched; they simply
  // stop being the only route.
  const zoomBy = useCallback((factor) => {
    setViewTransform((prev) => {
      const nextScale = Math.min(8, Math.max(0.4, prev.scale * factor))
      // Keep the viewport centre fixed, the same invariant the wheel handler
      // holds for the cursor position.
      const cx = layout.svgW / 2
      const cy = layout.svgH / 2
      return {
        scale: nextScale,
        x: cx - ((cx - prev.x) / prev.scale) * nextScale,
        y: cy - ((cy - prev.y) / prev.scale) * nextScale,
      }
    })
  }, [layout.svgW, layout.svgH])

  const panBy = useCallback((dx, dy) => {
    setViewTransform((prev) => ({ ...prev, x: prev.x + dx, y: prev.y + dy }))
  }, [])

  const PAN_STEP = 80

  // --- Render ---

  if (error) {
    return (
      <div style={{ padding: 24 }}>
        <form onSubmit={handleSearch} className="flex gap-2 mb-4">
          <input
            type="text" placeholder="Search entities (author, topic, method)…"
            value={query} onChange={e => { setQuery(e.target.value); setValidationError('') }}
            className="chat-input flex-1 p-2.5"
          />
          <button type="submit" className="btn btn-primary">Search</button>
        </form>
        {validationError && (
          <div className="caption mb-2" style={{ color: 'var(--text-3)' }}>{validationError}</div>
        )}
        <div className="info-box warning">
          {error.includes('503') || error.includes('KB pipeline')
            ? 'KB pipeline still running — first artifact pending. The topic clusters will populate once the KG is built.'
            : error.startsWith('422')
              ? `Search term too short — enter at least ${MIN_QUERY_LENGTH} characters.`
              : `Knowledge graph unavailable: ${error}`}
        </div>
      </div>
    )
  }

  const entities = data?.entities || []
  const relations = data?.relations || []

  // Build entity lookup for edge rendering
  const entityMap = {}
  entities.forEach(e => { entityMap[e.id] = e })

  // Count connections per entity for sizing
  const connCount = {}
  relations.forEach(r => {
    connCount[r.source] = (connCount[r.source] || 0) + 1
    connCount[r.target] = (connCount[r.target] || 0) + 1
  })
  const maxConn = Math.max(1, ...Object.values(connCount))

  // Limit displayed entities for performance
  const MAX_ENTITIES = 200
  const displayEntities = entities.slice(0, MAX_ENTITIES)
  const displayIds = new Set(displayEntities.map(e => e.id))
  const displayRelations = relations.filter(r => displayIds.has(r.source) && displayIds.has(r.target))

  // Full entity behind the current hover, for the tooltip below — looked up
  // by id rather than trusting the label already baked into the SVG <text>
  // so the tooltip always shows the complete, untruncated title.
  // Explicit null check: entity ids are integer PKs — truthiness would
  // treat a (theoretical) id of 0 as not-hovered (review).
  const hoveredEntityObj = hoverEntity != null ? entityMap[hoverEntity] : null

  return (
    <div className="corpus-kg-wrapper">
      <form onSubmit={handleSearch} className="flex gap-2 mb-4" style={{ padding: '0 12px' }}>
        <input
          type="text" placeholder="Filter by entity (author, topic, category)…"
          value={query} onChange={e => { setQuery(e.target.value); setValidationError('') }}
          className="chat-input flex-1 p-2.5"
        />
        <button type="submit" className="btn btn-primary" disabled={loading}>
          {loading ? 'Searching…' : 'Search'}
        </button>
      </form>

      {validationError && (
        <div className="caption mb-2" style={{ padding: '0 12px', color: 'var(--text-3)' }}>{validationError}</div>
      )}

      {data?.note && (
        <div className="caption" style={{ padding: '4px 12px', color: 'var(--text-4)', fontSize: '0.8rem' }}>
          {data.note}
        </div>
      )}

      <div className="flex gap-2 flex-wrap mb-3" style={{ padding: '0 12px' }}>
        <span className="tag tag-muted">{entities.length} entities</span>
        <span className="tag tag-muted">{relations.length} relations</span>
        {data?.filtered != null && <span className="tag tag-muted">{data.filtered} papers matched</span>}
      </div>

      {loading ? (
        <div style={{ padding: 40, textAlign: 'center' }} className="caption">Loading topic clusters…</div>
      ) : entities.length === 0 ? (
        // The old zero-state copy read as an empty-search-result, but on the
        // live path this endpoint returns 200 with empty sets whether the KB
        // pipeline has produced an artifact or not — indistinguishable from a
        // query simply matching nothing (#1368). /health's corpus_kg_built is
        // the live authority for which of those two states we're in: naming
        // the pipeline unconditionally would itself go stale into a new
        // over-claim the moment #1090 lands and a legitimate empty search
        // hits this same branch, so the copy below branches on the fetched
        // value instead of asserting either state.
        <div style={{ padding: 40, textAlign: 'center' }} className="caption">
          {healthLoading ? (
            'Checking knowledge-graph pipeline status…'
          ) : healthError ? (
            'Live pipeline status unavailable right now — try again shortly.'
          ) : health.corpus_kg_built ? (
            `No entities matched "${searchedTerm}".`
          ) : (
            <>
              No knowledge-graph entities have been extracted yet (#1090 produces the KB
              pipeline artifact; #1092 backfills kg_entities/kg_relations from it) — there's
              nothing to search until that lands. See /health's corpus_kg_built field for the
              live state; paper retrieval on the Catalog tab doesn't depend on it.
            </>
          )}
        </div>
      ) : (
        <div style={{ overflow: 'hidden', padding: '0 12px 12px', position: 'relative' }}>
          {/* Informative chart, so it gets the same role="img" + aria-label
              treatment every other real chart in the repo already carries
              (BacktestVisualizer, RiskAnalysis). Without it this 500px region
              was announced as nothing at all (1.1.1). */}
          <svg
            ref={setSvgRef}
            role="img"
            aria-label={`Topic cluster graph: ${entities.length} entities, ${relations.length} relations`}
            viewBox={`0 0 ${layout.svgW} ${layout.svgH}`}
            style={{
              width: '100%', maxWidth: 800, height: 500, background: 'rgba(0,0,0,0.15)', borderRadius: 8,
              cursor: 'grab', touchAction: 'none',
            }}
            onPointerDown={handlePointerDown}
            onPointerMove={handlePointerMove}
            onPointerUp={handlePointerUp}
            onPointerLeave={handlePointerUp}
          >
            <g transform={`translate(${viewTransform.x},${viewTransform.y}) scale(${viewTransform.scale})`}>
              {/* Edges */}
              {displayRelations.map((r, i) => {
                const s = layout.positions[r.source]
                const t = layout.positions[r.target]
                if (!s || !t) return null
                return (
                  <line
                    key={`edge-${i}`}
                    x1={s.x} y1={s.y} x2={t.x} y2={t.y}
                    stroke="rgba(100,100,140,0.15)"
                    strokeWidth={0.8}
                  />
                )
              })}

              {/* Nodes */}
              {displayEntities.map(e => {
                const pos = layout.positions[e.id]
                if (!pos) return null
                const r = 4 + (connCount[e.id] || 0) / maxConn * 8
                const color = TYPE_COLORS[e.type] || '#6366f1'
                const isHovered = hoverEntity === e.id
                return (
                  <g
                    key={`node-${e.id}`}
                    transform={`translate(${pos.x},${pos.y})`}
                    onMouseEnter={() => setHoverEntity(e.id)}
                    onMouseLeave={() => setHoverEntity(null)}
                    style={{ cursor: e.type === 'paper' ? 'pointer' : 'default' }}
                    onClick={() => e.type === 'paper' && onOpenPaper?.(e.id)}
                  >
                    {/* Native SVG tooltip: full, untruncated label as a browser-rendered
                        title on hover — a fallback alongside the HTML tooltip below. */}
                    <title>{e.label}</title>
                    <circle
                      r={isHovered ? r + 3 : r}
                      fill={color}
                      opacity={isHovered ? 1 : 0.75}
                      stroke={isHovered ? '#fff' : 'none'}
                      strokeWidth={1.5}
                    />
                    {/* Label on hover or for high-degree nodes */}
                    {(isHovered || (connCount[e.id] || 0) > maxConn * 0.3) && (
                      <text
                        x={r + 4}
                        y={4}
                        fontSize={10}
                        fill={isHovered ? '#fff' : 'var(--text-3)'}
                        fontFamily="system-ui"
                      >
                        {e.label?.length > 35 ? `${e.label.slice(0, 35)}…` : e.label}
                      </text>
                    )}
                  </g>
                )
              })}
            </g>

            {/* Legend — stays fixed in screen space, outside the zoom/pan group */}
            <g transform={`translate(12, 12)`}>
              {Object.keys(TYPE_ICONS).map((type, i) => (
                <g key={type} transform={`translate(0, ${i * 18})`}>
                  <circle r={5} fill={TYPE_COLORS[type]} />
                  <text x={10} y={4} fontSize={10} fill="var(--text-3)" fontFamily="system-ui">
                    {type}
                  </text>
                </g>
              ))}
            </g>
          </svg>

          <div
            className="corpus-kg-controls"
            role="group"
            aria-label="Graph view controls"
          >
            <button type="button" className="btn btn-outline" onClick={() => zoomBy(1.25)} aria-label="Zoom in">+</button>
            <button type="button" className="btn btn-outline" onClick={() => zoomBy(1 / 1.25)} aria-label="Zoom out">−</button>
            <button type="button" className="btn btn-outline" onClick={() => panBy(PAN_STEP, 0)} aria-label="Pan left">←</button>
            <button type="button" className="btn btn-outline" onClick={() => panBy(-PAN_STEP, 0)} aria-label="Pan right">→</button>
            <button type="button" className="btn btn-outline" onClick={() => panBy(0, PAN_STEP)} aria-label="Pan up">↑</button>
            <button type="button" className="btn btn-outline" onClick={() => panBy(0, -PAN_STEP)} aria-label="Pan down">↓</button>
            <button
              type="button"
              className="btn btn-outline corpus-kg-reset-view"
              onClick={resetView}
            >
              Reset view
            </button>
          </div>

          {/* Hover tooltip — full, untruncated entity title with strong
              contrast so it stays readable over a busy/filtered graph. */}
          {hoveredEntityObj && (
            <div
              className="corpus-kg-tooltip"
              style={{
                position: 'absolute', bottom: 16, left: 24, zIndex: 5,
                background: 'rgba(10,10,16,0.95)', borderRadius: 8, padding: '10px 14px',
                border: '1px solid var(--glass-border)', maxWidth: 420, pointerEvents: 'none',
              }}
            >
              <div
                className="caption"
                style={{ color: TYPE_COLORS[hoveredEntityObj.type] || 'var(--text-4)', marginBottom: 4, textTransform: 'capitalize' }}
              >
                {hoveredEntityObj.type}
              </div>
              <div className="body" style={{ color: '#fff', lineHeight: 1.4, fontSize: '0.95rem', wordBreak: 'break-word' }}>
                {hoveredEntityObj.label}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Entity list below graph */}
      {entities.length > 0 && (
        <div style={{ padding: '0 12px 12px' }}>
          {(() => {
            const byType = {}
            entities.forEach(e => {
              if (!byType[e.type]) byType[e.type] = []
              byType[e.type].push(e)
            })
            return Object.entries(byType).map(([type, items]) => (
              <div key={type} className="mb-3">
                <div className="label mb-1 flex items-center gap-1.5" style={{ textTransform: 'capitalize' }}>
                  <span className={`${TYPE_ICONS[type] || 'i-lucide-circle'} w-3.5 h-3.5`} />
                  {type}s ({items.length})
                </div>
                <div className="flex gap-1.5 flex-wrap">
                  {items.slice(0, 40).map(e => (
                    <span
                      key={e.id}
                      className="tag"
                      style={{
                        background: `${TYPE_COLORS[e.type] || '#6366f1'}22`,
                        borderColor: `${TYPE_COLORS[e.type] || '#6366f1'}44`,
                        color: TYPE_COLORS[e.type] || '#6366f1',
                        cursor: e.type === 'paper' ? 'pointer' : 'default',
                        maxWidth: 200,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                      title={e.label}
                      onClick={() => e.type === 'paper' && onOpenPaper?.(e.id)}
                    >
                      {e.label?.length > 30 ? `${e.label.slice(0, 30)}…` : e.label}
                    </span>
                  ))}
                  {items.length > 40 && (
                    <span className="tag tag-muted">+{items.length - 40} more</span>
                  )}
                </div>
              </div>
            ))
          })()}
        </div>
      )}
    </div>
  )
}
