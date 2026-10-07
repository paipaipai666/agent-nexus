import React, { useState } from 'react'
import { ChevronRight } from 'lucide-react'
import type { Message } from '../session/SessionManager'
import { formatDuration } from '../../utils/toolDisplay'
import ToolCard from './ToolCard'

/* ─── Tool Group Header — N calls · total duration · error badge ─── */
const ToolGroupHeader = React.memo(function ToolGroupHeader({
  msgs, collapsed, onToggle,
}: {
  msgs: Message[]
  collapsed: boolean
  onToggle: () => void
}) {
  const errorCount = msgs.filter(m => m.toolStatus === 'error').length
  const runningCount = msgs.filter(m => m.toolStatus === 'running').length
  const totalMs = msgs.reduce((s, m) => s + (m.toolDurationMs || 0), 0)
  const totalLabel = formatDuration(totalMs)
  const n = msgs.length

  return (
    <div
      className="flex items-center gap-2 px-3.5 py-2"
      style={{ background: 'var(--surface-2)', borderBottom: '1px solid var(--border)' }}
    >
      <span className="font-mono text-[11px] flex items-center gap-1.5" style={{ color: 'var(--fg-muted)' }}>
        {n} tool call{n === 1 ? '' : 's'}
        <span className="inline-flex items-center gap-1">
          {msgs.slice(0, 3).map((m, i) => (
            <span
              key={m.id || i}
              className="w-1.5 h-1.5 rounded-full"
              style={{
                background: m.toolStatus === 'error' ? 'var(--red)'
                  : m.toolStatus === 'running' ? 'var(--amber)'
                  : 'var(--green)',
              }}
            />
          ))}
        </span>
        {runningCount > 0 && (
          <span style={{ color: 'var(--amber)' }}>· running</span>
        )}
      </span>
      <div className="ml-auto flex items-center gap-2">
        {errorCount > 0 && (
          <span
            className="text-[10.5px] px-2 py-px rounded-full"
            style={{ color: 'var(--red)', background: 'var(--red-muted)' }}
          >
            {errorCount} error{errorCount === 1 ? '' : 's'}
          </span>
        )}
        {totalLabel && (
          <span className="font-mono text-[11px]" style={{ color: 'var(--fg-faint)' }}>{totalLabel}</span>
        )}
        <button
          onClick={onToggle}
          className="w-[22px] h-[22px] rounded-md grid place-items-center transition-colors hover:bg-[var(--surface-3)]"
          style={{ color: 'var(--fg-faint)' }}
          aria-label={collapsed ? '展开工具组' : '折叠工具组'}
        >
          <ChevronRight
            size={12}
            className="transition-transform duration-200"
            style={{ transform: collapsed ? 'rotate(-90deg)' : 'rotate(90deg)' }}
          />
        </button>
      </div>
    </div>
  )
})

/* Shared tool-group card: header + hairline-divided rows. */
function ToolGroup({ msgs, mcpNames }: { msgs: Message[]; mcpNames: Record<string, true> }) {
  const [collapsed, setCollapsed] = useState(false)
  return (
    <div className="max-w-3xl mx-auto px-6 py-2">
      <div
        className="max-w-[608px] overflow-hidden"
        style={{ background: 'var(--surface-1)', border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)' }}
      >
        <ToolGroupHeader msgs={msgs} collapsed={collapsed} onToggle={() => setCollapsed(c => !c)} />
        {!collapsed && msgs.map((m, i) => (
          <div key={m.id} style={i > 0 ? { borderTop: '1px solid var(--border-subtle)' } : undefined}>
            <ToolCard msg={m} isMcp={mcpNames[m.toolName ?? ''] === true} />
          </div>
        ))}
      </div>
    </div>
  )
}

export default ToolGroup
