import { useState, useEffect, useRef, useCallback } from 'react'
import { Search, FileText, Upload, Trash2, Loader2, CheckCircle2, XCircle } from 'lucide-react'
import { api } from '../services/api'
import { animateEntrance } from '../utils/animations'

interface IngestionProgress {
  runId: string
  filename: string
  stage: string
  pct: number
  message: string
  status: 'processing' | 'completed' | 'failed'
}

const STAGE_LABELS: Record<string, string> = {
  loading: 'Loading document',
  chunking: 'Chunking text',
  enriching: 'Enriching chunks',
  embedding: 'Generating embeddings',
  persisting: 'Saving to database',
  completed: 'Done',
  failed: 'Failed',
}

const ACTIVE_UPLOAD_KEY = 'agentnexus_active_upload'

function saveActiveUpload(runId: string, filename: string) {
  try { localStorage.setItem(ACTIVE_UPLOAD_KEY, JSON.stringify({ runId, filename })) } catch {}
}

function loadActiveUpload(): { runId: string; filename: string } | null {
  try {
    const raw = localStorage.getItem(ACTIVE_UPLOAD_KEY)
    return raw ? JSON.parse(raw) : null
  } catch { return null }
}

function clearActiveUpload() {
  try { localStorage.removeItem(ACTIVE_UPLOAD_KEY) } catch {}
}

