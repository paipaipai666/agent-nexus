import React, { useState, useRef, useEffect, useLayoutEffect, useCallback } from 'react'
import { useParams, useLocation, useNavigate } from 'react-router-dom'
import { Send, Square, Undo2, Redo2, History, FolderOpen, BookOpen, Bug, FlaskConical, Wrench, ArrowRight, GitBranch, Paperclip, X, Loader2 } from 'lucide-react'
import { BorderBeam } from 'border-beam'
import { api, type AttachmentRef, mimeForPath, humanSize } from '../services/api'
import { planModeArm } from '../services/planModeArm'
import { thinkingEffortArm } from '../services/thinkingEffortArm'
import ThinkEffortControl from '../components/chat/ThinkEffortControl'
import { useProjects, pickAndAddProject, workspaceKey } from '../services/projects'
import { useChatDraft, clearChatDraft, getChatDraft, saveChatDraft } from '../services/chatDrafts'
import InlineNotice from '../components/InlineNotice'
import { transformHistoryMessages } from '../utils/historyTransform'
import { usePrefersReducedMotion } from '../utils/effects'
import { useSession, type Message } from '../components/session/SessionProvider'
import { useTheme } from '../components/theme/ThemeProvider'
import InfoPanel from '../components/layout/InfoPanel'
import ModelPicker from '../components/chat/ModelPicker'
import PlanModeToggle from '../components/chat/PlanModeToggle'
import PlanReviewCard from '../components/chat/PlanReviewCard'
import ToolGroup from '../components/chat/ToolGroup'
import MessageBubble from '../components/chat/MessageBubble'
import HudAction from '../components/chat/HudAction'
import RecoveryPanel from '../components/chat/RecoveryPanel'

const PLAN_REVIEW_MARKER = '[PLAN_REVIEW]'

interface Checkpoint { id: string; question: string; answer: string; is_head: boolean }
let didAutoRestoreLastSession = false

type SkillInfo = { id: string; display_name: string; description: string; enabled: boolean }
type CommandCategory = 'system' | 'skill' | 'mcp' | 'plugin'

interface SlashCtx {
  sid: string | null
  defs: CommandDef[]
  addSys: (c: string) => void
  skills: SkillInfo[]
  setSkills: React.Dispatch<React.SetStateAction<SkillInfo[]>>
  refresh: () => Promise<void>
  clearView: () => void
  switchSession: (id: string) => void
}

interface CommandDef {
  cmd: string
  desc: string
  category: CommandCategory
  needsSession?: boolean
  err?: string
  run: (args: string, ctx: SlashCtx) => Promise<string | void> | string | void
}

function errMsg(e: unknown): string | undefined {
  if (e && typeof e === 'object' && 'message' in e && typeof e.message === 'string') return e.message
  return undefined
}

