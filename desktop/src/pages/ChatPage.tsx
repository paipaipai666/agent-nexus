import { useState, useRef, useEffect, useCallback } from 'react'
import { useParams } from 'react-router-dom'
import {
  Send, Square, Loader2, Undo2, Redo2, History,
  Wrench, CheckSquare, Server, Cpu,
  PanelRightOpen, PanelRightClose
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../services/api'
import { agentWs } from '../services/ws'

interface Message {
  id: string
  role: 'user' | 'assistant' | 'system' | 'tool'
  content: string
  toolName?: string
  toolStatus?: 'running' | 'done' | 'error'
  timestamp: Date
}

interface Checkpoint {
  id: string
  question: string
  answer: string
  is_head: boolean
}

// Command definitions matching TUI
const COMMAND_DEFS = [
  { cmd: '/help', desc: 'Show command help' },
  { cmd: '/clear', desc: 'Clear screen' },
  { cmd: '/undo', desc: 'Revert to previous checkpoint' },
  { cmd: '/redo', desc: 'Redo to next checkpoint' },
  { cmd: '/log', desc: 'View checkpoint log' },
  { cmd: '/status', desc: 'View version status' },
  { cmd: '/compact', desc: 'Compress conversation context' },
  { cmd: '/sessions', desc: 'List recent sessions' },
  { cmd: '/switch', desc: 'Switch session (usage: /switch <id>)' },
]

export default function ChatPage() {
  const { sessionId: routeSessionId } = useParams<{ sessionId?: string }>()
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [isRunning, setIsRunning] = useState(false)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [currentRunId, setCurrentRunId] = useState<string | null>(null)
  const [confirmRequest, setConfirmRequest] = useState<{summary: string} | null>(null)
  const currentAssistantIdRef = useRef<string | null>(null)
  const currentReasoningIdRef = useRef<string | null>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const msgCounterRef = useRef(0)
  const messageQueueRef = useRef<string[]>([])

  // HUD state
  const [versionStatus, setVersionStatus] = useState<any>(null)
  const [runtimeStatus, setRuntimeStatus] = useState<any>(null)
  const [showSidePanel, setShowSidePanel] = useState(true)
  const [sidePanelTab, setSidePanelTab] = useState<'tools' | 'todo' | 'mcp' | 'version'>('tools')
  const [registeredTools] = useState<any[]>([])
  const [todos, setTodos] = useState<any[]>([])
  const [mcpStatus, setMcpStatus] = useState<any>(null)
  const [checkpoints, setCheckpoints] = useState<Checkpoint[]>([])
  const [showCheckpoints, setShowCheckpoints] = useState(false)

  // Command palette state
  const [showPalette, setShowPalette] = useState(false)
  const [paletteFilter, setPaletteFilter] = useState('')

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [])

  // Initialize session
  useEffect(() => {
    if (routeSessionId) {
      api.restoreSession(routeSessionId).then(({ session_id }) => {
        setSessionId(session_id)
        agentWs.connect(session_id)
        api.listShortMemories().then(({ messages }) => {
          if (messages && messages.length > 0) {
            const historyMessages: Message[] = messages
              .filter(m => m.role === 'user' || m.role === 'assistant' || m.role === 'system')
              .map((m, idx) => ({
                id: `history-${idx}`,
                role: m.role as 'user' | 'assistant' | 'system',
                content: m.content,
                timestamp: new Date(),
              }))
            setMessages(historyMessages)
          }
        }).catch(console.error)
      }).catch(() => {
        api.createSession().then(({ session_id }) => {
          setSessionId(session_id)
          agentWs.connect(session_id)
        })
      })
    } else {
      api.createSession().then(({ session_id }) => {
        setSessionId(session_id)
        agentWs.connect(session_id)
      })
    }
    return () => agentWs.disconnect()
  }, [routeSessionId])

  // Load HUD data
  useEffect(() => {
    if (!sessionId) return
    api.getVersionStatus().then(setVersionStatus).catch(() => {})
    api.getVersionLog(5).then(d => setCheckpoints(d.checkpoints || [])).catch(() => {})
    api.getMcpStatus().then(setMcpStatus).catch(() => {})
    api.getRuntimeStatus().then(setRuntimeStatus).catch(() => {})
  }, [sessionId])

  // Listen for WS events
  useEffect(() => {
    const unsubs = [
      agentWs.on('thinking', (data) => {
        currentAssistantIdRef.current = null
        currentReasoningIdRef.current = null
        const id = data.seq != null ? `thinking-${data.seq}` : `thinking-${++msgCounterRef.current}`
        setMessages(prev => [...prev, { id, role: 'system', content: data.content || 'Thinking...', timestamp: new Date() }])
      }),
      agentWs.on('tool_call', (data) => {
        currentAssistantIdRef.current = null
        const id = data.seq != null ? `tool-${data.seq}` : `tool-${++msgCounterRef.current}`
        setMessages(prev => [...prev, {
          id, role: 'tool', content: `Calling: ${data.tool_name}`,
          toolName: data.tool_name, toolStatus: 'running', timestamp: new Date(),
        }])
      }),
      agentWs.on('tool_result', (data) => {
        setMessages(prev => prev.map(m =>
          m.toolName === data.tool_name && m.toolStatus === 'running'
            ? { ...m, toolStatus: 'done' as const, content: `${data.tool_name}: ${data.result || 'done'}` }
            : m
        ))
        // Refresh todos after tool calls
        if (sessionId) {
          api.getTodos(sessionId).then(d => setTodos(d.items || [])).catch(() => {})
        }
      }),
      agentWs.on('token', (data) => {
        currentReasoningIdRef.current = null
        const targetId = currentAssistantIdRef.current
        if (targetId) {
          setMessages(prev => prev.map(m => m.id === targetId ? { ...m, content: m.content + data.content } : m))
        } else {
          const newId = data.seq != null ? `assistant-${data.seq}` : `assistant-${++msgCounterRef.current}`
          currentAssistantIdRef.current = newId
          setMessages(prev => [...prev, { id: newId, role: 'assistant', content: data.content, timestamp: new Date() }])
        }
      }),
      agentWs.on('reasoning', (data) => {
        const targetId = currentReasoningIdRef.current
        if (targetId) {
          setMessages(prev => prev.map(m => m.id === targetId ? { ...m, content: m.content + data.content } : m))
        } else {
          const newId = data.seq != null ? `reasoning-${data.seq}` : `reasoning-${++msgCounterRef.current}`
          currentReasoningIdRef.current = newId
          setMessages(prev => [...prev, { id: newId, role: 'system', content: data.content, timestamp: new Date() }])
        }
      }),
      agentWs.on('answer', (data) => {
        const targetId = currentAssistantIdRef.current
        if (targetId) {
          setMessages(prev => prev.map(m => m.id === targetId ? { ...m, content: data.content } : m))
        } else {
          setMessages(prev => {
            const lastAssistantIdx = [...prev].reverse().findIndex(m => m.role === 'assistant')
            if (lastAssistantIdx !== -1) {
              const idx = prev.length - 1 - lastAssistantIdx
              return prev.map((m, i) => i === idx ? { ...m, content: data.content } : m)
            }
            const newId = data.seq != null ? `assistant-${data.seq}` : `assistant-${++msgCounterRef.current}`
            return [...prev, { id: newId, role: 'assistant' as const, content: data.content, timestamp: new Date() }]
          })
        }
        currentAssistantIdRef.current = null
        setIsRunning(false)
        setCurrentRunId(null)
        // Refresh version status and runtime after answer
        api.getVersionStatus().then(setVersionStatus).catch(() => {})
        api.getVersionLog(5).then(d => setCheckpoints(d.checkpoints || [])).catch(() => {})
        api.getRuntimeStatus().then(setRuntimeStatus).catch(() => {})
        // Process queued messages
        processQueue()
      }),
      agentWs.on('error', (data) => {
        const id = `error-${++msgCounterRef.current}`
        setMessages(prev => [...prev, { id, role: 'system', content: `Error: ${data.message}`, timestamp: new Date() }])
        setIsRunning(false)
        setCurrentRunId(null)
        processQueue()
      }),
      agentWs.on('done', () => {
        setIsRunning(false)
        setCurrentRunId(null)
        processQueue()
      }),
      agentWs.on('confirm_request', (data) => {
        setConfirmRequest({summary: data.summary})
      }),
    ]
    return () => unsubs.forEach(u => u())
  }, [sessionId])

  useEffect(scrollToBottom, [messages, scrollToBottom])

  const processQueue = () => {
    if (messageQueueRef.current.length > 0) {
      const next = messageQueueRef.current.shift()!
      setTimeout(() => sendMessage(next), 100)
    }
  }

  const sendMessage = (text: string) => {
    currentAssistantIdRef.current = null
    currentReasoningIdRef.current = null
    setMessages(prev => [...prev, { id: `user-${++msgCounterRef.current}`, role: 'user', content: text, timestamp: new Date() }])
    setIsRunning(true)
    agentWs.sendMessage(text)
  }

  const handleSend = () => {
    const text = input.trim()
    if (!text || !sessionId) return

    // Handle slash commands locally
    if (text.startsWith('/')) {
      handleSlashCommand(text)
      setInput('')
      return
    }

    setInput('')
    if (isRunning) {
      // Queue the message instead of dropping it
      messageQueueRef.current.push(text)
      setMessages(prev => [...prev, {
        id: `queued-${++msgCounterRef.current}`,
        role: 'system',
        content: `[Queued] ${text}`,
        timestamp: new Date(),
      }])
    } else {
      sendMessage(text)
    }
  }

  const handleSlashCommand = async (text: string) => {
    const parts = text.trim().split(/\s+/)
    const cmd = parts[0].toLowerCase()
    const args = parts.slice(1).join(' ')

    const addSystem = (content: string) => {
      setMessages(prev => [...prev, { id: `cmd-${++msgCounterRef.current}`, role: 'system', content, timestamp: new Date() }])
    }

    switch (cmd) {
      case '/help':
        addSystem(COMMAND_DEFS.map(c => `${c.cmd.padEnd(12)} ${c.desc}`).join('\n'))
        break
      case '/clear':
        setMessages([])
        messageQueueRef.current = []
        break
      case '/undo': {
        try {
          const res = await api.versionUndo()
          addSystem(`Undone to checkpoint: ${res.checkpoint?.id || 'ok'}`)
          setVersionStatus(await api.getVersionStatus())
          setCheckpoints((await api.getVersionLog(5)).checkpoints || [])
          // Reload STM
          const { messages: stm } = await api.listShortMemories()
          if (stm) {
            const historyMsgs = stm
              .filter(m => m.role === 'user' || m.role === 'assistant' || m.role === 'system')
              .map((m, idx) => ({ id: `history-${idx}`, role: m.role as any, content: m.content, timestamp: new Date() }))
            setMessages(historyMsgs)
          }
        } catch (e: any) { addSystem(`Undo failed: ${e.message}`) }
        break
      }
      case '/redo': {
        try {
          const res = await api.versionRedo()
          addSystem(`Redone to checkpoint: ${res.checkpoint?.id || 'ok'}`)
          setVersionStatus(await api.getVersionStatus())
          setCheckpoints((await api.getVersionLog(5)).checkpoints || [])
          const { messages: stm } = await api.listShortMemories()
          if (stm) {
            const historyMsgs = stm
              .filter(m => m.role === 'user' || m.role === 'assistant' || m.role === 'system')
              .map((m, idx) => ({ id: `history-${idx}`, role: m.role as any, content: m.content, timestamp: new Date() }))
            setMessages(historyMsgs)
          }
        } catch (e: any) { addSystem(`Redo failed: ${e.message}`) }
        break
      }
      case '/log': {
        try {
          const { checkpoints: cps } = await api.getVersionLog(10)
          if (cps.length === 0) { addSystem('No checkpoints.'); break }
          addSystem(cps.map(cp => `${cp.is_head ? '→ ' : '  '}${cp.id}  ${cp.question || '(no question)'}  ${cp.answer?.slice(0, 60) || ''}`).join('\n'))
        } catch (e: any) { addSystem(`Log failed: ${e.message}`) }
        break
      }
      case '/status': {
        try {
          const s = await api.getVersionStatus()
          addSystem(`Session: ${s.session_id}\nHEAD: ${s.head?.id || 'none'}\nCan undo: ${s.can_undo}\nCan redo: ${s.can_redo}`)
        } catch (e: any) { addSystem(`Status failed: ${e.message}`) }
        break
      }
      case '/compact': {
        addSystem('Compressing context...')
        try {
          const res = await api.compactContext(args)
          addSystem(`Compacted: ${res.tokens_saved} tokens saved`)
        } catch (e: any) { addSystem(`Compact failed: ${e.message}`) }
        break
      }
      case '/sessions': {
        try {
          const { sessions } = await api.getRecentSessions(10)
          if (sessions.length === 0) { addSystem('No recent sessions.'); break }
          addSystem(sessions.map(s => `${s.session_id.slice(0, 12)}  ${s.preview || '(no preview)'}  ${s.updated_at}`).join('\n'))
        } catch (e: any) { addSystem(`Sessions failed: ${e.message}`) }
        break
      }
      case '/switch': {
        if (!args) { addSystem('Usage: /switch <session_id>'); break }
        try {
          await api.restoreSession(args)
          window.location.href = `/chat/${args}`
        } catch (e: any) { addSystem(`Switch failed: ${e.message}`) }
        break
      }
      default:
        addSystem(`Unknown command: ${cmd}. Type /help for available commands.`)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value
    setInput(val)
    // Show command palette when typing /
    if (val.startsWith('/') && val.length > 0) {
      setPaletteFilter(val)
      setShowPalette(true)
    } else {
      setShowPalette(false)
    }
  }

  const handlePaletteSelect = (cmd: string) => {
    setInput(cmd + ' ')
    setShowPalette(false)
    inputRef.current?.focus()
  }

  const handleCancel = () => {
    if (currentRunId) agentWs.cancel(currentRunId)
  }

  const handleConfirm = (approved: boolean) => {
    agentWs.confirm('', approved)
    setConfirmRequest(null)
  }

  const handleUndo = async () => {
    try {
      await api.versionUndo()
      setVersionStatus(await api.getVersionStatus())
      setCheckpoints((await api.getVersionLog(5)).checkpoints || [])
    } catch {}
  }

  const handleRedo = async () => {
    try {
      await api.versionRedo()
      setVersionStatus(await api.getVersionStatus())
      setCheckpoints((await api.getVersionLog(5)).checkpoints || [])
    } catch {}
  }

  const filteredCommands = COMMAND_DEFS.filter(c => c.cmd.startsWith(paletteFilter))

  return (
    <div className="flex-1 flex overflow-hidden">
      {/* Main Chat Area */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          {messages.length === 0 && (
            <div className="flex items-center justify-center h-full text-text-muted">
              <div className="text-center">
                <p className="text-lg mb-2">Start a conversation</p>
                <p className="text-xs">Type /help for available commands</p>
              </div>
            </div>
          )}
          {messages.map((msg) => (
            <div key={msg.id} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div className={`max-w-[80%] rounded-lg px-3 py-2 text-sm ${
                msg.role === 'user'
                  ? 'bg-accent-primary/20 text-text-primary'
                  : msg.role === 'tool'
                  ? 'bg-accent-purple/10 text-accent-purple text-xs font-mono'
                  : msg.role === 'system'
                  ? 'bg-bg-tertiary text-text-muted text-xs italic'
                  : 'bg-bg-secondary text-text-primary'
              }`}>
                {msg.toolStatus === 'running' && <Loader2 size={12} className="inline mr-1 animate-spin" />}
                {msg.role === 'assistant' ? (
                  <div className="prose prose-sm prose-invert max-w-none">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
                  </div>
                ) : (
                  <pre className="whitespace-pre-wrap font-sans">{msg.content}</pre>
                )}
              </div>
            </div>
          ))}
          <div ref={messagesEndRef} />
        </div>

        {/* Confirm Dialog */}
        {confirmRequest && (
          <div className="border-t border-status-warning p-4 bg-status-warning/10">
            <div className="text-sm text-status-warning font-medium mb-2">Confirmation Required</div>
            <pre className="text-xs text-text-secondary bg-bg-tertiary p-2 rounded mb-3 overflow-auto max-h-32">{confirmRequest.summary}</pre>
            <div className="flex gap-2">
              <button onClick={() => handleConfirm(true)} className="px-3 py-1.5 text-sm bg-status-warning text-bg-primary rounded hover:opacity-90">Approve</button>
              <button onClick={() => handleConfirm(false)} className="px-3 py-1.5 text-sm bg-bg-tertiary text-text-secondary rounded hover:bg-bg-secondary">Deny</button>
            </div>
          </div>
        )}

        {/* Command Palette */}
        {showPalette && filteredCommands.length > 0 && (
          <div className="border-t border-border-default bg-bg-secondary max-h-48 overflow-y-auto">
            {filteredCommands.map(c => (
              <button
                key={c.cmd}
                onClick={() => handlePaletteSelect(c.cmd)}
                className="w-full text-left px-4 py-2 text-sm hover:bg-bg-tertiary flex items-center gap-3"
              >
                <span className="font-mono text-accent-primary">{c.cmd}</span>
                <span className="text-text-muted text-xs">{c.desc}</span>
              </button>
            ))}
          </div>
        )}

        {/* Input */}
        <div className="border-t border-border-default p-3 bg-bg-secondary">
          <div className="flex items-end gap-2">
            <textarea
              ref={inputRef}
              value={input}
              onChange={handleInputChange}
              onKeyDown={handleKeyDown}
              placeholder={isRunning ? "Agent running... messages will be queued" : "Type a message... (/ for commands)"}
              className="flex-1 bg-bg-tertiary text-text-primary rounded-lg px-3 py-2 text-sm resize-none border border-border-default focus:border-accent-primary focus:outline-none max-h-32"
              rows={1}
            />
            {isRunning ? (
              <button onClick={handleCancel} className="w-9 h-9 flex items-center justify-center rounded-lg bg-status-error/20 text-status-error hover:bg-status-error/30 transition-colors" title="Cancel">
                <Square size={16} />
              </button>
            ) : (
              <button onClick={handleSend} disabled={!input.trim()} className="w-9 h-9 flex items-center justify-center rounded-lg bg-accent-primary text-bg-primary hover:opacity-90 transition-opacity disabled:opacity-30" title="Send">
                <Send size={16} />
              </button>
            )}
          </div>

          {/* HUD Status Bar */}
          <div className="mt-2 flex items-center gap-3 text-xs text-text-muted overflow-x-auto">
            {/* Model */}
            {runtimeStatus && (
              <span className="flex items-center gap-1 shrink-0 font-mono">
                <Cpu size={11} />
                {runtimeStatus.model_id?.split('/').pop() || runtimeStatus.model_id}
              </span>
            )}
            {/* Context */}
            {runtimeStatus && (
              <span className="flex items-center gap-1 shrink-0" title={`STM: ${runtimeStatus.stm_tokens} / ${runtimeStatus.ctx_max}`}>
                ctx {Math.round(runtimeStatus.stm_tokens / 1000)}k/{Math.round(runtimeStatus.ctx_max / 1000)}k
                <span className="text-text-muted">
                  ({runtimeStatus.ctx_max > 0 ? Math.round(runtimeStatus.stm_tokens / runtimeStatus.ctx_max * 100) : 0}%)
                </span>
              </span>
            )}
            {/* Tokens */}
            {runtimeStatus?.total_usage && (
              <span className="shrink-0" title="Token usage: in/out">
                in:{(runtimeStatus.total_usage.input_tokens || 0).toLocaleString()} out:{(runtimeStatus.total_usage.output_tokens || 0).toLocaleString()}
              </span>
            )}
            {/* Version */}
            {versionStatus?.head && (
              <span className="flex items-center gap-1 shrink-0">
                <span className="w-1.5 h-1.5 rounded-full bg-accent-primary" />
                {versionStatus.head.id?.slice(0, 8)}
              </span>
            )}
            <div className="flex items-center gap-1 shrink-0">
              <button onClick={handleUndo} disabled={!versionStatus?.can_undo} className="p-0.5 hover:text-text-primary disabled:opacity-30 transition-colors" title="Undo">
                <Undo2 size={12} />
              </button>
              <button onClick={handleRedo} disabled={!versionStatus?.can_redo} className="p-0.5 hover:text-text-primary disabled:opacity-30 transition-colors" title="Redo">
                <Redo2 size={12} />
              </button>
              <button onClick={() => { setShowCheckpoints(!showCheckpoints); api.getVersionLog(10).then(d => setCheckpoints(d.checkpoints || [])) }} className="p-0.5 hover:text-text-primary transition-colors" title="Checkpoint log">
                <History size={12} />
              </button>
            </div>
            {messageQueueRef.current.length > 0 && (
              <span className="shrink-0 text-status-warning">Queue: {messageQueueRef.current.length}</span>
            )}
            <span className="ml-auto shrink-0">{sessionId?.slice(0, 12)}</span>
            <button onClick={() => setShowSidePanel(!showSidePanel)} className="p-0.5 hover:text-text-primary transition-colors shrink-0" title="Toggle side panel">
              {showSidePanel ? <PanelRightClose size={12} /> : <PanelRightOpen size={12} />}
            </button>
          </div>
        </div>

        {/* Checkpoint Log Overlay */}
        {showCheckpoints && (
          <div className="absolute bottom-24 left-4 right-4 max-w-lg bg-bg-secondary border border-border-default rounded-lg shadow-xl z-50 max-h-64 overflow-y-auto">
            <div className="p-3 border-b border-border-default flex items-center justify-between">
              <span className="text-sm font-medium text-text-primary">Checkpoints</span>
              <button onClick={() => setShowCheckpoints(false)} className="text-text-muted hover:text-text-primary"><Square size={12} /></button>
            </div>
            {checkpoints.length === 0 ? (
              <p className="p-3 text-xs text-text-muted">No checkpoints</p>
            ) : (
              checkpoints.map(cp => (
                <div key={cp.id} className={`px-3 py-2 text-xs border-b border-border-default last:border-b-0 ${cp.is_head ? 'bg-accent-primary/5' : ''}`}>
                  <div className="flex items-center gap-2">
                    {cp.is_head && <span className="text-accent-primary">→</span>}
                    <span className="font-mono text-accent-secondary">{cp.id}</span>
                    <span className="text-text-muted truncate">{cp.question || '(no question)'}</span>
                  </div>
                </div>
              ))
            )}
          </div>
        )}
      </div>

      {/* Side Panel */}
      {showSidePanel && (
        <div className="w-72 border-l border-border-default bg-bg-secondary flex flex-col overflow-hidden shrink-0">
          {/* Tabs */}
          <div className="flex border-b border-border-default">
            {(['tools', 'todo', 'mcp', 'version'] as const).map(tab => (
              <button
                key={tab}
                onClick={() => setSidePanelTab(tab)}
                className={`flex-1 py-2 text-xs font-medium transition-colors ${
                  sidePanelTab === tab ? 'text-accent-primary border-b-2 border-accent-primary' : 'text-text-muted hover:text-text-primary'
                }`}
              >
                {tab === 'tools' && <Wrench size={12} className="inline mr-1" />}
                {tab === 'todo' && <CheckSquare size={12} className="inline mr-1" />}
                {tab === 'mcp' && <Server size={12} className="inline mr-1" />}
                {tab === 'version' && <History size={12} className="inline mr-1" />}
                {tab.charAt(0).toUpperCase() + tab.slice(1)}
              </button>
            ))}
          </div>

          {/* Tab Content */}
          <div className="flex-1 overflow-y-auto p-3 space-y-2">
            {sidePanelTab === 'tools' && (
              <>
                {registeredTools.length === 0 ? (
                  <p className="text-xs text-text-muted">Tools are registered when the agent starts.</p>
                ) : (
                  registeredTools.map(tool => (
                    <div key={tool.name} className="flex items-center gap-2 text-xs py-1">
                      <span className={`w-1.5 h-1.5 rounded-full ${
                        tool.risk === 'high' ? 'bg-status-error' : tool.risk === 'medium' ? 'bg-status-warning' : 'bg-status-success'
                      }`} />
                      <span className="text-text-primary font-mono">{tool.name}</span>
                    </div>
                  ))
                )}
              </>
            )}

            {sidePanelTab === 'todo' && (
              <>
                {todos.length === 0 ? (
                  <p className="text-xs text-text-muted">No todo items.</p>
                ) : (
                  todos.map(todo => (
                    <div key={todo.id} className="flex items-start gap-2 text-xs py-1">
                      <span className="mt-0.5">
                        {todo.status === 'done' ? '✓' : todo.status === 'in_progress' ? '→' : '·'}
                      </span>
                      <span className={todo.status === 'done' ? 'text-text-muted line-through' : 'text-text-primary'}>
                        {todo.description}
                      </span>
                    </div>
                  ))
                )}
              </>
            )}

            {sidePanelTab === 'mcp' && (
              <>
                {!mcpStatus ? (
                  <p className="text-xs text-text-muted">Loading MCP status...</p>
                ) : (
                  <>
                    <div className="flex items-center gap-2 text-xs mb-2">
                      <span className={`w-2 h-2 rounded-full ${mcpStatus.started ? 'bg-status-success' : 'bg-text-muted'}`} />
                      <span className="text-text-primary">{mcpStatus.started ? 'Running' : 'Not started'}</span>
                    </div>
                    {(mcpStatus.servers || []).map((s: any) => (
                      <div key={s.name} className="flex items-center justify-between text-xs py-1.5 border-b border-border-default last:border-b-0">
                        <div className="flex items-center gap-2 min-w-0">
                          <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${s.connected ? 'bg-status-success' : 'bg-status-error'}`} />
                          <span className="text-text-primary truncate">{s.name}</span>
                        </div>
                        <span className="text-text-muted shrink-0 ml-2">{s.tool_names?.length || 0} tools</span>
                      </div>
                    ))}
                  </>
                )}
              </>
            )}

            {sidePanelTab === 'version' && (
              <>
                {versionStatus && (
                  <div className="space-y-2">
                    <div className="text-xs">
                      <span className="text-text-muted">HEAD: </span>
                      <span className="font-mono text-accent-secondary">{versionStatus.head?.id || 'none'}</span>
                    </div>
                    <div className="flex gap-2">
                      <button onClick={handleUndo} disabled={!versionStatus.can_undo} className="px-2 py-1 text-xs bg-bg-tertiary text-text-secondary rounded hover:bg-bg-secondary disabled:opacity-30 flex items-center gap-1">
                        <Undo2 size={11} /> Undo
                      </button>
                      <button onClick={handleRedo} disabled={!versionStatus.can_redo} className="px-2 py-1 text-xs bg-bg-tertiary text-text-secondary rounded hover:bg-bg-secondary disabled:opacity-30 flex items-center gap-1">
                        <Redo2 size={11} /> Redo
                      </button>
                    </div>
                    <div className="border-t border-border-default pt-2 mt-2">
                      <p className="text-xs text-text-muted mb-1">Recent checkpoints:</p>
                      {checkpoints.map(cp => (
                        <div key={cp.id} className={`text-xs py-1 ${cp.is_head ? 'text-accent-primary' : 'text-text-muted'}`}>
                          {cp.is_head ? '→ ' : '  '}{cp.id} — {cp.question?.slice(0, 30) || '?'}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
