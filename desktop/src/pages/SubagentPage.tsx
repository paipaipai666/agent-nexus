import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, Bot, Square } from 'lucide-react'
import { api, type SubagentInfo, type TimelineEvent } from '../services/api'
import { wsPool } from '../services/ws'
import { useSession } from '../components/session/SessionManager'

type SubagentStatus = 'thinking' | 'tool_calling' | 'interrupted' | 'completed' | 'failed'

/** Wire shape of a subagent_event payload (mirrors the backend contract). */
interface SubagentEventPayload {
  subagent_id?: string
  name?: string
  kind?: string
  status?: string
  role?: string
  task?: string
  content?: string
  tool_name?: string
  tool_call_id?: string
  arguments?: unknown
  result?: unknown
  duration_ms?: number
  reason?: string
  summary?: string
  error?: string
}

function isSubagentEventPayload(v: unknown): v is SubagentEventPayload {
  return typeof v === 'object' && v !== null
    && typeof (v as { subagent_id?: unknown }).subagent_id === 'string'
    && typeof (v as { kind?: unknown }).kind === 'string'
}

const STATUS_LABEL: Record<SubagentStatus, string> = {
  thinking: '思考中',
  tool_calling: '调用工具中',
  interrupted: '已中断',
  completed: '已完成',
  failed: '失败',
}

const STATUS_COLOR: Record<SubagentStatus, string> = {
  thinking: 'var(--amber)',
  tool_calling: 'var(--blue)',
  interrupted: 'var(--red)',
  completed: 'var(--green)',
  failed: 'var(--red)',
}

const ACTIVE: Record<string, true> = { thinking: true, tool_calling: true }

type TrajEntry =
  | { kind: 'thinking' | 'reasoning'; id: string; content: string }
  | { kind: 'note'; id: string; content: string }
  | { kind: 'tool'; id: string; toolCallId: string; toolName: string; args: string; status: 'running' | 'done'; result: string; durationMs: number }
  | { kind: 'text'; id: string; content: string }
  | { kind: 'summary'; id: string; status: string; content: string }

/** Fold one subagent_event into trajectory state. Shared by timeline
 *  backfill and the live WS subscription so ordering semantics stay
 *  identical for both. */
function foldEvent(
  data: SubagentEventPayload,
  entries: TrajEntry[],
  nextId: () => string,
): TrajEntry[] {
  const kind = data.kind
  if (kind === 'thinking') {
    const content = (data.content || '').trim()
    if (!content) return entries
    return [...entries, { kind: 'thinking', id: nextId(), content }]
  }
  if (kind === 'reasoning') {
    const content = data.content || ''
    const last = entries[entries.length - 1]
    if (last && last.kind === 'reasoning') {
      return [...entries.slice(0, -1), { ...last, content: last.content + content }]
    }
    return [...entries, { kind: 'reasoning', id: nextId(), content }]
  }
  if (kind === 'token') {
    const content = data.content || ''
    const last = entries[entries.length - 1]
    if (last && last.kind === 'text') {
      return [...entries.slice(0, -1), { ...last, content: last.content + content }]
    }
    return [...entries, { kind: 'text', id: nextId(), content }]
  }
  if (kind === 'tool_call') {
    let args = ''
    try { args = JSON.stringify(data.arguments ?? {}) } catch { args = '' }
    return [...entries, {
      kind: 'tool', id: nextId(), toolCallId: data.tool_call_id || '',
      toolName: data.tool_name || '', args,
      status: 'running' as const, result: '', durationMs: 0,
    }]
  }
  if (kind === 'tool_result') {
    const result = typeof data.result === 'string' ? data.result : String(data.result ?? '')
    const idx = (() => {
      if (data.tool_call_id) {
        const i = entries.findIndex(e => e.kind === 'tool' && e.toolCallId === data.tool_call_id && e.status === 'running')
        if (i >= 0) return i
      }
      for (let i = entries.length - 1; i >= 0; i--) {
        const e = entries[i]
        if (e.kind === 'tool' && e.status === 'running' && (!data.tool_name || e.toolName === data.tool_name)) return i
      }
      return -1
    })()
    if (idx < 0) return entries
    const updated = [...entries]
    const cur = updated[idx]
    if (cur.kind === 'tool') {
      updated[idx] = { ...cur, status: 'done', result, durationMs: data.duration_ms ?? 0 }
    }
    return updated
  }
  if (kind === 'retry') {
    return [...entries, { kind: 'note', id: nextId(), content: `重试: ${data.reason || ''}` }]
  }
  if (kind === 'finished') {
    const parts = [data.summary, data.error].filter(Boolean).join('\n')
    return [...entries, { kind: 'summary', id: nextId(), status: data.status || '', content: parts }]
  }
  return entries
}

