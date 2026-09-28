import React, { useState, useRef, useEffect, useCallback } from 'react'
import { useParams, useLocation, useNavigate } from 'react-router-dom'
import { Send, Square, Undo2, Redo2, History, ChevronDown, ChevronRight, FolderOpen, BookOpen, Bug, FlaskConical, Wrench, ArrowRight, GitBranch, Puzzle } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../services/api'
import { planModeArm } from '../services/planModeArm'
import { useProjects, pickAndAddProject } from '../services/projects'
import { animateMessage } from '../utils/animations'
import { toolIcon } from '../utils/toolIcons'
import { transformHistoryMessages } from '../utils/historyTransform'
import { unwrapStreamingAnswer } from '../utils/unwrapAnswer'
import { useSession, type Message } from '../components/session/SessionProvider'
import InfoPanel from '../components/layout/InfoPanel'
import ModelPicker from '../components/chat/ModelPicker'
import PlanModeToggle from '../components/chat/PlanModeToggle'
import PlanReviewCard from '../components/chat/PlanReviewCard'

/** Backend plan_mode.py PLAN_REVIEW_MARKER — keep in sync. */
const PLAN_REVIEW_MARKER = '[PLAN_REVIEW]'

interface Checkpoint { id: string; question: string; answer: string; is_head: boolean }
// Once per app launch: reopen the most recent session instead of landing on a
// session-less "disconnected" state. Consumed by whichever branch of the init
// effect runs first, so "New Chat" navigations never trigger a re-restore.
let didAutoRestoreLastSession = false

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

/** Empty-state prompt starters — concrete openers that fill the composer. */
const STARTERS = [
  {
    icon: BookOpen,
    label: 'Explain this codebase',
    prompt: 'Give me an overview of this codebase: architecture, main modules, and how they fit together.',
  },
  {
    icon: Bug,
    label: 'Help me fix a bug',
    prompt: "Help me debug an issue — I'll describe the symptoms, and you ask me for logs or code as needed.",
  },
  {
    icon: FlaskConical,
    label: 'Write tests',
    prompt: 'Write unit tests for the current module. Cover the main paths and the important edge cases.',
  },
  {
    icon: Wrench,
    label: 'Refactor for clarity',
    prompt: 'Review the current code for readability and refactoring opportunities, and propose changes incrementally.',
  },
]

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

/* ─── Tool Row — one quiet line inside a shared group card (v3) ───
 * Consecutive tool messages render inside a single bordered card (see the
 * thread renderer); each row is ~34px and expands only when running or
 * clicked. Icon comes from the tool-name map; unknown tools fall back to
 * their first letter, MCP tools to the puzzle glyph. */