function buildCommandDefs(): CommandDef[] {
  return [
    {
      cmd: '/help', desc: '查看命令帮助', category: 'system',
      run: (_args, ctx) => ctx.defs.map(c => `${c.cmd.padEnd(12)} ${c.desc}`).join('\n'),
    },
    { cmd: '/clear', desc: '清空当前显示', category: 'system', run: (_args, ctx) => { ctx.clearView() } },
    {
      cmd: '/undo', desc: '撤销到上一个检查点', category: 'system', needsSession: true, err: '撤销失败',
      run: async (_args, ctx) => {
        const r = await api.versionUndo(ctx.sid!)
        await ctx.refresh()
        return `Undone to checkpoint: ${r.checkpoint?.id || 'ok'}`
      },
    },
    {
      cmd: '/redo', desc: '重做到下一个检查点', category: 'system', needsSession: true, err: '重做失败',
      run: async (_args, ctx) => {
        const r = await api.versionRedo(ctx.sid!)
        await ctx.refresh()
        return `Redone to checkpoint: ${r.checkpoint?.id || 'ok'}`
      },
    },
    {
      cmd: '/log', desc: '查看检查点历史', category: 'system', needsSession: true, err: '历史加载失败',
      run: async (_args, ctx) => {
        const { checkpoints: cps } = await api.getVersionLog(ctx.sid!, 10)
        return cps.length === 0 ? 'No checkpoints.' : cps.map(cp => `${cp.is_head ? '→ ' : '  '}${cp.id}  ${cp.question || ''}`).join('\n')
      },
    },
    {
      cmd: '/status', desc: '查看会话版本状态', category: 'system', needsSession: true, err: '状态加载失败',
      run: async (_args, ctx) => {
        const s = await api.getVersionStatus(ctx.sid!)
        return `Session: ${s.session_id}\nHEAD: ${s.head?.id || 'none'}\nCan undo: ${s.can_undo}\nCan redo: ${s.can_redo}`
      },
    },
    {
      cmd: '/compact', desc: '整理对话上下文', category: 'system', needsSession: true, err: '上下文整理失败',
      run: async (args, ctx) => {
        ctx.addSys('正在整理上下文…')
        const r = await api.compactContext(ctx.sid!, args)
        return `Compacted: ${r.tokens_saved} tokens saved`
      },
    },
    {
      cmd: '/sessions', desc: '查看最近会话', category: 'system', err: '会话加载失败',
      run: async () => {
        const { sessions } = await api.getRecentSessions(10)
        return sessions.length === 0 ? 'No recent sessions.' : sessions.map(s => `${s.session_id.slice(0, 12)}  ${s.preview || ''}`).join('\n')
      },
    },
    {
      cmd: '/switch', desc: '切换会话，用法：/switch <会话 ID>', category: 'system',
      run: (args, ctx) => {
        if (!args) return 'Usage: /switch <session_id>'
        ctx.switchSession(args)
      },
    },
    {
      cmd: '/skill', desc: '查看、启用或停用技能', category: 'skill',
      run: async (args, ctx) => {
        if (!args || args === 'list') {
          try {
            const { skills: sk } = await api.listSkills()
            return sk.length === 0 ? 'No skills registered.' : sk.map(s => {
              const short = s.id.includes('/') ? s.id.split('/').pop()! : s.id
              return `${s.enabled ? '●' : '○'} ${short.padEnd(20)} ${s.display_name || s.description || ''}`
            }).join('\n')
          } catch (e) { return `Skills failed: ${errMsg(e)}` }
        }
        if (args.startsWith('enable ')) {
          const name = args.slice(7).trim()
          const full = ctx.skills.find(s => (s.id.includes('/') ? s.id.split('/').pop()! : s.id) === name)?.id || name
          try { await api.enableSkill(full); ctx.setSkills((await api.listSkills()).skills); return `Skill enabled: ${name}` } catch (e) { return `Enable failed: ${errMsg(e)}` }
        }
        if (args.startsWith('disable ')) {
          const name = args.slice(8).trim()
          const full = ctx.skills.find(s => (s.id.includes('/') ? s.id.split('/').pop()! : s.id) === name)?.id || name
          try { await api.disableSkill(full); ctx.setSkills((await api.listSkills()).skills); return `Skill disabled: ${name}` } catch (e) { return `Disable failed: ${errMsg(e)}` }
        }
        return 'Usage: /skill [list | enable <id> | disable <id>]'
      },
    },
    {
      cmd: '/mcp', desc: '查看 MCP 状态、工具或重新加载', category: 'mcp',
      run: async (args) => {
        if (!args || args === 'status') {
          try {
            const s = await api.getMcpStatus()
            const servers = s.servers || []
            return servers.length === 0 ? 'No MCP servers.' : servers.map((sv: any) => `${sv.connected ? '●' : '○'} ${sv.name}  ${sv.tool_names?.length || 0} tools`).join('\n')
          } catch (e) { return `MCP status failed: ${errMsg(e)}` }
        }
        if (args === 'tools') {
          try {
            const { tools } = await api.listMcpTools()
            return tools.length === 0 ? 'No MCP tools.' : tools.map(t => `${t.tool.padEnd(30)} [${t.server}]`).join('\n')
          } catch (e) { return `MCP tools failed: ${errMsg(e)}` }
        }
        if (args === 'reload') {
          try { await api.reloadMcp(); return 'MCP reloaded.' } catch (e) { return `MCP reload failed: ${errMsg(e)}` }
        }
        return 'Usage: /mcp [status | tools | reload]'
      },
    },
    {
      cmd: '/plugin', desc: '查看已发现的插件', category: 'plugin', err: '插件加载失败',
      run: async (args) => {
        if (args && args !== 'list' && args !== 'status') return '用法：/plugin [list]'
        const ext = await api.getExtensions()
        const entries: unknown = ext.discovered ?? ext.load_report?.loaded ?? []
        const names = Array.isArray(entries) ? entries.flatMap(entry =>
          typeof entry === 'object' && entry !== null && 'name' in entry && typeof entry.name === 'string' ? [entry.name] : []) : []
        return names.length === 0 ? '尚未发现插件。' : [...new Set(names)].join('\n')
      },
    },
  ]
}

const STARTERS = [
  {
    icon: BookOpen,
    label: '理解这个项目',
    prompt: '请介绍这个项目的架构、主要模块，以及模块之间的关系。',
  },
  {
    icon: Bug,
    label: '修复一个问题',
    prompt: '帮我排查一个问题。我会描述现象，请根据需要向我索取日志或代码。',
  },
  {
    icon: FlaskConical,
    label: '补充测试',
    prompt: '请为当前模块编写单元测试，覆盖主要流程和重要边界情况。',
  },
  {
    icon: Wrench,
    label: '改善代码结构',
    prompt: '请检查当前代码的可读性和可重构之处，逐步提出修改建议。',
  },
]