export default function SubagentPage() {
  const { sessionId = '', subagentId = '' } = useParams<{ sessionId?: string; subagentId?: string }>()
  const navigate = useNavigate()
  const { setSessionId, getLiveSessionState } = useSession()

  const [meta, setMeta] = useState<{ name: string; role: string; task: string } | null>(null)
  const [status, setStatus] = useState<SubagentStatus>('thinking')
  const [entries, setEntries] = useState<TrajEntry[]>([])
  const [found, setFound] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [cancelRequested, setCancelRequested] = useState(false)
  const idCounter = useRef(0)
  const nextId = () => `se-${++idCounter.current}`
  const scrollRef = useRef<HTMLDivElement>(null)

  // Activate the session so the WS connects and SessionManager subscribes
  // (same entry point ChatPage uses: setSessionId → activateSession).
  useEffect(() => {
    if (!sessionId) return
    api.restoreSession(sessionId).catch(() => {})
    setSessionId(sessionId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId])

  // Backfill from timeline + REST (covers reload mid-run and older entries),
  // then attach the live subscription.
  useEffect(() => {
    if (!sessionId || !subagentId) return
    let cancelled = false

    api.getSubagents(sessionId).then(d => {
      if (cancelled) return
      const info: SubagentInfo | undefined = (d.subagents || []).find(s => s.subagent_id === subagentId)
      if (info) {
        setMeta({ name: info.name, role: info.role, task: info.task })
        setStatus(info.status)
        setFound(true)
      }
    }).catch(() => {})

    api.getSessionEvents(sessionId, 0).then(d => {
      if (cancelled) return
      const rows = (d.events || []).filter((e: TimelineEvent) =>
        e.event_type === 'subagent_event' && isSubagentEventPayload(e.payload)
          && e.payload.subagent_id === subagentId)
      if (rows.length > 0) {
        let acc: TrajEntry[] = []
        let sawStarted = false
        for (const row of rows) {
          const p: SubagentEventPayload = row.payload
          if (p.kind === 'started') {
            sawStarted = true
            setMeta({ name: p.name || subagentId, role: p.role || '', task: p.task || '' })
            if (p.status === 'thinking' || p.status === 'tool_calling') setStatus(p.status)
            setFound(true)
            continue
          }
          if (p.kind === 'finished' && p.status) {
            setStatus(p.status as SubagentStatus)
            setFound(true)
          }
          acc = foldEvent(p, acc, nextId)
        }
        if (sawStarted) setEntries(acc)
      }
      setLoaded(true)
    }).catch(() => setLoaded(true))

    const unsub = wsPool.on(sessionId, 'subagent_event', (data: SubagentEventPayload) => {
      if (cancelled || data?.subagent_id !== subagentId) return
      if (data.kind === 'started') {
        setMeta({ name: data.name || subagentId, role: data.role || '', task: data.task || '' })
      }
      if (data.status && data.status in STATUS_LABEL) {
        setStatus(data.status as SubagentStatus)
      }
      if (data.kind === 'finished') {
        setCancelRequested(false)
      }
      setFound(true)
      setEntries(prev => foldEvent(data, prev, nextId))
    })

    return () => { cancelled = true; unsub() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, subagentId])

  // Live meta from the store when available (fresher than REST).
  const storeMeta = sessionId ? getLiveSessionState(sessionId)?.subagents.get(subagentId) : undefined
  const name = storeMeta?.name || meta?.name || subagentId
  const task = storeMeta?.task || meta?.task || ''
  const displayStatus: SubagentStatus = storeMeta?.status || status
  const isActive = displayStatus in ACTIVE

  useEffect(() => {
    scrollRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [entries])

  const handleInterrupt = () => {
    if (!sessionId || !isActive) return
    wsPool.cancelSubagent(sessionId, subagentId)
    setCancelRequested(true)
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden" style={{ background: 'var(--surface-0)' }}>
      {/* Header */}
      <div
        className="flex items-center gap-3 px-5 py-3 shrink-0"
        style={{ borderBottom: '1px solid var(--border)', background: 'var(--surface-1)' }}
      >
        <button
          onClick={() => navigate(`/chat/${sessionId}`)}
          className="w-8 h-8 flex items-center justify-center rounded-lg transition-colors hover:bg-[var(--surface-2)]"
          style={{ color: 'var(--fg-muted)' }}
          title="返回对话"
        >
          <ArrowLeft size={15} />
        </button>
        <Bot size={16} style={{ color: 'var(--accent)', flexShrink: 0 }} />
        <div className="min-w-0">
          <div className="text-[14px] font-medium truncate" style={{ color: 'var(--fg)' }}>{name}</div>
          {task && <div className="text-[11px] truncate" style={{ color: 'var(--fg-faint)' }} title={task}>{task}</div>}
        </div>
        <span
          className="ml-1 flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] shrink-0"
          style={{ color: STATUS_COLOR[displayStatus] || 'var(--fg-muted)', border: `1px solid ${STATUS_COLOR[displayStatus] || 'var(--border)'}` }}
        >
          <span className="w-1.5 h-1.5 rounded-full" style={{ background: STATUS_COLOR[displayStatus] || 'var(--fg-faint)' }} />
          {STATUS_LABEL[displayStatus] || displayStatus}
        </span>
        {isActive && (
          <button
            onClick={handleInterrupt}
            disabled={cancelRequested}
            className="ml-auto flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[12px] shrink-0 transition-colors disabled:opacity-60"
            style={{ background: 'var(--red-muted)', color: 'var(--red)' }}
            title="中断该子代理"
          >
            <Square size={12} />
            {cancelRequested ? '中断中…' : '中断'}
          </button>
        )}
      </div>

      {/* Trajectory */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        <div className="max-w-[720px] mx-auto space-y-2">
          {!found && loaded && (
            <p className="text-xs py-8 text-center" style={{ color: 'var(--fg-muted)' }}>
              未找到该子代理（可能来自更早的服务进程）
            </p>
          )}
          {entries.map(e => (
            <TrajCard key={e.id} entry={e} />
          ))}
          <div ref={scrollRef} />
        </div>
      </div>
    </div>
  )
}

function TrajCard({ entry }: { entry: TrajEntry }) {
  if (entry.kind === 'thinking' || entry.kind === 'reasoning' || entry.kind === 'note') {
    return (
      <div className="px-3.5 py-2.5 rounded-lg text-[13px] whitespace-pre-wrap break-words" style={{ background: 'var(--surface-2)', color: 'var(--fg-muted)' }}>
        {entry.content}
      </div>
    )
  }
  if (entry.kind === 'tool') {
    return (
      <div className="rounded-lg overflow-hidden" style={{ border: '1px solid var(--border)', background: 'var(--surface-1)' }}>
        <div className="flex items-center gap-2 px-3.5 py-2">
          <span
            className="w-1.5 h-1.5 rounded-full shrink-0"
            style={{ background: entry.status === 'running' ? 'var(--amber)' : 'var(--green)' }}
          />
          <span className="text-[12.5px] font-medium" style={{ color: 'var(--fg)' }}>
            {entry.toolName}
          </span>
          <span className="ml-auto text-[11px] font-mono" style={{ color: 'var(--fg-faint)' }}>
            {entry.status === 'running' ? 'running' : entry.durationMs ? `${(entry.durationMs / 1000).toFixed(1)}s` : 'done'}
          </span>
        </div>
        {(entry.args && entry.args !== '{}') || entry.result ? (
          <div className="px-3.5 pb-2.5 space-y-1.5">
            {entry.args && entry.args !== '{}' && (
              <pre className="px-3 py-2 rounded-md whitespace-pre-wrap break-words max-h-[160px] overflow-auto text-[11.5px] font-mono" style={{ background: 'var(--surface-3)', color: 'var(--fg-muted)' }}>
                {entry.args}
              </pre>
            )}
            {entry.result && (
              <pre className="px-3 py-2 rounded-md whitespace-pre-wrap break-words max-h-[220px] overflow-auto text-[11.5px] font-mono" style={{ background: 'var(--surface-3)', color: 'var(--fg-muted)' }}>
                {entry.result}
              </pre>
            )}
          </div>
        ) : null}
      </div>
    )
  }
  if (entry.kind === 'text') {
    return (
      <div className="px-3.5 py-2.5 rounded-lg text-[14px] whitespace-pre-wrap break-words" style={{ background: 'var(--surface-1)', color: 'var(--fg)' }}>
        {entry.content}
      </div>
    )
  }
  // summary
  return (
    <div className="px-3.5 py-2.5 rounded-lg text-[12.5px] whitespace-pre-wrap break-words" style={{ border: '1px solid var(--border)', color: 'var(--fg-muted)' }}>
      {entry.content}
    </div>
  )
}
