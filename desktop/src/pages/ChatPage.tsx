import React, { useState, useRef, useEffect, useCallback } from 'react'
import { useParams, useLocation, useNavigate } from 'react-router-dom'
import { Send, Square, Undo2, Redo2, History, ChevronDown, ChevronRight } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../services/api'
import { agentWs } from '../services/ws'
import { animateMessage } from '../utils/animations'
import { useSession } from '../components/session/SessionProvider'
import InfoPanel from '../components/layout/InfoPanel'

interface Message {
  id: string
  role: 'user' | 'assistant' | 'system' | 'tool'
  content: string
  toolName?: string
  toolStatus?: 'running' | 'done' | 'error'
  timestamp: Date
}

interface Checkpoint { id: string; question: string; answer: string; is_head: boolean }

const COMMAND_DEFS = [
  { cmd: '/help', desc: 'Show command help', category: 'system' },
  { cmd: '/clear', desc: 'Clear screen', category: 'system' },
  { cmd: '/undo', desc: 'Revert to previous checkpoint', category: 'system' },
  { cmd: '/redo', desc: 'Redo to next checkpoint', category: 'system' },
  { cmd: '/log', desc: 'View checkpoint log', category: 'system' },
  { cmd: '/status', desc: 'View version status', category: 'system' },
  { cmd: '/compact', desc: 'Compress conversation context', category: 'system' },
  { cmd: '/sessions', desc: 'List recent sessions', category: 'system' },
  { cmd: '/switch', desc: 'Switch session (usage: /switch <id>)', category: 'system' },
  { cmd: '/skill', desc: 'Manage skills (list/status/use/enable/disable)', category: 'skill' },
  { cmd: '/mcp', desc: 'Manage MCP servers (status/tools/resources)', category: 'mcp' },
  { cmd: '/plugin', desc: 'Manage plugins (list/status/enable/disable)', category: 'plugin' },
]

const animatedIds = new Set<string>()

