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
  useEffect(() => { loadMemories() }, [])

  const handleSearch = async () => {
    if (!searchQuery.trim()) return
    setIsSearching(true)
    try { const { results } = await api.searchMemory(searchQuery); setSearchResults(results) }
    catch (e) { console.error(e) } finally { setIsSearching(false) }
  }
  const handleDelete = async (memoryId: string) => {
    setDeletingId(memoryId)
    try { await api.deleteMemory(memoryId); setLongMemories(prev => prev.filter(m => (m.id || m.memory_id) !== memoryId)); if (searchResults) setSearchResults(prev => prev?.filter(m => (m.id || m.memory_id) !== memoryId) ?? null) }
    catch (e) { console.error(e) } finally { setDeletingId(null) }
  }
  const handleClearAll = async () => {
    if (!confirm('Clear all long-term memories? This cannot be undone.')) return
    setIsClearing(true)
    try { await api.clearMemories(); setLongMemories([]); setSearchResults(null) }
    catch (e) { console.error(e) } finally { setIsClearing(false) }
  }

  const displayMemories = searchResults ?? longMemories

  return (
    <div className="flex-1 flex flex-col overflow-hidden p-5 gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold" style={{ color: 'var(--fg)' }}>Memory</h1>
          <p className="text-xs mt-0.5" style={{ color: 'var(--fg-muted)' }}>{longMemories.length} long-term · {shortMessages.length} short-term</p>
        </div>
        {tab === 'long' && (
          <button onClick={handleClearAll} disabled={isClearing || longMemories.length === 0} className="btn-ghost text-xs flex items-center gap-1" style={{ color: 'var(--red)' }}>
            {isClearing ? <Loader2 size={12} className="animate-spin" /> : 'Clear All'}
          </button>
        )}
      </div>

      {/* Tabs */}
      <div className="flex gap-1 p-1 rounded-lg w-fit" style={{ background: 'var(--surface-2)' }}>
        {(['long', 'short'] as const).map(t => (
          <button key={t} onClick={() => { setTab(t); if (t === 'long') setSearchResults(null) }} className="px-3 py-1.5 rounded-md text-sm font-medium transition-all duration-150" style={{ background: tab === t ? 'var(--accent)' : 'transparent', color: tab === t ? 'white' : 'var(--fg-muted)' }}>
            {t === 'long' ? `Long-term (${longMemories.length})` : `Short-term (${shortMessages.length})`}
          </button>
        ))}
      </div>

      {/* Search */}
      {tab === 'long' && (
        <div className="flex gap-2">
          <input value={searchQuery} onChange={e => setSearchQuery(e.target.value)} onKeyDown={e => e.key === 'Enter' && handleSearch()} placeholder="Search memories..." className="input-field flex-1" />
          <button onClick={handleSearch} disabled={isSearching} className="btn-primary flex items-center gap-1.5"><Search size={14} /> Search</button>
          {searchResults && <button onClick={() => { setSearchResults(null); setSearchQuery('') }} className="btn-ghost">Clear</button>}
        </div>
      )}

      {/* Content */}
      <div className="flex-1 overflow-y-auto space-y-1.5">
        {tab === 'long' ? (
          displayMemories.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full gap-3">
              <div className="w-12 h-12 rounded-xl flex items-center justify-center" style={{ background: 'var(--surface-3)' }}><Brain size={24} style={{ color: 'var(--fg-faint)' }} /></div>
              <p className="text-sm" style={{ color: 'var(--fg-muted)' }}>{searchResults ? 'No results found.' : 'No long-term memories.'}</p>
            </div>
          ) : displayMemories.map((m, i) => {
            const memId = m.id || m.memory_id || ''
            return (
              <div key={memId || i} className="surface-card p-3 group hover:border-[var(--border-strong)] transition-colors">
                <div className="flex items-center gap-2 mb-1.5">
                  <div className="w-5 h-5 rounded flex items-center justify-center" style={{ background: 'var(--purple-muted)' }}><Brain size={11} style={{ color: 'var(--purple)' }} /></div>
                  <span className="text-xs font-medium" style={{ color: 'var(--purple)' }}>{m.category || 'general'}</span>
                  {m.importance && <span className="text-xs" style={{ color: 'var(--amber)' }}>{'★'.repeat(Math.min(m.importance, 5))}</span>}
                  <div className="ml-auto flex items-center gap-2">
                    {m.score != null && <span className="text-xs font-mono" style={{ color: 'var(--fg-faint)' }}>{Number(m.score).toFixed(3)}</span>}
                    {memId && <button onClick={() => handleDelete(memId)} disabled={deletingId === memId} className="p-1 rounded transition-all opacity-0 group-hover:opacity-100" style={{ color: 'var(--fg-faint)' }} onMouseEnter={e => { e.currentTarget.style.color = 'var(--red)'; e.currentTarget.style.background = 'var(--red-muted)' }} onMouseLeave={e => { e.currentTarget.style.color = 'var(--fg-faint)'; e.currentTarget.style.background = 'transparent' }}>{deletingId === memId ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}</button>}
                  </div>
                </div>
                <p className="text-sm" style={{ color: 'var(--fg)' }}>{m.content || m.text || JSON.stringify(m)}</p>
              </div>
            )
          })
        ) : (
          shortMessages.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full gap-3">
              <div className="w-12 h-12 rounded-xl flex items-center justify-center" style={{ background: 'var(--surface-3)' }}><Brain size={24} style={{ color: 'var(--fg-faint)' }} /></div>
              <p className="text-sm" style={{ color: 'var(--fg-muted)' }}>No conversation history.</p>
            </div>
          ) : shortMessages.map((m, i) => (
            <div key={i} className="rounded-lg px-3 py-2 text-sm animate-slide-up" style={{ animationDelay: `${i * 20}ms`, background: m.role === 'user' ? 'var(--accent-subtle)' : 'var(--surface-2)', border: `1px solid ${m.role === 'user' ? 'var(--accent-muted)' : 'var(--border)'}`, marginLeft: m.role === 'user' ? '2rem' : 0, marginRight: m.role !== 'user' ? '2rem' : 0 }}>
              <p className="text-xs mb-1 font-medium" style={{ color: m.role === 'user' ? 'var(--accent)' : 'var(--fg-faint)' }}>{m.role}</p>
              <p style={{ color: 'var(--fg)' }}>{m.content}</p>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
