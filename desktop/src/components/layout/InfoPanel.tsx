import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { ChevronDown, ChevronRight, CheckCircle, Circle, Clock, Wrench, Server, Zap, ListTodo, PanelRightOpen, PanelRightClose, Bot, Square } from 'lucide-react'
import { api, type SubagentInfo } from '../../services/api'
import { wsPool } from '../../services/ws'
import { useUIStore } from '../../services/ui'
import { useSession, type SubagentMeta } from '../session/SessionManager'

const SUBAGENT_STATUS_LABEL: Record<string, string> = {
  thinking: '思考中',
  tool_calling: '调用工具中',
  interrupted: '已中断',
  completed: '已完成',
  failed: '失败',
}

const SUBAGENT_STATUS_COLOR: Record<string, string> = {
  thinking: 'var(--amber)',
  tool_calling: 'var(--blue)',
  interrupted: 'var(--red)',
  completed: 'var(--green)',
  failed: 'var(--red)',
}

const SUBAGENT_ACTIVE: Record<string, true> = { thinking: true, tool_calling: true }

interface Todo {
  id: number
  description: string
  status: string
}

interface Skill {
  id: string
  display_name: string
  description: string
  enabled: boolean
}

interface MCPServer {
  name: string
  connected: boolean
  tool_names: string[]
}

