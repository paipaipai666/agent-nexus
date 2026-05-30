import { useState, useEffect } from 'react'
import { Server, RefreshCw, Loader2, AlertTriangle, Wrench, Package, FileText } from 'lucide-react'
import { api } from '../services/api'

interface MCPServer {
  name: string
  transport: string
  state: string
  connected: boolean
  tool_names: string[]
  resource_tool_names: string[]
  prompt_tool_names: string[]
  resource_count: number
  resource_template_count: number
  prompt_count: number
  reconnect_attempts: number
  last_ping_ms: number | null
}

export default function MCPPage() {
  const [servers, setServers] = useState<MCPServer[]>([])
  const [loading, setLoading] = useState(true)
  const [actionLoading, setActionLoading] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [expandedServer, setExpandedServer] = useState<string | null>(null)

  const loadStatus = async () => {
    try {
      const data = await api.getMcpStatus()
      setServers(data.servers || [])
      setError(null)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
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
    } catch (e: any) {
      setError(e.message)
    } finally {
      setActionLoading(null)
    }
  }

  if (loading) {
    return <div className="flex-1 flex items-center justify-center text-text-muted">Loading MCP status...</div>
  }

  const connected = servers.filter(s => s.connected).length
  const totalTools = servers.reduce((sum, s) => sum + s.tool_names.length, 0)
  const failures = servers.filter(s => s.state !== 'healthy').length

  return (
    <div className="flex-1 flex flex-col overflow-hidden p-4 gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-text-primary">MCP Servers</h1>
        <div className="flex items-center gap-2">
          {failures > 0 && (
            <button
              onClick={() => handleAction('retry')}
              disabled={!!actionLoading}
              className="px-3 py-1.5 bg-status-warning/20 text-status-warning rounded-md text-sm flex items-center gap-1 hover:bg-status-warning/30"
            >
              {actionLoading === 'retry-all' ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
              Retry Failed
            </button>
          )}
          <button
            onClick={() => handleAction('reload')}
            disabled={!!actionLoading}
            className="px-3 py-1.5 bg-accent-primary text-bg-primary rounded-md text-sm flex items-center gap-1"
          >
            {actionLoading === 'reload-all' ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
            Reload All
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-status-error/10 border border-status-error/30 text-status-error text-sm rounded-md px-3 py-2">
          {error}
        </div>
      )}

      {/* Summary Cards */}
      <div className="grid grid-cols-4 gap-3">
        {[
          { label: 'Servers', value: `${connected}/${servers.length}`, icon: Server },
          { label: 'Tools', value: totalTools, icon: Wrench },
          { label: 'Resources', value: servers.reduce((s, sv) => s + sv.resource_count, 0), icon: Package },
          { label: 'Prompts', value: servers.reduce((s, sv) => s + sv.prompt_count, 0), icon: FileText },
        ].map(({ label, value, icon: Icon }) => (
          <div key={label} className="bg-bg-secondary rounded-lg p-3 border border-border-default">
            <div className="flex items-center gap-1 mb-1">
              <Icon size={14} className="text-accent-secondary" />
              <span className="text-xs text-text-muted">{label}</span>
            </div>
            <p className="text-lg font-semibold text-text-primary">{value}</p>
          </div>
        ))}
      </div>

      {/* Server List */}
      <div className="flex-1 overflow-y-auto space-y-3">
        {servers.length === 0 ? (
          <p className="text-text-muted text-sm">No MCP servers configured.</p>
        ) : (
          servers.map(server => (
            <div key={server.name} className="bg-bg-secondary rounded-lg border border-border-default">
              <div className="p-4 flex items-center justify-between">
                <div className="flex items-center gap-3 min-w-0">
                  <div className={`w-2.5 h-2.5 rounded-full shrink-0 ${
                    server.connected ? 'bg-status-success' : server.state === 'healthy' ? 'bg-status-warning' : 'bg-status-error'
                  }`} />
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <h3 className="text-sm font-medium text-text-primary truncate">{server.name}</h3>
                      <span className="text-xs px-1.5 py-0.5 rounded bg-bg-tertiary text-text-muted font-mono">{server.transport}</span>
                    </div>
                    <p className="text-xs text-text-muted mt-0.5">
                      {server.tool_names.length} tools · {server.resource_count} resources · {server.prompt_count} prompts
                      {server.last_ping_ms != null && ` · ${server.last_ping_ms}ms`}
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <button
                    onClick={() => setExpandedServer(expandedServer === server.name ? null : server.name)}
                    className="px-2 py-1 text-xs text-text-muted hover:text-text-primary hover:bg-bg-tertiary rounded transition-colors"
                  >
                    {expandedServer === server.name ? 'Hide' : 'Details'}
                  </button>
                  <button
                    onClick={() => handleAction(server.connected ? 'disable' : 'enable', server.name)}
                    disabled={!!actionLoading}
                    className={`px-2 py-1 text-xs rounded transition-colors ${
                      server.connected
                        ? 'bg-status-error/20 text-status-error hover:bg-status-error/30'
                        : 'bg-status-success/20 text-status-success hover:bg-status-success/30'
                    }`}
                  >
                    {actionLoading?.includes(server.name) ? (
                      <Loader2 size={12} className="animate-spin" />
                    ) : server.connected ? (
                      'Disable'
                    ) : (
                      'Enable'
                    )}
                  </button>
                </div>
              </div>

              {/* Expanded Details */}
              {expandedServer === server.name && (
                <div className="border-t border-border-default p-4 space-y-3">
                  {server.state !== 'healthy' && (
                    <div className="flex items-center gap-2 text-status-warning text-xs">
                      <AlertTriangle size={12} />
                      <span>State: {server.state} (reconnect attempts: {server.reconnect_attempts})</span>
                    </div>
                  )}

                  {server.tool_names.length > 0 && (
                    <div>
                      <h4 className="text-xs font-medium text-text-secondary mb-1 flex items-center gap-1">
                        <Wrench size={11} /> Tools ({server.tool_names.length})
                      </h4>
                      <div className="flex flex-wrap gap-1">
                        {server.tool_names.map(t => (
                          <span key={t} className="text-xs px-1.5 py-0.5 bg-accent-primary/10 text-accent-primary rounded font-mono">{t}</span>
                        ))}
                      </div>
                    </div>
                  )}

                  {server.resource_tool_names.length > 0 && (
                    <div>
                      <h4 className="text-xs font-medium text-text-secondary mb-1 flex items-center gap-1">
                        <Package size={11} /> Resource Tools ({server.resource_tool_names.length})
                      </h4>
                      <div className="flex flex-wrap gap-1">
                        {server.resource_tool_names.map(t => (
                          <span key={t} className="text-xs px-1.5 py-0.5 bg-accent-purple/10 text-accent-purple rounded font-mono">{t}</span>
                        ))}
                      </div>
                    </div>
                  )}

                  {server.prompt_tool_names.length > 0 && (
                    <div>
                      <h4 className="text-xs font-medium text-text-secondary mb-1 flex items-center gap-1">
                        <FileText size={11} /> Prompt Tools ({server.prompt_tool_names.length})
                      </h4>
                      <div className="flex flex-wrap gap-1">
                        {server.prompt_tool_names.map(t => (
                          <span key={t} className="text-xs px-1.5 py-0.5 bg-status-warning/10 text-status-warning rounded font-mono">{t}</span>
                        ))}
                      </div>
                    </div>
                  )}

                  <button
                    onClick={() => handleAction('reload', server.name)}
                    disabled={!!actionLoading}
                    className="px-2 py-1 text-xs bg-bg-tertiary text-text-secondary rounded hover:bg-bg-secondary transition-colors flex items-center gap-1"
                  >
                    <RefreshCw size={11} /> Reload Server
                  </button>
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  )
}
