import { useEffect, useState, useCallback, useRef, useMemo } from 'react'

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
  paper: '📄',
  author: '👤',
  category: '🏷',
  topic: '💡',
  method: '⚙',
}

/**
 * Knowledge Graph viewer.
 *
 * Fetches from ``/api/papers/corpus/kg?entity=<q>`` and renders entities
 * + relations as an SVG graph. Entity search filters the KG. Falls back
 * gracefully on 503 or empty data.
 */
export default function CorpusKG({ onOpenPaper }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [appliedQuery, setAppliedQuery] = useState('')
  const [hoverEntity, setHoverEntity] = useState(null)
  const svgRef = useRef(null)

  const fetchKG = useCallback(async (q) => {
    setLoading(true)
    setError('')
    try {
      const params = q ? `?entity=${encodeURIComponent(q)}` : ''
      const res = await fetch(`${API_BASE}/api/papers/corpus/kg${params}`)
      if (res.status === 503) throw new Error('KB pipeline still running — first artifact pending')
      if (!res.ok) throw new Error(res.statusText)
      setData(await res.json())
    } catch (e) {
      setError(e.message || 'Failed to load knowledge graph')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchKG('') }, [fetchKG])

  const handleSearch = (e) => {
    e.preventDefault()
    setAppliedQuery(query)
    fetchKG(query)
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

  // --- Render ---

  if (error) {
    return (
      <div style={{ padding: 24 }}>
        <form onSubmit={handleSearch} className="flex gap-2 mb-4">
          <input
            type="text" placeholder="Search entities (author, topic, method)…"
            value={query} onChange={e => setQuery(e.target.value)}
            className="chat-input flex-1 p-2.5"
          />
          <button type="submit" className="btn btn-primary">Search</button>
        </form>
        <div className="info-box warning">
          {error.includes('503') || error.includes('KB pipeline')
            ? 'KB pipeline still running — first artifact pending. The knowledge graph will populate once the KG is built.'
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

  return (
    <div className="corpus-kg-wrapper">
      <form onSubmit={handleSearch} className="flex gap-2 mb-4" style={{ padding: '0 12px' }}>
        <input
          type="text" placeholder="Filter by entity (author, topic, category)…"
          value={query} onChange={e => setQuery(e.target.value)}
          className="chat-input flex-1 p-2.5"
        />
        <button type="submit" className="btn btn-primary" disabled={loading}>
          {loading ? 'Searching…' : 'Search'}
        </button>
      </form>

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
        <div style={{ padding: 40, textAlign: 'center' }} className="caption">Loading knowledge graph…</div>
      ) : entities.length === 0 ? (
        <div style={{ padding: 40, textAlign: 'center' }} className="caption">No entities found.</div>
      ) : (
        <div style={{ overflow: 'auto', padding: '0 12px 12px' }}>
          <svg
            ref={svgRef}
            viewBox={`0 0 ${layout.svgW} ${layout.svgH}`}
            style={{ width: '100%', maxWidth: 800, height: 500, background: 'rgba(0,0,0,0.15)', borderRadius: 8 }}
          >
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

            {/* Legend */}
            <g transform={`translate(12, 12)`}>
              {Object.entries(TYPE_ICONS).map(([type, icon], i) => (
                <g key={type} transform={`translate(0, ${i * 18})`}>
                  <circle r={5} fill={TYPE_COLORS[type]} />
                  <text x={10} y={4} fontSize={10} fill="var(--text-3)" fontFamily="system-ui">
                    {icon} {type}
                  </text>
                </g>
              ))}
            </g>
          </svg>
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
                <div className="label mb-1" style={{ textTransform: 'capitalize' }}>
                  {TYPE_ICONS[type] || ''} {type}s ({items.length})
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
