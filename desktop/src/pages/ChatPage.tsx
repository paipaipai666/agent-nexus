import React, { useState, useRef, useEffect, useCallback } from 'react'
import { useParams, useLocation, useNavigate } from 'react-router-dom'
import { Send, Square, Undo2, Redo2, History, FolderOpen, BookOpen, Bug, FlaskConical, Wrench, ArrowRight, GitBranch, Paperclip, X } from 'lucide-react'
import { BorderBeam } from 'border-beam'
import { api, type AttachmentRef, mimeForPath, humanSize } from '../services/api'
import { planModeArm } from '../services/planModeArm'
import { thinkingEffortArm } from '../services/thinkingEffortArm'
import ThinkEffortControl from '../components/chat/ThinkEffortControl'
import { useProjects, pickAndAddProject } from '../services/projects'
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
  handleSendMessage: (text: string, atts?: AttachmentRef[]) => void
  refresh: () => Promise<void>
  clearView: () => void
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
      cmd: '/help', desc: 'Show command help', category: 'system',
      run: (_args, ctx) => ctx.defs.map(c => `${c.cmd.padEnd(12)} ${c.desc}`).join('\n'),
    },
    { cmd: '/clear', desc: 'Clear screen', category: 'system', run: (_args, ctx) => { ctx.clearView() } },
    {
      cmd: '/undo', desc: 'Revert to previous checkpoint', category: 'system', needsSession: true, err: 'Undo failed',
      run: async (_args, ctx) => {
        const r = await api.versionUndo(ctx.sid!)
        await ctx.refresh()
        return `Undone to checkpoint: ${r.checkpoint?.id || 'ok'}`
      },
    },
    {
      cmd: '/redo', desc: 'Redo to next checkpoint', category: 'system', needsSession: true, err: 'Redo failed',
      run: async (_args, ctx) => {
        const r = await api.versionRedo(ctx.sid!)
        await ctx.refresh()
        return `Redone to checkpoint: ${r.checkpoint?.id || 'ok'}`
      },
    },
    {
      cmd: '/log', desc: 'View checkpoint log', category: 'system', needsSession: true, err: 'Log failed',
      run: async (_args, ctx) => {
        const { checkpoints: cps } = await api.getVersionLog(ctx.sid!, 10)
        return cps.length === 0 ? 'No checkpoints.' : cps.map(cp => `${cp.is_head ? '→ ' : '  '}${cp.id}  ${cp.question || ''}`).join('\n')
      },
    },
    {
      cmd: '/status', desc: 'View version status', category: 'system', needsSession: true, err: 'Status failed',
      run: async (_args, ctx) => {
        const s = await api.getVersionStatus(ctx.sid!)
        return `Session: ${s.session_id}\nHEAD: ${s.head?.id || 'none'}\nCan undo: ${s.can_undo}\nCan redo: ${s.can_redo}`
      },
    },
    {
      cmd: '/compact', desc: 'Compress conversation context', category: 'system', needsSession: true, err: 'Compact failed',
      run: async (args, ctx) => {
        ctx.addSys('Compressing context...')
        const r = await api.compactContext(ctx.sid!, args)
        return `Compacted: ${r.tokens_saved} tokens saved`
      },
    },
    {
      cmd: '/sessions', desc: 'List recent sessions', category: 'system', err: 'Sessions failed',
      run: async () => {
        const { sessions } = await api.getRecentSessions(10)
        return sessions.length === 0 ? 'No recent sessions.' : sessions.map(s => `${s.session_id.slice(0, 12)}  ${s.preview || ''}`).join('\n')
      },
    },
    {
      cmd: '/switch', desc: 'Switch session (usage: /switch <id>)', category: 'system',
      run: (args) => {
        if (!args) return 'Usage: /switch <session_id>'
        window.location.href = `/chat/${args}`
      },
    },
    {
      cmd: '/skill', desc: 'Manage skills (list/status/use/enable/disable)', category: 'skill',
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
      cmd: '/mcp', desc: 'Manage MCP servers (status/tools/resources)', category: 'mcp',
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
      cmd: '/plugin', desc: 'Manage plugins (list/status/enable/disable)', category: 'plugin', err: 'Plugins failed',
      run: async () => {
        const ext = await api.getExtensions()
        const names = Object.keys(ext)
        return names.length === 0 ? 'No plugins loaded.' : names.map(n => `● ${n}`).join('\n')
      },
    },
  ]
}

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

