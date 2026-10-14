import { create } from 'zustand'
import type { SubagentInfo } from './api'
import { isActive, mergeEvents, mergeMessages, type Communication, type SubagentEvent } from './subagentWorkspace'

interface Workspace {
  agents: Map<string, SubagentInfo>
  events: Map<string, SubagentEvent[]>
  messages: Map<string, Communication[]>
}

export const emptyWorkspace: Workspace = { agents: new Map(), events: new Map(), messages: new Map() }

export const useCollaborationStore = create<{
  sessions: Record<string, Workspace>
  reconcile: (sessionId: string, agents: SubagentInfo[]) => void
  ingest: (sessionId: string, events: SubagentEvent[]) => void
  receive: (sessionId: string, subagentId: string, messages: Communication[]) => void
}>((set) => ({
  sessions: {},
  reconcile: (sessionId, incoming) => set(state => {
    const current = state.sessions[sessionId] ?? emptyWorkspace
    const agents = new Map(current.agents)
    for (const agent of incoming) {
      if ((agent.updated_at ?? 0) >= (agents.get(agent.subagent_id)?.updated_at ?? 0)) agents.set(agent.subagent_id, agent)
    }
    return { sessions: { ...state.sessions, [sessionId]: { ...current, agents } } }
  }),
  receive: (sessionId, subagentId, incoming) => set(state => {
    const current = state.sessions[sessionId] ?? emptyWorkspace
    const messages = new Map(current.messages)
    messages.set(subagentId, mergeMessages(messages.get(subagentId) ?? [], incoming))
    return { sessions: { ...state.sessions, [sessionId]: { ...current, messages } } }
  }),
  ingest: (sessionId, incoming) => set(state => {
    const current = state.sessions[sessionId] ?? emptyWorkspace
    const agents = new Map(current.agents)
    const events = new Map(current.events)
    const messages = new Map(current.messages)
    for (const event of incoming) {
      const id = event.subagentId
      const previous = agents.get(id)
      const base: SubagentInfo = previous ?? { subagent_id: id, name: event.name || id, role: '', task: '', status: 'thinking', current_tool: '', started_at: event.ts, finished_at: null, steps_used: 0, error: '' }
      if (event.ts >= (base.updated_at ?? 0)) {
        const status = event.status ?? base.status
        agents.set(id, { ...base, name: event.name || base.name, status, updated_at: event.ts, run_id: event.runId || base.run_id,
          child_run_id: event.childRunId || base.child_run_id, steps_used: Math.max(base.steps_used, event.step),
          waiting_questions: event.waitingQuestions,
          collaborative: event.collaborative ?? base.collaborative,
          latest_progress: event.kind === 'communication' ? event.messages.filter(m => m.kind === 'progress').at(-1)?.content ?? base.latest_progress : base.latest_progress,
          task: event.kind === 'started' ? event.task : base.task, role: event.kind === 'started' ? event.role : base.role,
          current_tool: event.kind === 'tool_call' ? event.toolName : status === 'tool_calling' ? base.current_tool : '',
          finished_at: isActive(status) ? null : event.ts, error: event.kind === 'finished' ? event.error : base.error,
        })
      } else if (event.kind === 'started' && !base.task) {
        agents.set(id, { ...base, task: event.task, role: event.role, started_at: event.ts })
      }
      events.set(id, mergeEvents(events.get(id) ?? [], [event]))
      if (event.kind === 'communication') messages.set(id, mergeMessages(messages.get(id) ?? [], event.messages))
    }
    return { sessions: { ...state.sessions, [sessionId]: { agents, events, messages } } }
  }),
}))