export default function KnowledgePage() {
  const [documents, setDocuments] = useState<any[]>([])
  const [totalChunks, setTotalChunks] = useState(0)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<any[]>([])
  const [isSearching, setIsSearching] = useState(false)
  const [isUploading, setIsUploading] = useState(false)
  const [progress, setProgress] = useState<IngestionProgress | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const loadDocuments = useCallback((signal?: AbortSignal) => {
    api.listDocuments(signal).then(({ documents, total_chunks }) => {
      setDocuments(documents)
      setTotalChunks(total_chunks)
    }).catch((err) => {
      if (err.name !== 'AbortError') console.error(err)
    })
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    loadDocuments(controller.signal)
    return () => controller.abort()
  }, [loadDocuments])

  // Cleanup polling and timeouts on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
      if (timeoutRef.current) clearTimeout(timeoutRef.current)
    }
  }, [])

  // Resume polling for active upload on mount
  useEffect(() => {
    const saved = loadActiveUpload()
    if (saved && saved.runId) {
      setIsUploading(true)
      pollProgress(saved.runId, saved.filename)
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const pollProgress = useCallback((runId: string, filename: string) => {
    if (pollRef.current) clearInterval(pollRef.current)
    if (timeoutRef.current) clearTimeout(timeoutRef.current)

    let lastPct = -1
    let staleTicks = 0
    const MAX_STALE_TICKS = 30 // Stop after 30s of no progress change

    pollRef.current = setInterval(async () => {
      try {
        const run = await api.getIngestionProgress(runId)
        const stage = run.metadata?.progress_stage || run.status
        const pct = run.metadata?.progress_pct ?? 0
        const message = run.metadata?.progress_message || STAGE_LABELS[stage] || stage

        // Track stale progress (no change in pct)
        if (pct === lastPct && run.status === 'running') {
          staleTicks++
          if (staleTicks >= MAX_STALE_TICKS) {
            setProgress({ runId, filename, stage: 'failed', pct, message: 'Timed out — no progress', status: 'failed' })
            if (pollRef.current) clearInterval(pollRef.current)
            pollRef.current = null
            setIsUploading(false)
            clearActiveUpload()
            timeoutRef.current = setTimeout(() => setProgress(null), 5000)
            return
          }
        } else {
          staleTicks = 0
          lastPct = pct
        }

        if (run.status === 'completed') {
          setProgress({ runId, filename, stage: 'completed', pct: 100, message: 'Done', status: 'completed' })
          if (pollRef.current) clearInterval(pollRef.current)
          pollRef.current = null
          setIsUploading(false)
          clearActiveUpload()
          loadDocuments()
          timeoutRef.current = setTimeout(() => setProgress(null), 2000)
        } else if (run.status === 'failed') {
          setProgress({ runId, filename, stage: 'failed', pct: 0, message: run.error_message || 'Ingestion failed', status: 'failed' })
          if (pollRef.current) clearInterval(pollRef.current)
          pollRef.current = null
          setIsUploading(false)
          clearActiveUpload()
          timeoutRef.current = setTimeout(() => setProgress(null), 5000)
        } else {
          setProgress({ runId, filename, stage, pct, message, status: 'processing' })
        }
      } catch (err) {
        console.error('Poll error:', err)
      }
    }, 1000)
  }, [loadDocuments])

  const handleSearch = async () => {
    if (!searchQuery.trim()) return
    setIsSearching(true)
    try { const { results } = await api.searchKnowledge(searchQuery); setSearchResults(results) }
    catch (e) { console.error(e) }
    finally { setIsSearching(false) }
  }

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setIsUploading(true)
    setProgress({ runId: '', filename: file.name, stage: 'loading', pct: 5, message: 'Uploading file...', status: 'processing' })

    try {
      const { run_id, filename } = await api.uploadDocumentWithProgress(file)
      saveActiveUpload(run_id, filename)
      setProgress(prev => prev ? { ...prev, runId: run_id } : null)
      pollProgress(run_id, filename)
    } catch (err) {
      console.error('Upload failed:', err)
      setProgress({ runId: '', filename: file.name, stage: 'failed', pct: 0, message: String(err), status: 'failed' })
      setIsUploading(false)
      clearActiveUpload()
      setTimeout(() => setProgress(null), 5000)
    } finally {
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const handleDelete = async (docId: string) => {
    if (!confirm('Delete this document?')) return
    setDeletingId(docId)
    try { await api.deleteDocument(docId); loadDocuments() }
    catch (err) { console.error('Delete failed:', err) }
    finally { setDeletingId(null) }
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="px-6 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-xl uppercase tracking-wider" style={{ color: 'var(--fg)', fontFamily: 'var(--font-display)' }}>Knowledge Base</h1>
          <p className="text-xs mt-0.5" style={{ color: 'var(--fg-muted)' }}>{documents.length} documents · {totalChunks} chunks</p>
        </div>
        <input ref={fileInputRef} type="file" onChange={handleUpload} className="hidden" accept=".txt,.md,.pdf,.html,.doc,.docx,.json,.csv" />
        <button onClick={() => fileInputRef.current?.click()} disabled={isUploading} className="btn-primary flex items-center gap-1.5 text-sm">
          {isUploading ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
          {isUploading ? 'Processing...' : 'Upload'}
        </button>
      </div>

      {/* Progress Bar */}
      {progress && (
        <div className="px-6 pb-3">
          <div className="p-3 rounded-lg" style={{ background: 'var(--surface-2)', border: '1px solid var(--border)' }}>
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                {progress.status === 'completed' ? (
                  <CheckCircle2 size={14} style={{ color: 'var(--green)' }} />
                ) : progress.status === 'failed' ? (
                  <XCircle size={14} style={{ color: 'var(--red)' }} />
                ) : (
                  <Loader2 size={14} className="animate-spin" style={{ color: 'var(--accent)' }} />
                )}
                <span className="text-xs font-medium truncate max-w-[200px]" style={{ color: 'var(--fg)' }}>
                  {progress.filename}
                </span>
              </div>
              <span className="text-xs font-mono" style={{ color: 'var(--fg-muted)' }}>
                {progress.status === 'completed' ? '100%' : `${progress.pct}%`}
              </span>
            </div>
            {/* Progress bar track */}
            <div className="h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--surface-1)' }}>
              <div
                className="h-full rounded-full transition-all duration-300 ease-out"
                style={{
                  width: `${progress.status === 'completed' ? 100 : progress.pct}%`,
                  background: progress.status === 'failed'
                    ? 'var(--red)'
                    : progress.status === 'completed'
                      ? 'var(--green)'
                      : 'var(--accent)',
                }}
              />
            </div>
            <p className="text-xs mt-1.5" style={{ color: 'var(--fg-faint)' }}>
              {progress.message || STAGE_LABELS[progress.stage] || progress.stage}
            </p>
          </div>
        </div>
      )}

      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
        {/* Search */}
        <div className="flex gap-2">
          <input value={searchQuery} onChange={e => setSearchQuery(e.target.value)} onKeyDown={e => e.key === 'Enter' && handleSearch()} placeholder="Search knowledge base..." className="input-field flex-1" />
          <button onClick={handleSearch} disabled={isSearching} className="btn-primary flex items-center gap-1.5 text-sm">
            <Search size={14} /> Search
          </button>
        </div>

        {/* Search Results */}
        {searchResults.length > 0 && (
          <div ref={(el) => { if (el) animateEntrance(Array.from(el.children), { stagger: 0.05 }) }} className="space-y-2">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-medium" style={{ color: 'var(--fg-secondary)' }}>Search Results</h2>
              <button onClick={() => setSearchResults([])} className="text-xs transition-colors" style={{ color: 'var(--fg-faint)' }} onMouseEnter={e => e.currentTarget.style.color = 'var(--fg-secondary)'} onMouseLeave={e => e.currentTarget.style.color = 'var(--fg-faint)'}>Clear</button>
            </div>
            {searchResults.map((r, i) => (
              <div key={i} className="p-3 rounded-lg" style={{ background: 'var(--surface-2)', border: '1px solid var(--border)' }}>
                <p className="text-xs mb-1 font-mono" style={{ color: 'var(--fg-faint)' }}>{r.source || 'Unknown source'}</p>
                <p className="text-sm" style={{ color: 'var(--fg)' }}>{r.text || JSON.stringify(r)}</p>
                {r.score != null && <p className="text-xs mt-1.5 font-mono" style={{ color: 'var(--accent)' }}>Score: {Number(r.score).toFixed(3)}</p>}
              </div>
            ))}
          </div>
        )}

        {/* Documents */}
        <div ref={(el) => { if (el && documents.length > 0) animateEntrance(Array.from(el.children), { stagger: 0.04 }) }} className="space-y-1.5">
          {documents.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 gap-3">
              <div className="w-12 h-12 rounded-lg flex items-center justify-center" style={{ background: 'var(--surface-2)' }}>
                <FileText size={24} style={{ color: 'var(--fg-faint)' }} />
              </div>
              <p className="text-sm" style={{ color: 'var(--fg-muted)' }}>No documents ingested yet</p>
              <p className="text-xs" style={{ color: 'var(--fg-faint)' }}>Upload files to build your knowledge base</p>
            </div>
          ) : (
            documents.map((doc, i) => {
              const docId = doc.document_id || doc.doc_id || doc.id || ''
              return (
                <div key={i} className="p-3 rounded-lg flex items-center justify-between group transition-colors" style={{ background: 'var(--surface-1)', border: '1px solid var(--border)' }} onMouseEnter={e => e.currentTarget.style.background = 'var(--surface-2)'} onMouseLeave={e => e.currentTarget.style.background = 'var(--surface-1)'}>
                  <div className="flex items-center gap-3 min-w-0">
                    <div className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0" style={{ background: 'var(--accent-muted)' }}>
                      <FileText size={14} style={{ color: 'var(--accent)' }} />
                    </div>
                    <div className="min-w-0">
                      <p className="text-sm truncate" style={{ color: 'var(--fg)' }}>{doc.source_uri?.split(/[/\\]/).pop() || doc.source_id || doc.filename || `Document ${i + 1}`}</p>
                      <p className="text-xs" style={{ color: 'var(--fg-faint)' }}>{doc.chunk_count ?? '?'} chunks</p>
                    </div>
                  </div>
                  {docId && (
                    <button onClick={() => handleDelete(docId)} disabled={deletingId === docId} className="p-1.5 rounded-lg transition-all opacity-0 group-hover:opacity-100" style={{ color: 'var(--fg-faint)' }} onMouseEnter={e => { e.currentTarget.style.color = 'var(--red)'; e.currentTarget.style.background = 'var(--red-muted)' }} onMouseLeave={e => { e.currentTarget.style.color = 'var(--fg-faint)'; e.currentTarget.style.background = 'transparent' }}>
                      {deletingId === docId ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
                    </button>
                  )}
                </div>
              )
            })
          )}
        </div>
      </div>
    </div>
  )
}