export default function ChatPage() {
  const { sessionId: routeSessionId } = useParams<{ sessionId?: string }>()
  const location = useLocation()
  const navigate = useNavigate()
  const { theme } = useTheme()
  const reducedMotion = usePrefersReducedMotion()
  const currentSessionIdRef = useRef<string | null>(null)
  const [input, setInput] = useState('')
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const [attachments, setAttachments] = useState<AttachmentRef[]>([])
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
  const [plugins, setPlugins] = useState<Record<string, any>>({})
  const [pendingWorkspace, setPendingWorkspace] = useState<string | null>(null)
  const [defaultWorkspace, setDefaultWorkspace] = useState<string | null>(null)
  const { selected: selectedProject } = useProjects()

  const {
    sessionId, cwd, runtimeStatus: sessionRuntimeStatus, setSessionId: setGlobalSessionId, setRuntimeInfo, setCwd, setToolCount, setTodoCount,
    messages, setMessages, isRunning, confirmRequest,
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
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
    })
  }, [])

  const loadAndDisplayMessages = useCallback(async (forceSessionId?: string) => {
    const currentSid = forceSessionId || currentSessionIdRef.current
    console.log('[loadAndDisplayMessages] sid:', currentSid)
    if (currentSid) {
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
        stm = hist.messages && hist.messages.length > 0 ? hist.messages : (await api.listShortMemories()).messages
      } catch {
        stm = (await api.listShortMemories()).messages
      }
      console.log('[loadAndDisplayMessages] Backend returned:', stm?.length, 'messages for sid:', currentSid)
      if (!stm || stm.length === 0) {
        if (currentSid) {
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
    } catch (err) { console.error('Failed to load messages:', err) }
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
    api.getExtensions().then(setPlugins).catch(() => {})
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
    didAutoRestoreLastSession = true
    const initRestore = (sid: string) => {
      console.log('[initRestore] Restoring session:', sid)
      currentSessionIdRef.current = sid
      setGlobalSessionId(sid)
      loadAndDisplayMessages(sid).catch((err) => console.error('[initRestore] loadAndDisplayMessages failed:', err))
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
      api.restoreSession(routeSessionId)
        .then(({ session_id, restored }) => {
          currentSessionIdRef.current = session_id
          if (restored === false || session_id !== routeSessionId) {
            navigate(`/chat/${session_id}`, { replace: true })
            initNew(session_id)
          } else {
            initRestore(session_id)
          }
        })
        .catch(() => api.createSession().then(async ({ session_id }) => {
          if (planModeArm.consume()) {
            try { await api.setPlanMode(session_id, true) } catch {  }
          }
          const armedEffort = thinkingEffortArm.consume()
          if (armedEffort !== null) {
            try { await api.setThinkingEffort(session_id, armedEffort) } catch {  }
          }
          currentSessionIdRef.current = session_id; initNew(session_id)
        }))
    } else {
      resetForSessionSwitch()
      currentSessionIdRef.current = null
      setGlobalSessionId(null)
      api.getConfig().then((config: any) => { if (config.cwd) setDefaultWorkspace(config.cwd) }).catch(() => {})
      fetchDynamicCommands()
      if (isFirstMount) {
        api.getRecentSessions(5).then((d) => {
          const last = d.sessions?.find((s) => s.preview)
          if (last && !currentSessionIdRef.current) {
            navigate(`/chat/${last.session_id}`, { replace: true })
          }
        }).catch(() => {})
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeSessionId])

  useEffect(scrollToBottom, [messages, scrollToBottom])

  useEffect(() => {
    const handleNewChat = () => {
      currentSessionIdRef.current = null
      pendingFirstMessageRef.current = null
      setGlobalSessionId(null)
      setPendingWorkspace(null)
    }
    window.addEventListener('new-chat', handleNewChat)
    return () => window.removeEventListener('new-chat', handleNewChat)
  }, [])

  const pendingFirstMessageRef = useRef<{ text: string; attachments?: AttachmentRef[] } | null>(null)

  useEffect(() => {
    const pending = pendingFirstMessageRef.current
    if (!pending) return
    pendingFirstMessageRef.current = null
    sendMessage(pending.text, pending.attachments)
  }, [sendMessage])

  const handleSendMessage = useCallback((text: string, atts?: AttachmentRef[]) => {
    if (!currentSessionIdRef.current) {
      pendingFirstMessageRef.current = { text, attachments: atts }
      api.createSession(undefined, pendingWorkspace ?? selectedProject ?? null).then(async ({ session_id }) => {
        if (planModeArm.consume()) {
          try { await api.setPlanMode(session_id, true) } catch {  }
        }
        const armedEffort = thinkingEffortArm.consume()
        if (armedEffort !== null) {
          try { await api.setThinkingEffort(session_id, armedEffort) } catch {  }
        }
        currentSessionIdRef.current = session_id
        initNew(session_id)
        setPendingWorkspace(null)
        navigate(`/chat/${session_id}`, { replace: true })
      }).catch((err) => {
        pendingFirstMessageRef.current = null
        setInput(text)
        setAttachments(atts ?? [])
        window.alert(`创建会话失败：${err instanceof Error ? err.message : err}`)
      })
    } else {
      sendMessage(text, atts)
      if (location.pathname === '/') {
        navigate(`/chat/${currentSessionIdRef.current}`, { replace: true })
      }
    }
  }, [sendMessage, location.pathname, navigate, initNew, pendingWorkspace, selectedProject])

  const chipWorkspace = cwd ?? pendingWorkspace ?? (sessionId ? null : selectedProject) ?? defaultWorkspace
  const handleWorkspacePick = async () => {
    if (sessionId) return
    const picked = await pickAndAddProject(chipWorkspace)
    if (picked) setPendingWorkspace(picked)
  }

  const addSys = useCallback((c: string) => {
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
  }, [attachments, addSys])

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

  const handleSend = async () => {
    const text = input.trim(); if (!text && attachments.length === 0) return
    if (attachments.length > 0 && window.electronAPI) {
      try {
        const stats = await window.electronAPI.statFiles(attachments.map(a => a.path))
        const dead = new Set(stats.filter(s => !s.ok).map(s => s.path))
        if (dead.size > 0) {
          setDeadPaths(dead)
          const names = attachments.filter(a => dead.has(a.path)).map(a => a.name).join(', ')
          addSys(`附件已失效: ${names}。已阻止发送，请移除失效附件后重试。`)
          return
        }
        setDeadPaths(new Set())
      } catch {  }
    }
    const atts = attachments.length > 0 ? attachments : undefined
    setInput(''); setShowPalette(false); setPaletteDismissed(false)
    setAttachments([]); setDeadPaths(new Set())
    const out = text || '（见附件）'
    if (out.startsWith('/')) { handleSlashCommand(out); return }
    if (isRunning) { queueMessage(out, atts) }
    else handleSendMessage(out, atts)
  }

  const handleSlashCommand = async (text: string) => {
    const parts = text.trim().split(/\s+/); const cmd = parts[0].toLowerCase(); const args = parts.slice(1).join(' ')
    const addSys = (c: string) => setMessages(prev => [...prev, { id: `cmd-${incrementMsgCounter()}`, role: 'system', content: c, timestamp: new Date() }])
    const sid = currentSessionIdRef.current
    const ctx: SlashCtx = {
      sid,
      defs: [],
      addSys,
      skills, setSkills, handleSendMessage,
      refresh: async () => {
        setVersionStatus(await api.getVersionStatus(sid!))
        setCheckpoints((await api.getVersionLog(sid!, 5)).checkpoints || [])
        await loadAndDisplayMessages()
      },
      clearView: () => { setMessages([]); animatedIds.clear() },
    }
    ctx.defs = buildCommandDefs()
    const def = ctx.defs.find(d => d.cmd === cmd)
    if (def) {
      if (def.needsSession && !sid) { addSys('No active session.'); return }
      try {
        const out = await def.run(args, ctx)
        if (typeof out === 'string') addSys(out)
      } catch (e) { addSys(`${def.err ?? `${cmd} failed`}: ${errMsg(e)}`) }
      return
    }
    const skillMatch = skills.find(s => {
      if (!s.enabled) return false
      const shortId = s.id.includes('/') ? s.id.split('/').pop()! : s.id
      return `/${shortId}` === cmd
    })
    if (skillMatch) { handleSendMessage(text); return }
    addSys(`Unknown command: ${cmd}. Type /help for commands.`)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
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
    if (!sid) return
    try {
      await api.versionUndo(sid)
      setVersionStatus(await api.getVersionStatus(sid))
      setCheckpoints((await api.getVersionLog(sid, 5)).checkpoints || [])
      await loadAndDisplayMessages()
    } catch { }
  }
  const handleRedo = async () => {
    const sid = currentSessionIdRef.current
    if (!sid) return
    try {
      await api.versionRedo(sid)
      setVersionStatus(await api.getVersionStatus(sid))
      setCheckpoints((await api.getVersionLog(sid, 5)).checkpoints || [])
      await loadAndDisplayMessages()
    } catch { }
  }

  const allCommands = [
    ...buildCommandDefs(),
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
      {}
      <div className="flex-1 flex flex-col overflow-hidden">
      {}
      <div className="flex-1 overflow-y-auto">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full gap-6 animate-empty-in px-6">
            <div className="text-center">
              <h1
                className="text-[27px] font-semibold mb-3"
                style={{ letterSpacing: '-0.025em' }}
              >
                <span className="metal-heading">What are we building?</span>
              </h1>
              <p className="text-[15px] max-w-md mx-auto" style={{ color: 'var(--fg-muted)' }}>
                Code. Debug. Create. Ship.
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

      {}
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
                <span className="text-[14px] font-semibold" style={{ color: 'var(--fg)' }}>Confirmation required</span>
              </div>
              <pre className="text-[12.5px] rounded-xl p-3 mb-4 overflow-auto max-h-32 font-mono" style={{ background: 'var(--surface-2)', color: 'var(--fg-secondary)' }}>{confirmRequest.summary}</pre>
              <div className="flex gap-2.5">
                <button onClick={() => handleConfirm(true)} className="btn-primary text-sm">Approve</button>
                <button onClick={() => handleConfirm(false)} className="btn-ghost text-sm">Deny</button>
              </div>
            </div>
          </BorderBeam>
        </div>
        )
      )}

      {}
      <div className="px-6 pb-6 relative">
        {}
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
              >
                <Paperclip size={14} />
              </button>
            )}
            <textarea
              ref={inputRef}
              value={input}
              onChange={handleInputChange}
              onKeyDown={handleKeyDown}
              aria-busy={isRunning}
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
                disabled={!input.trim() && attachments.length === 0}
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
                onClick={() => {
                  setShowCheckpoints(!showCheckpoints)
                  const sid = currentSessionIdRef.current
                  if (sid) api.getVersionLog(sid, 10).then(d => setCheckpoints(d.checkpoints || []))
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
      {}
      <InfoPanel />
    </div>
  )
}
