import { useState, useEffect } from 'react'
import { FileText, Search, Shield, Clock, AlertTriangle } from 'lucide-react'
import { api } from '../services/api'

interface AuditEntry {
  tool_name: string
  caller: string
  params: string
  result_summary: string
  duration_ms: number
  hitl_triggered: boolean
  error: string | null
  timestamp: number
  risk_level?: string
  schema_validation?: string
}

const riskColors: Record<string, string> = {
  low: 'var(--fg-faint)',
  medium: 'var(--amber)',
  high: 'var(--red)',
}

export default function AuditPage() {
  const [entries, setEntries] = useState<AuditEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [limit, setLimit] = useState(50)
  const [toolFilter, setToolFilter] = useState('')
  const [searchQuery, setSearchQuery] = useState('')

  useEffect(() => {
    setLoading(true)
    api.getAudit(limit, toolFilter || undefined)
      .then(({ entries: data }) => setEntries(data || []))
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [limit, toolFilter])

  const filtered = entries.filter(e => {
    if (!searchQuery) return true
    const q = searchQuery.toLowerCase()
    return (
      e.tool_name.toLowerCase().includes(q) ||
      e.caller.toLowerCase().includes(q) ||
      e.params.toLowerCase().includes(q) ||
      (e.error && e.error.toLowerCase().includes(q))
    )
  })

  const formatTime = (ts: number) => {
    const d = new Date(ts * 1000)
    return d.toLocaleString()
  }

  const uniqueTools = [...new Set(entries.map(e => e.tool_name))].sort()

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="px-6 py-4 flex items-center justify-between" style={{ borderBottom: '1px solid var(--border)' }}>
        <div>
          <h1 className="text-lg font-semibold" style={{ color: 'var(--fg)' }}>Audit Log</h1>
          <p className="text-xs mt-0.5" style={{ color: 'var(--fg-muted)' }}>Tool call audit trail with risk levels</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2" style={{ color: 'var(--fg-faint)' }} />
            <input
              type="text"
              placeholder="Search..."
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              className="input-field text-xs pl-7 w-40"
            />
          </div>
          <select value={toolFilter} onChange={e => setToolFilter(e.target.value)} className="input-field text-xs">
            <option value="">All tools</option>
            {uniqueTools.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
          <select value={limit} onChange={e => setLimit(Number(e.target.value))} className="input-field text-xs">
            <option value={20}>20</option>
            <option value={50}>50</option>
            <option value={100}>100</option>
          </select>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
        {/* Summary */}
        <div className="grid grid-cols-4 gap-3">
          {[
            { label: 'Total Calls', value: entries.length, icon: FileText, color: 'var(--accent)' },
            { label: 'Errors', value: entries.filter(e => e.error).length, icon: AlertTriangle, color: 'var(--red)' },
            { label: 'HITL Triggered', value: entries.filter(e => e.hitl_triggered).length, icon: Shield, color: 'var(--amber)' },
            { label: 'Avg Duration', value: entries.length ? `${Math.round(entries.reduce((s, e) => s + e.duration_ms, 0) / entries.length)}ms` : '-', icon: Clock, color: 'var(--cyan)' },
          ].map(({ label, value, icon: Icon, color }) => (
            <div key={label} className="p-3 rounded-lg" style={{ background: 'var(--surface-1)', border: '1px solid var(--border)' }}>
              <div className="flex items-center gap-2 mb-1">
                <Icon size={12} style={{ color }} />
                <span className="text-xs" style={{ color: 'var(--fg-muted)' }}>{label}</span>
              </div>
              <p className="text-lg font-semibold" style={{ color: 'var(--fg)' }}>{value}</p>
            </div>
          ))}
        </div>

        {/* Entry List */}
        {loading ? (
          <div className="flex items-center justify-center py-12">
            <div className="w-5 h-5 border-2 border-t-transparent rounded-full animate-spin" style={{ borderColor: 'var(--fg-faint)', borderTopColor: 'transparent' }} />
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 gap-3">
            <FileText size={32} style={{ color: 'var(--fg-faint)' }} />
            <p className="text-sm" style={{ color: 'var(--fg-muted)' }}>No audit entries found</p>
          </div>
        ) : (
          <div className="space-y-1.5">
            {filtered.map((entry, i) => {
              const isError = !!entry.error
              const riskColor = riskColors[entry.risk_level || 'low'] || 'var(--fg-faint)'
              return (
                <div
                  key={i}
                  className="px-3.5 py-3 rounded-lg"
                  style={{ background: isError ? 'var(--red-muted)' : entry.hitl_triggered ? 'var(--amber-muted)' : 'var(--surface-1)', border: '1px solid var(--border)' }}
                >
                  <div className="flex items-center gap-2 mb-1.5">
                    <span className="text-xs font-mono font-medium px-1.5 py-0.5 rounded" style={{ background: 'var(--surface-3)', color: 'var(--accent)' }}>
                      {entry.tool_name}
                    </span>
                    <span className="text-xs" style={{ color: 'var(--fg-faint)' }}>by {entry.caller}</span>
                    {entry.risk_level && entry.risk_level !== 'low' && (
                      <span className="text-xs px-1.5 py-0.5 rounded" style={{ background: 'var(--surface-3)', color: riskColor }}>
                        {entry.risk_level}
                      </span>
                    )}
                    {entry.hitl_triggered && (
                      <span className="text-xs px-1.5 py-0.5 rounded" style={{ background: 'var(--amber-muted)', color: 'var(--amber)' }}>HITL</span>
                    )}
                    {entry.schema_validation === 'failed' && (
                      <span className="text-xs px-1.5 py-0.5 rounded" style={{ background: 'var(--red-muted)', color: 'var(--red)' }}>schema fail</span>
                    )}
                    <span className="text-xs ml-auto font-mono" style={{ color: 'var(--fg-faint)' }}>
                      {entry.duration_ms.toFixed(0)}ms
                    </span>
                    <span className="text-xs" style={{ color: 'var(--fg-faint)' }}>{formatTime(entry.timestamp)}</span>
                  </div>

                  <div className="flex items-start gap-4 text-xs" style={{ color: 'var(--fg-muted)' }}>
                    <div className="flex-1 min-w-0">
                      <span className="font-medium" style={{ color: 'var(--fg-faint)' }}>Params: </span>
                      <span className="font-mono break-all">{entry.params || '(none)'}</span>
                    </div>
                    <div className="flex-1 min-w-0">
                      <span className="font-medium" style={{ color: 'var(--fg-faint)' }}>Result: </span>
                      <span className="font-mono break-all">{entry.result_summary || '(none)'}</span>
                    </div>
                  </div>

                  {isError && (
                    <p className="text-xs mt-1.5 font-mono" style={{ color: 'var(--red)' }}>Error: {entry.error}</p>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
