import { useState, useEffect, useRef, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  Brain, Wrench, MessageSquare, Flag, Play, AlertTriangle, Shield,
  Circle, GitBranch, Clock, Database, RefreshCw, ArrowLeft,
} from 'lucide-react'
import { api, type TimelineEvent } from '../services/api'

// WS GUI events drop step_id/db id, so live updates poll the REST cursor —
// one authoritative stream, 1.5s latency, no id-mapping hacks.
const POLL_MS = 1500

const NODE_STYLE: Record<string, { icon: typeof Circle; color: string; label: string }> = {
  thinking: { icon: Brain, color: 'var(--accent)', label: 'Thought' },
  tool_start: { icon: Wrench, color: 'var(--amber)', label: 'Tool call' },
  tool_done: { icon: Wrench, color: 'var(--green)', label: 'Tool result' },
  message_delta: { icon: MessageSquare, color: 'var(--blue)', label: 'Answer' },
  run_started: { icon: Play, color: 'var(--fg-muted)', label: 'Run started' },
  run_finished: { icon: Flag, color: 'var(--green)', label: 'Run finished' },
  run_interrupted: { icon: Flag, color: 'var(--red)', label: 'Run interrupted' },
  run_persisted: { icon: Flag, color: 'var(--fg-faint)', label: 'Run persisted' },
  error: { icon: AlertTriangle, color: 'var(--red)', label: 'Error' },
  confirm_request: { icon: Shield, color: 'var(--amber)', label: 'Confirm required' },
  turn_journal: { icon: Brain, color: 'var(--accent)', label: 'Journal' },
}

// TurnRecord.status vocabulary: running | finished | empty_answer | failed | interrupted.
const RUN_STATUS_STYLE: Record<string, { color: string; label: string } | undefined> = {
  failed: { color: 'var(--red)', label: 'Run failed' },
  interrupted: { color: 'var(--red)', label: 'Run interrupted' },
  empty_answer: { color: 'var(--amber)', label: 'Run finished (no answer)' },
}

// 纯 FSM 转移事件 —— 时间线上是噪声（TOOLS_DONE/ANSWER_READY 与紧随其后的
// tool_done/Answer 表达同一件事），渲染时过滤，落盘仍保留完整日志。
const HIDDEN_JOURNAL: Record<string, true> = {
  START: true, TOOLS_DONE: true, ROUND_READY: true, ANSWER_READY: true, ABORT: true,
}

function nodeMeta(e: TimelineEvent): { icon: typeof Circle; color: string; label: string } {
  if (e.event_type === 'turn_journal') {
    const inner = String(e.payload.event || '')
    if (inner === 'ANSWER_THOUGHT') return { icon: Brain, color: 'var(--accent)', label: 'Thought (answer)' }
    if (inner === 'TOOLS_REQUESTED') return { icon: Brain, color: 'var(--accent)', label: 'Thought' }
    if (inner === 'FAULT') return { icon: AlertTriangle, color: 'var(--red)', label: 'Fault' }
    if (inner === 'LOOP_WARNING') return { icon: AlertTriangle, color: 'var(--amber)', label: 'Loop warning' }
    if (inner === 'BUDGET_REMINDER') return { icon: AlertTriangle, color: 'var(--amber)', label: 'Budget reminder' }
    if (inner === 'ANSWER_VETOED') return { icon: Shield, color: 'var(--amber)', label: 'Answer vetoed' }
    return { icon: Circle, color: 'var(--fg-faint)', label: inner || 'Journal' }
  }
  if (e.event_type === 'run_finished') {
    const s = RUN_STATUS_STYLE[String(e.payload.status)] || { color: 'var(--green)', label: 'Run finished' }
    return { icon: Flag, color: s.color, label: s.label }
  }
  return NODE_STYLE[e.event_type] || { icon: Circle, color: 'var(--fg-faint)', label: e.event_type }
}

function nodeDetail(e: TimelineEvent): string {
  const p = e.payload
  switch (e.event_type) {
    case 'turn_journal':
      return [p.thought, p.detail, p.reason,
          p.warn_count !== undefined ? `warning #${String(p.warn_count)}` : '']
        .filter(Boolean).join(' · ')
    case 'tool_start':
      return `${p.name} ${JSON.stringify(p.arguments || {})}`
    case 'tool_done':
      return `${p.name} → ${p.result || ''}`
    case 'message_delta':
      return String(p.text || '').slice(0, 200)
    case 'run_finished':
      // 真出错时把具体原因顶出来，而不是只标一个 error
      return [p.status ? `status=${String(p.status)}` : '', p.error, p.reason, p.detail]
        .filter(Boolean).join(' · ')
    default:
      return Object.keys(p).length ? JSON.stringify(p) : ''
  }
}

