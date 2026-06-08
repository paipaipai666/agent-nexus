import { useState, useEffect, useRef } from 'react'
import { Search, FileText, Upload, Trash2, Loader2, BookOpen, AlertTriangle, CheckCircle, BarChart3, Shield } from 'lucide-react'
import { api } from '../services/api'

interface WikiPageItem {
  page_id: string
  title: string
  page_type: string
  confidence: string
  statement_count: number
  source_namespace: string
  created_at: string
  updated_at: string
}

interface WikiStats {
  page_count: number
  statement_count: number
  pending_reviews: number
  confidence_distribution: Record<string, number>
  calibration_needed: boolean
}

interface ReviewItem {
  item_id: string
  priority: number
  page_id: string
  description: string
  status: string
  deadline: string
}

type Tab = 'pages' | 'query' | 'review' | 'stats'

const confidenceColor: Record<string, string> = {
  high: 'var(--green)',
  medium: 'var(--yellow)',
  low: 'var(--red)',
  untrusted: 'var(--red)',
}

const confidenceLabel: Record<string, string> = {
  high: 'High',
  medium: 'Medium',
  low: 'Low',
  untrusted: 'Untrusted',
}

const priorityLabel: Record<number, { label: string; color: string }> = {
  1: { label: 'P1', color: 'var(--red)' },
  2: { label: 'P2', color: 'var(--yellow)' },
  3: { label: 'P3', color: 'var(--fg-faint)' },
}

