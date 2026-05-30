import { useState, useEffect } from 'react'
import { Brain, Search, Trash2, Loader2 } from 'lucide-react'
import { api } from '../services/api'

export default function MemoryPage() {
  const [tab, setTab] = useState<'short' | 'long'>('long')
  const [longMemories, setLongMemories] = useState<any[]>([])
  const [shortMessages, setShortMessages] = useState<any[]>([])
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<any[] | null>(null)
  const [isSearching, setIsSearching] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [isClearing, setIsClearing] = useState(false)

  const loadMemories = () => {
    api.listMemories(50).then(({ memories }) => setLongMemories(memories)).catch(console.error)
    api.listShortMemories().then(({ messages }) => setShortMessages(messages)).catch(console.error)
  }

  useEffect(() => {
    loadMemories()
  }, [])

  const handleSearch = async () => {
    if (!searchQuery.trim()) return
    setIsSearching(true)
    try {
      const { results } = await api.searchMemory(searchQuery)
      setSearchResults(results)
    } catch (e) {
      console.error(e)
    } finally {
      setIsSearching(false)
    }
  }

  const handleDelete = async (memoryId: string) => {
    setDeletingId(memoryId)
    try {
      await api.deleteMemory(memoryId)
      setLongMemories(prev => prev.filter(m => (m.id || m.memory_id) !== memoryId))
      if (searchResults) {
        setSearchResults(prev => prev?.filter(m => (m.id || m.memory_id) !== memoryId) ?? null)
      }
    } catch (e) {
      console.error(e)
    } finally {
      setDeletingId(null)
    }
  }

  const handleClearAll = async () => {
    if (!confirm('Clear all long-term memories? This cannot be undone.')) return
    setIsClearing(true)
    try {
      await api.clearMemories()
      setLongMemories([])
      setSearchResults(null)
    } catch (e) {
      console.error(e)
    } finally {
      setIsClearing(false)
    }
  }

  const displayMemories = searchResults ?? longMemories

  return (
    <div className="flex-1 flex flex-col overflow-hidden p-4 gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-text-primary">Memory</h1>
        {tab === 'long' && (
          <button
            onClick={handleClearAll}
            disabled={isClearing || longMemories.length === 0}
            className="px-2 py-1 text-xs text-text-muted hover:text-status-error hover:bg-status-error/10 rounded transition-colors disabled:opacity-50"
          >
            {isClearing ? <Loader2 size={12} className="animate-spin" /> : 'Clear All'}
          </button>
        )}
      </div>

      {/* Tabs */}
      <div className="flex gap-1 bg-bg-tertiary rounded-lg p-1 w-fit">
        <button
          onClick={() => { setTab('long'); setSearchResults(null) }}
          className={`px-3 py-1 rounded-md text-sm transition-colors ${
            tab === 'long' ? 'bg-accent-primary text-bg-primary' : 'text-text-muted hover:text-text-primary'
          }`}
        >
          Long-term ({longMemories.length})
        </button>
        <button
          onClick={() => setTab('short')}
          className={`px-3 py-1 rounded-md text-sm transition-colors ${
            tab === 'short' ? 'bg-accent-primary text-bg-primary' : 'text-text-muted hover:text-text-primary'
          }`}
        >
          Short-term ({shortMessages.length})
        </button>
      </div>

      {/* Search (long-term only) */}
      {tab === 'long' && (
        <div className="flex gap-2">
          <input
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleSearch()}
            placeholder="Search memories..."
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
          {searchResults && (
            <button
              onClick={() => { setSearchResults(null); setSearchQuery('') }}
              className="px-3 py-2 bg-bg-tertiary text-text-secondary rounded-md text-sm hover:bg-bg-secondary"
            >
              Clear
            </button>
          )}
        </div>
      )}

      {/* Content */}
      <div className="flex-1 overflow-y-auto space-y-2">
        {tab === 'long' ? (
          displayMemories.length === 0 ? (
            <p className="text-text-muted text-sm">
              {searchResults ? 'No results found.' : 'No long-term memories.'}
            </p>
          ) : (
            displayMemories.map((m, i) => {
              const memId = m.id || m.memory_id || ''
              return (
                <div key={memId || i} className="bg-bg-secondary rounded-md p-3 border border-border-default group">
                  <div className="flex items-center gap-2 mb-1">
                    <Brain size={14} className="text-accent-purple" />
                    <span className="text-xs text-accent-purple">{m.category || 'general'}</span>
                    {m.importance && (
                      <span className="text-xs text-text-muted">{'★'.repeat(Math.min(m.importance, 5))}</span>
                    )}
                    <div className="ml-auto flex items-center gap-2">
                      {m.score != null && (
                        <span className="text-xs text-text-muted">Score: {Number(m.score).toFixed(3)}</span>
                      )}
                      {memId && (
                        <button
                          onClick={() => handleDelete(memId)}
                          disabled={deletingId === memId}
                          className="p-1 text-text-muted hover:text-status-error hover:bg-status-error/10 rounded transition-colors opacity-0 group-hover:opacity-100"
                          title="Delete memory"
                        >
                          {deletingId === memId ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
                        </button>
                      )}
                    </div>
                  </div>
                  <p className="text-sm text-text-primary">{m.content || m.text || JSON.stringify(m)}</p>
                </div>
              )
            })
          )
        ) : (
          shortMessages.length === 0 ? (
            <p className="text-text-muted text-sm">No conversation history.</p>
          ) : (
            shortMessages.map((m, i) => (
              <div key={i} className={`rounded-md p-3 text-sm border ${
                m.role === 'user'
                  ? 'bg-accent-primary/10 border-accent-primary/20 ml-8'
                  : 'bg-bg-secondary border-border-default mr-8'
              }`}>
                <p className="text-xs text-text-muted mb-1">{m.role}</p>
                <p className="text-text-primary">{m.content}</p>
              </div>
            ))
          )
        )}
      </div>
    </div>
  )
}