/* ─── Collapsible Section ─── */
function Collapsible({ header, children, defaultExpanded = false, className = '' }: {
  header: React.ReactNode
  children: React.ReactNode
  defaultExpanded?: boolean
  className?: string
}) {
  const [expanded, setExpanded] = useState(defaultExpanded)

  return (
    <div className={className}>
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1.5 w-full text-left hover:opacity-80 transition-opacity"
      >
        {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        {header}
      </button>
      {expanded && (
        <div className="mt-1.5 ml-4">
          {children}
        </div>
      )}
    </div>
  )
}

/* ─── Tool Card ─── */
const ToolCard = React.memo(function ToolCard({ msg }: { msg: Message }) {
  const statusColor = msg.toolStatus === 'running' ? 'var(--amber)' : msg.toolStatus === 'error' ? 'var(--red)' : 'var(--green)'
  const statusBg = msg.toolStatus === 'running' ? 'var(--amber-muted)' : msg.toolStatus === 'error' ? 'var(--red-muted)' : 'var(--green-muted)'
  const statusLabel = msg.toolStatus === 'running' ? 'running' : msg.toolStatus === 'error' ? 'error' : 'done'

  const lines = msg.content.split('\n')
  const hasDiff = lines.some(l => l.startsWith('+') || l.startsWith('-') || l.startsWith('@@'))

  return (
    <div className="my-3 overflow-hidden max-w-[560px]" style={{ background: 'var(--surface-1)', border: '1px solid var(--border)', borderRadius: 'var(--radius)' }}>
      <Collapsible
        defaultExpanded={msg.toolStatus === 'running'}
        header={
          <div className="flex items-center gap-2 flex-1 min-w-0">
            <div
              className="w-4 h-4 rounded flex items-center justify-center text-[9px] font-semibold shrink-0"
              style={{ background: statusBg, color: statusColor }}
            >
              {(msg.toolName || 'T')[0].toUpperCase()}
            </div>
            <span className="font-mono text-[11px] truncate" style={{ color: 'var(--accent)' }}>{msg.toolName || 'tool'}</span>
            <div className="ml-auto flex items-center gap-1 text-[10px] font-medium shrink-0" style={{ color: statusColor }}>
              {msg.toolStatus === 'running' && (
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="animate-spin"><circle cx="12" cy="12" r="10" strokeDasharray="50" strokeDashoffset="15" /></svg>
              )}
              {statusLabel}
            </div>
          </div>
        }
        className="px-3 py-1.5 font-mono"
      >
        <div className="px-3.5 py-2.5 font-mono text-xs leading-relaxed" style={{ color: 'var(--fg-secondary)' }}>
          {hasDiff ? (
            <div className="font-mono text-xs leading-[1.7]">
              {lines.map((line, i) => {
                if (line.startsWith('@@')) return <div key={i} style={{ color: 'var(--blue)' }}>{line}</div>
                if (line.startsWith('+') && !line.startsWith('+++')) return <div key={i} style={{ color: 'var(--green)' }}>{line}</div>
                if (line.startsWith('-') && !line.startsWith('---')) return <div key={i} style={{ color: 'var(--red)' }}>{line}</div>
                return <div key={i}>{line || ' '}</div>
              })}
            </div>
          ) : (
            <pre className="whitespace-pre-wrap">{msg.content}</pre>
          )}
        </div>
      </Collapsible>
    </div>
  )
})

/* ─── Message Bubble ─── */
const MessageBubble = React.memo(function MessageBubble({ msg }: { msg: Message }) {
  return (
    <div
      ref={(el) => {
        if (el && !animatedIds.has(msg.id)) {
          animatedIds.add(msg.id)
          animateMessage(el, msg.role)
        }
      }}
      className="py-2"
    >
      <div className="max-w-3xl mx-auto px-6">
        {/* Role label — only for user messages */}
        {msg.role === 'user' && (
          <div className="flex items-center gap-2 mb-1">
            <span className="text-[11px] uppercase tracking-wider" style={{ color: 'var(--accent)', fontFamily: 'var(--font-display)' }}>
              You
            </span>
          </div>
        )}

        {/* Content */}
        {msg.role === 'tool' ? (
          <ToolCard msg={msg} />
        ) : msg.role === 'user' ? (
          <div className="text-[14px] leading-relaxed" style={{ color: 'var(--fg)' }}>
            {msg.content}
          </div>
        ) : msg.role === 'system' ? (
          <Collapsible
            defaultExpanded={false}
            header={
              <span className="text-[11px] font-mono" style={{ color: 'var(--fg-muted)' }}>
                {msg.content.slice(0, 60)}{msg.content.length > 60 ? '...' : ''}
              </span>
            }
          >
            <pre className="whitespace-pre-wrap font-mono text-xs leading-relaxed" style={{ color: 'var(--fg-muted)' }}>
              {msg.content}
            </pre>
          </Collapsible>
        ) : (
          <div className="markdown-body">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
          </div>
        )}
      </div>
    </div>
  )
})

/* ─── Main Chat Page ─── */
export default function ChatPage() {
  const { sessionId: routeSessionId } = useParams<{ sessionId?: string }>()
  const location = useLocation()
  const navigate = useNavigate()
  const currentSessionIdRef = useRef<string | null>(null)
  const skipNextDisconnectRef = useRef(false)
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [isRunning, setIsRunning] = useState(false)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [currentRunId, setCurrentRunId] = useState<string | null>(null)
  const [confirmRequest, setConfirmRequest] = useState<{ summary: string } | null>(null)
  const currentAssistantIdRef = useRef<string | null>(null)
  const currentReasoningIdRef = useRef<string | null>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const msgCounterRef = useRef(0)
  const messageQueueRef = useRef<string[]>([])

  const tokenBufferRef = useRef<string>('')
  const tokenFlushRef = useRef<number>(0)

  // HUD state
  const [versionStatus, setVersionStatus] = useState<any>(null)
  const [runtimeStatus, setRuntimeStatus] = useState<any>(null)
  const [checkpoints, setCheckpoints] = useState<Checkpoint[]>([])
  const [showCheckpoints, setShowCheckpoints] = useState(false)

  const [showPalette, setShowPalette] = useState(false)
  const [paletteFilter, setPaletteFilter] = useState('')
  const [paletteIndex, setPaletteIndex] = useState(0)
  const paletteAnimDoneRef = useRef(false)

  const [skills, setSkills] = useState<Array<{ id: string; display_name: string; description: string; enabled: boolean }>>([])
  const [mcpTools, setMcpTools] = useState<Array<{ server: string; tool: string; transport: string }>>([])
  const [plugins, setPlugins] = useState<Record<string, any>>({})

  const { setSessionId: setGlobalSessionId, setModelName, setContextUsed, setRuntimeInfo, setCwd, setToolCount, setTodoCount } = useSession()

  const scrollRafRef = useRef<number>(0)
  const lastEscapeAtRef = useRef<number>(0)
  const ESC_DOUBLE_TAP_MS = 600
  const scrollToBottom = useCallback(() => {
    if (scrollRafRef.current) return
    scrollRafRef.current = requestAnimationFrame(() => {
      scrollRafRef.current = 0
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
    })
  }, [])

  const cleanToolContent = (content: string): { name: string; display: string } => {
    const actionMatch = content.match(/^Action:\s*(\w+)\[/)
    const name = actionMatch ? actionMatch[1] : 'tool'
    const obsIdx = content.indexOf('\nObservation:')
    let display = obsIdx >= 0 ? content.slice(obsIdx + 13).trim() : content
    if (display.length > 500) display = display.slice(0, 500) + '\n...(truncated)'
    return { name, display }
  }

  const loadAndDisplayMessages = useCallback(async () => {
    try {
      let stm: Array<{ role: string; content: string; ts?: number }>
      try {
        const currentSid = sessionIdRef.current
        const hist = await api.listSessionHistory(0, currentSid || undefined)
        stm = hist.messages && hist.messages.length > 0 ? hist.messages : (await api.listShortMemories()).messages
      } catch {
        stm = (await api.listShortMemories()).messages
      }
      if (!stm || stm.length === 0) { setMessages([]); return }

      const transformed: Message[] = []
      let idx = 0
      const ts = (m: any) => new Date(m.ts || Date.now())
      let pendingTools: Message[] = []

      const flushPendingTools = () => {
        for (const t of pendingTools) { t.id = `h-${idx++}`; transformed.push(t) }
        pendingTools = []
      }

      for (const m of stm) {
        const role = m.role
        const content = (m.content || '').trim()

        if (role === 'system' && content.startsWith('[上下文已裁剪]')) continue
        if (role === 'system' && content.startsWith('[恢复文件]')) continue

        if (role === 'system' && content.startsWith('[最终答案]')) {
          const answer = content.replace(/^\[最终答案\]\s*/, '').trim()
          if (answer) { flushPendingTools(); transformed.push({ id: `h-${idx++}`, role: 'assistant', content: answer, timestamp: ts(m) }) }
          continue
        }

        if (role === 'system' && content.startsWith('[会话摘要]')) {
          const summary = content.replace(/^\[会话摘要\]\s*/, '').trim()
          if (summary) {
            flushPendingTools()
            const display = summary.length > 300 ? summary.slice(0, 300) + '…' : summary
            transformed.push({ id: `h-${idx++}`, role: 'system', content: `[Context compacted] ${display}`, timestamp: ts(m) })
          }
          continue
        }

        if (role === 'user') { flushPendingTools(); transformed.push({ id: `h-${idx++}`, role: 'user', content, timestamp: ts(m) }); continue }

        if (role === 'tool') {
          const { name, display } = cleanToolContent(m.content)
          pendingTools.push({ id: '', role: 'tool', content: display, toolName: name, toolStatus: 'done', timestamp: ts(m) })
          continue
        }

        if (role === 'assistant') { flushPendingTools(); transformed.push({ id: `h-${idx++}`, role: 'system', content, timestamp: ts(m) }); continue }

        if (role === 'system' && content.length > 0) { flushPendingTools(); transformed.push({ id: `h-${idx++}`, role: 'system', content, timestamp: ts(m) }) }
      }

      flushPendingTools()
      setMessages(transformed)
    } catch (err) { console.error('Failed to load messages:', err) }
  }, [])

  const sessionIdRef = useRef<string | null>(null)

  useEffect(() => {
    setGlobalSessionId(sessionId)
  }, [sessionId, setGlobalSessionId])

  useEffect(() => {
    if (runtimeStatus?.model_id) {
      setModelName(runtimeStatus.model_id.split('/').pop() || runtimeStatus.model_id)
    }
    if (runtimeStatus?.ctx_max > 0) {
      setContextUsed(Math.round(runtimeStatus.stm_tokens / runtimeStatus.ctx_max * 100))
    }
    if (runtimeStatus) {
      setRuntimeInfo({
        stmTokens: runtimeStatus.stm_tokens,
        ctxMax: runtimeStatus.ctx_max,
        totalInput: runtimeStatus.total_usage?.input_tokens,
        totalOutput: runtimeStatus.total_usage?.output_tokens,
        stepCount: runtimeStatus.step_count,
      })
    }
  }, [runtimeStatus, setModelName, setContextUsed, setRuntimeInfo])

  useEffect(() => {
    const fetchDynamicCommands = () => {
      api.listSkills().then(d => setSkills(d.skills || [])).catch(() => {})
      api.listMcpTools().then(d => setMcpTools(d.tools || [])).catch(() => {})
      api.getExtensions().then(setPlugins).catch(() => {})
    }

    const initNew = (sid: string) => {
      sessionIdRef.current = sid
      setSessionId(sid)
      setMessages([])
      animatedIds.clear()
      currentAssistantIdRef.current = null
      currentReasoningIdRef.current = null
      messageQueueRef.current = []
      agentWs.connect(sid)
      api.clearShortMemory().catch(() => {})
      api.getRuntimeStatus().then(setRuntimeStatus).catch(() => {})
      api.getConfig().then((config: any) => {
        if (config.cwd) setCwd(config.cwd)
      }).catch(() => {})
      Promise.all([
        api.listMcpTools().catch(() => ({ tools: [] })),
        api.listSkills().catch(() => ({ skills: [] })),
      ]).then(([mcpData, skillsData]) => {
        setToolCount((mcpData.tools || []).length + (skillsData.skills || []).filter((s: any) => s.enabled).length)
      }).catch(() => {})
      api.getTodos(sid).then(d => setTodoCount(d.count || 0)).catch(() => {})
      fetchDynamicCommands()
    }

    const initRestore = (sid: string) => {
      sessionIdRef.current = sid
      setSessionId(sid)
      agentWs.connect(sid)
      loadAndDisplayMessages().catch(() => {})
      api.getVersionStatus().then(setVersionStatus).catch(() => {})
      api.getVersionLog(5).then(d => setCheckpoints(d.checkpoints || [])).catch(() => {})
      api.getRuntimeStatus().then(setRuntimeStatus).catch(() => {})
      api.getConfig().then((config: any) => {
        if (config.cwd) setCwd(config.cwd)
      }).catch(() => {})
      Promise.all([
        api.listMcpTools().catch(() => ({ tools: [] })),
        api.listSkills().catch(() => ({ skills: [] })),
      ]).then(([mcpData, skillsData]) => {
        setToolCount((mcpData.tools || []).length + (skillsData.skills || []).filter((s: any) => s.enabled).length)
      }).catch(() => {})
      api.getTodos(sid).then(d => setTodoCount(d.count || 0)).catch(() => {})
      fetchDynamicCommands()
    }

    if (routeSessionId) {
      // Guard: if navigating to the session we're already in, keep the live socket.
      if (routeSessionId === currentSessionIdRef.current) {
        return () => agentWs.disconnect()
      }
      api.restoreSession(routeSessionId)
        .then(({ session_id }) => { currentSessionIdRef.current = session_id; initRestore(session_id) })
        .catch(() => api.clearShortMemory().then(() => api.createSession()).then(({ session_id }) => { currentSessionIdRef.current = session_id; initNew(session_id) }))
    } else {
      api.clearShortMemory().then(() => api.createSession()).then(({ session_id }) => { currentSessionIdRef.current = session_id; initNew(session_id) })
    }
    return () => {
      if (skipNextDisconnectRef.current) {
        skipNextDisconnectRef.current = false
        return
      }
      agentWs.disconnect()
    }
  }, [routeSessionId])

  useEffect(() => {
    const unsubs = [
      agentWs.on('thinking', (data) => {
        currentAssistantIdRef.current = null; currentReasoningIdRef.current = null
        setMessages(prev => [...prev, { id: `t-${++msgCounterRef.current}`, role: 'system', content: data.content || 'Thinking...', timestamp: new Date() }])
      }),
      agentWs.on('tool_call', (data) => {
        currentAssistantIdRef.current = null
        setMessages(prev => [...prev, { id: `tc-${++msgCounterRef.current}`, role: 'tool', content: `Calling: ${data.tool_name}`, toolName: data.tool_name, toolStatus: 'running', timestamp: new Date() }])
      }),
      agentWs.on('tool_result', (data) => {
        let updated = false
        setMessages(prev => prev.map(m => {
          if (!updated && m.toolName === data.tool_name && m.toolStatus === 'running') {
            updated = true
            return { ...m, toolStatus: 'done' as const, content: `${data.tool_name}: ${data.result || 'done'}` }
          }
          return m
        }))
      }),
      agentWs.on('token', (data) => {
        currentReasoningIdRef.current = null
        const tid = currentAssistantIdRef.current
        if (tid) {
          tokenBufferRef.current += data.content
          if (!tokenFlushRef.current) {
            tokenFlushRef.current = requestAnimationFrame(() => {
              tokenFlushRef.current = 0
              const batch = tokenBufferRef.current
              tokenBufferRef.current = ''
              if (!batch) return
              const id = currentAssistantIdRef.current
              if (id) setMessages(prev => prev.map(m => m.id === id ? { ...m, content: m.content + batch } : m))
            })
          }
        } else {
          const nid = `a-${++msgCounterRef.current}`
          currentAssistantIdRef.current = nid
          setMessages(prev => [...prev, { id: nid, role: 'assistant', content: data.content, timestamp: new Date() }])
        }
      }),
      agentWs.on('reasoning', (data) => {
        const tid = currentReasoningIdRef.current
        if (tid) { setMessages(prev => prev.map(m => m.id === tid ? { ...m, content: m.content + data.content } : m)) }
        else { const nid = `r-${++msgCounterRef.current}`; currentReasoningIdRef.current = nid; setMessages(prev => [...prev, { id: nid, role: 'system', content: data.content, timestamp: new Date() }]) }
      }),
      agentWs.on('answer', (data) => {
        if (tokenFlushRef.current) { cancelAnimationFrame(tokenFlushRef.current); tokenFlushRef.current = 0 }
        tokenBufferRef.current = ''
        const tid = currentAssistantIdRef.current
        if (tid) { setMessages(prev => prev.map(m => m.id === tid ? { ...m, content: data.content } : m)) }
        else { setMessages(prev => { const li = [...prev].reverse().findIndex(m => m.role === 'assistant'); if (li !== -1) { const idx = prev.length - 1 - li; return prev.map((m, i) => i === idx ? { ...m, content: data.content } : m) } return [...prev, { id: `a-${++msgCounterRef.current}`, role: 'assistant' as const, content: data.content, timestamp: new Date() }] }) }
        currentAssistantIdRef.current = null; setIsRunning(false); setCurrentRunId(null)
        api.getVersionStatus().then(setVersionStatus).catch(() => {})
        api.getVersionLog(5).then(d => setCheckpoints(d.checkpoints || [])).catch(() => {})
        api.getRuntimeStatus().then(setRuntimeStatus).catch(() => {})
        processQueue()
      }),
      agentWs.on('error', (data) => {
        const isCancelled = data.message === 'cancelled' || data.run_id
        const label = isCancelled ? '⏹ Agent cancelled' : `Error: ${data.message}`
        setMessages(prev => [...prev, { id: `e-${++msgCounterRef.current}`, role: 'system', content: label, timestamp: new Date() }])
        setIsRunning(false); setCurrentRunId(null); processQueue()
      }),
      agentWs.on('done', () => { setIsRunning(false); setCurrentRunId(null); processQueue() }),
      agentWs.on('run_started', (data) => { if (data.run_id) setCurrentRunId(data.run_id) }),
      agentWs.on('confirm_request', (data) => { setConfirmRequest({ summary: data.summary }) }),
    ]
    return () => {
      unsubs.forEach(u => u())
      if (tokenFlushRef.current) { cancelAnimationFrame(tokenFlushRef.current); tokenFlushRef.current = 0 }
      if (scrollRafRef.current) { cancelAnimationFrame(scrollRafRef.current); scrollRafRef.current = 0 }
    }
  }, [sessionId])

  useEffect(scrollToBottom, [messages, scrollToBottom])

  const processQueue = () => { if (messageQueueRef.current.length > 0) { const next = messageQueueRef.current.shift()!; setTimeout(() => sendMessage(next), 100) } }

  const sendMessage = (text: string) => {
    currentAssistantIdRef.current = null; currentReasoningIdRef.current = null
    setMessages(prev => [...prev, { id: `u-${++msgCounterRef.current}`, role: 'user', content: text, timestamp: new Date() }])
    setIsRunning(true); agentWs.sendMessage(text)
    if (location.pathname === '/' && currentSessionIdRef.current) {
      skipNextDisconnectRef.current = true
      navigate(`/chat/${currentSessionIdRef.current}`, { replace: true })
    }
  }

  const handleSend = () => {
    const text = input.trim(); if (!text || !sessionId) return
    setInput(''); setShowPalette(false)
    if (text.startsWith('/')) { handleSlashCommand(text); return }
    if (isRunning) { messageQueueRef.current.push(text); setMessages(prev => [...prev, { id: `q-${++msgCounterRef.current}`, role: 'system', content: `[Queued] ${text}`, timestamp: new Date() }]) }
    else sendMessage(text)
  }

  const handleSlashCommand = async (text: string) => {
    const parts = text.trim().split(/\s+/); const cmd = parts[0].toLowerCase(); const args = parts.slice(1).join(' ')
    const addSys = (c: string) => setMessages(prev => [...prev, { id: `cmd-${++msgCounterRef.current}`, role: 'system', content: c, timestamp: new Date() }])
    switch (cmd) {
      case '/help': addSys(COMMAND_DEFS.map(c => `${c.cmd.padEnd(12)} ${c.desc}`).join('\n')); break
      case '/clear': setMessages([]); messageQueueRef.current = []; animatedIds.clear(); break
      case '/undo':
        try {
          const r = await api.versionUndo()
          setVersionStatus(await api.getVersionStatus())
          setCheckpoints((await api.getVersionLog(5)).checkpoints || [])
          await loadAndDisplayMessages()
          addSys(`Undone to checkpoint: ${r.checkpoint?.id || 'ok'}`)
        } catch (e: any) { addSys(`Undo failed: ${e.message}`) }
        break
      case '/redo':
        try {
          const r = await api.versionRedo()
          setVersionStatus(await api.getVersionStatus())
          setCheckpoints((await api.getVersionLog(5)).checkpoints || [])
          await loadAndDisplayMessages()
          addSys(`Redone to checkpoint: ${r.checkpoint?.id || 'ok'}`)
        } catch (e: any) { addSys(`Redo failed: ${e.message}`) }
        break
      case '/log': try { const { checkpoints: cps } = await api.getVersionLog(10); addSys(cps.length === 0 ? 'No checkpoints.' : cps.map(cp => `${cp.is_head ? '→ ' : '  '}${cp.id}  ${cp.question || ''}`).join('\n')) } catch (e: any) { addSys(`Log failed: ${e.message}`) }; break
      case '/status': try { const s = await api.getVersionStatus(); addSys(`Session: ${s.session_id}\nHEAD: ${s.head?.id || 'none'}\nCan undo: ${s.can_undo}\nCan redo: ${s.can_redo}`) } catch (e: any) { addSys(`Status failed: ${e.message}`) }; break
      case '/compact': addSys('Compressing context...'); try { const r = await api.compactContext(args); addSys(`Compacted: ${r.tokens_saved} tokens saved`) } catch (e: any) { addSys(`Compact failed: ${e.message}`) }; break
      case '/sessions': try { const { sessions } = await api.getRecentSessions(10); addSys(sessions.length === 0 ? 'No recent sessions.' : sessions.map(s => `${s.session_id.slice(0, 12)}  ${s.preview || ''}`).join('\n')) } catch (e: any) { addSys(`Sessions failed: ${e.message}`) }; break
      case '/switch':
        if (!args) { addSys('Usage: /switch <session_id>'); break }
        window.location.href = `/chat/${args}`
        break
      case '/skill':
        if (!args || args === 'list') {
          try {
            const { skills: sk } = await api.listSkills()
            addSys(sk.length === 0 ? 'No skills registered.' : sk.map(s => {
              const short = s.id.includes('/') ? s.id.split('/').pop()! : s.id
              return `${s.enabled ? '●' : '○'} ${short.padEnd(20)} ${s.display_name || s.description || ''}`
            }).join('\n'))
          } catch (e: any) { addSys(`Skills failed: ${e.message}`) }
        } else if (args.startsWith('enable ')) {
          const name = args.slice(7).trim()
          const full = skills.find(s => (s.id.includes('/') ? s.id.split('/').pop()! : s.id) === name)?.id || name
          try { await api.enableSkill(full); addSys(`Skill enabled: ${name}`); setSkills((await api.listSkills()).skills) } catch (e: any) { addSys(`Enable failed: ${e.message}`) }
        } else if (args.startsWith('disable ')) {
          const name = args.slice(8).trim()
          const full = skills.find(s => (s.id.includes('/') ? s.id.split('/').pop()! : s.id) === name)?.id || name
          try { await api.disableSkill(full); addSys(`Skill disabled: ${name}`); setSkills((await api.listSkills()).skills) } catch (e: any) { addSys(`Disable failed: ${e.message}`) }
        } else { addSys('Usage: /skill [list | enable <id> | disable <id>]') }
        break
      case '/mcp':
        if (!args || args === 'status') {
          try {
            const s = await api.getMcpStatus()
            const servers = s.servers || []
            addSys(servers.length === 0 ? 'No MCP servers.' : servers.map((sv: any) => `${sv.connected ? '●' : '○'} ${sv.name}  ${sv.tool_names?.length || 0} tools`).join('\n'))
          } catch (e: any) { addSys(`MCP status failed: ${e.message}`) }
        } else if (args === 'tools') {
          try {
            const { tools } = await api.listMcpTools()
            addSys(tools.length === 0 ? 'No MCP tools.' : tools.map(t => `${t.tool.padEnd(30)} [${t.server}]`).join('\n'))
          } catch (e: any) { addSys(`MCP tools failed: ${e.message}`) }
        } else if (args === 'reload') {
          try { await api.reloadMcp(); addSys('MCP reloaded.') } catch (e: any) { addSys(`MCP reload failed: ${e.message}`) }
        } else { addSys('Usage: /mcp [status | tools | reload]') }
        break
      case '/plugin':
        try {
          const ext = await api.getExtensions()
          const names = Object.keys(ext)
          addSys(names.length === 0 ? 'No plugins loaded.' : names.map(n => `● ${n}`).join('\n'))
        } catch (e: any) { addSys(`Plugins failed: ${e.message}`) }
        break
      default: {
        const skillMatch = skills.find(s => {
          if (!s.enabled) return false
          const shortId = s.id.includes('/') ? s.id.split('/').pop()! : s.id
          return `/${shortId}` === cmd
        })
        if (skillMatch) {
          sendMessage(text)
        } else {
          addSys(`Unknown command: ${cmd}. Type /help for commands.`)
        }
      }
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (showPalette && filteredCommands.length > 0) {
      if (e.key === 'ArrowDown') { e.preventDefault(); setPaletteIndex(i => (i + 1) % filteredCommands.length); return }
      if (e.key === 'ArrowUp') { e.preventDefault(); setPaletteIndex(i => (i - 1 + filteredCommands.length) % filteredCommands.length); return }
      if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); handlePaletteSelect(filteredCommands[paletteIndex].cmd); return }
      if (e.key === 'Escape') { e.preventDefault(); setShowPalette(false); paletteAnimDoneRef.current = false; return }
    }
    if (e.key === 'Escape' && isRunning) {
      e.preventDefault()
      const now = Date.now()
      if (now - lastEscapeAtRef.current <= ESC_DOUBLE_TAP_MS) {
        lastEscapeAtRef.current = 0
        handleCancel()
        return
      }
      lastEscapeAtRef.current = now
      return
    }
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() }
  }
  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const v = e.target.value
    setInput(v)
    const show = v.startsWith('/') && v.length > 0
    if (!show) paletteAnimDoneRef.current = false
    setShowPalette(show)
    setPaletteFilter(v)
    setPaletteIndex(0)
  }
  const handlePaletteSelect = (cmd: string) => { setInput(cmd + ' '); setShowPalette(false); setPaletteIndex(0); paletteAnimDoneRef.current = false; inputRef.current?.focus() }
  const handleCancel = () => { if (currentRunId) agentWs.cancel(currentRunId) }
  const handleConfirm = (approved: boolean) => { agentWs.confirm('', approved); setConfirmRequest(null) }
  const handleUndo = async () => {
    try {
      await api.versionUndo()
      setVersionStatus(await api.getVersionStatus())
      setCheckpoints((await api.getVersionLog(5)).checkpoints || [])
      await loadAndDisplayMessages()
    } catch { }
  }
  const handleRedo = async () => {
    try {
      await api.versionRedo()
      setVersionStatus(await api.getVersionStatus())
      setCheckpoints((await api.getVersionLog(5)).checkpoints || [])
      await loadAndDisplayMessages()
    } catch { }
  }

  const allCommands = [
    ...COMMAND_DEFS,
    ...skills.filter(s => s.enabled).map(s => {
      const shortId = s.id.includes('/') ? s.id.split('/').pop()! : s.id
      return { cmd: `/${shortId}`, desc: s.display_name || s.description || 'Invoke skill', category: 'skill' as const }
    }),
    ...mcpTools.map(t => ({
      cmd: `/${t.tool}`,
      desc: `MCP tool (${t.server})`,
      category: 'mcp' as const,
    })),
    ...Object.keys(plugins).map(name => ({
      cmd: `/${name}`,
      desc: `Plugin`,
      category: 'plugin' as const,
    })),
  ]

  const filteredCommands = allCommands.filter(c => {
    const q = paletteFilter.toLowerCase()
    return c.cmd.startsWith(q) || c.desc.toLowerCase().includes(q)
  })

  return (
    <div className="flex-1 flex overflow-hidden" style={{ background: 'var(--surface-0)' }}>
      {/* Main Chat Area */}
      <div className="flex-1 flex flex-col overflow-hidden">
      {/* Messages */}
      <div className="flex-1 overflow-y-auto">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full gap-6 animate-fade-in px-6">
            <div className="text-center">
              <h1
                className="text-[42px] mb-2 tracking-wide uppercase"
                style={{ color: 'var(--fg)', fontFamily: 'var(--font-display)' }}
              >
                What are we building?
              </h1>
              <p className="text-[16px] max-w-md mx-auto" style={{ color: 'var(--fg-secondary)' }}>
                Code. Debug. Create. Ship.
              </p>
            </div>

            {/* Quick Actions */}
            <div className="w-full max-w-2xl mt-4">
              <div className="text-[11px] font-medium tracking-widest mb-3 uppercase" style={{ color: 'var(--fg-muted)', fontFamily: 'var(--font-mono)' }}>
                Start a conversation
              </div>
              <div className="grid grid-cols-3 gap-3">
                {[
                  { icon: '🐛', label: 'Debug', desc: 'Find and fix errors' },
                  { icon: '🔧', label: 'Refactor', desc: 'Clean up code' },
                  { icon: '🧪', label: 'Test', desc: 'Write tests' },
                  { icon: '🌿', label: 'Branch', desc: 'Git operations' },
                  { icon: '📄', label: 'Document', desc: 'Write docs' },
                  { icon: '💡', label: 'Architect', desc: 'Plan systems' },
                ].map(action => (
                  <button
                    key={action.label}
                    onClick={() => { setInput(action.desc); inputRef.current?.focus() }}
                    className="flex items-center gap-3 p-3 rounded-md text-left transition-colors"
                    style={{ background: 'var(--surface-1)', border: '1px solid var(--border)' }}
                    onMouseEnter={e => { e.currentTarget.style.background = 'var(--surface-2)' }}
                    onMouseLeave={e => { e.currentTarget.style.background = 'var(--surface-1)' }}
                  >
                    <div className="w-10 h-10 rounded-md flex items-center justify-center text-lg" style={{ background: 'var(--accent-muted)' }}>
                      {action.icon}
                    </div>
                    <div>
                      <div className="text-[13px] font-medium" style={{ color: 'var(--fg)' }}>{action.label}</div>
                      <div className="text-[11px]" style={{ color: 'var(--fg-muted)' }}>{action.desc}</div>
                    </div>
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}
        {messages.map((msg) => (
          <MessageBubble key={msg.id} msg={msg} />
        ))}
        <div ref={messagesEndRef} />
      </div>

      {/* Confirm */}
      {confirmRequest && (
        <div className="max-w-3xl mx-auto w-full px-6 mb-3">
          <div className="rounded-lg p-4" style={{ background: 'var(--amber-muted)', border: '1px solid var(--amber-muted)' }}>
            <div className="text-sm font-medium mb-2" style={{ color: 'var(--amber)' }}>Confirmation Required</div>
            <pre className="text-xs rounded-lg p-2.5 mb-3 overflow-auto max-h-32 font-mono" style={{ background: 'var(--surface-1)', color: 'var(--fg-secondary)' }}>{confirmRequest.summary}</pre>
            <div className="flex gap-2">
              <button onClick={() => handleConfirm(true)} className="btn-primary text-sm">Approve</button>
              <button onClick={() => handleConfirm(false)} className="btn-ghost text-sm">Deny</button>
            </div>
          </div>
        </div>
      )}

      {/* Input Area */}
      <div className="px-6 pb-6 relative">
        {/* Command Palette */}
        {showPalette && filteredCommands.length > 0 && (
          <div
            className="absolute bottom-full left-6 right-6 mb-2 rounded-lg overflow-hidden max-h-64 overflow-y-auto z-50 animate-slide-up"
            style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', boxShadow: '0 8px 32px rgba(0,0,0,0.3)' }}
          >
            {(() => {
              const CATEGORY_LABELS: Record<string, string> = { system: 'System', skill: 'Skills', mcp: 'MCP Tools', plugin: 'Plugins' }
              const CATEGORY_COLORS: Record<string, string> = { system: 'var(--fg-faint)', skill: 'var(--green)', mcp: 'var(--blue)', plugin: 'var(--amber)' }
              let lastCat = ''
              return filteredCommands.map((c, i) => {
                const showHeader = c.category !== lastCat && (lastCat = c.category)
                return (
                  <React.Fragment key={c.cmd}>
                    {showHeader && (
                      <div className="px-4 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-wider" style={{ color: CATEGORY_COLORS[c.category] || 'var(--fg-faint)', borderTop: lastCat !== 'system' ? '1px solid var(--border)' : 'none' }}>
                        {CATEGORY_LABELS[c.category] || c.category}
                      </div>
                    )}
                    <button
                      ref={(el) => { if (el && i === paletteIndex) el.scrollIntoView({ block: 'nearest' }) }}
                      onClick={() => handlePaletteSelect(c.cmd)}
                      onMouseEnter={() => setPaletteIndex(i)}
                      className="w-full text-left px-4 py-2 text-sm flex items-center gap-3 transition-colors"
                      style={{
                        color: 'var(--fg)',
                        background: i === paletteIndex ? 'var(--accent-subtle)' : 'transparent',
                      }}
                    >
                      <span className="font-mono font-medium" style={{ color: 'var(--accent)' }}>{c.cmd}</span>
                      <span className="text-xs" style={{ color: i === paletteIndex ? 'var(--fg-secondary)' : 'var(--fg-faint)' }}>{c.desc}</span>
                    </button>
                  </React.Fragment>
                )
              })
            })()}
          </div>
        )}

        {/* Floating Input */}
        <div
          className="max-w-3xl mx-auto"
          style={{ background: 'var(--surface-1)', border: '2px solid var(--border-strong)', borderRadius: 'var(--radius)', overflow: 'hidden', boxShadow: 'var(--shadow-hard)' }}
        >
          <div className="flex items-end gap-2.5 p-3">
            <textarea
              ref={inputRef}
              value={input}
              onChange={handleInputChange}
              onKeyDown={handleKeyDown}
              placeholder={isRunning ? "Agent running... messages will be queued" : "Message Nexus..."}
              className="flex-1 bg-transparent text-sm resize-none focus:outline-none max-h-32 min-h-[24px]"
              style={{ color: 'var(--fg)', fontFamily: 'var(--font-sans)' }}
              rows={1}
            />
            {isRunning ? (
              <button onClick={handleCancel} className="w-8 h-8 flex items-center justify-center rounded-lg transition-all shrink-0" style={{ background: 'var(--red-muted)', color: 'var(--red)' }}><Square size={14} /></button>
            ) : (
              <button
                onClick={handleSend}
                disabled={!input.trim()}
                className="w-8 h-8 flex items-center justify-center rounded-lg transition-all shrink-0"
                style={{
                  background: input.trim() ? 'var(--accent)' : 'var(--surface-3)',
                  color: input.trim() ? '#ffffff' : 'var(--fg-faint)',
                }}
              >
                <Send size={14} />
              </button>
            )}
          </div>

          {/* HUD row — session actions only */}
          <div className="flex items-center gap-1 px-3 py-1.5 font-mono text-[10px] overflow-x-auto whitespace-nowrap" style={{ borderTop: '1px solid var(--border)', color: 'var(--fg-muted)' }}>
            {versionStatus?.head && (
              <span className="flex items-center gap-1 shrink-0">
                <span className="w-1 h-1 rounded-full" style={{ background: 'var(--accent)' }} />
                checkpoint: {versionStatus.head.id?.slice(0, 8)}
              </span>
            )}
            <div className="flex items-center gap-0.5 shrink-0 ml-auto">
              <button onClick={handleUndo} disabled={!versionStatus?.can_undo} className="p-0.5 rounded transition-colors disabled:opacity-30 hover:text-[var(--fg)]" style={{ color: 'var(--fg-muted)' }}><Undo2 size={10} /></button>
              <button onClick={handleRedo} disabled={!versionStatus?.can_redo} className="p-0.5 rounded transition-colors disabled:opacity-30 hover:text-[var(--fg)]" style={{ color: 'var(--fg-muted)' }}><Redo2 size={10} /></button>
              <button onClick={() => { setShowCheckpoints(!showCheckpoints); api.getVersionLog(10).then(d => setCheckpoints(d.checkpoints || [])) }} className="p-0.5 rounded transition-colors hover:text-[var(--fg)]" style={{ color: 'var(--fg-muted)' }}><History size={10} /></button>
            </div>
          </div>
        </div>
      </div>

      {/* Checkpoint Overlay */}
      {showCheckpoints && (
        <div className="absolute bottom-28 left-5 w-96 rounded-lg overflow-hidden z-50 animate-slide-up" style={{ background: 'var(--surface-2)', border: '1px solid var(--border-strong)', boxShadow: 'var(--shadow-lg)' }}>
          <div className="px-4 py-3 flex items-center justify-between" style={{ borderBottom: '1px solid var(--border)' }}>
            <span className="text-sm font-medium" style={{ color: 'var(--fg)' }}>Checkpoints</span>
            <button onClick={() => setShowCheckpoints(false)} className="p-1 rounded-lg" style={{ color: 'var(--fg-faint)' }}><Square size={12} /></button>
          </div>
          {checkpoints.length === 0 ? <p className="p-4 text-xs" style={{ color: 'var(--fg-muted)' }}>No checkpoints</p> : checkpoints.map(cp => (
            <div key={cp.id} className="px-4 py-2.5" style={{ borderBottom: '1px solid var(--border)', background: cp.is_head ? 'var(--accent-subtle)' : 'transparent' }}>
              <div className="flex items-center gap-2">
                {cp.is_head && <span style={{ color: 'var(--accent)' }}>→</span>}
                <span className="text-xs font-mono" style={{ color: 'var(--accent)' }}>{cp.id}</span>
                <span className="text-xs truncate" style={{ color: 'var(--fg-muted)' }}>{cp.question || '(no question)'}</span>
              </div>
            </div>
          ))}
        </div>
      )}
      </div>
      {/* Info Panel */}
      <InfoPanel sessionId={sessionId} />
    </div>
  )
}
