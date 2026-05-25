import { useState, useEffect, useCallback } from 'react'
import CustomSelect from './CustomSelect'
import CorpusGraph from './CorpusGraph'
import CorpusKG from './CorpusKG'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

async function apiGet(path) {
  const res = await fetch(`${API_BASE}${path}`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

const TABS = ['catalog', 'overview', 'graph', 'knowledge-graph']

export default function CorpusExplorer() {
  const [tab, setTab] = useState('catalog')
  const [overview, setOverview] = useState(null)
  const [papers, setPapers] = useState([])
  const [selectedPaper, setSelectedPaper] = useState(null)
  const [search, setSearch] = useState('')
  const [categoryFilter, setCategoryFilter] = useState('')
  const [page, setPage] = useState(1)
  const [totalPapers, setTotalPapers] = useState(0)
  const [loading, setLoading] = useState(false)

  // Fetch overview
  useEffect(() => {
    apiGet('/api/corpus/overview')
      .then(setOverview)
      .catch(() => setOverview(null))
  }, [])

  // Fetch papers catalog
  const fetchPapers = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams({ page: String(page), limit: '20' })
      if (search) params.set('search', search)
      if (categoryFilter) params.set('category', categoryFilter)
      // Trailing slash matters: FastAPI 307-redirects /api/papers → /api/papers/,
      // and the browser silently drops the query string on the redirect, so the
      // catalog rendered "0 papers found" despite the backend having 10000.
      const data = await apiGet(`/api/papers/?${params}`)
      setPapers(data.papers || [])
      setTotalPapers(data.total || 0)
    } catch { setPapers([]) }
    setLoading(false)
  }, [page, search, categoryFilter])

  useEffect(() => { fetchPapers() }, [fetchPapers])

  // Fetch paper detail
  const openPaper = async (arxivId) => {
    try {
      const data = await apiGet(`/api/papers/${arxivId}`)
      setSelectedPaper(data)
    } catch { setSelectedPaper(null) }
  }

  if (selectedPaper) {
    return <PaperDetail paper={selectedPaper} onBack={() => setSelectedPaper(null)} />
  }

  return (
    <div className="corpus-explorer">
      <div className="corpus-header">
        <h2>Research Corpus Explorer</h2>
        {overview && (
          <div className="corpus-stats">
            <span className="stat-chip">{overview.total_papers?.toLocaleString()} papers</span>
            <span className="stat-chip">{overview.categories?.length} categories</span>
            <span className="stat-chip">{overview.source} source</span>
          </div>
        )}
      </div>

      <div className="corpus-tabs">
        {TABS.map(t => (
          <button key={t} className={`corpus-tab${tab === t ? ' active' : ''}`} onClick={() => setTab(t)}>
            {t.replace('-', ' ').replace(/\b\w/g, c => c.toUpperCase())}
          </button>
        ))}
      </div>

      {tab === 'overview' && <OverviewTab overview={overview} />}
      {tab === 'catalog' && (
        <CatalogTab
          papers={papers} total={totalPapers} page={page} loading={loading}
          search={search} setSearch={setSearch}
          categoryFilter={categoryFilter} setCategoryFilter={setCategoryFilter}
          setPage={setPage} openPaper={openPaper}
          categories={overview?.categories || []}
        />
      )}
      {tab === 'graph' && (
        <div className="corpus-graph-container" style={{ padding: '8px 0' }}>
          <CorpusGraph />
        </div>
      )}
      {tab === 'knowledge-graph' && (
        <div className="corpus-kg-container" style={{ padding: '8px 0' }}>
          <CorpusKG onOpenPaper={openPaper} />
        </div>
      )}
    </div>
  )
}

