import { useState, useEffect, useRef } from 'react'
import { Search, FileText, Upload, Trash2, Loader2 } from 'lucide-react'
import { api } from '../services/api'
import { animateEntrance } from '../utils/animations'

export default function KnowledgePage() {
  const [documents, setDocuments] = useState<any[]>([])
  const [totalChunks, setTotalChunks] = useState(0)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<any[]>([])
  const [isSearching, setIsSearching] = useState(false)
  const [isUploading, setIsUploading] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const loadDocuments = () => {
    api.listDocuments().then(({ documents, total_chunks }) => {
      setDocuments(documents)
      setTotalChunks(total_chunks)
    }).catch(console.error)
  }

  useEffect(() => { loadDocuments() }, [])

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
    try { await api.uploadDocument(file); loadDocuments() }
    catch (err) { console.error('Upload failed:', err) }
    finally { setIsUploading(false); if (fileInputRef.current) fileInputRef.current.value = '' }
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
          {isUploading ? 'Uploading...' : 'Upload'}
        </button>
      </div>

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
