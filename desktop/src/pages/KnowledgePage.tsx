import { useState, useEffect, useRef } from 'react'
import { Search, FileText, Upload, Trash2, Loader2 } from 'lucide-react'
import { api } from '../services/api'

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

  useEffect(() => {
    loadDocuments()
  }, [])

  const handleSearch = async () => {
    if (!searchQuery.trim()) return
    setIsSearching(true)
    try {
      const { results } = await api.searchKnowledge(searchQuery)
      setSearchResults(results)
    } catch (e) {
      console.error(e)
    } finally {
      setIsSearching(false)
    }
  }

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setIsUploading(true)
    try {
      await api.uploadDocument(file)
      loadDocuments()
    } catch (err) {
      console.error('Upload failed:', err)
    } finally {
      setIsUploading(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const handleDelete = async (docId: string) => {
    if (!confirm('Delete this document?')) return
    setDeletingId(docId)
    try {
      await api.deleteDocument(docId)
      loadDocuments()
    } catch (err) {
      console.error('Delete failed:', err)
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden p-4 gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-text-primary">Knowledge Base</h1>
        <div className="flex items-center gap-2">
          <input
            ref={fileInputRef}
            type="file"
            onChange={handleUpload}
            className="hidden"
            accept=".txt,.md,.pdf,.html,.doc,.docx,.json,.csv"
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={isUploading}
            className="px-3 py-1.5 bg-accent-primary text-bg-primary rounded-md text-sm flex items-center gap-1 disabled:opacity-50"
          >
            {isUploading ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
            {isUploading ? 'Uploading...' : 'Upload'}
          </button>
        </div>
      </div>

      {/* Search */}
      <div className="flex gap-2">
        <input
          value={searchQuery}
          onChange={e => setSearchQuery(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleSearch()}
          placeholder="Search knowledge base..."
          className="flex-1 bg-bg-tertiary text-text-primary rounded-md px-3 py-2 text-sm border border-border-default focus:border-accent-primary focus:outline-none"
        />
        <button
          onClick={handleSearch}
          disabled={isSearching}
          className="px-3 py-2 bg-accent-primary text-bg-primary rounded-md text-sm flex items-center gap-1"
        >
          <Search size={14} />
          Search
        </button>
      </div>

      {/* Search Results */}
      {searchResults.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium text-text-secondary">Search Results</h2>
            <button
              onClick={() => setSearchResults([])}
              className="text-xs text-text-muted hover:text-text-primary"
            >
              Clear
            </button>
          </div>
          {searchResults.map((r, i) => (
            <div key={i} className="bg-bg-secondary rounded-md p-3 text-sm text-text-primary border border-border-default">
              <p className="text-text-muted text-xs mb-1">{r.source || 'Unknown source'}</p>
              <p>{r.text || JSON.stringify(r)}</p>
              {r.score != null && (
                <p className="text-text-muted text-xs mt-1">Score: {Number(r.score).toFixed(3)}</p>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Documents */}
      <div className="flex-1 overflow-y-auto space-y-2">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-medium text-text-secondary">
            Documents ({documents.length}) — {totalChunks} chunks
          </h2>
        </div>
        {documents.length === 0 ? (
          <p className="text-text-muted text-sm">No documents ingested yet.</p>
        ) : (
          documents.map((doc, i) => {
            const docId = doc.document_id || doc.doc_id || doc.id || ''
            return (
              <div key={i} className="bg-bg-secondary rounded-md p-3 flex items-center justify-between border border-border-default">
                <div className="flex items-center gap-2 min-w-0">
                  <FileText size={16} className="text-accent-secondary shrink-0" />
                  <div className="min-w-0">
                    <p className="text-sm text-text-primary truncate">
                      {doc.source_uri?.split(/[/\\]/).pop() || doc.source_id || doc.filename || doc.name || `Document ${i + 1}`}
                    </p>
                    <p className="text-xs text-text-muted">{doc.chunk_count ?? '?'} chunks</p>
                  </div>
                </div>
                {docId && (
                  <button
                    onClick={() => handleDelete(docId)}
                    disabled={deletingId === docId}
                    className="p-1.5 text-text-muted hover:text-status-error hover:bg-status-error/10 rounded transition-colors shrink-0 disabled:opacity-50"
                    title="Delete document"
                  >
                    {deletingId === docId ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
                  </button>
                )}
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
