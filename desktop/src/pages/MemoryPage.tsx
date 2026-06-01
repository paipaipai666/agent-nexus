import { useState, useEffect } from 'react'
import { Brain, Search, Trash2, Loader2, Zap, Sparkles } from 'lucide-react'
import { api } from '../services/api'

const CATEGORY_COLORS: Record<string, { bg: string; text: string; label: string }> = {
  fact: { bg: 'var(--purple-muted)', text: 'var(--purple)', label: 'Fact' },
  preference: { bg: 'var(--blue-muted)', text: 'var(--blue)', label: 'Preference' },
  note: { bg: 'var(--surface-3)', text: 'var(--fg-muted)', label: 'Note' },
  // Legacy names
  entity_fact: { bg: 'var(--purple-muted)', text: 'var(--purple)', label: 'Fact' },
  conclusion: { bg: 'var(--purple-muted)', text: 'var(--purple)', label: 'Fact' },
  user_preference: { bg: 'var(--blue-muted)', text: 'var(--blue)', label: 'Preference' },
  tool_preference: { bg: 'var(--blue-muted)', text: 'var(--blue)', label: 'Preference' },
  task_progress: { bg: 'var(--surface-3)', text: 'var(--fg-muted)', label: 'Note' },
  error_pattern: { bg: 'var(--surface-3)', text: 'var(--fg-muted)', label: 'Note' },
  conversation: { bg: 'var(--surface-3)', text: 'var(--fg-muted)', label: 'Note' },
}

function getCategoryStyle(cat: string) {
  return CATEGORY_COLORS[cat] || CATEGORY_COLORS.note
}

function ImportanceBar({ value, effective }: { value: number; effective?: number }) {
  const display = effective ?? value
  const pct = Math.round(display * 100)
  const color = pct >= 80 ? 'var(--green)' : pct >= 50 ? 'var(--amber)' : 'var(--fg-faint)'
  return (
    <div className="flex items-center gap-1.5" title={`Base: ${Math.round(value * 100)}% | Effective: ${pct}%`}>
      <div className="w-16 h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--surface-4)' }}>
        <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="text-2xs font-mono" style={{ color: 'var(--fg-faint)' }}>{pct}%</span>
    </div>
  )
}

export default function MemoryPage() {
  const [tab, setTab] = useState<'long' | 'short'>('long')
  const [longMemories, setLongMemories] = useState<any[]>([])
  const [shortMessages, setShortMessages] = useState<any[]>([])
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<any[] | null>(null)
  const [isSearching, setIsSearching] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [isClearing, setIsClearing] = useState(false)
  const [isReflecting, setIsReflecting] = useState(false)
  const [reflectResult, setReflectResult] = useState<string | null>(null)

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
  const handleReflect = async () => {
    setIsReflecting(true)
    setReflectResult(null)
    try {
      const r = await api.runReflection(7, 50)
      if (r.error || r.reason) {
        setReflectResult(r.error || r.reason || 'Unknown result')
      } else {
        setReflectResult(`Found ${r.patterns_found} patterns, saved ${r.patterns_saved} from ${r.memories_reviewed} memories`)
        loadMemories() // refresh list
      }
    } catch (e: any) {
      setReflectResult(`Error: ${e.message || 'Unknown error'}`)
    } finally { setIsReflecting(false) }
  }

  const displayMemories = searchResults ?? longMemories

  // Category counts
  const catCounts = longMemories.reduce((acc: Record<string, number>, m: any) => {
    const cat = m.category || 'unknown'
    acc[cat] = (acc[cat] || 0) + 1
    return acc
  }, {})

  return (
    <div className="flex-1 flex flex-col overflow-hidden p-5 gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold" style={{ color: 'var(--fg)' }}>Memory</h1>
          <p className="text-xs mt-0.5" style={{ color: 'var(--fg-muted)' }}>{longMemories.length} long-term · {shortMessages.length} short-term</p>
        </div>
        {tab === 'long' && (
          <div className="flex items-center gap-2">
            <button onClick={handleReflect} disabled={isReflecting || longMemories.length === 0} className="btn-ghost text-xs flex items-center gap-1" style={{ color: 'var(--cyan)' }}>
              {isReflecting ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
              Reflect
            </button>
            <button onClick={handleClearAll} disabled={isClearing || longMemories.length === 0} className="btn-ghost text-xs flex items-center gap-1" style={{ color: 'var(--red)' }}>
              {isClearing ? <Loader2 size={12} className="animate-spin" /> : 'Clear All'}
            </button>
          </div>
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

      {/* Category chips */}
      {tab === 'long' && longMemories.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(catCounts).map(([cat, count]) => {
            const style = getCategoryStyle(cat)
            return (
              <span key={cat} className="px-2 py-0.5 rounded-full text-2xs font-medium" style={{ background: style.bg, color: style.text }}>
                {style.label}: {count}
              </span>
            )
          })}
        </div>
      )}

      {/* Reflect result */}
      {reflectResult && (
        <div className="text-xs px-3 py-2 rounded-lg" style={{ background: 'var(--cyan-muted)', color: 'var(--cyan)' }}>
          {reflectResult}
          <button onClick={() => setReflectResult(null)} className="ml-2 underline">dismiss</button>
        </div>
      )}

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
            const catStyle = getCategoryStyle(m.category)
            return (
              <div key={memId || i} className="surface-card p-3 group hover:border-[var(--border-strong)] transition-colors">
                <div className="flex items-center gap-2 mb-1.5">
                  <span className="px-1.5 py-0.5 rounded text-2xs font-medium" style={{ background: catStyle.bg, color: catStyle.text }}>{catStyle.label}</span>
                  <ImportanceBar value={m.importance || 0.5} effective={m.effective_importance} />
                  {m.access_count > 0 && (
                    <span className="flex items-center gap-0.5 text-2xs" style={{ color: 'var(--fg-faint)' }} title={`${m.access_count} times accessed`}>
                      <Zap size={9} /> {m.access_count}
                    </span>
                  )}
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
