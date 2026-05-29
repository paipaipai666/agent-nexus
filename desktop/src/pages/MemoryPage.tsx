import { useState, useEffect } from 'react'
import { Brain } from 'lucide-react'
import { api } from '../services/api'

export default function MemoryPage() {
  const [tab, setTab] = useState<'short' | 'long'>('long')
  const [longMemories, setLongMemories] = useState<any[]>([])
  const [shortMessages, setShortMessages] = useState<any[]>([])

  useEffect(() => {
    api.listMemories(50).then(({ memories }) => setLongMemories(memories)).catch(console.error)
    api.listShortMemories().then(({ messages }) => setShortMessages(messages)).catch(console.error)
  }, [])

  return (
    <div className="flex-1 flex flex-col overflow-hidden p-4 gap-4">
      <h1 className="text-lg font-semibold text-text-primary">Memory</h1>

      {/* Tabs */}
      <div className="flex gap-1 bg-bg-tertiary rounded-lg p-1 w-fit">
        <button
          onClick={() => setTab('long')}
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

      {/* Content */}
      <div className="flex-1 overflow-y-auto space-y-2">
        {tab === 'long' ? (
          longMemories.length === 0 ? (
            <p className="text-text-muted text-sm">No long-term memories.</p>
          ) : (
            longMemories.map((m, i) => (
              <div key={i} className="bg-bg-secondary rounded-md p-3 border border-border-default">
                <div className="flex items-center gap-2 mb-1">
                  <Brain size={14} className="text-accent-purple" />
                  <span className="text-xs text-accent-purple">{m.category || 'general'}</span>
                  <span className="text-xs text-text-muted ml-auto">
                    {m.importance ? `★ ${m.importance}` : ''}
                  </span>
                </div>
                <p className="text-sm text-text-primary">{m.content || m.text || JSON.stringify(m)}</p>
              </div>
            ))
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
