import { useState, useEffect } from 'react'
import { Puzzle, AlertTriangle, CheckCircle } from 'lucide-react'
import { api } from '../services/api'

interface Plugin {
  name: string
  enabled: boolean
  path: string
  errors: string[]
}

export default function PluginsPage() {
  const [plugins, setPlugins] = useState<Plugin[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.getExtensions()
      .then((data) => {
        // Parse ExtensionStatusReport structure
        const report = data
        const discovered = report.discovered || report.load_report?.loaded || []
        const disabled = report.load_report?.disabled || []
        const failed = report.load_report?.failed || []

        const all: Plugin[] = []
        for (const d of discovered) {
          all.push({ name: d.name, enabled: true, path: d.path || '', errors: d.errors || [] })
        }
        for (const d of disabled) {
          all.push({ name: d.name, enabled: false, path: d.path || '', errors: d.errors || [] })
        }
        for (const d of failed) {
          all.push({ name: d.name, enabled: false, path: d.path || '', errors: d.errors || ['Failed to load'] })
        }

        // Deduplicate by name
        const seen = new Set<string>()
        const deduped = all.filter(p => {
          if (seen.has(p.name)) return false
          seen.add(p.name)
          return true
        })

        setPlugins(deduped)
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return <div className="flex-1 flex items-center justify-center text-text-muted">Loading plugins...</div>
  }

  const enabledCount = plugins.filter(p => p.enabled).length
  const errorCount = plugins.filter(p => p.errors.length > 0).length

  return (
    <div className="flex-1 flex flex-col overflow-hidden p-4 gap-4">
      <h1 className="text-lg font-semibold text-text-primary">Plugins & Extensions</h1>

      {error && (
        <div className="bg-status-error/10 border border-status-error/30 text-status-error text-sm rounded-md px-3 py-2">
          {error}
        </div>
      )}

      {/* Summary */}
      <div className="grid grid-cols-3 gap-3">
        {[
          { label: 'Total', value: plugins.length, icon: Puzzle },
          { label: 'Enabled', value: enabledCount, icon: CheckCircle },
          { label: 'Errors', value: errorCount, icon: AlertTriangle },
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

      {/* Plugin List */}
      <div className="flex-1 overflow-y-auto space-y-3">
        {plugins.length === 0 ? (
          <p className="text-text-muted text-sm">No plugins discovered.</p>
        ) : (
          plugins.map(plugin => (
            <div key={plugin.name} className="bg-bg-secondary rounded-lg p-4 border border-border-default">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3 min-w-0">
                  <Puzzle size={16} className={plugin.enabled ? 'text-accent-primary' : 'text-text-muted'} />
                  <div className="min-w-0">
                    <h3 className="text-sm font-medium text-text-primary truncate">{plugin.name}</h3>
                    {plugin.path && (
                      <p className="text-xs text-text-muted font-mono truncate">{plugin.path}</p>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  {plugin.errors.length > 0 && (
                    <span className="text-xs px-2 py-0.5 rounded-full bg-status-error/20 text-status-error flex items-center gap-1">
                      <AlertTriangle size={10} />
                      {plugin.errors.length} error{plugin.errors.length > 1 ? 's' : ''}
                    </span>
                  )}
                  <span className={`text-xs px-2 py-0.5 rounded-full ${
                    plugin.enabled
                      ? 'bg-status-success/20 text-status-success'
                      : 'bg-bg-tertiary text-text-muted'
                  }`}>
                    {plugin.enabled ? 'Enabled' : 'Disabled'}
                  </span>
                </div>
              </div>
              {plugin.errors.length > 0 && (
                <div className="mt-2 space-y-1">
                  {plugin.errors.map((err, i) => (
                    <p key={i} className="text-xs text-status-error bg-status-error/5 rounded px-2 py-1">{err}</p>
                  ))}
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  )
}
