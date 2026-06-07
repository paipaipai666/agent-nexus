import { useState, useEffect } from 'react'
import { Heart, CheckCircle, AlertTriangle, XCircle, Server, Brain, HardDrive, Database, RefreshCw } from 'lucide-react'
import { api } from '../services/api'

interface HealthCheck {
  status: string
  detail?: string
  model?: string
  free_gb?: number
  total_gb?: number
  used_pct?: number
  total?: number
  healthy?: number
  degraded?: number
  failed?: number
  memory_count?: number | string
  path?: string
}

interface HealthResult {
  status: string
  checks: Record<string, HealthCheck>
  uptime_seconds: number
  timestamp: number
}

const checkIcons: Record<string, typeof Heart> = {
  llm: Brain,
  mcp: Server,
  memory: Database,
  traces_dir: HardDrive,
  disk_space: HardDrive,
}

const checkLabels: Record<string, string> = {
  llm: 'LLM Provider',
  mcp: 'MCP Servers',
  memory: 'Memory Store',
  traces_dir: 'Traces Directory',
  disk_space: 'Disk Space',
}

function StatusIcon({ status, size = 16 }: { status: string; size?: number }) {
  if (status === 'ok') return <CheckCircle size={size} style={{ color: 'var(--green)' }} />
  if (status === 'degraded') return <AlertTriangle size={size} style={{ color: 'var(--amber)' }} />
  return <XCircle size={size} style={{ color: 'var(--red)' }} />
}

export default function HealthPage() {
  const [health, setHealth] = useState<HealthResult | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const loadHealth = () => {
    setLoading(true)
    setError(null)
    api.getHealth()
      .then(setHealth)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }

  useEffect(() => { loadHealth() }, [])

  const formatUptime = (seconds: number) => {
    if (seconds < 60) return `${Math.round(seconds)}s`
    if (seconds < 3600) return `${Math.round(seconds / 60)}m`
    if (seconds < 86400) return `${Math.round(seconds / 3600)}h`
    return `${Math.round(seconds / 86400)}d`
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="px-6 py-4 flex items-center justify-between" style={{ borderBottom: '1px solid var(--border)' }}>
        <div>
          <h1 className="text-lg font-semibold" style={{ color: 'var(--fg)' }}>System Health</h1>
          <p className="text-xs mt-0.5" style={{ color: 'var(--fg-muted)' }}>Subsystem readiness checks</p>
        </div>
        <button
          onClick={loadHealth}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs transition-colors"
          style={{ background: 'var(--surface-2)', color: 'var(--fg-muted)', border: '1px solid var(--border)' }}
          onMouseEnter={e => e.currentTarget.style.background = 'var(--surface-3)'}
          onMouseLeave={e => e.currentTarget.style.background = 'var(--surface-2)'}
        >
          <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
          Refresh
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
        {error && (
          <div className="p-4 rounded-lg" style={{ background: 'var(--red-muted)' }}>
            <p className="text-sm" style={{ color: 'var(--red)' }}>Health check failed: {error}</p>
          </div>
        )}

        {health && (
          <>
            {/* Overall Status */}
            <div className="p-5 rounded-lg flex items-center gap-4" style={{ background: 'var(--surface-1)', border: '1px solid var(--border)' }}>
              <StatusIcon status={health.status} size={32} />
              <div>
                <p className="text-xl font-semibold" style={{ color: health.status === 'ok' ? 'var(--green)' : 'var(--amber)' }}>
                  {health.status === 'ok' ? 'All Systems Operational' : 'Degraded'}
                </p>
                {health.uptime_seconds > 0 && (
                  <p className="text-xs mt-1" style={{ color: 'var(--fg-faint)' }}>
                    Uptime: {formatUptime(health.uptime_seconds)}
                  </p>
                )}
              </div>
            </div>

            {/* Individual Checks */}
            <div className="space-y-3">
              {Object.entries(health.checks).map(([name, check]) => {
                const Icon = checkIcons[name] || Heart
                const label = checkLabels[name] || name
                const statusColor = check.status === 'ok' ? 'var(--green)' : check.status === 'degraded' ? 'var(--amber)' : 'var(--red)'

                return (
                  <div key={name} className="p-4 rounded-lg" style={{ background: 'var(--surface-1)', border: '1px solid var(--border)' }}>
                    <div className="flex items-center gap-3 mb-2">
                      <div className="w-8 h-8 rounded-lg flex items-center justify-center" style={{ background: 'var(--surface-3)' }}>
                        <Icon size={16} style={{ color: statusColor }} />
                      </div>
                      <div className="flex-1">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium" style={{ color: 'var(--fg)' }}>{label}</span>
                          <StatusIcon status={check.status} size={14} />
                        </div>
                        <span className="text-xs" style={{ color: statusColor }}>{check.status.toUpperCase()}</span>
                      </div>
                    </div>

                    <div className="ml-11 space-y-1">
                      {check.detail && <p className="text-xs" style={{ color: 'var(--fg-muted)' }}>{check.detail}</p>}
                      {check.model && <p className="text-xs" style={{ color: 'var(--fg-faint)' }}>Model: {check.model}</p>}
                      {check.total != null && (
                        <p className="text-xs" style={{ color: 'var(--fg-faint)' }}>
                          Servers: {check.healthy}/{check.total} healthy
                          {check.degraded ? `, ${check.degraded} degraded` : ''}
                          {check.failed ? `, ${check.failed} failed` : ''}
                        </p>
                      )}
                      {check.free_gb != null && (
                        <div>
                          <p className="text-xs" style={{ color: 'var(--fg-faint)' }}>
                            Free: {check.free_gb}GB / {check.total_gb}GB ({check.used_pct}% used)
                          </p>
                          <div className="mt-1 h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--surface-4)' }}>
                            <div
                              className="h-full rounded-full transition-all"
                              style={{
                                width: `${check.used_pct}%`,
                                background: (check.used_pct ?? 0) > 90 ? 'var(--red)' : (check.used_pct ?? 0) > 70 ? 'var(--amber)' : 'var(--green)',
                              }}
                            />
                          </div>
                        </div>
                      )}
                      {check.memory_count != null && <p className="text-xs" style={{ color: 'var(--fg-faint)' }}>Memories: {check.memory_count}</p>}
                      {check.path && <p className="text-xs font-mono" style={{ color: 'var(--fg-faint)' }}>{check.path}</p>}
                    </div>
                  </div>
                )
              })}
            </div>
          </>
        )}

        {loading && !health && (
          <div className="flex items-center justify-center py-12">
            <div className="w-5 h-5 border-2 border-t-transparent rounded-full animate-spin" style={{ borderColor: 'var(--fg-faint)', borderTopColor: 'transparent' }} />
          </div>
        )}
      </div>
    </div>
  )
}
