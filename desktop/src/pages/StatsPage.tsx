import { useState, useEffect } from 'react'
import { BarChart3, Activity, DollarSign, Clock } from 'lucide-react'
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts'
import { api } from '../services/api'

export default function StatsPage() {
  const [stats, setStats] = useState<Record<string, any>>({})
  const [logs, setLogs] = useState<any[]>([])
  const [days, setDays] = useState(7)

  useEffect(() => {
    api.getStats(days).then(setStats).catch(console.error)
    api.getLogs(days).then(({ traces }) => setLogs(traces)).catch(console.error)
  }, [days])

  const byDate = stats.by_date || {}
  const chartData = Object.entries(byDate).map(([date, d]: [string, any]) => ({ date, input: d.input_tokens || 0, output: d.output_tokens || 0 })).sort((a, b) => a.date.localeCompare(b.date))
  const totalCost = stats.total_cost_cny ?? null

  const CustomTooltip = ({ active, payload, label }: any) => {
    if (!active || !payload) return null
    return (
      <div style={{ background: 'var(--surface-3)', border: '1px solid var(--border-strong)', borderRadius: '8px', padding: '8px 12px', fontSize: '12px' }}>
        <p style={{ color: 'var(--fg-muted)', marginBottom: '4px' }}>{label}</p>
        {payload.map((p: any) => (
          <p key={p.name} style={{ color: p.color }}>{p.name}: {p.value.toLocaleString()}</p>
        ))}
      </div>
    )
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden p-5 gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold" style={{ color: 'var(--fg)' }}>Stats & Logs</h1>
          <p className="text-xs mt-0.5" style={{ color: 'var(--fg-muted)' }}>Token usage and trace logs</p>
        </div>
        <select value={days} onChange={e => setDays(Number(e.target.value))} className="input-field text-sm">
          <option value={1}>1 day</option>
          <option value={7}>7 days</option>
          <option value={30}>30 days</option>
        </select>
      </div>

      {/* Stats Cards */}
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
              <div className="w-6 h-6 rounded-md flex items-center justify-center" style={{ background: `${color}15` }}><Icon size={12} style={{ color }} /></div>
              <span className="text-xs" style={{ color: 'var(--fg-muted)' }}>{label}</span>
            </div>
            <p className="text-lg font-semibold" style={{ color: 'var(--fg)' }}>{value}</p>
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
              <XAxis dataKey="date" tick={{ fontSize: 10, fill: 'var(--fg-faint)' }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fontSize: 10, fill: 'var(--fg-faint)' }} axisLine={false} tickLine={false} />
              <Tooltip content={<CustomTooltip />} />
              <Bar dataKey="input" fill="var(--accent)" name="Input" radius={[3, 3, 0, 0]} />
              <Bar dataKey="output" fill="var(--cyan)" name="Output" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Logs */}
      <div className="flex-1 overflow-y-auto">
        <h2 className="text-sm font-medium mb-2" style={{ color: 'var(--fg-secondary)' }}>Recent Traces</h2>
        {logs.length === 0 ? (
          <p className="text-sm" style={{ color: 'var(--fg-muted)' }}>No traces found.</p>
        ) : (
          <div className="space-y-1">
            {logs.slice(0, 30).map((trace, i) => (
              <div key={i} className="surface-card px-3 py-2.5 flex items-center justify-between">
                <div className="flex items-center gap-2.5">
                  <span className="text-xs font-mono" style={{ color: 'var(--accent)' }}>{trace.trace_id?.slice(0, 12)}</span>
                  <span className="text-xs" style={{ color: 'var(--fg-faint)' }}>{trace.date}</span>
                </div>
                <span className="text-xs" style={{ color: 'var(--fg-muted)' }}>{trace.span_count} spans</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
