import { useState, useEffect } from 'react'
import { Bell, AlertTriangle, AlertOctagon, Info, Shield, Filter } from 'lucide-react'
import { api } from '../services/api'

interface Alert {
  alert_type: string
  severity: string
  message: string
  details: Record<string, any>
  timestamp: number
  trace_id: string
}

const severityConfig: Record<string, { icon: typeof Bell; color: string; bg: string }> = {
  critical: { icon: AlertOctagon, color: 'var(--red)', bg: 'var(--red-muted)' },
  warning: { icon: AlertTriangle, color: 'var(--amber)', bg: 'var(--amber-muted)' },
  info: { icon: Info, color: 'var(--blue)', bg: 'var(--blue-muted)' },
}

const typeLabels: Record<string, string> = {
  drift: 'Drift',
  cost_exceed: 'Cost Exceed',
  tool_failure_spike: 'Tool Failure Spike',
  slow_hook: 'Slow Hook',
  mcp_degraded: 'MCP Degraded',
  consecutive_failure: 'Consecutive Failure',
  human_takeover: 'Human Takeover',
}

export default function AlertsPage() {
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [rules, setRules] = useState<Array<{ type: string; index: number }>>([])
  const [days, setDays] = useState(7)
  const [severityFilter, setSeverityFilter] = useState<string>('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    Promise.all([
      api.getAlerts(days, severityFilter || undefined),
      api.getAlertRules(),
    ]).then(([alertsRes, rulesRes]) => {
      setAlerts(alertsRes.alerts)
      setRules(rulesRes.rules)
    }).catch(console.error).finally(() => setLoading(false))
  }, [days, severityFilter])

  const formatTime = (ts: number) => {
    const d = new Date(ts * 1000)
    return d.toLocaleString()
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="px-6 py-4 flex items-center justify-between" style={{ borderBottom: '1px solid var(--border)' }}>
        <div>
          <h1 className="text-lg font-semibold" style={{ color: 'var(--fg)' }}>Alerts</h1>
          <p className="text-xs mt-0.5" style={{ color: 'var(--fg-muted)' }}>
            {alerts.length} alert{alerts.length !== 1 ? 's' : ''} in the last {days} days
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1.5">
            <Filter size={12} style={{ color: 'var(--fg-muted)' }} />
            <select
              value={severityFilter}
              onChange={e => setSeverityFilter(e.target.value)}
              className="input-field text-xs"
            >
              <option value="">All severities</option>
              <option value="critical">Critical</option>
              <option value="warning">Warning</option>
              <option value="info">Info</option>
            </select>
          </div>
          <select value={days} onChange={e => setDays(Number(e.target.value))} className="input-field text-sm">
            <option value={1}>1 day</option>
            <option value={7}>7 days</option>
            <option value={30}>30 days</option>
          </select>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
        {/* Alert Rules */}
        <div className="p-4 rounded-lg" style={{ background: 'var(--surface-1)', border: '1px solid var(--border)' }}>
          <div className="flex items-center gap-2 mb-3">
            <Shield size={14} style={{ color: 'var(--accent)' }} />
            <h2 className="text-sm font-medium" style={{ color: 'var(--fg-secondary)' }}>Active Rules ({rules.length})</h2>
          </div>
          <div className="flex flex-wrap gap-2">
            {rules.map((rule, i) => (
              <span key={i} className="text-xs px-2.5 py-1 rounded-full" style={{ background: 'var(--surface-3)', color: 'var(--fg-muted)' }}>
                {rule.type}
              </span>
            ))}
            {rules.length === 0 && <span className="text-xs" style={{ color: 'var(--fg-faint)' }}>No rules configured</span>}
          </div>
        </div>

        {/* Alert List */}
        {loading ? (
          <div className="flex items-center justify-center py-12">
            <div className="w-5 h-5 border-2 border-t-transparent rounded-full animate-spin" style={{ borderColor: 'var(--fg-faint)', borderTopColor: 'transparent' }} />
          </div>
        ) : alerts.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 gap-3">
            <Bell size={32} style={{ color: 'var(--fg-faint)' }} />
            <p className="text-sm" style={{ color: 'var(--fg-muted)' }}>No alerts in the selected period</p>
          </div>
        ) : (
          <div className="space-y-2">
            {alerts.map((alert, i) => {
              const config = severityConfig[alert.severity] || severityConfig.info
              const Icon = config.icon
              return (
                <div key={i} className="p-3.5 rounded-lg" style={{ background: `linear-gradient(to right, ${config.bg}, var(--surface-1))`, border: '1px solid var(--border)' }}>
                  <div className="flex items-start gap-3">
                    <div className="w-7 h-7 rounded-lg flex items-center justify-center shrink-0 mt-0.5" style={{ background: config.bg }}>
                      <Icon size={14} style={{ color: config.color }} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="text-xs font-medium px-1.5 py-0.5 rounded" style={{ background: config.bg, color: config.color }}>
                          {alert.severity.toUpperCase()}
                        </span>
                        <span className="text-xs px-1.5 py-0.5 rounded" style={{ background: 'var(--surface-3)', color: 'var(--fg-muted)' }}>
                          {typeLabels[alert.alert_type] || alert.alert_type}
                        </span>
                        <span className="text-xs ml-auto" style={{ color: 'var(--fg-faint)' }}>{formatTime(alert.timestamp)}</span>
                      </div>
                      <p className="text-sm" style={{ color: 'var(--fg)' }}>{alert.message}</p>
                      {alert.trace_id && (
                        <p className="text-xs mt-1 font-mono" style={{ color: 'var(--fg-faint)' }}>trace: {alert.trace_id}</p>
                      )}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