export default function WikiPage() {
  const [tab, setTab] = useState<Tab>('pages')
  const [pages, setPages] = useState<WikiPageItem[]>([])
  const [stats, setStats] = useState<WikiStats | null>(null)
  const [reviews, setReviews] = useState<ReviewItem[]>([])
  const [loading, setLoading] = useState(false)
  const [selectedPage, setSelectedPage] = useState<any>(null)

  // Query state
  const [query, setQuery] = useState('')
  const [queryResult, setQueryResult] = useState<any>(null)
  const [isQuerying, setIsQuerying] = useState(false)

  // Ingest state
  const [ingestText, setIngestText] = useState('')
  const [ingestUri, setIngestUri] = useState('')
  const [isIngesting, setIsIngesting] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    loadData()
  }, [tab])

  const loadData = async () => {
    setLoading(true)
    try {
      if (tab === 'pages') {
        const { pages } = await api.listWikiPages()
        setPages(pages)
      } else if (tab === 'stats') {
        const s = await api.getWikiStats()
        setStats(s)
      } else if (tab === 'review') {
        const { items } = await api.listWikiReviews()
        setReviews(items)
      }
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  const handleQuery = async () => {
    if (!query.trim()) return
    setIsQuerying(true)
    try {
      const result = await api.wikiQuery(query)
      setQueryResult(result)
    } catch (e) {
      console.error(e)
    } finally {
      setIsQuerying(false)
    }
  }

  const handleIngestText = async () => {
    if (!ingestText.trim() || !ingestUri.trim()) return
    setIsIngesting(true)
    try {
      await api.wikiIngestText(ingestText, ingestUri)
      setIngestText('')
      setIngestUri('')
      loadData()
    } catch (e) {
      console.error(e)
    } finally {
      setIsIngesting(false)
    }
  }

  const handleIngestFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setIsIngesting(true)
    try {
      await api.wikiIngestFile(file)
      loadData()
    } catch (e) {
      console.error(e)
    } finally {
      setIsIngesting(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const handleDeletePage = async (pageId: string) => {
    if (!confirm('Delete this wiki page?')) return
    try {
      await api.deleteWikiPage(pageId)
      loadData()
    } catch (e) {
      console.error(e)
    }
  }

  const handlePageClick = async (pageId: string) => {
    try {
      const page = await api.getWikiPage(pageId)
      setSelectedPage(page)
    } catch (e) {
      console.error(e)
    }
  }

  const handleResolveReview = async (itemId: string) => {
    try {
      await api.resolveWikiReview(itemId)
      loadData()
    } catch (e) {
      console.error(e)
    }
  }

  const handleRunLint = async () => {
    setLoading(true)
    try {
      await api.wikiLint()
      loadData()
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  const handleProcessReviews = async () => {
    try {
      await api.processWikiReviews()
      loadData()
    } catch (e) {
      console.error(e)
    }
  }

  const tabs: { key: Tab; label: string; icon: any }[] = [
    { key: 'pages', label: 'Pages', icon: BookOpen },
    { key: 'query', label: 'Query', icon: Search },
    { key: 'review', label: 'Review', icon: AlertTriangle },
    { key: 'stats', label: 'Stats', icon: BarChart3 },
  ]

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="px-6 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-xl uppercase tracking-wider" style={{ color: 'var(--fg)', fontFamily: 'var(--font-display)' }}>Wiki</h1>
          <p className="text-xs mt-0.5" style={{ color: 'var(--fg-muted)' }}>
            Hybrid knowledge system with confidence routing
          </p>
        </div>
        <div className="flex gap-2">
          <input ref={fileInputRef} type="file" onChange={handleIngestFile} className="hidden" accept=".txt,.md,.pdf,.html,.json" />
          <button onClick={() => fileInputRef.current?.click()} disabled={isIngesting} className="btn-primary flex items-center gap-1.5 text-sm">
            {isIngesting ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
            {isIngesting ? 'Ingesting...' : 'Ingest File'}
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="px-6 flex gap-1 border-b" style={{ borderColor: 'var(--border)' }}>
        {tabs.map(t => (
          <button
            key={t.key}
            onClick={() => { setTab(t.key); setSelectedPage(null) }}
            className="flex items-center gap-1.5 px-3 py-2 text-sm transition-colors"
            style={{
              color: tab === t.key ? 'var(--fg)' : 'var(--fg-muted)',
              borderBottom: tab === t.key ? '2px solid var(--accent)' : '2px solid transparent',
            }}
          >
            <t.icon size={14} />
            {t.label}
            {t.key === 'review' && reviews.length > 0 && (
              <span className="ml-1 px-1.5 py-0.5 text-[10px] rounded-full" style={{ background: 'var(--red-muted)', color: 'var(--red)' }}>
                {reviews.length}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        {loading ? (
          <div className="flex justify-center py-16">
            <Loader2 size={24} className="animate-spin" style={{ color: 'var(--fg-faint)' }} />
          </div>
        ) : (
          <>
            {/* Pages Tab */}
            {tab === 'pages' && (
              <div className="flex gap-4 h-full">
                {/* Page List */}
                <div className={`${selectedPage ? 'w-1/3' : 'w-full'} space-y-1.5`}>
                  {/* Inline Ingest */}
                  <div className="mb-4 p-3 rounded-lg" style={{ background: 'var(--surface-2)', border: '1px solid var(--border)' }}>
                    <p className="text-xs font-medium mb-2" style={{ color: 'var(--fg-secondary)' }}>Quick Ingest</p>
                    <input
                      value={ingestUri}
                      onChange={e => setIngestUri(e.target.value)}
                      placeholder="Source URI (e.g., article name)"
                      className="input-field w-full mb-2 text-sm"
                    />
                    <textarea
                      value={ingestText}
                      onChange={e => setIngestText(e.target.value)}
                      placeholder="Paste source text here..."
                      className="input-field w-full h-20 text-sm resize-none mb-2"
                    />
                    <button onClick={handleIngestText} disabled={isIngesting || !ingestText.trim() || !ingestUri.trim()} className="btn-primary text-xs">
                      {isIngesting ? 'Ingesting...' : 'Ingest to Wiki'}
                    </button>
                  </div>

                  {pages.length === 0 ? (
                    <div className="flex flex-col items-center justify-center py-16 gap-3">
                      <BookOpen size={24} style={{ color: 'var(--fg-faint)' }} />
                      <p className="text-sm" style={{ color: 'var(--fg-muted)' }}>No wiki pages yet</p>
                      <p className="text-xs" style={{ color: 'var(--fg-faint)' }}>Ingest sources to build your wiki</p>
                    </div>
                  ) : (
                    pages.map(page => (
                      <div
                        key={page.page_id}
                        onClick={() => handlePageClick(page.page_id)}
                        className="p-3 rounded-lg flex items-center justify-between cursor-pointer group transition-colors"
                        style={{
                          background: selectedPage?.page_id === page.page_id ? 'var(--surface-2)' : 'var(--surface-1)',
                          border: '1px solid var(--border)',
                        }}
                        onMouseEnter={e => e.currentTarget.style.background = 'var(--surface-2)'}
                        onMouseLeave={e => {
                          if (selectedPage?.page_id !== page.page_id) e.currentTarget.style.background = 'var(--surface-1)'
                        }}
                      >
                        <div className="flex items-center gap-3 min-w-0">
                          <div className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0" style={{ background: 'var(--accent-muted)' }}>
                            <FileText size={14} style={{ color: 'var(--accent)' }} />
                          </div>
                          <div className="min-w-0">
                            <p className="text-sm truncate" style={{ color: 'var(--fg)' }}>{page.title}</p>
                            <p className="text-xs" style={{ color: 'var(--fg-faint)' }}>
                              {page.page_type} · {page.statement_count} statements
                            </p>
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] px-1.5 py-0.5 rounded-full font-mono" style={{ background: `${confidenceColor[page.confidence]}20`, color: confidenceColor[page.confidence] }}>
                            {confidenceLabel[page.confidence] || page.confidence}
                          </span>
                          <button
                            onClick={e => { e.stopPropagation(); handleDeletePage(page.page_id) }}
                            className="p-1 rounded opacity-0 group-hover:opacity-100 transition-opacity"
                            style={{ color: 'var(--fg-faint)' }}
                          >
                            <Trash2 size={12} />
                          </button>
                        </div>
                      </div>
                    ))
                  )}
                </div>

                {/* Page Detail */}
                {selectedPage && (
                  <div className="flex-1 p-4 rounded-lg overflow-y-auto" style={{ background: 'var(--surface-1)', border: '1px solid var(--border)' }}>
                    <div className="flex items-center justify-between mb-4">
                      <div>
                        <h2 className="text-lg font-medium" style={{ color: 'var(--fg)' }}>{selectedPage.title}</h2>
                        <p className="text-xs" style={{ color: 'var(--fg-faint)' }}>
                          {selectedPage.page_type} · {selectedPage.page_id}
                        </p>
                      </div>
                      <button onClick={() => setSelectedPage(null)} className="text-xs" style={{ color: 'var(--fg-faint)' }}>Close</button>
                    </div>

                    {/* Confidence */}
                    <div className="flex items-center gap-2 mb-4">
                      <Shield size={14} style={{ color: confidenceColor[selectedPage.confidence] }} />
                      <span className="text-sm" style={{ color: confidenceColor[selectedPage.confidence] }}>
                        {confidenceLabel[selectedPage.confidence]} confidence
                      </span>
                    </div>

                    {/* Content */}
                    <div className="mb-4 p-3 rounded-lg text-sm" style={{ background: 'var(--surface-2)', color: 'var(--fg)', whiteSpace: 'pre-wrap' }}>
                      {selectedPage.content || 'No content'}
                    </div>

                    {/* Statements */}
                    {selectedPage.statements?.length > 0 && (
                      <div className="mb-4">
                        <h3 className="text-sm font-medium mb-2" style={{ color: 'var(--fg-secondary)' }}>Statements</h3>
                        <div className="space-y-2">
                          {selectedPage.statements.map((stmt: any) => (
                            <div key={stmt.statement_id} className="p-2 rounded-lg text-xs" style={{ background: 'var(--surface-2)', border: '1px solid var(--border)' }}>
                              <p style={{ color: 'var(--fg)' }}>{stmt.text}</p>
                              <div className="flex items-center gap-2 mt-1">
                                <span className="font-mono px-1 py-0.5 rounded" style={{ background: 'var(--surface-1)', color: 'var(--fg-faint)', fontSize: '10px' }}>
                                  {stmt.verified_synthesis_level || stmt.synthesis_level}
                                </span>
                                {stmt.source_chunk_ids?.length > 0 && (
                                  <span className="font-mono" style={{ color: 'var(--fg-faint)', fontSize: '10px' }}>
                                    {stmt.source_chunk_ids.length} source(s)
                                  </span>
                                )}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Canonical Definitions */}
                    {selectedPage.canonical_definitions && Object.keys(selectedPage.canonical_definitions).length > 0 && (
                      <div>
                        <h3 className="text-sm font-medium mb-2" style={{ color: 'var(--fg-secondary)' }}>Canonical Definitions</h3>
                        <div className="space-y-2">
                          {Object.entries(selectedPage.canonical_definitions).map(([term, def]: [string, any]) => (
                            <div key={term} className="p-2 rounded-lg" style={{ background: 'var(--surface-2)', border: '1px solid var(--border)' }}>
                              <p className="text-xs font-medium" style={{ color: 'var(--accent)' }}>{term}</p>
                              {def.consensus ? (
                                <p className="text-xs mt-1" style={{ color: 'var(--fg)' }}>{def.consensus}</p>
                              ) : (
                                <p className="text-xs mt-1 italic" style={{ color: 'var(--fg-faint)' }}>
                                  No consensus (divergence: {def.divergence?.toFixed(2)})
                                </p>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* Query Tab */}
            {tab === 'query' && (
              <div className="max-w-2xl mx-auto space-y-4">
                <div className="flex gap-2">
                  <input
                    value={query}
                    onChange={e => setQuery(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && handleQuery()}
                    placeholder="Ask a question..."
                    className="input-field flex-1"
                  />
                  <button onClick={handleQuery} disabled={isQuerying} className="btn-primary flex items-center gap-1.5 text-sm">
                    {isQuerying ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />}
                    Query
                  </button>
                </div>

                {queryResult && (
                  <div className="p-4 rounded-lg" style={{ background: 'var(--surface-1)', border: '1px solid var(--border)' }}>
                    <div className="flex items-center gap-2 mb-3">
                      <span className="text-xs px-2 py-1 rounded-full font-mono" style={{ background: queryResult.used_wiki ? 'var(--accent-muted)' : 'var(--surface-2)', color: queryResult.used_wiki ? 'var(--accent)' : 'var(--fg-muted)' }}>
                        {queryResult.used_wiki ? 'Wiki' : 'RAG'}
                      </span>
                      <span className="text-xs" style={{ color: 'var(--fg-faint)' }}>
                        {queryResult.decision} · {queryResult.confidence}
                      </span>
                    </div>

                    <p className="text-sm" style={{ color: 'var(--fg)', whiteSpace: 'pre-wrap' }}>
                      {queryResult.answer || 'No answer'}
                    </p>

                    {queryResult.disclaimer && (
                      <div className="mt-3 p-2 rounded-lg flex items-start gap-2" style={{ background: 'var(--yellow-muted)', border: '1px solid var(--yellow)' }}>
                        <AlertTriangle size={14} style={{ color: 'var(--yellow)', flexShrink: 0, marginTop: 2 }} />
                        <p className="text-xs" style={{ color: 'var(--yellow)' }}>{queryResult.disclaimer}</p>
                      </div>
                    )}

                    {queryResult.source_chunks?.length > 0 && (
                      <div className="mt-3">
                        <p className="text-[10px] font-mono" style={{ color: 'var(--fg-faint)' }}>
                          Sources: {queryResult.source_chunks.slice(0, 5).join(', ')}
                        </p>
                      </div>
                    )}

                    {queryResult.rag_results?.length > 0 && (
                      <div className="mt-3 space-y-2">
                        <p className="text-xs font-medium" style={{ color: 'var(--fg-secondary)' }}>RAG Results</p>
                        {queryResult.rag_results.slice(0, 3).map((r: any, i: number) => (
                          <div key={i} className="p-2 rounded-lg text-xs" style={{ background: 'var(--surface-2)', border: '1px solid var(--border)' }}>
                            <p style={{ color: 'var(--fg)' }}>{r.text?.slice(0, 200)}...</p>
                            {r.score != null && (
                              <p className="font-mono mt-1" style={{ color: 'var(--accent)', fontSize: '10px' }}>Score: {r.score.toFixed(3)}</p>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* Review Tab */}
            {tab === 'review' && (
              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <p className="text-sm" style={{ color: 'var(--fg-muted)' }}>{reviews.length} pending items</p>
                  <div className="flex gap-2">
                    <button onClick={handleRunLint} className="btn-primary text-xs">Run Lint</button>
                    <button onClick={handleProcessReviews} className="btn-primary text-xs">Process Overdue</button>
                  </div>
                </div>

                {reviews.length === 0 ? (
                  <div className="flex flex-col items-center justify-center py-16 gap-3">
                    <CheckCircle size={24} style={{ color: 'var(--green)' }} />
                    <p className="text-sm" style={{ color: 'var(--fg-muted)' }}>No pending reviews</p>
                  </div>
                ) : (
                  reviews.map(item => (
                    <div key={item.item_id} className="p-3 rounded-lg flex items-start justify-between" style={{ background: 'var(--surface-1)', border: '1px solid var(--border)' }}>
                      <div className="flex items-start gap-3">
                        <span className="text-xs px-1.5 py-0.5 rounded-full font-mono font-bold" style={{ color: priorityLabel[item.priority]?.color }}>
                          {priorityLabel[item.priority]?.label || `P${item.priority}`}
                        </span>
                        <div>
                          <p className="text-sm" style={{ color: 'var(--fg)' }}>{item.description}</p>
                          <p className="text-[10px] mt-1 font-mono" style={{ color: 'var(--fg-faint)' }}>
                            {item.page_id && `Page: ${item.page_id} · `}
                            Deadline: {item.deadline?.slice(0, 10)}
                          </p>
                        </div>
                      </div>
                      <button
                        onClick={() => handleResolveReview(item.item_id)}
                        className="btn-primary text-xs shrink-0"
                      >
                        Resolve
                      </button>
                    </div>
                  ))
                )}
              </div>
            )}

            {/* Stats Tab */}
            {tab === 'stats' && stats && (
              <div className="max-w-xl mx-auto space-y-6">
                <div className="grid grid-cols-2 gap-4">
                  <div className="p-4 rounded-lg" style={{ background: 'var(--surface-1)', border: '1px solid var(--border)' }}>
                    <p className="text-xs" style={{ color: 'var(--fg-muted)' }}>Pages</p>
                    <p className="text-2xl font-medium" style={{ color: 'var(--fg)' }}>{stats.page_count}</p>
                  </div>
                  <div className="p-4 rounded-lg" style={{ background: 'var(--surface-1)', border: '1px solid var(--border)' }}>
                    <p className="text-xs" style={{ color: 'var(--fg-muted)' }}>Statements</p>
                    <p className="text-2xl font-medium" style={{ color: 'var(--fg)' }}>{stats.statement_count}</p>
                  </div>
                  <div className="p-4 rounded-lg" style={{ background: 'var(--surface-1)', border: '1px solid var(--border)' }}>
                    <p className="text-xs" style={{ color: 'var(--fg-muted)' }}>Pending Reviews</p>
                    <p className="text-2xl font-medium" style={{ color: stats.pending_reviews > 0 ? 'var(--yellow)' : 'var(--green)' }}>{stats.pending_reviews}</p>
                  </div>
                  <div className="p-4 rounded-lg" style={{ background: 'var(--surface-1)', border: '1px solid var(--border)' }}>
                    <p className="text-xs" style={{ color: 'var(--fg-muted)' }}>Calibration</p>
                    <p className="text-2xl font-medium" style={{ color: stats.calibration_needed ? 'var(--yellow)' : 'var(--green)' }}>
                      {stats.calibration_needed ? 'Needed' : 'OK'}
                    </p>
                  </div>
                </div>

                {/* Confidence Distribution */}
                {stats.confidence_distribution && Object.keys(stats.confidence_distribution).length > 0 && (
                  <div className="p-4 rounded-lg" style={{ background: 'var(--surface-1)', border: '1px solid var(--border)' }}>
                    <h3 className="text-sm font-medium mb-3" style={{ color: 'var(--fg-secondary)' }}>Confidence Distribution</h3>
                    <div className="space-y-2">
                      {Object.entries(stats.confidence_distribution).map(([level, count]) => (
                        <div key={level} className="flex items-center gap-2">
                          <span className="text-xs w-20" style={{ color: confidenceColor[level] || 'var(--fg-muted)' }}>
                            {confidenceLabel[level] || level}
                          </span>
                          <div className="flex-1 h-4 rounded-full overflow-hidden" style={{ background: 'var(--surface-2)' }}>
                            <div
                              className="h-full rounded-full transition-all"
                              style={{
                                width: `${(count / stats.page_count) * 100}%`,
                                background: confidenceColor[level] || 'var(--fg-faint)',
                              }}
                            />
                          </div>
                          <span className="text-xs font-mono w-8 text-right" style={{ color: 'var(--fg-faint)' }}>{count}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