export default function InfoPanel() {
  const [todos, setTodos] = useState<Todo[]>([])
  const [skills, setSkills] = useState<Skill[]>([])
  const [mcpServers, setMcpServers] = useState<MCPServer[]>([])
  // Subagents: REST hydrates on mount/session switch (covers reload mid-run);
  // live updates arrive via subagent_event into SessionManager's store.
  const [restSubagents, setRestSubagents] = useState<SubagentInfo[]>([])
  const navigate = useNavigate()
  const { sessionId, getLiveSessionState } = useSession()
  const [expandedSections, setExpandedSections] = useState<Record<string, boolean>>({
    todos: true,
    tools: true,
    subagents: true,
  })
  const { infoPanelCollapsed, toggleInfoPanel } = useUIStore()

  useEffect(() => {
    if (!sessionId) return

    const fetchData = () => {
      api.getTodos(sessionId).then(d => setTodos(d.items || [])).catch(() => {})
      api.listSkills().then(d => setSkills(d.skills || [])).catch(() => {})
      api.getMcpStatus().then(d => setMcpServers(d.servers || [])).catch(() => {})
      api.getSubagents(sessionId).then(d => setRestSubagents(d.subagents || [])).catch(() => {})
    }

    fetchData()
    const interval = setInterval(fetchData, 10000)
    return () => clearInterval(interval)
  }, [sessionId])

  const toggleSection = (key: string) => {
    setExpandedSections(prev => ({ ...prev, [key]: !prev[key] }))
  }

  const enabledSkills = skills.filter(s => s.enabled)
  const connectedServers = mcpServers.filter(s => s.connected)
  const mcpToolCount = mcpServers.reduce((sum, s) => sum + s.tool_names.length, 0)
  const totalTools = mcpToolCount + enabledSkills.length

  // Merge REST hydration with live store entries (store is fresher — wins on id).
  const storeSubagents = (sessionId ? getLiveSessionState(sessionId)?.subagents : undefined)
  const mergedSubagents = new Map<string, SubagentMeta>()
  for (const s of restSubagents) {
    mergedSubagents.set(s.subagent_id, {
      id: s.subagent_id, name: s.name, role: s.role, task: s.task,
      status: s.status, currentTool: s.current_tool,
    })
  }
  if (storeSubagents) {
    for (const [id, meta] of storeSubagents) mergedSubagents.set(id, meta)
  }

  return (
    <InfoPanelCard
      totalTools={totalTools}
      collapsed={infoPanelCollapsed}
      onToggleCollapse={toggleInfoPanel}
    >
      {/* Subagents */}
      {mergedSubagents.size > 0 && (
        <Section
          title={`Subagents (${mergedSubagents.size})`}
          icon={<Bot size={14} />}
          expanded={expandedSections.subagents}
          onToggle={() => toggleSection('subagents')}
        >
          <div className="space-y-1">
            {[...mergedSubagents.values()].map(s => {
              const status = s.status
              const active = status in SUBAGENT_ACTIVE
              return (
                <div
                  key={s.id}
                  className="flex items-center gap-2 px-2 py-1 rounded cursor-pointer transition-colors hover:bg-[var(--surface-2)]"
                  style={{ background: 'var(--surface-3)' }}
                  title={s.task || s.name}
                  onClick={() => sessionId && navigate(`/chat/${sessionId}/subagent/${s.id}`)}
                >
                  <Bot size={12} style={{ color: 'var(--accent)', flexShrink: 0 }} />
                  <span className="text-[12.5px] truncate" style={{ color: 'var(--fg)' }}>{s.name}</span>
                  <span className="ml-auto flex items-center gap-1.5 shrink-0">
                    <span
                      className="w-1.5 h-1.5 rounded-full"
                      style={{ background: SUBAGENT_STATUS_COLOR[status] || 'var(--fg-faint)' }}
                      title={SUBAGENT_STATUS_LABEL[status] || status}
                    />
                    <span className="text-[11px]" style={{ color: SUBAGENT_STATUS_COLOR[status] || 'var(--fg-faint)' }}>
                      {SUBAGENT_STATUS_LABEL[status] || status}
                    </span>
                    {active && (
                      <button
                        onClick={e => {
                          e.stopPropagation()
                          if (sessionId) wsPool.cancelSubagent(sessionId, s.id)
                        }}
                        className="p-0.5 rounded"
                        style={{ color: 'var(--red)' }}
                        title="中断该子代理"
                      >
                        <Square size={10} />
                      </button>
                    )}
                  </span>
                </div>
              )
            })}
          </div>
        </Section>
      )}

      {/* Todo List */}
      <Section
        title="Todo List"
        icon={<ListTodo size={14} />}
        expanded={expandedSections.todos}
        onToggle={() => toggleSection('todos')}
      >
        <div className="space-y-1.5">
          {todos.length === 0 ? (
            <p className="text-xs" style={{ color: 'var(--fg-muted)' }}>No todos</p>
          ) : (
            todos.map(todo => (
              <div
                key={todo.id}
                className="flex items-center gap-2 p-1.5 rounded"
                style={{ background: 'var(--surface-3)' }}
              >
                {todo.status === 'done' ? (
                  <CheckCircle size={12} style={{ color: 'var(--green)' }} />
                ) : todo.status === 'in_progress' ? (
                  <Clock size={12} style={{ color: 'var(--amber)' }} />
                ) : (
                  <Circle size={12} style={{ color: 'var(--fg-faint)' }} />
                )}
                <span className="text-[12.5px] truncate" style={{ color: 'var(--fg)' }}>{todo.description}</span>
              </div>
            ))
          )}
        </div>
      </Section>

      {/* Available Tools */}
      <Section
        title={`Available Tools (${totalTools})`}
        icon={<Wrench size={14} />}
        expanded={expandedSections.tools}
        onToggle={() => toggleSection('tools')}
      >
        <div className="space-y-3">
          {/* MCP Tools */}
          {connectedServers.length > 0 && (
            <div>
              <div className="flex items-center gap-1.5 mb-1.5">
                <Server size={12} style={{ color: 'var(--blue)' }} />
                <span className="text-[12px] font-medium" style={{ color: 'var(--fg-muted)' }}>MCP</span>
              </div>
              <div className="space-y-1">
                {connectedServers.map(server => (
                  <div key={server.name} className="flex items-center gap-2 px-2 py-1 rounded" style={{ background: 'var(--surface-3)' }}>
                    <span className="w-1.5 h-1.5 rounded-full" style={{ background: 'var(--green)' }} />
                    <span className="text-[12.5px] font-medium truncate" style={{ color: 'var(--fg)' }}>{server.name}</span>
                    <span className="text-[11.5px] ml-auto" style={{ color: 'var(--fg-faint)' }}>{server.tool_names.length}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Skills */}
          {enabledSkills.length > 0 && (
            <div>
              <div className="flex items-center gap-1.5 mb-1.5">
                <Zap size={12} style={{ color: 'var(--accent)' }} />
                <span className="text-[12px] font-medium" style={{ color: 'var(--fg-muted)' }}>Skills</span>
              </div>
              <div className="space-y-1">
                {enabledSkills.map(skill => (
                  <div key={skill.id} className="flex items-center gap-2 px-2 py-1 rounded" style={{ background: 'var(--surface-3)' }}>
                    <span className="w-1.5 h-1.5 rounded-full" style={{ background: 'var(--green)' }} />
                    <span className="text-[12.5px] font-medium truncate" style={{ color: 'var(--fg)' }}>{skill.display_name || skill.id}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {connectedServers.length === 0 && enabledSkills.length === 0 && (
            <p className="text-xs" style={{ color: 'var(--fg-muted)' }}>No tools available</p>
          )}
        </div>
      </Section>
    </InfoPanelCard>
  )
}

/**
 * Floating, collapsible session card (v2.1). Anchored to the top-right of the
 * chat canvas with a float shadow + glass blur; collapses to a small pill so
 * the card never permanently consumes horizontal space.
 */
function InfoPanelCard({ children, totalTools, collapsed, onToggleCollapse }: {
  children: React.ReactNode
  totalTools: number
  collapsed: boolean
  onToggleCollapse: () => void
}) {
  if (collapsed) {
    return (
      <button
        onClick={onToggleCollapse}
        title="Show session panel"
        className="absolute right-4 top-3 z-20 flex items-center gap-2 h-8 px-3 rounded-full transition-all"
        style={{
          background: 'color-mix(in srgb, var(--surface-2) 88%, transparent)',
          backdropFilter: 'blur(16px) saturate(1.3)',
          WebkitBackdropFilter: 'blur(16px) saturate(1.3)',
          border: '1px solid var(--border)',
          boxShadow: 'var(--shadow-float)',
          color: 'var(--fg-secondary)',
          animation: 'rise-in 0.2s var(--ease)',
        }}
      >
        <PanelRightOpen size={13} />
        <span className="text-[12.5px] font-medium">Tools</span>
        <span
          className="text-[11.5px] font-mono px-1.5 py-px rounded-full"
          style={{ background: 'var(--accent-muted)', color: 'var(--accent)' }}
        >
          {totalTools}
        </span>
      </button>
    )
  }

  return (
    <div
      className="absolute right-4 top-3 bottom-3 w-[300px] z-20 flex flex-col overflow-hidden rounded-2xl"
      style={{
        background: 'color-mix(in srgb, var(--surface-2) 92%, transparent)',
        backdropFilter: 'blur(20px) saturate(1.3)',
        WebkitBackdropFilter: 'blur(20px) saturate(1.3)',
        border: '1px solid var(--border)',
        boxShadow: 'var(--shadow-float)',
        animation: 'rise-in 0.25s var(--ease)',
      }}
    >
      {/* Card header */}
      <div
        className="flex items-center gap-2 px-3 h-9 shrink-0"
        style={{ borderBottom: '1px solid var(--border-subtle)' }}
      >
        <span
          className="text-[12.5px] font-medium"
          style={{ color: 'var(--fg-muted)' }}
        >
          Session
        </span>
        <button
          onClick={onToggleCollapse}
          title="Hide session panel"
          className="ml-auto p-1 rounded-md transition-colors"
          style={{ color: 'var(--fg-muted)' }}
        >
          <PanelRightClose size={13} />
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {children}
      </div>
    </div>
  )
}

function Section({ title, icon, expanded, onToggle, children }: {
  title: string
  icon: React.ReactNode
  expanded: boolean
  onToggle: () => void
  children: React.ReactNode
}) {
  return (
    <div className="rounded-md" style={{ background: 'var(--surface-2)', border: '1px solid var(--border)' }}>
      <button
        onClick={onToggle}
        className="w-full flex items-center gap-2 px-3 py-2 text-left"
      >
        <span style={{ color: 'var(--fg-muted)' }}>{icon}</span>
        <span className="text-[12px] font-medium flex-1" style={{ color: 'var(--fg)' }}>{title}</span>
        {expanded ? <ChevronDown size={12} style={{ color: 'var(--fg-muted)' }} /> : <ChevronRight size={12} style={{ color: 'var(--fg-muted)' }} />}
      </button>
      {expanded && (
        <div className="px-3 pb-3">
          {children}
        </div>
      )}
    </div>
  )
}