function OverviewTab({ overview }) {
  if (!overview) return <div className="corpus-loading">Loading overview...</div>

  const maxCatCount = Math.max(...(overview.categories || []).map(c => c.count), 1)
  const maxYearCount = Math.max(...(overview.year_distribution || []).map(y => y.count), 1)

  return (
    <div className="corpus-overview">
      <div className="overview-section">
        <h3>Category Distribution</h3>
        <div className="bar-chart">
          {(overview.categories || []).map(c => (
            <div key={c.name} className="bar-row">
              <span className="bar-label" title={c.name}>{c.label || c.name}</span>
              <div className="bar-track">
                <div className="bar-fill" style={{ width: `${(c.count / maxCatCount) * 100}%` }} />
              </div>
              <span className="bar-count">{c.count.toLocaleString()}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="overview-section">
        <h3>Year Distribution</h3>
        <div className="year-chart">
          {(overview.year_distribution || []).map(y => (
            <div key={y.year} className="year-bar-row">
              <span className="year-label">{y.year}</span>
              <div className="year-track">
                <div className="year-fill" style={{ width: `${(y.count / maxYearCount) * 100}%` }} />
              </div>
              <span className="year-count">{y.count.toLocaleString()}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="overview-summary">
        <h3>Library Summary</h3>
        <div className="summary-grid">
          <div className="summary-card">
            <div className="summary-value">{overview.total_papers?.toLocaleString()}</div>
            <div className="summary-label">Total Papers</div>
          </div>
          <div className="summary-card">
            <div className="summary-value">{overview.categories?.length}</div>
            <div className="summary-label">Categories</div>
          </div>
          <div className="summary-card">
            <div className="summary-value">{overview.year_distribution?.length}</div>
            <div className="summary-label">Year Span</div>
          </div>
        </div>
      </div>
    </div>
  )
}

function formatAuthors(authors) {
  if (!Array.isArray(authors) || authors.length === 0) return '—'
  if (authors.length === 1) return authors[0]
  if (authors.length === 2) return `${authors[0]} & ${authors[1]}`
  return `${authors[0]}, ${authors[1]} et al.`
}

function truncate(s, n) {
  if (!s) return ''
  return s.length > n ? `${s.slice(0, n)}…` : s
}

function CatalogTab({ papers, total, page, loading, search, setSearch, categoryFilter, setCategoryFilter, setPage, openPaper, categories }) {
  const totalPages = Math.ceil(total / 20)
  return (
    <div className="corpus-catalog">
      <div className="catalog-controls">
        <input
          type="text" placeholder="Search papers..." value={search}
          onChange={e => { setSearch(e.target.value); setPage(1) }}
          className="catalog-search"
        />
        <CustomSelect
          value={categoryFilter}
          onChange={v => { setCategoryFilter(v); setPage(1) }}
          style={{ width: 180 }}
          options={[{ value: '', label: 'All Categories' }, ...categories.map(c => ({ value: c.name, label: `${c.label || c.name} (${c.count})` }))]}
        />
      </div>

      {loading ? <div className="corpus-loading">Loading...</div> : (
        <>
          <div className="catalog-results-info">{total.toLocaleString()} papers found</div>
          <div className="overflow-x-auto rounded-lg border border-[var(--glass-border)]">
            <table className="lib-table" style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
              <thead>
                <tr style={{ background: 'rgba(255,255,255,0.03)', textAlign: 'left', borderBottom: '1px solid var(--glass-border)' }}>
                  <th style={{ padding: '10px 14px', whiteSpace: 'nowrap' }}>arxiv ID</th>
                  <th style={{ padding: '10px 14px' }}>Authors</th>
                  <th style={{ padding: '10px 14px', textAlign: 'right', whiteSpace: 'nowrap' }}>Year</th>
                  <th style={{ padding: '10px 14px' }}>Title</th>
                  <th style={{ padding: '10px 14px', whiteSpace: 'nowrap' }}>Category</th>
                </tr>
              </thead>
              <tbody>
                {papers.map(p => (
                  <tr
                    key={p.arxiv_id}
                    onClick={() => openPaper(p.arxiv_id)}
                    style={{ borderBottom: '1px solid var(--glass-border)', cursor: 'pointer' }}
                    className="hover:bg-[rgba(255,255,255,0.03)]"
                  >
                    <td style={{ padding: '8px 14px', fontFamily: 'var(--mono, monospace)', color: 'var(--text-3)', whiteSpace: 'nowrap' }}>
                      {p.arxiv_id}
                    </td>
                    <td style={{ padding: '8px 14px', color: 'var(--text-2)' }} title={Array.isArray(p.authors) ? p.authors.join(', ') : ''}>
                      {formatAuthors(p.authors)}
                    </td>
                    <td style={{ padding: '8px 14px', textAlign: 'right', fontFamily: 'var(--mono, monospace)', color: 'var(--text-3)', whiteSpace: 'nowrap' }}>
                      {p.published ? p.published.slice(0, 4) : '—'}
                    </td>
                    <td style={{ padding: '8px 14px', color: 'var(--text-1)' }} title={p.title || p.arxiv_id}>
                      {truncate(p.title || p.arxiv_id, 80)}
                    </td>
                    <td style={{ padding: '8px 14px', whiteSpace: 'nowrap' }}>
                      {p.primary_category && (
                        <span
                          className="tag tag-muted"
                          title={p.category_label ? `${p.primary_category} — ${p.category_label}` : p.primary_category}
                        >
                          {p.category_label || p.primary_category}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {totalPages > 1 && (
            <div className="catalog-pagination">
              <button disabled={page <= 1} onClick={() => setPage(p => p - 1)}>Previous</button>
              <span>Page {page} of {totalPages}</span>
              <button disabled={page >= totalPages} onClick={() => setPage(p => p + 1)}>Next</button>
            </div>
          )}
        </>
      )}
    </div>
  )
}

function PaperDetail({ paper, onBack }) {
  // Always derive an arxiv URL from the id — backend doesn't always populate
  // pdf_url, but arxiv.org/abs/{id} is canonical and always works.
  const arxivAbsUrl = paper.arxiv_id ? `https://arxiv.org/abs/${paper.arxiv_id}` : null
  const arxivPdfUrl = paper.pdf_url || (paper.arxiv_id ? `https://arxiv.org/pdf/${paper.arxiv_id}` : null)

  return (
    <div className="corpus-explorer">
      <button className="back-btn flex items-center gap-1.5" onClick={onBack}><span className="i-lucide-arrow-left w-4 h-4" /> Back to Explorer</button>
      <div className="paper-detail" style={{ maxWidth: 820 }}>
        <h2 className="leading-snug mb-2">{paper.title || paper.arxiv_id}</h2>

        {paper.authors?.length > 0 && (
          <div className="caption mb-3 text-[0.92rem]">
            {paper.authors.join(', ')}
          </div>
        )}

        <div className="paper-detail-meta flex flex-wrap gap-2 mb-3.5">
          <span className="tag tag-muted mono">arxiv:{paper.arxiv_id}</span>
          {paper.primary_category && (
            <span
              className="tag tag-muted"
              title={paper.category_label ? `${paper.primary_category} — ${paper.category_label}` : paper.primary_category}
            >
              {paper.category_label || paper.primary_category}
            </span>
          )}
          {paper.published && <span className="tag tag-muted">{(paper.published || '').slice(0, 10)}</span>}
          {paper.topic_label && <span className="tag tag-accent">Topic: {paper.topic_label}</span>}
        </div>

        <div className="flex gap-2.5 mb-5 flex-wrap">
          {arxivAbsUrl && (
            <a
              href={arxivAbsUrl} target="_blank" rel="noopener noreferrer"
              className="btn btn-primary"
              style={{ padding: '6px 14px', fontSize: '0.85rem' }}
            >
              arxiv.org abstract ↗
            </a>
          )}
          {arxivPdfUrl && (
            <a
              href={arxivPdfUrl} target="_blank" rel="noopener noreferrer"
              className="btn btn-outline"
              style={{ padding: '6px 14px', fontSize: '0.85rem' }}
            >
              PDF ↗
            </a>
          )}
        </div>

        {paper.abstract && (
          <div className="paper-abstract-full" style={{ marginBottom: 24 }}>
            <h4 style={{ marginBottom: 8, fontSize: '0.9rem', color: 'var(--text-3)', letterSpacing: '0.05em', textTransform: 'uppercase' }}>Abstract</h4>
            <p style={{ lineHeight: 1.7, fontSize: '0.95rem', color: 'var(--text-2)' }}>{paper.abstract}</p>
          </div>
        )}

        <div className="paper-provenance" style={{ borderTop: '1px solid var(--glass-border)', paddingTop: 18, marginTop: 18 }}>
          <h4 style={{ marginBottom: 10, fontSize: '0.9rem', color: 'var(--text-3)', letterSpacing: '0.05em', textTransform: 'uppercase' }}>
            Cited by {paper.citing_strategies?.length || 0} strateg{paper.citing_strategies?.length === 1 ? 'y' : 'ies'}
          </h4>
          {paper.citing_strategies?.length > 0 ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {paper.citing_strategies.map(s => (
                <div key={s.id || s.name} className="card" style={{ padding: 12, display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
                  <div>
                    <div style={{ fontWeight: 600, fontSize: '0.92rem' }}>{s.name || s.id}</div>
                    {s.method && (
                      <div className="caption" style={{ marginTop: 2 }}>
                        via <span className="tag tag-muted" style={{ marginLeft: 4 }}>{s.method}</span>
                        {s.status && <span style={{ marginLeft: 6 }}>· {s.status}</span>}
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="caption" style={{ color: 'var(--text-4)' }}>
              No strategies in the library currently cite this paper. Generate one from{' '}
              <a href="/generate" style={{ color: 'var(--accent)' }}>Generate</a> — when the
              fusion engine selects this paper, the link will appear here.
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
