import { useEffect, useRef, useState, useCallback, useMemo } from 'react'
import ForceGraph2D from 'react-force-graph-2d'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

// Cluster palette — high-contrast, colorblind-friendly-ish
const CLUSTER_PALETTE = [
  '#6366f1', '#06b6d4', '#f59e0b', '#ef4444', '#10b981',
  '#8b5cf6', '#ec4899', '#14b8a6', '#f97316', '#84cc16',
  '#e879f9', '#22d3ee', '#fb923c', '#a3e635', '#f472b6',
]

/**
 * SPECTER2 similarity force-directed graph.
 *
 * Fetches from ``/api/papers/corpus/graph`` and renders an interactive
 * force-directed layout. Nodes are colored by ``cluster_id`` (or category
 * as fallback). Node size is driven by edge count (degree). Hover shows
 * arxiv_id + title tooltip.
 *
 * Falls back gracefully when the endpoint returns empty data or 503.
 */
export default function CorpusGraph() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [hoverNode, setHoverNode] = useState(null)
  const containerRef = useRef(null)
  const fgRef = useRef(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fetch(`${API_BASE}/api/papers/corpus/graph?sample=1000&lod=1`)
      .then(r => {
        if (r.status === 503) throw new Error('KB pipeline still running — first artifact pending')
        if (!r.ok) throw new Error(r.statusText)
        return r.json()
      })
      .then(d => { if (!cancelled) setData(d) })
      .catch(e => { if (!cancelled) setError(e.message || 'Failed to load graph') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  // Build a lookup for cluster → color
  const clusterColorMap = useMemo(() => {
    if (!data?.nodes) return {}
    const clusters = [...new Set(data.nodes.map(n => n.cluster || 'default'))]
    const map = {}
    clusters.forEach((c, i) => { map[c] = CLUSTER_PALETTE[i % CLUSTER_PALETTE.length] })
    return map
  }, [data])

  // Build adjacency for degree calculation
  const degreeMap = useMemo(() => {
    const deg = {}
    if (data?.nodes) data.nodes.forEach(n => { deg[n.id] = 0 })
    if (data?.edges) data.edges.forEach(e => {
      deg[e.source] = (deg[e.source] || 0) + 1
      deg[e.target] = (deg[e.target] || 0) + 1
    })
    return deg
  }, [data])

  // Transform data for react-force-graph-2d
  const graphData = useMemo(() => {
    if (!data?.nodes) return { nodes: [], links: [] }
    const maxDeg = Math.max(1, ...Object.values(degreeMap))
    return {
      nodes: data.nodes.map(n => ({
        id: n.id,
        label: n.title || n.id,
        cluster: n.cluster || 'default',
        val: 1 + (degreeMap[n.id] || 0) / maxDeg * 4,  // node size 1–5
        color: clusterColorMap[n.cluster || 'default'] || CLUSTER_PALETTE[0],
        categories: n.categories || [],
      })),
      links: (data.edges || []).map(e => ({
        source: e.source,
        target: e.target,
        value: e.weight || 1,
      })),
    }
  }, [data, clusterColorMap, degreeMap])

  // Custom node painting
  const nodeCanvasObject = useCallback((node, ctx, globalScale) => {
    const radius = Math.max(2, node.val * 1.5)
    ctx.fillStyle = node.color
    ctx.beginPath()
    ctx.arc(node.x, node.y, radius, 0, 2 * Math.PI)
    ctx.fill()

    // Label only when zoomed in enough
    if (globalScale > 1.5) {
      ctx.fillStyle = '#d4d4d8'
      ctx.font = `${Math.max(8, 10 / globalScale)}px system-ui`
      ctx.textAlign = 'center'
      ctx.fillText(node.label.slice(0, 40), node.x, node.y + radius + 10 / globalScale)
    }
  }, [])

  const nodePointerAreaPaint = useCallback((node, color, ctx) => {
    const radius = Math.max(4, node.val * 2)
    ctx.fillStyle = color
    ctx.beginPath()
    ctx.arc(node.x, node.y, radius, 0, 2 * Math.PI)
    ctx.fill()
  }, [])

  // --- Loading / error / empty states ---

  if (loading) {
    return (
      <div className="corpus-graph-loading" style={{ padding: 40, textAlign: 'center' }}>
        <div className="caption">Loading similarity graph…</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="corpus-graph-error" style={{ padding: 24 }}>
        <div className="info-box warning">
          {error.includes('503') || error.includes('KB pipeline')
            ? 'KB pipeline still running — first artifact pending. The graph will populate once embeddings are computed.'
            : `Graph unavailable: ${error}`}
        </div>
      </div>
    )
  }

  if (!data || data.status === 'empty' || !data.nodes || data.nodes.length === 0) {
    return (
      <div className="corpus-graph-empty" style={{ padding: 40, textAlign: 'center' }}>
        <div className="caption">No papers in corpus yet.</div>
      </div>
    )
  }

  // Legend
  const legendClusters = Object.entries(clusterColorMap).slice(0, 12)

  return (
    <div ref={containerRef} className="corpus-graph-wrapper" style={{ position: 'relative' }}>
      {data.note && (
        <div className="corpus-note caption" style={{ padding: '8px 12px', color: 'var(--text-4)', fontSize: '0.8rem' }}>
          {data.note}
        </div>
      )}

      <div className="corpus-graph-stats flex gap-2 flex-wrap mb-2" style={{ padding: '0 12px' }}>
        <span className="tag tag-muted">{data.sampled || data.nodes.length} nodes</span>
        <span className="tag tag-muted">{data.edges?.length || 0} edges</span>
        <span className="tag tag-muted">{data.total_papers?.toLocaleString()} total papers</span>
      </div>

      {/* Force graph */}
      <ForceGraph2D
        ref={fgRef}
        graphData={graphData}
        nodeCanvasObject={nodeCanvasObject}
        nodePointerAreaPaint={nodePointerAreaPaint}
        onNodeHover={node => setHoverNode(node)}
        linkColor={() => 'rgba(100,100,140,0.08)'}
        linkWidth={0.5}
        backgroundColor="transparent"
        nodeRelSize={1}
        warmupTicks={50}
        cooldownTicks={100}
        width={containerRef.current?.offsetWidth || 800}
        height={500}
      />

      {/* Legend */}
      <div className="corpus-graph-legend" style={{
        position: 'absolute', top: 48, right: 12,
        background: 'rgba(10,10,16,0.85)', borderRadius: 8, padding: '10px 14px',
        border: '1px solid var(--glass-border)', fontSize: '0.78rem', maxWidth: 200,
      }}>
        <div className="caption mb-1 uppercase tracking-wider" style={{ color: 'var(--text-4)' }}>Clusters</div>
        {legendClusters.map(([cluster, color]) => (
          <div key={cluster} style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3 }}>
            <span style={{ width: 10, height: 10, borderRadius: '50%', background: color, flexShrink: 0 }} />
            <span style={{ color: 'var(--text-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {cluster.length > 28 ? `${cluster.slice(0, 28)}…` : cluster}
            </span>
          </div>
        ))}
      </div>

      {/* Hover tooltip */}
      {hoverNode && (
        <div className="corpus-graph-tooltip" style={{
          position: 'absolute', bottom: 16, left: 12,
          background: 'rgba(10,10,16,0.92)', borderRadius: 8, padding: '10px 14px',
          border: '1px solid var(--glass-border)', maxWidth: 360, pointerEvents: 'none',
        }}>
          <div className="mono caption" style={{ color: 'var(--text-4)', marginBottom: 4 }}>{hoverNode.id}</div>
          <div className="body" style={{ color: 'var(--text-1)', lineHeight: 1.4 }}>{hoverNode.label}</div>
          {hoverNode.cluster && (
            <div className="caption mt-1" style={{ color: clusterColorMap[hoverNode.cluster] }}>
              Cluster: {hoverNode.cluster}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
