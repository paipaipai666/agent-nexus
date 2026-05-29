import { useState, useEffect } from 'react'
import { Search, FileText } from 'lucide-react'
import { api } from '../services/api'

export default function KnowledgePage() {
  const [documents, setDocuments] = useState<any[]>([])
  const [totalChunks, setTotalChunks] = useState(0)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<any[]>([])
  const [isSearching, setIsSearching] = useState(false)

  useEffect(() => {
    api.listDocuments().then(({ documents, total_chunks }) => {
      setDocuments(documents)
      setTotalChunks(total_chunks)
    }).catch(console.error)
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

  return (
    <div className="flex-1 flex flex-col overflow-hidden p-4 gap-4">
      <h1 className="text-lg font-semibold text-text-primary">Knowledge Base</h1>

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
          <h2 className="text-sm font-medium text-text-secondary">Search Results</h2>
          {searchResults.map((r, i) => (
            <div key={i} className="bg-bg-secondary rounded-md p-3 text-sm text-text-primary border border-border-default">
              <p className="text-text-muted text-xs mb-1">{r.source || 'Unknown source'}</p>
              <p>{r.text || JSON.stringify(r)}</p>
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
          documents.map((doc, i) => (
            <div key={i} className="bg-bg-secondary rounded-md p-3 flex items-center justify-between border border-border-default">
              <div className="flex items-center gap-2">
                <FileText size={16} className="text-accent-secondary" />
                <div>
                  <p className="text-sm text-text-primary">{doc.filename || doc.name || `Document ${i + 1}`}</p>
                  <p className="text-xs text-text-muted">{doc.chunk_count || '?'} chunks</p>
                </div>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
