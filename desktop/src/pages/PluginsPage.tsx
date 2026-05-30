import { useState, useEffect } from 'react'
import { Puzzle, AlertTriangle, CheckCircle } from 'lucide-react'
import { api } from '../services/api'

interface Plugin { name: string; enabled: boolean; path: string; errors: string[] }

export default function PluginsPage() {
  const [plugins, setPlugins] = useState<Plugin[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.getExtensions().then((data) => {
      const discovered = data.discovered || data.load_report?.loaded || []
      const disabled = data.load_report?.disabled || []
      const failed = data.load_report?.failed || []
      const all: Plugin[] = []
      for (const d of discovered) all.push({ name: d.name, enabled: true, path: d.path || '', errors: d.errors || [] })
      for (const d of disabled) all.push({ name: d.name, enabled: false, path: d.path || '', errors: d.errors || [] })
      for (const d of failed) all.push({ name: d.name, enabled: false, path: d.path || '', errors: d.errors || ['Failed to load'] })
      const seen = new Set<string>()
      setPlugins(all.filter(p => { if (seen.has(p.name)) return false; seen.add(p.name); return true }))
    }).catch(e => setError(e.message)).finally(() => setLoading(false))
  }, [])

  if (loading) return <div className="flex-1 flex items-center justify-center"><div className="w-6 h-6 border-2 border-t-transparent rounded-full animate-spin" style={{ borderColor: 'var(--fg-faint)', borderTopColor: 'transparent' }} /></div>

  const enabledCount = plugins.filter(p => p.enabled).length
  const errorCount = plugins.filter(p => p.errors.length > 0).length

  return (
    <div className="flex-1 flex flex-col overflow-hidden p-5 gap-4">
      <div>
        <h1 className="text-lg font-semibold" style={{ color: 'var(--fg)' }}>Plugins & Extensions</h1>
        <p className="text-xs mt-0.5" style={{ color: 'var(--fg-muted)' }}>{enabledCount} enabled · {errorCount} errors</p>
      </div>

      {error && <div className="rounded-lg px-3 py-2 text-sm" style={{ background: 'var(--red-muted)', color: 'var(--red)' }}>{error}</div>}

      <div className="grid grid-cols-3 gap-3">
        {[
          { label: 'Total', value: plugins.length, icon: Puzzle, color: 'var(--fg-secondary)' },
          { label: 'Enabled', value: enabledCount, icon: CheckCircle, color: 'var(--green)' },
          { label: 'Errors', value: errorCount, icon: AlertTriangle, color: 'var(--red)' },
        ].map(({ label, value, icon: Icon, color }) => (
          <div key={label} className="surface-card p-3.5">
            <div className="flex items-center gap-2 mb-1.5">
              <div className="w-6 h-6 rounded-md flex items-center justify-center" style={{ background: `${color}15` }}><Icon size={12} style={{ color }} /></div>
              <span className="text-xs" style={{ color: 'var(--fg-muted)' }}>{label}</span>
            </div>
            <p className="text-xl font-semibold" style={{ color: 'var(--fg)' }}>{value}</p>
          </div>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto space-y-2">
        {plugins.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full gap-3">
            <div className="w-12 h-12 rounded-xl flex items-center justify-center" style={{ background: 'var(--surface-3)' }}><Puzzle size={24} style={{ color: 'var(--fg-faint)' }} /></div>
            <p className="text-sm" style={{ color: 'var(--fg-muted)' }}>No plugins discovered</p>
          </div>
        ) : plugins.map(plugin => (
          <div key={plugin.name} className="surface-card p-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3 min-w-0">
                <div className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0" style={{ background: plugin.enabled ? 'var(--green-muted)' : 'var(--surface-3)' }}>
                  <Puzzle size={14} style={{ color: plugin.enabled ? 'var(--green)' : 'var(--fg-faint)' }} />
                </div>
                <div className="min-w-0">
                  <h3 className="text-sm font-medium truncate" style={{ color: 'var(--fg)' }}>{plugin.name}</h3>
                  {plugin.path && <p className="text-xs font-mono truncate" style={{ color: 'var(--fg-faint)' }}>{plugin.path}</p>}
                </div>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {plugin.errors.length > 0 && (
                  <span className="text-xs px-2 py-0.5 rounded-full flex items-center gap-1" style={{ background: 'var(--red-muted)', color: 'var(--red)' }}>
                    <AlertTriangle size={10} />{plugin.errors.length}
                  </span>
                )}
                <span className="text-xs px-2 py-0.5 rounded-full" style={{ background: plugin.enabled ? 'var(--green-muted)' : 'var(--surface-3)', color: plugin.enabled ? 'var(--green)' : 'var(--fg-faint)' }}>{plugin.enabled ? 'Enabled' : 'Disabled'}</span>
              </div>
            </div>
            {plugin.errors.length > 0 && (
              <div className="mt-2.5 space-y-1">{plugin.errors.map((err, i) => <p key={i} className="text-xs rounded-md px-2.5 py-1.5" style={{ background: 'var(--red-muted)', color: 'var(--red)' }}>{err}</p>)}</div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