export default function ChatPage() {
  const { sessionId: routeSessionId } = useParams<{ sessionId?: string }>()
  const location = useLocation()
  const navigate = useNavigate()
  const { theme } = useTheme()
  const reducedMotion = usePrefersReducedMotion()
  const currentSessionIdRef = useRef<string | null>(null)
  const { selected: selectedProject } = useProjects()
  const [pendingWorkspace, setPendingWorkspace] = useState<string | null>(null)
  const draftKey = routeSessionId ? `session:${routeSessionId}` : `new:${workspaceKey(pendingWorkspace ?? selectedProject ?? '')}`
  const { input, setInput, attachments, setAttachments } = useChatDraft(draftKey)
  const draftKeyRef = useRef(draftKey)
  draftKeyRef.current = draftKey
  const sendGeneration = useRef(0)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const pinnedToBottom = useRef(true)
  const submitting = useRef<object | null>(null)
  const [creatingSession, setCreatingSession] = useState(false)
  const [hasNewContent, setHasNewContent] = useState(false)
  const [sendError, setSendError] = useState<string | null>(null)
  const [restoreAttempt, setRestoreAttempt] = useState(0)
  const [restoreState, setRestoreState] = useState<{ kind: 'idle' } | { kind: 'loading' } | { kind: 'failed'; message: string }>({ kind: 'idle' })
  const [literalMessage, setLiteralMessage] = useState(false)
  const [actionNotice, setActionNotice] = useState<string | null>(null)
  const [commandNotice, setCommandNotice] = useState<{ tone: 'info' | 'error'; message: string } | null>(null)
  const [versionBusy, setVersionBusy] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const [deadPaths, setDeadPaths] = useState<Set<string>>(new Set())

  const [versionStatus, setVersionStatus] = useState<any>(null)
  const [defaultRuntimeStatus, setDefaultRuntimeStatus] = useState<Awaited<ReturnType<typeof api.getRuntimeStatus>> | null>(null)
  const [checkpoints, setCheckpoints] = useState<Checkpoint[]>([])
  const [showCheckpoints, setShowCheckpoints] = useState(false)

  const [showPalette, setShowPalette] = useState(false)
  const [paletteFilter, setPaletteFilter] = useState('')
  const [paletteIndex, setPaletteIndex] = useState(0)
  const [paletteDismissed, setPaletteDismissed] = useState(false)
  const paletteAnimDoneRef = useRef(false)

  const [skills, setSkills] = useState<Array<{ id: string; display_name: string; description: string; enabled: boolean }>>([])
  const [mcpTools, setMcpTools] = useState<Array<{ server: string; tool: string; transport: string }>>([])
  const [defaultWorkspace, setDefaultWorkspace] = useState<string | null>(null)

  const {
    sessionId, cwd, runtimeStatus: sessionRuntimeStatus, setSessionId: setGlobalSessionId, setRuntimeInfo, setCwd, setToolCount, setTodoCount,
    messages, setMessages, isRunning, pendingSubmission, stopState, confirmRequest,
    sendMessage, cancelRun, confirmToolCall, queueMessage, animatedIds, incrementMsgCounter, resetForSessionSwitch, getLiveDisplayMessages,
  } = useSession()
  const runtimeStatus = sessionId ? sessionRuntimeStatus : defaultRuntimeStatus

  const scrollRafRef = useRef<number>(0)
  const lastEscapeAtRef = useRef<number>(0)
  const ESC_DOUBLE_TAP_MS = 600
  const scrollToBottom = useCallback(() => {
    if (scrollRafRef.current) return
    scrollRafRef.current = requestAnimationFrame(() => {
      scrollRafRef.current = 0
      const container = scrollContainerRef.current
      if (pinnedToBottom.current && container) container.scrollTop = container.scrollHeight
    })
  }, [])

  useLayoutEffect(() => {
    const field = inputRef.current
    if (!field) return
    field.style.height = 'auto'
    field.style.height = `${Math.max(26, Math.min(128, field.scrollHeight))}px`
  }, [input, draftKey])

  useEffect(() => {
    sendGeneration.current++
    submitting.current = null
    setCreatingSession(false)
    pinnedToBottom.current = true
    setHasNewContent(false)
    setDeadPaths(new Set())
    setSendError(null)
    setLiteralMessage(false)
    setActionNotice(null)
    setCommandNotice(null)
    setShowPalette(false)
    setPaletteFilter('')
  }, [draftKey])

  useLayoutEffect(() => () => {
    sendGeneration.current++
    if (scrollRafRef.current) {
      cancelAnimationFrame(scrollRafRef.current)
      scrollRafRef.current = 0
    }
  }, [])

  const loadAndDisplayMessages = useCallback(async (options: { sessionId?: string; reload?: boolean } = {}) => {
    const currentSid = options.sessionId || currentSessionIdRef.current
    console.log('[loadAndDisplayMessages] sid:', currentSid)
    if (currentSid && !options.reload) {
      const cachedMessages = getLiveDisplayMessages(currentSid)
      if (cachedMessages && cachedMessages.length > 0) {
        console.log('[loadAndDisplayMessages] Using Map cache:', cachedMessages.length, 'messages')
        for (const m of cachedMessages) animatedIds.add(m.id)
        return
      }
    }
    try {
      let stm: Array<{ role: string; content: string; ts?: number }>
      try {
        const hist = await api.listSessionHistory(0, currentSid || undefined)
        stm = currentSid ? hist.messages ?? [] : hist.messages && hist.messages.length > 0 ? hist.messages : (await api.listShortMemories()).messages
      } catch {
        if (currentSid) throw new Error('会话历史暂时不可用，请重试。')
        stm = (await api.listShortMemories()).messages
      }
      if (currentSid !== currentSessionIdRef.current) return
      console.log('[loadAndDisplayMessages] Backend returned:', stm?.length, 'messages for sid:', currentSid)
      if (!stm || stm.length === 0) {
        if (currentSid && !options.reload) {
          const existingMessages = getLiveDisplayMessages(currentSid)
          if (existingMessages && existingMessages.length > 0) return
        }
        setMessages([])
        return
      }

      const transformed = transformHistoryMessages(stm)
      console.log('[loadAndDisplayMessages] Transformed:', transformed.length, 'messages. Setting on active session.')
      for (const m of transformed) animatedIds.add(m.id)
      setMessages(transformed)
    } catch (err) { if (currentSid === currentSessionIdRef.current) setActionNotice(`加载会话历史失败：${errMsg(err) ?? '请重试'}`) }
  }, [])

  useEffect(() => {
    let cancelled = false
    let pending = false
    const refresh = async () => {
      if (pending) return
      pending = true
      try {
        const status = await api.getRuntimeStatus(sessionId ?? undefined)
        if (!cancelled) {
          if (sessionId) setRuntimeInfo(sessionId, status)
          else setDefaultRuntimeStatus(status)
        }
      } catch {
      } finally {
        pending = false
      }
    }
    void refresh()
    const interval = window.setInterval(refresh, isRunning ? 1000 : 5000)
    return () => { cancelled = true; window.clearInterval(interval) }
  }, [sessionId, isRunning, setRuntimeInfo])

  const fetchDynamicCommands = useCallback(() => {
    api.listSkills().then(d => setSkills(d.skills || [])).catch(() => {})
    api.listMcpTools().then(d => setMcpTools(d.tools || [])).catch(() => {})
  }, [])

  const initNew = useCallback((sid: string) => {
    currentSessionIdRef.current = sid
    setGlobalSessionId(sid)
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

  useEffect(() => {
    api.getConfig().then((c) => {
      const cfgCwd = c?.cwd
      if (typeof cfgCwd === 'string' && cfgCwd) setDefaultWorkspace(cfgCwd)
    }).catch(() => {})
  }, [])

  useEffect(() => {
    const isFirstMount = !didAutoRestoreLastSession
    let cancelled = false
    didAutoRestoreLastSession = true
    const initRestore = (sid: string) => {
      console.log('[initRestore] Restoring session:', sid)
      currentSessionIdRef.current = sid
      setGlobalSessionId(sid)
      void loadAndDisplayMessages({ sessionId: sid })
      api.getVersionStatus(sid).then(setVersionStatus).catch(() => {})
      api.getVersionLog(sid, 5).then(d => setCheckpoints(d.checkpoints || [])).catch(() => {})
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
      if (routeSessionId === currentSessionIdRef.current) {
        return
      }
      resetForSessionSwitch()
      currentSessionIdRef.current = null
      setGlobalSessionId(null)
      setRestoreState({ kind: 'loading' })
      api.restoreSession(routeSessionId)
        .then(({ session_id, restored }) => {
          if (cancelled) return
          if (restored === false || session_id !== routeSessionId) throw new Error('请求的会话未能恢复，请检查会话列表')
          currentSessionIdRef.current = session_id
          setRestoreState({ kind: 'idle' })
          initRestore(session_id)
        })
        .catch(error => { if (!cancelled) setRestoreState({ kind: 'failed', message: `恢复会话失败：${errMsg(error) ?? '请检查后端连接'}。草稿已保留。` }) })
    } else {
      setRestoreState({ kind: 'idle' })
      resetForSessionSwitch()
      currentSessionIdRef.current = null
      setGlobalSessionId(null)
      api.getConfig().then((config: any) => { if (config.cwd) setDefaultWorkspace(config.cwd) }).catch(() => {})
      fetchDynamicCommands()
      if (isFirstMount) {
        api.getRecentSessions(5).then((d) => {
          const last = d.sessions?.find((s) => s.preview)
          if (!cancelled && last && !currentSessionIdRef.current) {
            navigate(`/chat/${last.session_id}`, { replace: true })
          }
        }).catch(() => {})
      }
    }
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeSessionId, restoreAttempt])

  useEffect(() => {
    if (pinnedToBottom.current) scrollToBottom()
    else setHasNewContent(true)
  }, [messages, scrollToBottom])

  useEffect(() => {
    const handleNewChat = () => {
      sendGeneration.current++
      submitting.current = null
      setCreatingSession(false)
      clearChatDraft(`new:${workspaceKey(selectedProject ?? '')}`)
      setShowPalette(false)
      setPaletteFilter('')
      setCommandNotice(null)
      setActionNotice(null)
      setSendError(null)
      setLiteralMessage(false)
      setDeadPaths(new Set())
      currentSessionIdRef.current = null
      pendingFirstMessageRef.current = null
      setGlobalSessionId(null)
      setPendingWorkspace(null)
    }
    window.addEventListener('new-chat', handleNewChat)
    return () => window.removeEventListener('new-chat', handleNewChat)
  }, [selectedProject])

  const pendingFirstMessageRef = useRef<{ text: string; attachments?: AttachmentRef[] } | null>(null)

  useEffect(() => {
    const pending = pendingFirstMessageRef.current
    if (!pending) return
    pendingFirstMessageRef.current = null
    sendMessage(pending.text, pending.attachments)
  }, [sendMessage])

  const handleSendMessage = async (text: string, atts?: AttachmentRef[]) => {
    const sourceKey = draftKey
    const sourceDraft = getChatDraft(sourceKey)
    const generation = sendGeneration.current
    const isCurrent = () => generation === sendGeneration.current && sourceKey === draftKeyRef.current
    if (!currentSessionIdRef.current) {
      setCreatingSession(true)
      try {
        const { session_id } = await api.createSession(undefined, pendingWorkspace ?? selectedProject ?? null)
        if (!isCurrent()) return
        if (planModeArm.consume()) {
          try { await api.setPlanMode(session_id, true) } catch {  }
        }
        const armedEffort = thinkingEffortArm.consume()
        if (armedEffort !== null) {
          try { await api.setThinkingEffort(session_id, armedEffort) } catch {  }
        }
        if (!isCurrent()) return
        const remainingDraft = getChatDraft(sourceKey)
        if (remainingDraft !== sourceDraft) saveChatDraft(`session:${session_id}`, remainingDraft)
        clearChatDraft(sourceKey)
        pendingFirstMessageRef.current = { text, attachments: atts }
        currentSessionIdRef.current = session_id
        initNew(session_id)
        setPendingWorkspace(null)
        navigate(`/chat/${session_id}`, { replace: true })
      } catch (error) {
        if (isCurrent()) setSendError(`创建会话失败：${errMsg(error) ?? '请重试'}。草稿已保留，可以再次发送。`)
      } finally {
        if (isCurrent()) setCreatingSession(false)
      }
    } else {
      const accepted = sendMessage(text, atts)
      if (accepted && getChatDraft(sourceKey) === sourceDraft) clearChatDraft(sourceKey)
      if (location.pathname === '/') {
        navigate(`/chat/${currentSessionIdRef.current}`, { replace: true })
      }
    }
  }

  const chipWorkspace = cwd ?? pendingWorkspace ?? (sessionId ? null : selectedProject) ?? defaultWorkspace
  const handleWorkspacePick = async () => {
    if (sessionId) return
    const picked = await pickAndAddProject(chipWorkspace)
    if (picked) setPendingWorkspace(picked)
  }

  const addSys = useCallback((c: string) => {
    if (!currentSessionIdRef.current) { setActionNotice(c); return }
    setMessages(prev => [...prev, { id: `sys-${incrementMsgCounter()}`, role: 'system', content: c, timestamp: new Date() }])
  }, [setMessages, incrementMsgCounter])

  const addAttachments = useCallback(async (candidates: { path: string; name: string; size: number }[]) => {
    const bridge = window.electronAPI
    if (!bridge || candidates.length === 0) return
    const fresh = candidates.filter(c => c.path && !attachments.some(a => a.path === c.path))
    if (fresh.length === 0) return
    try {
      const stats = await bridge.statFiles(fresh.map(c => c.path))
      const ok: AttachmentRef[] = []
      const rejected: string[] = []
      for (const c of fresh) {
        const s = stats.find(x => x.path === c.path)
        if (s && s.ok) ok.push({ path: c.path, name: c.name, size: s.size, mime: mimeForPath(c.path) })
        else rejected.push(c.name)
      }
      if (rejected.length > 0) addSys(`附件不可用（不存在或是文件夹）: ${rejected.join(', ')}`)
      if (ok.length > 0) setAttachments(prev => [...prev, ...ok])
    } catch {
      setAttachments(prev => [...prev, ...fresh.map(c => ({ path: c.path, name: c.name, size: c.size, mime: mimeForPath(c.path) }))])
    }
  }, [attachments, addSys, setAttachments])

  const handlePickFiles = async () => {
    const picked = await window.electronAPI?.pickFiles()
    if (picked) addAttachments(picked)
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault(); setDragOver(false)
    const bridge = window.electronAPI
    if (!bridge) return
    const files = Array.from(e.dataTransfer.files)
      .map(f => {
        const p = bridge.getPathForFile(f)
        return p ? { path: p, name: f.name, size: f.size } : null
      })
      .filter((c): c is { path: string; name: string; size: number } => c !== null)
    addAttachments(files)
  }

  const handleSend = async (asMessage = false) => {
    if (restoreState.kind !== 'idle') return
    if (submitting.current) return
    const sourceDraft = getChatDraft(draftKey)
    const generation = sendGeneration.current
    const isCurrent = () => generation === sendGeneration.current && draftKey === draftKeyRef.current
    const text = input.trim(); if (!text && attachments.length === 0) return
    const command = text.split(/\s+/)[0].toLowerCase()
    if (!asMessage && text.startsWith('/') && !allCommands.some(item => item.cmd === command)) {
      setSendError(`未找到命令「${command}」。输入和附件已保留，可将它作为普通消息发送。`)
      setLiteralMessage(true)
      return
    }
    const request = {}
    submitting.current = request
    try {
    setSendError(null)
    setLiteralMessage(false)
    if (attachments.length > 0 && window.electronAPI) {
      try {
        const stats = await window.electronAPI.statFiles(attachments.map(a => a.path))
        if (!isCurrent()) return
        if (getChatDraft(draftKey) !== sourceDraft) {
          setSendError('附件检查期间输入或附件发生了变化，最新草稿已保留，请再次发送。')
          return
        }
        const dead = new Set(stats.filter(s => !s.ok).map(s => s.path))
        if (dead.size > 0) {
          setDeadPaths(dead)
          const names = attachments.filter(a => dead.has(a.path)).map(a => a.name).join(', ')
          if (currentSessionIdRef.current) addSys(`附件已失效: ${names}。已阻止发送，请移除失效附件后重试。`)
          setSendError(`附件已失效：${names}。请移除或重新选择后发送。`)
          return
        }
        setDeadPaths(new Set())
      } catch {  }
    }
    if (!isCurrent()) return
    if (getChatDraft(draftKey) !== sourceDraft) {
      setSendError('发送检查期间草稿发生了变化，最新内容已保留，请再次发送。')
      return
    }
    const atts = attachments.length > 0 ? attachments : undefined
    setShowPalette(false); setPaletteDismissed(false); setDeadPaths(new Set())
    const out = text || '（见附件）'
    pinnedToBottom.current = true
    if (!asMessage && buildCommandDefs().some(item => item.cmd === command)) {
      clearChatDraft(draftKey)
      await handleSlashCommand(out)
      return
    }
    if (isRunning) { queueMessage(out, atts); clearChatDraft(draftKey) }
    else await handleSendMessage(out, atts)
    } finally { if (submitting.current === request) submitting.current = null }
  }

  const handleSlashCommand = async (text: string) => {
    const parts = text.trim().split(/\s+/); const cmd = parts[0].toLowerCase(); const args = parts.slice(1).join(' ')
    const sid = currentSessionIdRef.current
    const addSys = (content: string, failed = false) => {
      if (!sid) setCommandNotice({ tone: failed ? 'error' : 'info', message: content })
      else setMessages(previous => [...previous, { id: `cmd-${incrementMsgCounter()}`, role: 'system', content, timestamp: new Date() }])
    }
    const ctx: SlashCtx = {
      sid,
      defs: [],
      addSys,
      skills, setSkills,
      switchSession: id => navigate(`/chat/${encodeURIComponent(id)}`),
      refresh: async () => {
        setVersionStatus(await api.getVersionStatus(sid!))
        setCheckpoints((await api.getVersionLog(sid!, 5)).checkpoints || [])
        await loadAndDisplayMessages({ sessionId: sid ?? undefined, reload: true })
      },
      clearView: () => { if (sid) setMessages([]); setCommandNotice(null); animatedIds.clear() },
    }
    ctx.defs = buildCommandDefs()
    const def = ctx.defs.find(d => d.cmd === cmd)
    if (def) {
      if (def.needsSession && !sid) { addSys('请先发送一条消息以创建会话。', true); return }
      try {
        const out = await def.run(args, ctx)
        if (typeof out === 'string') addSys(out)
      } catch (e) { addSys(`${def.err ?? `${cmd} 执行失败`}：${errMsg(e) ?? '请重试'}`, true) }
      return
    }
    const skillMatch = skills.find(s => {
      if (!s.enabled) return false
      const shortId = s.id.includes('/') ? s.id.split('/').pop()! : s.id
      return `/${shortId}` === cmd
    })
    if (skillMatch) { await handleSendMessage(text); return }
    addSys(`Unknown command: ${cmd}. Type /help for commands.`)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.nativeEvent.isComposing || e.keyCode === 229) return
    if (showPalette && filteredCommands.length > 0) {
      if (e.key === 'ArrowDown') { e.preventDefault(); setPaletteIndex(i => (i + 1) % filteredCommands.length); return }
      if (e.key === 'ArrowUp') { e.preventDefault(); setPaletteIndex(i => (i - 1 + filteredCommands.length) % filteredCommands.length); return }
      if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); handlePaletteSelect(filteredCommands[paletteIndex].cmd); return }
      if (e.key === 'Escape') { e.preventDefault(); setShowPalette(false); setPaletteDismissed(true); paletteAnimDoneRef.current = false; return }
    }
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
    setSendError(null)
    setLiteralMessage(false)
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
    const sid = currentSessionIdRef.current
    if (!sid || versionBusy) return
    setVersionBusy(true)
    setActionNotice(null)
    try {
      await api.versionUndo(sid)
      setVersionStatus(await api.getVersionStatus(sid))
      setCheckpoints((await api.getVersionLog(sid, 5)).checkpoints || [])
      await loadAndDisplayMessages({ sessionId: sid, reload: true })
    } catch (error) { setActionNotice(`撤销失败：${errMsg(error) ?? '请重试'}`) }
    finally { setVersionBusy(false) }
  }
  const handleRedo = async () => {
    const sid = currentSessionIdRef.current
    if (!sid || versionBusy) return
    setVersionBusy(true)
    setActionNotice(null)
    try {
      await api.versionRedo(sid)
      setVersionStatus(await api.getVersionStatus(sid))
      setCheckpoints((await api.getVersionLog(sid, 5)).checkpoints || [])
      await loadAndDisplayMessages({ sessionId: sid, reload: true })
    } catch (error) { setActionNotice(`重做失败：${errMsg(error) ?? '请重试'}`) }
    finally { setVersionBusy(false) }
  }

  const allCommands = [
    ...buildCommandDefs(),
    ...skills.filter(s => s.enabled).map(s => {
      const shortId = s.id.includes('/') ? s.id.split('/').pop()! : s.id
      return { cmd: `/${shortId}`, desc: s.display_name || s.description || 'Invoke skill', category: 'skill' as const }
    }),
  ]

  const filteredCommands = allCommands.filter(c => {
    const q = paletteFilter.toLowerCase()
    return c.cmd.startsWith(q) || c.desc.toLowerCase().includes(q)
  })

  return (
    <div className="flex-1 flex overflow-hidden relative" style={{ background: 'var(--surface-0)' }}>
      {}
      <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
      {}
      <div className="relative flex-1 min-h-0">
      <div ref={scrollContainerRef} data-testid="chat-scroll" className="h-full overflow-y-auto"
        onScroll={() => {
          const container = scrollContainerRef.current
          if (!container) return
          pinnedToBottom.current = container.scrollHeight - container.scrollTop - container.clientHeight < 64
          if (pinnedToBottom.current) setHasNewContent(false)
        }}>
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full gap-6 animate-empty-in px-6">
            <div className="text-center">
              <h1
                className="text-[27px] font-semibold mb-3"
                style={{ letterSpacing: '-0.025em' }}
              >
                <span className="metal-heading">今天要完成什么？</span>
              </h1>
              <p className="text-[15px] max-w-md mx-auto" style={{ color: 'var(--fg-muted)' }}>
                选择项目，描述任务，从这里开始。
              </p>
            </div>

            {}
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
            <ToolGroup
              key={g.msgs[0].id}
              msgs={g.msgs}
              mcpNames={mcpNames}
            />
          ))
        })()}
        <div ref={messagesEndRef} />
      </div>
      {hasNewContent && <button type="button" className="absolute bottom-3 left-1/2 -translate-x-1/2 btn-primary text-sm"
        onClick={() => {
          pinnedToBottom.current = true
          setHasNewContent(false)
          const container = scrollContainerRef.current
          if (container) container.scrollTop = container.scrollHeight
        }}>回到最新消息</button>}
      </div>

      {}
      {confirmRequest && (
        confirmRequest.summary.startsWith(PLAN_REVIEW_MARKER) ? (
          <div className="max-w-3xl mx-auto w-full px-6 mb-3">
            <PlanReviewCard
              plan={confirmRequest.summary.slice(PLAN_REVIEW_MARKER.length).trim()}
              onApprove={() => handleConfirm(true)}
              onDeny={() => handleConfirm(false)}
              disabled={confirmRequest.status === 'timed-out' || confirmRequest.status === 'sending'}
            />
          </div>
        ) : (
        <div className="max-w-3xl mx-auto w-full px-6 mb-3">
          <BorderBeam
            size="pulse-inner"
            colorVariant="mono"
            theme={theme}
            active={!reducedMotion}
            strength={0.7}
            borderRadius={16}
          >
            <div className="rounded-2xl p-5" style={{ background: 'var(--surface-1)', border: '1px solid var(--border-strong)', boxShadow: 'var(--shadow-float)' }}>
              <div className="flex items-center gap-2 mb-3">
                <span className="w-2 h-2 rounded-full" style={{ background: 'var(--amber)' }} />
                <span className="text-[14px] font-semibold" style={{ color: 'var(--fg)' }}>需要你的确认</span>
              </div>
              <pre className="text-[12.5px] rounded-xl p-3 mb-4 overflow-auto max-h-32 font-mono" style={{ background: 'var(--surface-2)', color: 'var(--fg-secondary)' }}>{confirmRequest.summary}</pre>
              <div className="flex gap-2.5">
                <button disabled={confirmRequest.status === 'timed-out' || confirmRequest.status === 'sending'} onClick={() => handleConfirm(true)} className="btn-primary text-sm">批准执行</button>
                <button disabled={confirmRequest.status === 'timed-out' || confirmRequest.status === 'sending'} onClick={() => handleConfirm(false)} className="btn-ghost text-sm">拒绝执行</button>
              </div>
              {confirmRequest.status === 'timed-out' && <p role="status">确认已过期或已处理，请查询任务状态。</p>}
              {confirmRequest.status === 'sending' && <p role="status">正在等待确认回执…</p>}
            </div>
          </BorderBeam>
        </div>
        )
      )}

      {}
      <div className="px-6 pb-6 relative">
        <div className="max-w-[720px] mx-auto mb-2 space-y-2">
          <RecoveryPanel />
          {restoreState.kind === 'loading' && <p role="status">正在恢复会话…</p>}
          {restoreState.kind === 'failed' && <div role="alert" className="space-y-2 text-sm">
            <p>{restoreState.message}</p>
            <button className="btn-ghost" onClick={() => setRestoreAttempt(value => value + 1)}>重试恢复会话</button>
            <button className="btn-ghost" onClick={() => navigate('/')}>新建会话</button>
          </div>}
          {commandNotice && <div className="max-h-44 overflow-auto"><InlineNotice message={commandNotice.message} tone={commandNotice.tone} onDismiss={() => setCommandNotice(null)} /></div>}
          <InlineNotice message={actionNotice} onDismiss={() => setActionNotice(null)} />
          <InlineNotice message={sendError} onDismiss={() => { setSendError(null); setLiteralMessage(false) }} />
          {literalMessage && <button type="button" className="btn-ghost text-sm" onClick={() => handleSend(true)}>作为普通消息发送</button>}
        </div>
        {}
        {showPalette && filteredCommands.length > 0 && (
          <div
            className="absolute bottom-full left-6 right-6 mb-2 rounded-xl overflow-hidden max-h-64 overflow-y-auto z-50 animate-slide-up"
            style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', boxShadow: 'var(--shadow-elevated)' }}
          >

            {(() => {
              const CATEGORY_LABELS: Record<string, string> = { system: '系统命令', skill: '技能', mcp: 'MCP', plugin: '插件' }
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
        <BorderBeam
          size="md"
          theme={theme}
          active={isRunning && !reducedMotion}
          strength={0.85}
          borderRadius={12}
          style={{ maxWidth: 720, margin: '0 auto' }}
        >
        <div
          className="transition-all"
          style={{
            background: 'var(--surface-1)',
            border: `1px solid ${dragOver ? 'var(--accent-ring)' : 'var(--border-strong)'}`,
            borderRadius: 'var(--radius-xl)',
            overflow: 'hidden',
            boxShadow: 'var(--shadow-float)',
            transitionDuration: '200ms',
            transitionTimingFunction: 'var(--ease)',
          }}
          onDragOver={e => { e.preventDefault(); setDragOver(true) }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
          onFocusCapture={e => { e.currentTarget.style.borderColor = 'var(--accent-ring)'; e.currentTarget.style.boxShadow = '0 0 0 3px var(--accent-glow), var(--shadow-float)' }}
          onBlurCapture={e => { e.currentTarget.style.borderColor = 'var(--border-strong)'; e.currentTarget.style.boxShadow = 'var(--shadow-float)' }}
        >

          {}
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

          {}
          {attachments.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5 px-3 pt-2">
              {attachments.map(a => {
                const dead = deadPaths.has(a.path)
                return (
                  <span
                    key={a.path}
                    title={a.path}
                    className="flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] max-w-[280px]"
                    style={{
                      background: 'var(--surface-2)',
                      border: `1px solid ${dead ? 'var(--red)' : 'var(--border)'}`,
                      color: dead ? 'var(--red)' : 'var(--fg-muted)',
                      textDecoration: dead ? 'line-through' : 'none',
                    }}
                  >
                    <span className="truncate">{a.name}</span>
                    <span style={{ color: 'var(--fg-faint)' }}>{humanSize(a.size)}</span>
                    <button
                      onClick={() => {
                        setAttachments(prev => prev.filter(x => x.path !== a.path))
                        setDeadPaths(prev => { const n = new Set(prev); n.delete(a.path); return n })
                      }}
                      className="p-0.5 rounded"
                      style={{ color: 'var(--fg-faint)' }}
                      title="移除附件"
                      aria-label={`移除附件 ${a.name}`}
                    >
                      <X size={10} />
                    </button>
                  </span>
                )
              })}
            </div>
          )}

          <div className="flex items-end gap-2.5 p-3 pt-1.5">
            {window.electronAPI && (
              <button
                onClick={handlePickFiles}
                className="w-8 h-8 flex items-center justify-center rounded-lg transition-all shrink-0 hover:bg-[var(--surface-2)]"
                style={{ color: 'var(--fg-muted)' }}
                title="添加附件（本地文件，按路径引用）"
                aria-label="添加附件"
              >
                <Paperclip size={14} />
              </button>
            )}
            <textarea
              ref={inputRef}
              value={input}
              onChange={handleInputChange}
              onKeyDown={handleKeyDown}
              aria-label="消息输入"
              aria-busy={isRunning || creatingSession}
              placeholder={isRunning ? "Agent 正在运行，消息将加入队列" : "向 Nexus 发送消息…"}
              className="flex-1 bg-transparent text-[15px] resize-none focus:outline-none max-h-32 min-h-[26px]"
              style={{ color: 'var(--fg)', fontFamily: 'var(--font-sans)' }}
              rows={1}
            />
            {isRunning ? (
              <button onClick={handleCancel} disabled={stopState === 'requesting'} aria-label="停止任务" title={stopState === 'requesting' ? '正在确认停止' : '停止任务'} className="w-8 h-8 flex items-center justify-center rounded-lg transition-all shrink-0" style={{ background: 'var(--red-muted)', color: 'var(--red)' }}><Square size={14} /></button>
            ) : (
              <button
                onClick={() => handleSend()}
                aria-label="发送消息"
                title="发送消息（Enter）；换行（Shift+Enter）"
                disabled={!!pendingSubmission || restoreState.kind !== 'idle' || creatingSession || (!input.trim() && attachments.length === 0)}
                className="w-8 h-8 flex items-center justify-center rounded-lg transition-all shrink-0 disabled:opacity-60"
                style={{
                  background: 'var(--accent)',
                  color: 'var(--on-accent)',
                }}
              >
                {creatingSession ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
              </button>
            )}
          </div>

          {}
          <div className="flex items-center gap-1 px-3 py-2 text-[12px] overflow-x-auto whitespace-nowrap" style={{ borderTop: '1px solid var(--border-subtle)', color: 'var(--fg-muted)' }}>
            <ModelPicker
              currentModel={runtimeStatus?.model_id ?? null}
              onSwitched={(id) => {
                if (!runtimeStatus) return
                const next = { ...runtimeStatus, model_id: id, context_usage: null }
                if (sessionId) setRuntimeInfo(sessionId, next)
                else setDefaultRuntimeStatus(next)
              }}
            />
            <ThinkEffortControl />
            <PlanModeToggle />
            {versionStatus?.head && (
              <span className="flex items-center gap-1 shrink-0">
                <span className="w-1 h-1 rounded-full" style={{ background: 'var(--accent)' }} />
                检查点：{versionStatus.head.id?.slice(0, 8)}
              </span>
            )}
            <div className="flex items-center gap-1 shrink-0 ml-auto">
              <HudAction icon={GitBranch} label="时间线" disabled={!sessionId}
                title="查看任务时间线与上下文"
                onClick={() => sessionId && navigate(`/chat/${sessionId}/timeline`)} />
              <HudAction icon={Undo2} label="撤销" disabled={isRunning || versionBusy || !versionStatus?.can_undo}
                title="恢复上一个会话检查点，仅恢复对话上下文，不撤销文件修改" onClick={handleUndo} />
              <HudAction icon={Redo2} label="重做" disabled={isRunning || versionBusy || !versionStatus?.can_redo}
                title="重做到下一个检查点" onClick={handleRedo} />
              <HudAction icon={History} label="检查点"
                title="查看检查点历史"
                onClick={() => {
                  setShowCheckpoints(!showCheckpoints)
                  const sid = currentSessionIdRef.current
                  if (sid) api.getVersionLog(sid, 10).then(d => setCheckpoints(d.checkpoints || []))
                    .catch(error => setActionNotice(`检查点加载失败：${errMsg(error) ?? '请重试'}`))
                }} />
            </div>
          </div>
        </div>
        </BorderBeam>
      </div>

      {}
      {showCheckpoints && (
        <div className="absolute bottom-28 left-5 w-96 rounded-xl overflow-hidden z-50 animate-slide-up" style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', boxShadow: 'var(--shadow-elevated)' }}>
          <div className="px-4 py-3 flex items-center justify-between" style={{ borderBottom: '1px solid var(--border)' }}>
            <span className="text-sm font-medium" style={{ color: 'var(--fg)' }}>检查点</span>
            <button onClick={() => setShowCheckpoints(false)} aria-label="关闭检查点" className="p-1 rounded-lg" style={{ color: 'var(--fg-faint)' }}><X size={12} /></button>
          </div>
          <p className="px-4 py-2 text-xs" style={{ color: 'var(--fg-muted)' }}>检查点恢复会话上下文。工作区文件和外部操作不会随之回滚。</p>
          {checkpoints.length === 0 ? <p className="p-4 text-xs" style={{ color: 'var(--fg-muted)' }}>暂无检查点</p> : checkpoints.map(cp => (
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
      {}
      <InfoPanel />
    </div>
  )
}
