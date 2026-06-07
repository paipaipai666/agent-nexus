import { useState, useEffect } from 'react'
import { ChevronDown, ChevronRight, CheckCircle, Circle, Clock, Wrench, Server, Zap, ListTodo } from 'lucide-react'
import { api } from '../../services/api'

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

export default function InfoPanel({ sessionId }: { sessionId: string | null }) {
  const [todos, setTodos] = useState<Todo[]>([])
  const [skills, setSkills] = useState<Skill[]>([])
  const [mcpServers, setMcpServers] = useState<MCPServer[]>([])
  const [expandedSections, setExpandedSections] = useState<Record<string, boolean>>({
    timeline: true,
    todos: true,
    tools: true,
  })

  useEffect(() => {
    if (!sessionId) return

    const fetchData = () => {
      api.getTodos(sessionId).then(d => setTodos(d.items || [])).catch(() => {})
      api.listSkills().then(d => setSkills(d.skills || [])).catch(() => {})
      api.getMcpStatus().then(d => setMcpServers(d.servers || [])).catch(() => {})
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

  return (
    <div
      className="w-[280px] shrink-0 flex flex-col overflow-hidden"
      style={{
        background: 'var(--surface-1)',
        borderLeft: '1px solid var(--border)',
      }}
    >
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* Task Timeline */}
        <Section
          title="Task Timeline"
          icon={<Clock size={14} />}
          expanded={expandedSections.timeline}
          onToggle={() => toggleSection('timeline')}
        >
          <div className="space-y-2">
            {todos.length === 0 ? (
              <p className="text-xs" style={{ color: 'var(--fg-muted)' }}>No tasks yet</p>
            ) : (
              todos.slice(0, 5).map(todo => (
                <div key={todo.id} className="flex items-start gap-2">
                  {todo.status === 'done' ? (
                    <CheckCircle size={12} className="mt-0.5 shrink-0" style={{ color: 'var(--green)' }} />
                  ) : todo.status === 'in_progress' ? (
                    <Clock size={12} className="mt-0.5 shrink-0" style={{ color: 'var(--amber)' }} />
                  ) : (
                    <Circle size={12} className="mt-0.5 shrink-0" style={{ color: 'var(--fg-faint)' }} />
                  )}
                  <span className="text-xs" style={{ color: 'var(--fg-secondary)' }}>{todo.description}</span>
                </div>
              ))
            )}
          </div>
        </Section>

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
                  style={{ background: 'var(--surface-2)' }}
                >
                  {todo.status === 'done' ? (
                    <CheckCircle size={12} style={{ color: 'var(--green)' }} />
                  ) : todo.status === 'in_progress' ? (
                    <Clock size={12} style={{ color: 'var(--amber)' }} />
                  ) : (
                    <Circle size={12} style={{ color: 'var(--fg-faint)' }} />
                  )}
                  <span className="text-xs truncate" style={{ color: 'var(--fg)' }}>{todo.description}</span>
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
                  <span className="text-[10px] font-medium uppercase tracking-wider" style={{ color: 'var(--fg-muted)' }}>MCP</span>
                </div>
                <div className="space-y-1">
                  {connectedServers.map(server => (
                    <div key={server.name} className="flex items-center gap-2 px-2 py-1 rounded" style={{ background: 'var(--surface-2)' }}>
                      <span className="w-1.5 h-1.5 rounded-full" style={{ background: 'var(--green)' }} />
                      <span className="text-[11px] font-medium truncate" style={{ color: 'var(--fg)' }}>{server.name}</span>
                      <span className="text-[10px] ml-auto" style={{ color: 'var(--fg-faint)' }}>{server.tool_names.length}</span>
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
                  <span className="text-[10px] font-medium uppercase tracking-wider" style={{ color: 'var(--fg-muted)' }}>Skills</span>
                </div>
                <div className="space-y-1">
                  {enabledSkills.map(skill => (
                    <div key={skill.id} className="flex items-center gap-2 px-2 py-1 rounded" style={{ background: 'var(--surface-2)' }}>
                      <span className="w-1.5 h-1.5 rounded-full" style={{ background: 'var(--green)' }} />
                      <span className="text-[11px] font-medium truncate" style={{ color: 'var(--fg)' }}>{skill.display_name || skill.id}</span>
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