const ToolCard = React.memo(function ToolCard({ msg, isMcp }: { msg: Message; isMcp?: boolean }) {
  const [expanded, setExpanded] = useState(msg.toolStatus === 'running')
  const userToggled = useRef(false)
  useEffect(() => {
    // auto-expand while running; collapse again when done unless the user
    // explicitly opened the row (v3: cards rest collapsed — the running
    // expansion must not leave a wall of gray text behind)
    if (msg.toolStatus === 'running') setExpanded(true)
    else if (!userToggled.current) setExpanded(false)
  }, [msg.toolStatus])

  const statusColor = msg.toolStatus === 'running' ? 'var(--amber)' : msg.toolStatus === 'error' ? 'var(--red)' : 'var(--green)'
  const statusLabel = msg.toolStatus === 'running' ? 'running' : msg.toolStatus === 'error' ? 'error' : 'done'
  const Icon = toolIcon(msg.toolName)

  const lines = msg.content.split('\n')
  const hasDiff = lines.some(l => l.startsWith('+') || l.startsWith('-') || l.startsWith('@@'))

  return (
    <div className="group/tool">
      <button
        onClick={() => { userToggled.current = true; setExpanded(!expanded) }}
        className="w-full flex items-center gap-2.5 px-3.5 py-2 text-left transition-colors hover:bg-[var(--surface-2)]"
      >
        <span
          className="w-[18px] h-[18px] rounded-[5px] grid place-items-center shrink-0"
          style={{ background: 'var(--surface-2)', color: 'var(--fg-muted)' }}
        >
          {Icon ? <Icon size={11} strokeWidth={2} /> : isMcp
            ? <Puzzle size={11} strokeWidth={2} />
            : <span className="font-mono text-[9.5px] font-semibold">{(msg.toolName || 'T')[0].toUpperCase()}</span>}
        </span>
        <span className="font-mono text-[12.5px] truncate" style={{ color: 'var(--fg-secondary)' }}>{msg.toolName || 'tool'}</span>
        <span className="ml-auto flex items-center gap-1.5 text-[11.5px] shrink-0" style={{ color: statusColor }}>
          {msg.toolStatus === 'running' && (
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="animate-spin"><circle cx="12" cy="12" r="10" strokeDasharray="50" strokeDashoffset="15" /></svg>
          )}
          {statusLabel}
        </span>
        <ChevronRight
          size={13}
          className="shrink-0 transition-transform duration-200"
          style={{ color: 'var(--fg-faint)', transform: expanded ? 'rotate(90deg)' : 'none' }}
        />
      </button>
      {expanded && (
        <div className="px-4 pb-3 font-mono text-[12px] leading-[1.7]" style={{ color: 'var(--fg-muted)' }}>
          {hasDiff ? (
            <div>
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
      )}
    </div>
  )
})

/* ─── Message Bubble ─── */
const MessageBubble = React.memo(function MessageBubble({ msg, animatedIds }: { msg: Message; animatedIds: Set<string> }) {
  return (
    <div
      ref={(el) => {
        if (el && !animatedIds.has(msg.id)) {
          animatedIds.add(msg.id)
          animateMessage(el, msg.role)
        }
      }}
      className="py-3"
    >
      <div className="max-w-3xl mx-auto px-6">
        {/* Role label — only for user messages */}
        {msg.role === 'user' && (
          <div className="flex items-center justify-end gap-2 mb-1.5">
            <span className="text-[12px] font-medium" style={{ color: 'var(--fg-muted)', letterSpacing: '0.02em' }}>
              You
            </span>
          </div>
        )}

        {/* Content */}
        {msg.role === 'tool' ? (
          <ToolCard msg={msg} />
        ) : msg.role === 'user' ? (
          <div className="flex justify-end">
            {/* w-full: give the column a definite width so the bubble's
                max-w-[85%] resolves against the chat column, not against
                this shrink-to-fit container — the circular reference used
                to squeeze every bubble to 85% of its own text width,
                wrapping lines that should fit on one. */}
            <div className="flex flex-col items-end w-full">
              <div
                className="text-[15px] leading-[1.55] rounded-[18px] rounded-br-[6px] px-4 py-2.5 w-fit max-w-[85%] whitespace-pre-wrap break-words"
                style={{ background: 'var(--surface-2)', color: 'var(--fg)' }}
              >
                {msg.content}
              </div>
              {msg.reaction && (
                <div className="flex items-center gap-1.5 mt-1 text-[11px] italic" style={{ color: 'var(--fg-muted)' }}>
                  <span className="not-italic text-sm leading-none">{msg.reaction.emoji}</span>
                  {msg.reaction.comment && <span>{msg.reaction.comment}</span>}
                </div>
              )}
            </div>
          </div>
        ) : msg.role === 'system' ? (
          <Collapsible
            defaultExpanded={false}
            header={
              <span className="text-[12px] font-mono" style={{ color: 'var(--fg-muted)' }}>
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
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {unwrapStreamingAnswer(msg.content)}
            </ReactMarkdown>
          </div>
        )}
      </div>
    </div>
  )
})

/* ─── Main Chat Page ─── */

/** HUD action — quiet 28px icon button (v3). The label moves to the tooltip;
 *  at HUD density, text competed with the model/plan chips for attention. */
function HudAction({ icon: Icon, label, onClick, disabled, title }: {
  icon: typeof History
  label: string
  onClick: () => void
  disabled?: boolean
  title?: string
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title ?? label}
      aria-label={label}
      className="flex items-center justify-center w-7 h-7 rounded-lg transition-colors hover:bg-[var(--surface-3)] hover:text-[var(--fg)] disabled:opacity-30 disabled:hover:bg-transparent disabled:hover:text-[var(--fg-muted)]"
      style={{ color: 'var(--fg-muted)' }}
    >
      <Icon size={14} style={{ flexShrink: 0 }} />
    </button>
  )
}

export default function ChatPage() {
  const { sessionId: routeSessionId } = useParams<{ sessionId?: string }>()
  const location = useLocation()
  const navigate = useNavigate()
  const currentSessionIdRef = useRef<string | null>(null)
  const [input, setInput] = useState('')
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  // HUD state
  const [versionStatus, setVersionStatus] = useState<any>(null)
  const [runtimeStatus, setRuntimeStatus] = useState<any>(null)
  const [checkpoints, setCheckpoints] = useState<Checkpoint[]>([])
  const [showCheckpoints, setShowCheckpoints] = useState(false)

  const [showPalette, setShowPalette] = useState(false)
  const [paletteFilter, setPaletteFilter] = useState('')
  const [paletteIndex, setPaletteIndex] = useState(0)
  const [paletteDismissed, setPaletteDismissed] = useState(false)
  const paletteAnimDoneRef = useRef(false)

  const [skills, setSkills] = useState<Array<{ id: string; display_name: string; description: string; enabled: boolean }>>([])
  const [mcpTools, setMcpTools] = useState<Array<{ server: string; tool: string; transport: string }>>([])
  const [plugins, setPlugins] = useState<Record<string, any>>({})
  // Per-session workspace: pending value for a not-yet-created chat, plus the
  // server default shown on the new-chat screen.
  const [pendingWorkspace, setPendingWorkspace] = useState<string | null>(null)
  const [defaultWorkspace, setDefaultWorkspace] = useState<string | null>(null)
  const { selected: selectedProject } = useProjects()

  const {
    sessionId, cwd, setSessionId: setGlobalSessionId, setModelName, setContextUsed, setRuntimeInfo, setCwd, setToolCount, setTodoCount,
    messages, setMessages, isRunning, confirmRequest,
    sendMessage, cancelRun, confirmToolCall, queueMessage, animatedIds, incrementMsgCounter, resetForSessionSwitch, getLiveDisplayMessages,
  } = useSession()

  const scrollRafRef = useRef<number>(0)
  const prevIsRunningRef = useRef(false)
  const lastEscapeAtRef = useRef<number>(0)
  const ESC_DOUBLE_TAP_MS = 600
  const scrollToBottom = useCallback(() => {
    if (scrollRafRef.current) return
    scrollRafRef.current = requestAnimationFrame(() => {
      scrollRafRef.current = 0
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
    })
  }, [])

  const loadAndDisplayMessages = useCallback(async (forceSessionId?: string) => {
    const currentSid = forceSessionId || currentSessionIdRef.current
    console.log('[loadAndDisplayMessages] sid:', currentSid)
    // Check SessionManager's in-memory Map first — preserves streaming content
    // that hasn't been persisted to the backend yet.
    // Use getLiveSessionState (reads from sessionsRef) to avoid stale closure:
    // this function has empty deps and must always read the latest Map.
    if (currentSid) {
      const cachedMessages = getLiveDisplayMessages(currentSid)
      if (cachedMessages && cachedMessages.length > 0) {
        // The provider already exposes the flattened display view (committed +
        // in-flight step), so the UI reflects live state — no setMessages here.
        // Writing the snapshot back would clobber events that landed after the read
        // (replace-with-stale-array race while a run is streaming).
        console.log('[loadAndDisplayMessages] Using Map cache:', cachedMessages.length, 'messages')
        for (const m of cachedMessages) animatedIds.add(m.id)
        return
      }
    }
    try {
      let stm: Array<{ role: string; content: string; ts?: number }>
      try {
        const hist = await api.listSessionHistory(0, currentSid || undefined)
        stm = hist.messages && hist.messages.length > 0 ? hist.messages : (await api.listShortMemories()).messages
      } catch {
        stm = (await api.listShortMemories()).messages
      }
      console.log('[loadAndDisplayMessages] Backend returned:', stm?.length, 'messages for sid:', currentSid)
      if (!stm || stm.length === 0) {
        // Backend returned no messages. Only clear if the Map also has nothing.
        if (currentSid) {
          const existingMessages = getLiveDisplayMessages(currentSid)
          if (existingMessages && existingMessages.length > 0) return
        }
        setMessages([])
        return
      }

      const transformed = transformHistoryMessages(stm)
      console.log('[loadAndDisplayMessages] Transformed:', transformed.length, 'messages. Setting on active session.')
      // Mark all restored messages as already animated
      for (const m of transformed) animatedIds.add(m.id)
      setMessages(transformed)
    } catch (err) { console.error('Failed to load messages:', err) }
  }, [])

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

  // Re-fetch runtime status when agent run completes (isRunning: true → false)
  useEffect(() => {
    if (prevIsRunningRef.current && !isRunning) {
      api.getRuntimeStatus(sessionId ?? undefined).then(setRuntimeStatus).catch(() => {})
    }
    prevIsRunningRef.current = isRunning
  }, [isRunning])

  const fetchDynamicCommands = useCallback(() => {
    api.listSkills().then(d => setSkills(d.skills || [])).catch(() => {})
    api.listMcpTools().then(d => setMcpTools(d.tools || [])).catch(() => {})
    api.getExtensions().then(setPlugins).catch(() => {})
  }, [])

  const initNew = useCallback((sid: string) => {
    currentSessionIdRef.current = sid
    setGlobalSessionId(sid)
    // Don't call setMessages([]) here — React batching means activeSessionId
    // is still the OLD session, so this would clear the old session's messages.
    // The new session already starts with empty messages in SessionManager's Map.
    api.getRuntimeStatus(sid).then(setRuntimeStatus).catch(() => {})
    // Session's own workspace wins; server default cwd is the chip fallback.
    api.getSession(sid).then((s) => {
      setCwd(s.workspace ?? null)
    }).catch(() => {})
    api.getConfig().then((config: any) => {
      if (config.cwd) setDefaultWorkspace(config.cwd)
    }).catch(() => {})
    Promise.all([
      api.listMcpTools().catch(() => ({ tools: [] })),
      api.listSkills().catch(() => ({ skills: [] })),
    ]).then(([mcpData, skillsData]) => {
      setToolCount((mcpData.tools || []).length + (skillsData.skills || []).filter((s: any) => s.enabled).length)
    }).catch(() => {})
    api.getTodos(sid).then(d => setTodoCount(d.count || 0)).catch(() => {})
    fetchDynamicCommands()
  }, [fetchDynamicCommands])

  // Server default workspace — shown on the new-chat screen before the user
  // picks a per-session folder.
  useEffect(() => {
    api.getConfig().then((c) => {
      const cfgCwd = c?.cwd
      if (typeof cfgCwd === 'string' && cfgCwd) setDefaultWorkspace(cfgCwd)
    }).catch(() => {})
  }, [])

  useEffect(() => {
    const isFirstMount = !didAutoRestoreLastSession
    didAutoRestoreLastSession = true
    const initRestore = (sid: string) => {
      console.log('[initRestore] Restoring session:', sid)
      currentSessionIdRef.current = sid
      setGlobalSessionId(sid)
      // Pass session ID explicitly — currentSessionIdRef may not be updated yet
      // due to React batching. loadAndDisplayMessages checks SessionManager's
      // Map first, so it handles both cached and backend-fetched messages correctly.
      loadAndDisplayMessages(sid).catch((err) => console.error('[initRestore] loadAndDisplayMessages failed:', err))
      api.getVersionStatus().then(setVersionStatus).catch(() => {})
      api.getVersionLog(5).then(d => setCheckpoints(d.checkpoints || [])).catch(() => {})
      api.getRuntimeStatus(sid).then(setRuntimeStatus).catch(() => {})
      // Session's own workspace wins; server default cwd is the chip fallback.
      api.getSession(sid).then((s) => {
        setCwd(s.workspace ?? null)
      }).catch(() => {})
      api.getConfig().then((config: any) => {
        if (config.cwd) setDefaultWorkspace(config.cwd)
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
      // Guard: if navigating to the session we're already in, skip re-init.
      if (routeSessionId === currentSessionIdRef.current) {
        return
      }
      // Reset running state and queue before switching sessions
      resetForSessionSwitch()
      api.restoreSession(routeSessionId)
        .then(({ session_id, restored }) => {
          currentSessionIdRef.current = session_id
          if (restored === false || session_id !== routeSessionId) {
            // Session was recreated (old one lost) — update URL to new session
            navigate(`/chat/${session_id}`, { replace: true })
            initNew(session_id)
          } else {
            initRestore(session_id)
          }
        })
        .catch(() => api.createSession().then(async ({ session_id }) => {
          if (planModeArm.consume()) {
            try { await api.setPlanMode(session_id, true) } catch { /* best-effort */ }
          }
          currentSessionIdRef.current = session_id; initNew(session_id)
        }))
    } else {
      // Don't create session eagerly — defer to first message send.
      // This prevents empty "New session" cards from accumulating in the sidebar.
      resetForSessionSwitch()
      currentSessionIdRef.current = null
      setGlobalSessionId(null)
      api.getRuntimeStatus(sessionId ?? undefined).then(setRuntimeStatus).catch(() => {})
      api.getConfig().then((config: any) => { if (config.cwd) setDefaultWorkspace(config.cwd) }).catch(() => {})
      fetchDynamicCommands()
      if (isFirstMount) {
        api.getRecentSessions(5).then((d) => {
          // Skip empty sessions (e.g. the internal server-build session):
          // the backend generates a preview from the last checkpoint/message,
          // so a session with any content always has one.
          const last = d.sessions?.find((s) => s.preview)
          // Bail if the user already started a new chat while we were fetching.
          if (last && !currentSessionIdRef.current) {
            navigate(`/chat/${last.session_id}`, { replace: true })
          }
        }).catch(() => {})
      }
    }
    // WebSocket lifecycle is managed by SessionProvider — no disconnect here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeSessionId])

  // Auto-scroll on new messages
  useEffect(scrollToBottom, [messages, scrollToBottom])

  // Reset session state when "New Chat" is clicked (even when already on '/')
  useEffect(() => {
    const handleNewChat = () => {
      currentSessionIdRef.current = null
      pendingFirstMessageRef.current = null
      setGlobalSessionId(null)
      setPendingWorkspace(null) // fall back to the selected project
    }
    window.addEventListener('new-chat', handleNewChat)
    return () => window.removeEventListener('new-chat', handleNewChat)
  }, [])

  // Pending first message for lazy session creation (route '/' without session)
  const pendingFirstMessageRef = useRef<string | null>(null)

  // Send pending first message once session is activated (WS connects on activeSessionId change)
  useEffect(() => {
    const pending = pendingFirstMessageRef.current
    if (!pending) return
    pendingFirstMessageRef.current = null
    // sendMessageInternal retries WS send if not yet connected, so this is safe
    sendMessage(pending)
  }, [sendMessage])

  // Handle first message — create session lazily if needed, then send.
  const handleSendMessage = useCallback((text: string) => {
    if (!currentSessionIdRef.current) {
      // No session yet (first message from route '/') — create lazily, bound to
      // the pending per-session workspace if the user picked one.
      // Defer the actual send to a useEffect that fires after activeSessionId
      // is set and the WS connection is established by SessionManager.
      pendingFirstMessageRef.current = text
      api.createSession(undefined, pendingWorkspace ?? selectedProject ?? null).then(async ({ session_id }) => {
        // Plan mode armed via the HUD toggle pre-session: apply it BEFORE the
        // first message can be sent, so the very first run is already gated.
        if (planModeArm.consume()) {
          try { await api.setPlanMode(session_id, true) } catch { /* best-effort; toggle shows off */ }
        }
        currentSessionIdRef.current = session_id
        initNew(session_id) // sets activeSessionId → triggers WS connect + pending send effect
        setPendingWorkspace(null) // the session carries its folder now
        navigate(`/chat/${session_id}`, { replace: true })
      }).catch((err) => {
        pendingFirstMessageRef.current = null
        setInput(text) // restore the unsent message
        window.alert(`创建会话失败：${err instanceof Error ? err.message : err}`)
      })
    } else {
      sendMessage(text)
      if (location.pathname === '/') {
        navigate(`/chat/${currentSessionIdRef.current}`, { replace: true })
      }
    }
  }, [sendMessage, location.pathname, navigate, initNew, pendingWorkspace, selectedProject])

  // Workspace chip — the folder this chat runs in. Fixed once the session
  // exists; before the first message it defaults to the selected project.
  const chipWorkspace = cwd ?? pendingWorkspace ?? (sessionId ? null : selectedProject) ?? defaultWorkspace
  const handleWorkspacePick = async () => {
    if (sessionId) return // locked after creation
    const picked = await pickAndAddProject(chipWorkspace)
    if (picked) setPendingWorkspace(picked)
  }

  const handleSend = () => {
    const text = input.trim(); if (!text) return
    setInput(''); setShowPalette(false); setPaletteDismissed(false)
    if (text.startsWith('/')) { handleSlashCommand(text); return }
    if (isRunning) { queueMessage(text) }
    else handleSendMessage(text)
  }

  const handleSlashCommand = async (text: string) => {
    const parts = text.trim().split(/\s+/); const cmd = parts[0].toLowerCase(); const args = parts.slice(1).join(' ')
    const addSys = (c: string) => setMessages(prev => [...prev, { id: `cmd-${incrementMsgCounter()}`, role: 'system', content: c, timestamp: new Date() }])
    switch (cmd) {
      case '/help': addSys(COMMAND_DEFS.map(c => `${c.cmd.padEnd(12)} ${c.desc}`).join('\n')); break
      case '/clear': setMessages([]); animatedIds.clear(); break
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
          handleSendMessage(text)
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
      if (e.key === 'Escape') { e.preventDefault(); setShowPalette(false); setPaletteDismissed(true); paletteAnimDoneRef.current = false; return }
    }
    // Palette open with no matches (e.g. mid-typing an argument) — Esc still dismisses
    if (showPalette && e.key === 'Escape') { e.preventDefault(); setShowPalette(false); setPaletteDismissed(true); return }
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
    // Dismissal (Esc) persists until the user leaves the slash-command context,
    // e.g. clears the leading '/'. Otherwise the palette would pop back open on
    // every keystroke while typing.
    const isSlashContext = v.startsWith('/') && v.length > 0
    if (!isSlashContext) setPaletteDismissed(false)
    const show = isSlashContext && !paletteDismissed
    if (!show) paletteAnimDoneRef.current = false
    setShowPalette(show)
    setPaletteFilter(v)
    setPaletteIndex(0)
  }
  const handlePaletteSelect = (cmd: string) => { setInput(cmd + ' '); setShowPalette(false); setPaletteIndex(0); paletteAnimDoneRef.current = false; inputRef.current?.focus() }
  const handleCancel = () => { cancelRun() }
  const handleConfirm = (approved: boolean) => { confirmToolCall(approved) }
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
    <div className="flex-1 flex overflow-hidden relative" style={{ background: 'var(--surface-0)' }}>
      {/* Main Chat Area */}
      <div className="flex-1 flex flex-col overflow-hidden">
      {/* Messages */}
      <div className="flex-1 overflow-y-auto">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full gap-6 animate-fade-in px-6">
            <div className="text-center">
              <h1
                className="text-[27px] font-semibold mb-3"
                style={{ color: 'var(--fg)', letterSpacing: '-0.025em' }}
              >
                What are we building?
              </h1>
              <p className="text-[15px] max-w-md mx-auto" style={{ color: 'var(--fg-muted)' }}>
                Code. Debug. Create. Ship.
              </p>
            </div>

            {/* Prompt starters — concrete one-tap openers that fill the composer */}
            <div className="w-full max-w-2xl mt-10">
              <div className="grid grid-cols-2 gap-2.5">
                {STARTERS.map(action => {
                  const Icon = action.icon
                  return (
                    <button
                      key={action.label}
                      onClick={() => { setInput(action.prompt); inputRef.current?.focus() }}
                      className="group/starter flex items-center gap-3 px-4 py-3.5 rounded-2xl text-left transition-all"
                      style={{
                        background: 'var(--surface-1)',
                        border: '1px solid var(--border)',
                        transitionDuration: '150ms',
                        transitionTimingFunction: 'var(--ease)',
                      }}
                      onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--border-strong)'; e.currentTarget.style.background = 'var(--surface-2)' }}
                      onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.background = 'var(--surface-1)' }}
                    >
                      <div
                        className="w-8 h-8 rounded-[9px] flex items-center justify-center shrink-0"
                        style={{ background: 'var(--accent-soft)', color: 'var(--accent)' }}
                      >
                        <Icon size={15} />
                      </div>
                      <span className="min-w-0">
                        <span className="block text-[14px] font-medium truncate" style={{ color: 'var(--fg)' }}>{action.label}</span>
                      </span>
                      <ArrowRight
                        size={14}
                        className="ml-auto opacity-0 group-hover/starter:opacity-100 transition-all shrink-0 -translate-x-1 group-hover/starter:translate-x-0"
                        style={{ color: 'var(--fg-faint)', transitionDuration: '150ms' }}
                      />
                    </button>
                  )
                })}
              </div>
            </div>
          </div>
        )}
        {(() => {
          // v3: consecutive tool calls share ONE card (hairline-divided rows)
          // so N parallel calls cost N quiet lines, not N boxes.
          const mcpNames = Object.fromEntries(mcpTools.map(t => [t.tool, true])) as Record<string, true>
          type Group = { kind: 'msg', msg: Message } | { kind: 'tools', msgs: Message[] }
          const groups: Group[] = []
          for (const msg of messages) {
            if (msg.role === 'tool') {
              const last = groups[groups.length - 1]
              if (last && last.kind === 'tools') last.msgs.push(msg)
              else groups.push({ kind: 'tools', msgs: [msg] })
            } else {
              groups.push({ kind: 'msg', msg })
            }
          }
          return groups.map((g) => g.kind === 'msg' ? (
            <MessageBubble key={g.msg.id} msg={g.msg} animatedIds={animatedIds} />
          ) : (
            <div key={g.msgs[0].id} className="max-w-3xl mx-auto px-6 py-2">
              <div
                className="max-w-[608px] overflow-hidden"
                style={{ background: 'var(--surface-1)', border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)' }}
              >
                {g.msgs.map((m, i) => (
                  <div key={m.id} style={i > 0 ? { borderTop: '1px solid var(--border-subtle)' } : undefined}>
                    <ToolCard msg={m} isMcp={mcpNames[m.toolName ?? ''] === true} />
                  </div>
                ))}
              </div>
            </div>
          ))
        })()}
        <div ref={messagesEndRef} />
      </div>

      {/* Confirm / Plan review */}
      {confirmRequest && (
        confirmRequest.summary.startsWith(PLAN_REVIEW_MARKER) ? (
          <div className="max-w-3xl mx-auto w-full px-6 mb-3">
            <PlanReviewCard
              plan={confirmRequest.summary.slice(PLAN_REVIEW_MARKER.length).trim()}
              onApprove={() => handleConfirm(true)}
              onDeny={() => handleConfirm(false)}
            />
          </div>
        ) : (
        <div className="max-w-3xl mx-auto w-full px-6 mb-3">
          <div className="rounded-2xl p-5" style={{ background: 'var(--surface-1)', border: '1px solid var(--border-strong)', boxShadow: 'var(--shadow-float)' }}>
            <div className="flex items-center gap-2 mb-3">
              <span className="w-2 h-2 rounded-full" style={{ background: 'var(--amber)' }} />
              <span className="text-[14px] font-semibold" style={{ color: 'var(--fg)' }}>Confirmation required</span>
            </div>
            <pre className="text-[12.5px] rounded-xl p-3 mb-4 overflow-auto max-h-32 font-mono" style={{ background: 'var(--surface-2)', color: 'var(--fg-secondary)' }}>{confirmRequest.summary}</pre>
            <div className="flex gap-2.5">
              <button onClick={() => handleConfirm(true)} className="btn-primary text-sm">Approve</button>
              <button onClick={() => handleConfirm(false)} className="btn-ghost text-sm">Deny</button>
            </div>
          </div>
        </div>
        )
      )}

      {/* Input Area */}
      <div className="px-6 pb-6 relative">
        {/* Command Palette */}
        {showPalette && filteredCommands.length > 0 && (
          <div
            className="absolute bottom-full left-6 right-6 mb-2 rounded-xl overflow-hidden max-h-64 overflow-y-auto z-50 animate-slide-up"
            style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', boxShadow: 'var(--shadow-elevated)' }}
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
        <div
          className="max-w-3xl mx-auto transition-all"
          style={{ background: 'var(--surface-1)', border: '1px solid var(--border-strong)', borderRadius: 'var(--radius-xl)', overflow: 'hidden', boxShadow: 'var(--shadow-float)', transitionDuration: '200ms', transitionTimingFunction: 'var(--ease)' }}
          onFocusCapture={e => { e.currentTarget.style.borderColor = 'var(--accent-ring)'; e.currentTarget.style.boxShadow = '0 0 0 3px var(--accent-glow), var(--shadow-float)' }}
          onBlurCapture={e => { e.currentTarget.style.borderColor = 'var(--border-strong)'; e.currentTarget.style.boxShadow = 'var(--shadow-float)' }}
        >

          {/* Workspace chip — this chat's folder; locked once created */}
          <div className="flex items-center px-3 pt-2">
            {sessionId ? (
              <div
                className="flex items-center gap-1.5 px-1.5 py-0.5"
                style={{ color: 'var(--fg-muted)' }}
                title={chipWorkspace ? `工作区：${chipWorkspace}（创建后不可更改）` : undefined}
              >
                <FolderOpen size={12} style={{ color: 'var(--fg-faint)', flexShrink: 0 }} />
                <span className="text-[11.5px] font-mono truncate max-w-[320px]">
                  {chipWorkspace ?? '默认工作区'}
                </span>
              </div>
            ) : (
              <button
                onClick={handleWorkspacePick}
                className="flex items-center gap-1.5 px-1.5 py-0.5 rounded transition-colors hover:bg-[var(--surface-2)]"
                style={{ color: 'var(--fg-muted)' }}
                title={chipWorkspace ? `工作区：${chipWorkspace}\n发送第一条消息前可更改` : '选择该会话的工作区文件夹'}
              >
                <FolderOpen size={11} style={{ color: 'var(--fg-faint)', flexShrink: 0 }} />
                <span className="text-[10px] font-mono truncate max-w-[320px]">
                  {chipWorkspace ?? '选择工作区…'}
                </span>
              </button>
            )}
          </div>

          <div className="flex items-end gap-2.5 p-3 pt-1.5">
            <textarea
              ref={inputRef}
              value={input}
              onChange={handleInputChange}
              onKeyDown={handleKeyDown}
              placeholder={isRunning ? "Agent running... messages will be queued" : "Message Nexus..."}
              className="flex-1 bg-transparent text-[15px] resize-none focus:outline-none max-h-32 min-h-[26px]"
              style={{ color: 'var(--fg)', fontFamily: 'var(--font-sans)' }}
              rows={1}
            />
            {isRunning ? (
              <button onClick={handleCancel} className="w-8 h-8 flex items-center justify-center rounded-lg transition-all shrink-0" style={{ background: 'var(--red-muted)', color: 'var(--red)' }}><Square size={14} /></button>
            ) : (
              <button
                onClick={handleSend}
                disabled={!input.trim()}
                className="w-8 h-8 flex items-center justify-center rounded-lg transition-all shrink-0 disabled:opacity-60"
                style={{
                  background: 'var(--accent)',
                  color: 'var(--on-accent)',
                }}
              >
                <Send size={14} />
              </button>
            )}
          </div>

          {/* HUD row — model switcher + session actions */}
          <div className="flex items-center gap-1 px-3 py-2 text-[12px] overflow-x-auto whitespace-nowrap" style={{ borderTop: '1px solid var(--border-subtle)', color: 'var(--fg-muted)' }}>
            <ModelPicker
              currentModel={runtimeStatus?.model_id ?? null}
              onSwitched={(id) => setRuntimeStatus((prev: Record<string, unknown> | null) => (prev ? { ...prev, model_id: id } : prev))}
            />
            <PlanModeToggle sessionId={sessionId} />
            {versionStatus?.head && (
              <span className="flex items-center gap-1 shrink-0">
                <span className="w-1 h-1 rounded-full" style={{ background: 'var(--accent)' }} />
                checkpoint: {versionStatus.head.id?.slice(0, 8)}
              </span>
            )}
            <div className="flex items-center gap-1 shrink-0 ml-auto">
              <HudAction icon={GitBranch} label="时间线" disabled={!sessionId}
                title="Session timeline — every step + full context"
                onClick={() => sessionId && navigate(`/chat/${sessionId}/timeline`)} />
              <HudAction icon={Undo2} label="撤销" disabled={!versionStatus?.can_undo}
                title="撤销到上一个 checkpoint" onClick={handleUndo} />
              <HudAction icon={Redo2} label="重做" disabled={!versionStatus?.can_redo}
                title="重做到下一个 checkpoint" onClick={handleRedo} />
              <HudAction icon={History} label="检查点"
                title="查看 checkpoint 历史"
                onClick={() => { setShowCheckpoints(!showCheckpoints); api.getVersionLog(10).then(d => setCheckpoints(d.checkpoints || [])) }} />
            </div>
          </div>
        </div>
      </div>

      {/* Checkpoint Overlay */}
      {showCheckpoints && (
        <div className="absolute bottom-28 left-5 w-96 rounded-xl overflow-hidden z-50 animate-slide-up" style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', boxShadow: 'var(--shadow-elevated)' }}>
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
