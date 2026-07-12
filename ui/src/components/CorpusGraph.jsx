import { useEffect, useRef, useState, useCallback, useMemo } from 'react'
import ForceGraph2D from 'react-force-graph-2d'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

// Cluster palette — high-contrast, colorblind-friendly-ish
const CLUSTER_PALETTE = [
  '#6366f1', '#06b6d4', '#f59e0b', '#ef4444', '#10b981',
  '#8b5cf6', '#ec4899', '#14b8a6', '#f97316', '#84cc16',
  '#e879f9', '#22d3ee', '#fb923c', '#a3e635', '#f472b6',
]

// Client-side safety net: the backend doesn't currently enforce the
// `sample` query param, so a future full-corpus response can't overwhelm
// the force-graph render. Mirrors CorpusKG.jsx's MAX_ENTITIES cap.
const MAX_GRAPH_POINTS = 1000

/**
 * SPECTER2 similarity force-directed graph.
 *
 * Fetches from ``/api/corpus/graph`` and renders an interactive
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
  const [containerWidth, setContainerWidth] = useState(800)
  // The wrapper div only mounts once data has loaded (the loading/error/empty
  // states render before it), so a plain useRef + useEffect(..., []) would see
  // containerRef.current as null on the one time the effect runs and never
  // retry. A callback ref fires exactly when the node attaches (including
  // after that later render), which is what we need here.
  const [containerNode, setContainerNode] = useState(null)
  const containerRef = useCallback(node => setContainerNode(node), [])
  const fgRef = useRef(null)

  // Track the container's actual width so the canvas always matches it —
  // both on mount and on resize/rotation, instead of locking in a stale/
  // fallback width forever.
  useEffect(() => {
    const el = containerNode
    if (!el) return
    // ResizeObserver isn't available everywhere (older browsers, some test
    // runners/jsdom) — fall back to a one-time width read from the
    // container's current layout instead of throwing.
    if (typeof ResizeObserver === 'undefined') {
      setContainerWidth(el.offsetWidth || 800)
      return
    }
    const ro = new ResizeObserver(entries => {
      const w = entries[0]?.contentRect?.width
      if (w) setContainerWidth(w)
    })
    ro.observe(el)
    setContainerWidth(el.offsetWidth || 800)
    return () => ro.disconnect()
  }, [containerNode])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fetch(`${API_BASE}/api/corpus/graph?sample=1000&lod=1`)
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
    // New API returns {points: [{cluster_id}]}, old returned {nodes: [{cluster}]}
    const items = data?.points || data?.nodes || []
    const clusters = [...new Set(items.map(n => n.cluster_id || n.cluster || 'default'))]
    const map = {}
    clusters.forEach((c, i) => { map[c] = CLUSTER_PALETTE[i % CLUSTER_PALETTE.length] })
    return map
  }, [data])

  // Transform data for react-force-graph-2d
  // New /api/corpus/graph returns {points: [{arxiv_id, x, y, cluster_id}]} (UMAP coords)
  // Legacy endpoint returned {nodes, edges}
  const graphData = useMemo(() => {
    if (data?.points) {
      // New API: points with pre-computed UMAP x,y
      const points = data.points.slice(0, MAX_GRAPH_POINTS)
      return {
        nodes: points.map(p => ({
          id: p.arxiv_id,
          label: p.arxiv_id,
          cluster: p.cluster_id || 'default',
          val: 2,
          color: clusterColorMap[p.cluster_id || 'default'] || CLUSTER_PALETTE[0],
          fx: p.x * 50,  // scale UMAP coords for display
          fy: p.y * 50,
        })),
        links: [],
      }
    }
    // Legacy fallback
    if (!data?.nodes) return { nodes: [], links: [] }
    const nodes = data.nodes.slice(0, MAX_GRAPH_POINTS)
    const nodeIds = new Set(nodes.map(n => n.id))
    return {
      nodes: nodes.map(n => ({
        id: n.id,
        label: n.title || n.id,
        cluster: n.cluster || 'default',
        val: 2,
        color: clusterColorMap[n.cluster || 'default'] || CLUSTER_PALETTE[0],
      })),
      links: (data.edges || [])
        .filter(e => nodeIds.has(e.source) && nodeIds.has(e.target))
        .map(e => ({
          source: e.source,
          target: e.target,
          value: e.weight || 1,
        })),
    }
  }, [data, clusterColorMap])

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

  if (!data || data.status === 'empty' || ((!data.nodes || data.nodes.length === 0) && (!data.points || data.points.length === 0))) {
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
        <span className="tag tag-muted">{data.point_count || data.points?.length || data.nodes?.length || 0} points</span>
        <span className="tag tag-muted">{data.cluster_count || 0} clusters</span>
        <span className="tag tag-muted">{data.total_papers?.toLocaleString() || ''} total papers</span>
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
        width={containerWidth}
        height={500}
      />

      {/* Legend — shrinks on narrow containers so it doesn't permanently
          cover most of the graph on a phone-width viewport. */}
      <div className="corpus-graph-legend" style={{
        position: 'absolute', top: 48, right: 12,
        background: 'rgba(10,10,16,0.85)', borderRadius: 8,
        padding: containerWidth < 480 ? '8px 10px' : '10px 14px',
        border: '1px solid var(--glass-border)',
        fontSize: containerWidth < 480 ? '0.68rem' : '0.78rem',
        maxWidth: containerWidth < 480 ? 120 : 200,
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
