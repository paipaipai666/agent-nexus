import { useState, useEffect } from 'react'
import { Server, RefreshCw, Loader2, AlertTriangle, Wrench, Package, FileText } from 'lucide-react'
import { api } from '../services/api'
import { animateEntrance } from '../utils/animations'

interface MCPServer {
  name: string; transport: string; state: string; connected: boolean
  tool_names: string[]; resource_tool_names: string[]; prompt_tool_names: string[]
  resource_count: number; resource_template_count: number; prompt_count: number
  reconnect_attempts: number; last_ping_ms: number | null
}

export default function MCPPage() {
  const [servers, setServers] = useState<MCPServer[]>([])
  const [loading, setLoading] = useState(true)
  const [actionLoading, setActionLoading] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [expandedServer, setExpandedServer] = useState<string | null>(null)

  const loadStatus = async () => {
    try { const data = await api.getMcpStatus(); setServers(data.servers || []); setError(null) }
    catch (e: any) { setError(e.message) }
    finally { setLoading(false) }
  }

  useEffect(() => { loadStatus() }, [])

  const handleAction = async (action: string, serverName?: string) => {
    setActionLoading(`${action}-${serverName || 'all'}`)
    try {
      if (action === 'enable' && serverName) await api.enableMcpServer(serverName)
      else if (action === 'disable' && serverName) await api.disableMcpServer(serverName)
      else if (action === 'reload') await api.reloadMcp(serverName)
      else if (action === 'retry') await api.retryMcp(serverName)
      await loadStatus()
    } catch (e: any) { setError(e.message) }
    finally { setActionLoading(null) }
  }

  if (loading) return <div className="flex-1 flex items-center justify-center"><div className="w-6 h-6 border-2 border-t-transparent rounded-full animate-spin" style={{ borderColor: 'var(--fg-faint)', borderTopColor: 'transparent' }} /></div>

  const connected = servers.filter(s => s.connected).length
  const totalTools = servers.reduce((sum, s) => sum + s.tool_names.length, 0)
  const failures = servers.filter(s => s.state !== 'healthy').length

  return (
    <div className="flex-1 flex flex-col overflow-hidden p-5 gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold" style={{ color: 'var(--fg)' }}>MCP Servers</h1>
          <p className="text-xs mt-0.5" style={{ color: 'var(--fg-muted)' }}>{connected} connected · {totalTools} tools</p>
        </div>
        <div className="flex items-center gap-2">
          {failures > 0 && (
            <button onClick={() => handleAction('retry')} disabled={!!actionLoading} className="btn-ghost flex items-center gap-1.5" style={{ color: 'var(--amber)', borderColor: 'var(--amber-muted)' }}>
              <RefreshCw size={14} /> Retry Failed
            </button>
          )}
          <button onClick={() => handleAction('reload')} disabled={!!actionLoading} className="btn-primary flex items-center gap-1.5">
            <RefreshCw size={14} /> Reload All
          </button>
        </div>
      </div>

      {error && <div className="rounded-lg px-3 py-2 text-sm" style={{ background: 'var(--red-muted)', color: 'var(--red)', border: '1px solid rgba(239,68,68,0.2)' }}>{error}</div>}

      {/* Summary Cards */}
      <div ref={(el) => { if (el) animateEntrance(Array.from(el.children), { stagger: 0.08 }) }} className="grid grid-cols-4 gap-3">
        {[
          { label: 'Servers', value: `${connected}/${servers.length}`, icon: Server, color: 'var(--green)' },
          { label: 'Tools', value: totalTools, icon: Wrench, color: 'var(--accent)' },
          { label: 'Resources', value: servers.reduce((s, sv) => s + sv.resource_count, 0), icon: Package, color: 'var(--blue)' },
          { label: 'Prompts', value: servers.reduce((s, sv) => s + sv.prompt_count, 0), icon: FileText, color: 'var(--purple)' },
        ].map(({ label, value, icon: Icon, color }) => (
          <div key={label} className="surface-card p-3.5">
            <div className="flex items-center gap-2 mb-1.5">
              <div className="w-6 h-6 rounded-md flex items-center justify-center" style={{ background: `${color}15` }}>
                <Icon size={12} style={{ color }} />
              </div>
              <span className="text-xs" style={{ color: 'var(--fg-muted)' }}>{label}</span>
            </div>
            <p className="text-xl font-semibold" style={{ color: 'var(--fg)' }}>{value}</p>
          </div>
        ))}
      </div>

      {/* Server List */}
      <div className="flex-1 overflow-y-auto space-y-2">
        {servers.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full gap-3">
            <div className="w-12 h-12 rounded-xl flex items-center justify-center" style={{ background: 'var(--surface-3)' }}><Server size={24} style={{ color: 'var(--fg-faint)' }} /></div>
            <p className="text-sm" style={{ color: 'var(--fg-muted)' }}>No MCP servers configured</p>
          </div>
        ) : servers.map(server => (
          <div key={server.name} className="surface-card overflow-hidden">
            <div className="p-4 flex items-center justify-between">
              <div className="flex items-center gap-3 min-w-0">
                <div className="w-2 h-2 rounded-full shrink-0" style={{ background: server.connected ? 'var(--green)' : server.state === 'healthy' ? 'var(--amber)' : 'var(--red)', boxShadow: server.connected ? '0 0 8px var(--green-muted)' : 'none' }} />
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <h3 className="text-sm font-medium truncate" style={{ color: 'var(--fg)' }}>{server.name}</h3>
                    <span className="text-xs px-1.5 py-0.5 rounded font-mono" style={{ background: 'var(--surface-3)', color: 'var(--fg-faint)' }}>{server.transport}</span>
                  </div>
                  <p className="text-xs mt-0.5" style={{ color: 'var(--fg-faint)' }}>{server.tool_names.length} tools · {server.resource_count} resources · {server.prompt_count} prompts{server.last_ping_ms != null && ` · ${server.last_ping_ms}ms`}</p>
                </div>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <button onClick={() => setExpandedServer(expandedServer === server.name ? null : server.name)} className="px-2.5 py-1 text-xs rounded-md transition-colors" style={{ color: 'var(--fg-muted)' }} onMouseEnter={e => { e.currentTarget.style.background = 'var(--surface-3)'; e.currentTarget.style.color = 'var(--fg)' }} onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--fg-muted)' }}>{expandedServer === server.name ? 'Hide' : 'Details'}</button>
                <button onClick={() => handleAction(server.connected ? 'disable' : 'enable', server.name)} disabled={!!actionLoading} className="px-2.5 py-1 text-xs rounded-md transition-colors" style={{ background: server.connected ? 'var(--red-muted)' : 'var(--green-muted)', color: server.connected ? 'var(--red)' : 'var(--green)' }}>
                  {actionLoading?.includes(server.name) ? <Loader2 size={12} className="animate-spin" /> : server.connected ? 'Disable' : 'Enable'}
                </button>
              </div>
            </div>
            {expandedServer === server.name && (
              <div className="px-4 pb-4 space-y-3 animate-slide-up" style={{ borderTop: '1px solid var(--border)' }}>
                {server.state !== 'healthy' && (
                  <div className="flex items-center gap-2 text-xs mt-3" style={{ color: 'var(--amber)' }}><AlertTriangle size={12} /><span>State: {server.state} (reconnects: {server.reconnect_attempts})</span></div>
                )}
                {server.tool_names.length > 0 && (
                  <div>
                    <h4 className="text-xs font-medium mb-1.5 flex items-center gap-1" style={{ color: 'var(--fg-muted)' }}><Wrench size={11} /> Tools ({server.tool_names.length})</h4>
                    <div className="flex flex-wrap gap-1">{server.tool_names.map(t => <span key={t} className="text-xs px-1.5 py-0.5 rounded font-mono" style={{ background: 'var(--accent-subtle)', color: 'var(--accent)' }}>{t}</span>)}</div>
                  </div>
                )}
                <button onClick={() => handleAction('reload', server.name)} disabled={!!actionLoading} className="btn-ghost text-xs flex items-center gap-1"><RefreshCw size={11} /> Reload</button>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
