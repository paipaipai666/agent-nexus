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

  // Build chart data from by_date breakdown
  const byDate = stats.by_date || {}
  const chartData = Object.entries(byDate).map(([date, d]: [string, any]) => ({
    date,
    input: d.input_tokens || 0,
    output: d.output_tokens || 0,
  })).sort((a, b) => a.date.localeCompare(b.date))

  const totalCost = stats.total_cost_cny ?? null

  return (
    <div className="flex-1 flex flex-col overflow-hidden p-4 gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-text-primary">Stats & Logs</h1>
        <select
          value={days}
          onChange={e => setDays(Number(e.target.value))}
          className="bg-bg-tertiary text-text-primary rounded-md px-2 py-1 text-sm border border-border-default"
        >
          <option value={1}>1 day</option>
          <option value={7}>7 days</option>
          <option value={30}>30 days</option>
        </select>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        {[
          { label: 'Total Tasks', value: stats.total_tasks ?? '-', icon: BarChart3 },
          { label: 'Input Tokens', value: stats.total_input_tokens?.toLocaleString() ?? '-', icon: Activity },
          { label: 'Output Tokens', value: stats.total_output_tokens?.toLocaleString() ?? '-', icon: Activity },
          { label: 'Avg Latency', value: stats.avg_latency_ms ? `${Math.round(stats.avg_latency_ms)}ms` : '-', icon: Clock },
          { label: 'Total Cost', value: totalCost != null ? `¥${Number(totalCost).toFixed(4)}` : '-', icon: DollarSign },
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

      {/* Token Usage Chart */}
      {chartData.length > 0 && (
        <div className="bg-bg-secondary rounded-lg border border-border-default p-4">
          <h2 className="text-sm font-medium text-text-secondary mb-3">Token Usage</h2>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-default, #333)" />
              <XAxis dataKey="date" tick={{ fontSize: 11, fill: 'var(--color-text-muted, #888)' }} />
              <YAxis tick={{ fontSize: 11, fill: 'var(--color-text-muted, #888)' }} />
              <Tooltip
                contentStyle={{
                  backgroundColor: 'var(--color-bg-secondary, #222)',
                  border: '1px solid var(--color-border-default, #444)',
                  borderRadius: '8px',
                  fontSize: '12px',
                }}
              />
              <Bar dataKey="input" fill="#6366f1" name="Input Tokens" radius={[2, 2, 0, 0]} />
              <Bar dataKey="output" fill="#22d3ee" name="Output Tokens" radius={[2, 2, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Logs */}
      <div className="flex-1 overflow-y-auto">
        <h2 className="text-sm font-medium text-text-secondary mb-2">Recent Traces</h2>
        {logs.length === 0 ? (
          <p className="text-text-muted text-sm">No traces found.</p>
        ) : (
          <div className="space-y-1">
            {logs.slice(0, 30).map((trace, i) => (
              <div key={i} className="bg-bg-secondary rounded-md px-3 py-2 flex items-center justify-between text-sm border border-border-default">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-xs text-accent-secondary">{trace.trace_id?.slice(0, 12)}</span>
                  <span className="text-text-muted text-xs">{trace.date}</span>
                </div>
                <div className="flex items-center gap-3 text-xs text-text-muted">
                  <span>{trace.span_count} spans</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
