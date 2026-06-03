import { useState, useEffect } from 'react'
import { BarChart3, Activity, DollarSign, Clock, CheckCircle, AlertTriangle, Layers, ChevronDown, ChevronRight } from 'lucide-react'
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts'
import { api } from '../services/api'

export default function StatsPage() {
  const [stats, setStats] = useState<Record<string, any>>({})
  const [logs, setLogs] = useState<any[]>([])
  const [days, setDays] = useState(7)
  const [expandedTrace, setExpandedTrace] = useState<string | null>(null)
  const [traceSpans, setTraceSpans] = useState<any[]>([])
  const [loadingTrace, setLoadingTrace] = useState(false)

  useEffect(() => {
    api.getStats(days).then(setStats).catch(console.error)
    api.getLogs(days).then(({ traces }) => setLogs(traces)).catch(console.error)
  }, [days])

  const loadTraceDetail = async (traceId: string) => {
    if (expandedTrace === traceId) {
      setExpandedTrace(null)
      setTraceSpans([])
      return
    }
    setLoadingTrace(true)
    setExpandedTrace(traceId)
    try {
      const { spans } = await api.getTraceDetail(traceId)
      setTraceSpans(spans)
    } catch {
      setTraceSpans([])
    } finally {
      setLoadingTrace(false)
    }
  }

  const byDate = stats.by_date || {}
  const merged: Record<string, { input: number; output: number }> = {}
  for (const [key, models] of Object.entries(byDate) as [string, any][]) {
    const baseDate = key.replace(/_\d+$/, '')
    if (!merged[baseDate]) merged[baseDate] = { input: 0, output: 0 }
    if (typeof models === 'object' && models !== null) {
      for (const model of Object.values(models) as any[]) {
        merged[baseDate].input += model.input || 0
        merged[baseDate].output += model.output || 0
      }
    }
  }
  const chartData = Object.entries(merged)
    .filter(([, d]) => d.input > 0 || d.output > 0)
    .map(([date, d]) => ({ date, input: d.input, output: d.output }))
    .sort((a, b) => a.date.localeCompare(b.date))
  const totalCost = stats.total_cost_cny ?? null

  const CustomTooltip = ({ active, payload, label }: any) => {
    if (!active || !payload) return null
    return (
      <div style={{ background: 'var(--surface-3)', border: '1px solid var(--border-strong)', borderRadius: 'var(--radius)', padding: '8px 12px', fontSize: '12px' }}>
        <p style={{ color: 'var(--fg-muted)', marginBottom: '4px' }}>{label}</p>
        {payload.map((p: any) => (
          <p key={p.name} style={{ color: p.color }}>{p.name}: {p.value.toLocaleString()}</p>
        ))}
      </div>
    )
  }

  const spanNameColor = (name: string) => {
    if (name === 'task') return 'var(--accent)'
    if (name === 'llm') return 'var(--blue)'
    if (name.startsWith('tool')) return 'var(--green)'
    if (name === 'plan_node') return 'var(--cyan)'
    if (name === 'final_answer') return 'var(--amber)'
    return 'var(--fg-muted)'
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden p-5 gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold" style={{ color: 'var(--fg)' }}>Stats &amp; Logs</h1>
          <p className="text-xs mt-0.5" style={{ color: 'var(--fg-muted)' }}>Token usage, task metrics, and trace logs</p>
        </div>
        <select value={days} onChange={e => setDays(Number(e.target.value))} className="input-field text-sm">
          <option value={1}>1 day</option>
          <option value={7}>7 days</option>
          <option value={30}>30 days</option>
        </select>
      </div>

      {/* Stats Cards — row 1: original */}
      <div className="grid grid-cols-5 gap-3">
        {[
          { label: 'Total Tasks', value: stats.total_tasks ?? '-', icon: BarChart3, color: 'var(--accent)' },
          { label: 'Input Tokens', value: stats.total_input_tokens?.toLocaleString() ?? '-', icon: Activity, color: 'var(--blue)' },
          { label: 'Output Tokens', value: stats.total_output_tokens?.toLocaleString() ?? '-', icon: Activity, color: 'var(--cyan)' },
          { label: 'Avg Latency', value: stats.avg_latency_ms ? `${Math.round(stats.avg_latency_ms)}ms` : '-', icon: Clock, color: 'var(--amber)' },
          { label: 'Total Cost', value: totalCost != null ? `¥${Number(totalCost).toFixed(4)}` : '-', icon: DollarSign, color: 'var(--green)' },
        ].map(({ label, value, icon: Icon, color }) => (
          <div key={label} className="surface-card p-3.5">
            <div className="flex items-center gap-2 mb-1.5">
              <div className="w-6 h-6 rounded flex items-center justify-center" style={{ background: `${color}18` }}><Icon size={12} style={{ color }} /></div>
              <span className="text-xs" style={{ color: 'var(--fg-muted)' }}>{label}</span>
            </div>
            <p className="text-lg font-semibold" style={{ color: 'var(--fg)' }}>{value}</p>
          </div>
        ))}
      </div>

      {/* Stats Cards — row 2: new observability metrics */}
      <div className="grid grid-cols-4 gap-3">
        {[
          {
            label: 'Task Success Rate',
            value: stats.task_success_rate != null ? `${(stats.task_success_rate * 100).toFixed(1)}%` : '-',
            sub: stats.task_success_count != null ? `${stats.task_success_count}/${stats.total_tasks}` : '',
            icon: CheckCircle,
            color: (stats.task_success_rate ?? 0) >= 0.8 ? 'var(--green)' : 'var(--amber)',
          },
          {
            label: 'Tool Failure Rate',
            value: stats.tool_failure_rate != null ? `${(stats.tool_failure_rate * 100).toFixed(1)}%` : '-',
            sub: stats.tool_total_count != null ? `${stats.tool_error_count}/${stats.tool_total_count} calls` : '',
            icon: AlertTriangle,
            color: (stats.tool_failure_rate ?? 0) < 0.1 ? 'var(--green)' : 'var(--red)',
          },
          {
            label: 'Avg Context Length',
            value: stats.avg_context_length ? `${(stats.avg_context_length / 1000).toFixed(1)}k` : '-',
            sub: stats.max_context_length ? `max ${(stats.max_context_length / 1000).toFixed(1)}k` : '',
            icon: Layers,
            color: 'var(--cyan)',
          },
          {
            label: 'Cache Hit Rate',
            value: stats.cache_hit_rate != null ? `${(stats.cache_hit_rate * 100).toFixed(1)}%` : '-',
            sub: stats.cache_saved_cost_cny ? `saved ¥${stats.cache_saved_cost_cny.toFixed(4)}` : '',
            icon: Activity,
            color: 'var(--blue)',
          },
        ].map(({ label, value, sub, icon: Icon, color }) => (
          <div key={label} className="surface-card p-3.5">
            <div className="flex items-center gap-2 mb-1.5">
              <div className="w-6 h-6 rounded flex items-center justify-center" style={{ background: `${color}18` }}><Icon size={12} style={{ color }} /></div>
              <span className="text-xs" style={{ color: 'var(--fg-muted)' }}>{label}</span>
            </div>
            <p className="text-lg font-semibold" style={{ color: 'var(--fg)' }}>{value}</p>
            {sub && <p className="text-xs mt-0.5" style={{ color: 'var(--fg-faint)' }}>{sub}</p>}
          </div>
        ))}
      </div>

      {/* Chart */}
      {chartData.length > 0 && (
        <div className="surface-card p-4">
          <h2 className="text-sm font-medium mb-3" style={{ color: 'var(--fg-secondary)' }}>Token Usage</h2>
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
              <XAxis dataKey="date" tick={{ fontSize: 10, fill: 'var(--fg-faint)', fontFamily: 'var(--font-mono)' }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fontSize: 10, fill: 'var(--fg-faint)', fontFamily: 'var(--font-mono)' }} axisLine={false} tickLine={false} />
              <Tooltip content={<CustomTooltip />} />
              <Bar dataKey="input" fill="var(--accent)" name="Input" radius={[2, 2, 0, 0]} />
              <Bar dataKey="output" fill="var(--cyan)" name="Output" radius={[2, 2, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Logs with expandable trace detail */}
      <div className="flex-1 overflow-y-auto">
        <h2 className="text-sm font-medium mb-2" style={{ color: 'var(--fg-secondary)' }}>Recent Traces</h2>
        {logs.length === 0 ? (
          <p className="text-sm" style={{ color: 'var(--fg-muted)' }}>No traces found.</p>
        ) : (
          <div className="space-y-1">
            {logs.slice(0, 30).map((trace, i) => (
              <div key={i}>
                <button
                  onClick={() => loadTraceDetail(trace.trace_id)}
                  className="w-full surface-card px-3 py-2.5 flex items-center justify-between text-left transition-colors"
                  onMouseEnter={e => e.currentTarget.style.background = 'var(--surface-3)'}
                  onMouseLeave={e => e.currentTarget.style.background = ''}
                >
                  <div className="flex items-center gap-2.5">
                    {expandedTrace === trace.trace_id
                      ? <ChevronDown size={12} style={{ color: 'var(--fg-muted)' }} />
                      : <ChevronRight size={12} style={{ color: 'var(--fg-muted)' }} />
                    }
                    <span className="text-xs font-mono" style={{ color: 'var(--accent)' }}>{trace.trace_id?.slice(0, 12)}</span>
                    <span className="text-xs" style={{ color: 'var(--fg-faint)' }}>{trace.date}</span>
                  </div>
                  <span className="text-xs" style={{ color: 'var(--fg-muted)' }}>{trace.span_count} spans</span>
                </button>

                {/* Expanded trace detail */}
                {expandedTrace === trace.trace_id && (
                  <div className="ml-4 mt-1 mb-2 surface-card p-3" style={{ borderLeft: '2px solid var(--accent-muted)' }}>
                    {loadingTrace ? (
                      <div className="flex items-center gap-2 py-2">
                        <div className="w-4 h-4 border-2 border-t-transparent rounded-full animate-spin" style={{ borderColor: 'var(--fg-faint)', borderTopColor: 'transparent' }} />
                        <span className="text-xs" style={{ color: 'var(--fg-muted)' }}>Loading spans...</span>
                      </div>
                    ) : traceSpans.length === 0 ? (
                      <p className="text-xs" style={{ color: 'var(--fg-muted)' }}>No spans found.</p>
                    ) : (
                      <div className="space-y-1.5 max-h-80 overflow-y-auto">
                        {traceSpans.map((span: any, j: number) => {
                          const status = span.metadata?.status || 'ok'
                          const isError = status === 'error'
                          return (
                            <div key={j} className="flex items-start gap-2 py-1.5 px-2 rounded" style={{ background: isError ? 'rgba(239,68,68,0.06)' : 'transparent' }}>
                              <span className="text-xs font-mono px-1.5 py-0.5 rounded shrink-0" style={{ background: `${spanNameColor(span.name)}18`, color: spanNameColor(span.name) }}>
                                {span.name}
                              </span>
                              <div className="flex-1 min-w-0">
                                <div className="flex items-center gap-2">
                                  <span className="text-xs font-mono" style={{ color: 'var(--fg-faint)' }}>{span.latency_ms?.toFixed(0)}ms</span>
                                  {isError && <span className="text-xs px-1 rounded" style={{ background: 'rgba(239,68,68,0.15)', color: 'var(--red)' }}>error</span>}
                                  {span.metadata?.risk_level && span.metadata.risk_level !== 'low' && (
                                    <span className="text-xs px-1 rounded" style={{ background: 'rgba(245,158,11,0.15)', color: 'var(--amber)' }}>{span.metadata.risk_level}</span>
                                  )}
                                </div>
                                {span.metadata?.error && (
                                  <p className="text-xs mt-0.5 truncate" style={{ color: 'var(--red)' }}>{span.metadata.error}</p>
                                )}
                                {span.input?.task && (
                                  <p className="text-xs mt-0.5 truncate" style={{ color: 'var(--fg-muted)' }}>{span.input.task}</p>
                                )}
                                {span.output?.result_summary && (
                                  <p className="text-xs mt-0.5 truncate" style={{ color: 'var(--fg-faint)' }}>→ {span.output.result_summary}</p>
                                )}
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