export default function TimelinePage() {
  const { sessionId } = useParams<{ sessionId: string }>()
  const navigate = useNavigate()
  const [events, setEvents] = useState<TimelineEvent[]>([])
  const [selected, setSelected] = useState<TimelineEvent | null>(null)
  const [ctx, setCtx] = useState<{ step: number; run: string; messages: Array<{ role: string; content?: string }>; count: number; chars: number } | null>(null)
  const lastIdRef = useRef(0)
  const listRef = useRef<HTMLDivElement>(null)

  const loadInitial = useCallback((sid: string) => {
    api.getSessionEvents(sid, 0)
      .then(({ events: evs, last_id }) => {
        setEvents(evs)
        lastIdRef.current = last_id
      })
      .catch(() => { setEvents([]) })
  }, [])

  // Initial load on session switch + polling cursor for live append.
  useEffect(() => {
    if (!sessionId) return
    setEvents([]); lastIdRef.current = 0; setSelected(null); setCtx(null)
    loadInitial(sessionId)
    const t = setInterval(() => {
      api.getSessionEvents(sessionId, lastIdRef.current)
        .then(({ events: evs, last_id }) => {
          if (evs.length > 0) {
            setEvents(prev => [...prev, ...evs])
            lastIdRef.current = last_id
          }
        })
        .catch(() => {})
    }, POLL_MS)
    return () => clearInterval(t)
  }, [sessionId, loadInitial])

  // Load context snapshot for the selected event's step.
  useEffect(() => {
    if (!selected || !sessionId || selected.step_id <= 0) { if (!selected || selected.step_id <= 0) setCtx(null); return }
    let cancelled = false
    api.getContextSnapshot(sessionId, selected.run_id, selected.step_id)
      .then(snap => {
        if (cancelled) return
        setCtx({
          step: selected.step_id, run: selected.run_id,
          messages: snap.messages as Array<{ role: string; content?: string }>,
          count: snap.message_count, chars: snap.char_count,
        })
      })
      .catch(() => { if (!cancelled) setCtx(null) })
    return () => { cancelled = true }
  }, [selected, sessionId])

  const visible = events.filter(e =>
    e.event_type !== 'turn_journal'
    || (String(e.payload.event) !== '' && !HIDDEN_JOURNAL[String(e.payload.event)])
  )
  const runs = new Map<string, TimelineEvent[]>()
  for (const e of visible) {
    if (!runs.has(e.run_id)) runs.set(e.run_id, [])
    runs.get(e.run_id)!.push(e)
  }

  const roleColor: Record<string, string> = {
    system: 'var(--fg-faint)', user: 'var(--blue)',
    assistant: 'var(--accent)', tool: 'var(--green)',
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="px-6 py-4 flex items-center justify-between" style={{ borderBottom: '1px solid var(--border)' }}>
        <div>
          <h1 className="text-lg font-semibold" style={{ color: 'var(--fg)' }}>Timeline</h1>
          <p className="text-xs mt-0.5" style={{ color: 'var(--fg-muted)' }}>
            Every step the agent took, with the full context sent to the model at each step
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => navigate(sessionId ? `/chat/${sessionId}` : '/')}
            className="flex items-center gap-1.5 px-2 py-1.5 rounded text-xs transition-colors hover:opacity-70"
            style={{ color: 'var(--fg-muted)', border: '1px solid var(--border)' }}
          >
            <ArrowLeft size={12} />
            Back to Chat
          </button>
          <span className="text-[10px] font-mono px-1.5 py-1 rounded" style={{ color: 'var(--fg-faint)', background: 'var(--surface-2)' }}>
            {sessionId ?? 'no session'}
          </span>
          <button
            onClick={() => sessionId && loadInitial(sessionId)}
            className="p-1.5 rounded hover:opacity-70"
            style={{ color: 'var(--fg-muted)' }}
            title="Reload"
          >
            <RefreshCw size={14} />
          </button>
        </div>
      </div>

      <div className="flex-1 flex overflow-hidden">
        {/* Timeline column */}
        <div ref={listRef} className="flex-1 overflow-y-auto px-6 py-4">
          {!sessionId ? (
            <div className="flex flex-col items-center justify-center py-12 gap-3">
              <GitBranch size={32} style={{ color: 'var(--fg-faint)' }} />
              <p className="text-sm" style={{ color: 'var(--fg-muted)' }}>No session selected.</p>
            </div>
          ) : events.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 gap-3">
              <GitBranch size={32} style={{ color: 'var(--fg-faint)' }} />
              <p className="text-sm" style={{ color: 'var(--fg-muted)' }}>
                No events recorded yet — send a message in Chat first.
              </p>
            </div>
          ) : (
            [...runs.entries()].map(([runId, runEvents]) => (
              <div key={runId} className="mb-6">
                <div className="flex items-center gap-2 mb-2 sticky top-0 py-1" style={{ background: 'var(--surface-1, transparent)' }}>
                  <GitBranch size={12} style={{ color: 'var(--fg-faint)' }} />
                  <span className="text-[10px] font-mono uppercase tracking-wider" style={{ color: 'var(--fg-faint)' }}>
                    run {runId.slice(-8)}
                  </span>
                  <span className="text-[10px]" style={{ color: 'var(--fg-faint)' }}>{runEvents.length} events</span>
                </div>
                <div className="relative ml-2">
                  <div className="absolute left-[7px] top-1 bottom-1 w-px" style={{ background: 'var(--border)' }} />
                  {runEvents.map((e, i) => {
                    const meta = nodeMeta(e)
                    const Icon = meta.icon
                    const isSel = selected?.id === e.id
                    const prevStep = i > 0 ? runEvents[i - 1].step_id : -1
                    return (
                      <div key={e.id}>
                        {e.step_id > 0 && e.step_id !== prevStep && (
                          <div className="ml-6 mt-3 mb-1 text-[10px] font-semibold uppercase tracking-wider" style={{ color: 'var(--fg-muted)' }}>
                            Step {e.step_id}
                          </div>
                        )}
                        <button
                          onClick={() => setSelected(isSel ? null : e)}
                          className="relative flex items-start gap-3 w-full text-left py-1.5 px-1 rounded"
                          style={isSel ? { background: 'var(--surface-3)' } : undefined}
                        >
                          <span
                            className="relative z-10 mt-0.5 w-[15px] h-[15px] rounded-full flex items-center justify-center shrink-0"
                            style={{ background: 'var(--surface-2)', border: `2px solid ${meta.color}` }}
                          >
                            <Icon size={8} style={{ color: meta.color }} />
                          </span>
                          <span className="flex-1 min-w-0">
                            <span className="flex items-baseline gap-2">
                              <span className="text-xs font-medium" style={{ color: meta.color }}>{meta.label}</span>
                              <span className="text-[10px] shrink-0" style={{ color: 'var(--fg-faint)' }}>
                                <Clock size={8} className="inline mr-0.5 -mt-0.5" />{new Date(e.ts * 1000).toLocaleTimeString()}
                              </span>
                            </span>
                            {nodeDetail(e) && (
                              <span className="block text-[11px] font-mono truncate" style={{ color: 'var(--fg-muted)' }}>
                                {nodeDetail(e)}
                              </span>
                            )}
                          </span>
                        </button>
                      </div>
                    )
                  })}
                </div>
              </div>
            ))
          )}
        </div>

        {/* Context panel */}
        <div
          className="w-[420px] shrink-0 overflow-y-auto px-4 py-4"
          style={{ borderLeft: '1px solid var(--border)' }}
        >
          {ctx ? (
            <>
              <div className="flex items-center gap-2 mb-1">
                <Database size={12} style={{ color: 'var(--accent)' }} />
                <span className="text-xs font-semibold" style={{ color: 'var(--fg)' }}>
                  Context @ step {ctx.step}
                </span>
              </div>
              <p className="text-[10px] mb-3 font-mono" style={{ color: 'var(--fg-faint)' }}>
                run {ctx.run.slice(-8)} · {ctx.count} messages · {ctx.chars.toLocaleString()} chars (~{Math.round(ctx.chars / 4).toLocaleString()} tokens)
              </p>
              <div className="space-y-2">
                {ctx.messages.map((m, i) => (
                  <div key={i} className="p-2 rounded" style={{ background: 'var(--surface-2)' }}>
                    <span
                      className="text-[9px] font-semibold uppercase tracking-wider"
                      style={{ color: roleColor[m.role] || 'var(--fg-faint)' }}
                    >
                      {m.role}
                    </span>
                    <pre className="text-[11px] whitespace-pre-wrap break-words mt-1" style={{ color: 'var(--fg)', fontFamily: 'inherit' }}>
                      {m.content || JSON.stringify(m)}
                    </pre>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <div className="flex flex-col items-center justify-center h-full gap-2">
              <Database size={24} style={{ color: 'var(--fg-faint)' }} />
              <p className="text-xs text-center px-4" style={{ color: 'var(--fg-muted)' }}>
                {selected && selected.step_id <= 0
                  ? 'This event has no model call attached (run-level event).'
                  : 'Click a timeline node to see the exact context sent to the model at that step.'}
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
