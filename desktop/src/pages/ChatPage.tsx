import { useState, useRef, useEffect, useCallback } from 'react'
import { useParams } from 'react-router-dom'
import {
  Send, Square, Loader2, Undo2, Redo2, History,
  Wrench, CheckSquare, Server, Cpu,
  PanelRightOpen, PanelRightClose, Sparkles
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

interface Checkpoint { id: string; question: string; answer: string; is_head: boolean }

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

  // Command palette
  const [showPalette, setShowPalette] = useState(false)
  const [paletteFilter, setPaletteFilter] = useState('')

  const scrollToBottom = useCallback(() => { messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [])

  // Init session
  useEffect(() => {
    if (routeSessionId) {
      api.restoreSession(routeSessionId).then(({ session_id }) => {
        setSessionId(session_id); agentWs.connect(session_id)
        api.listShortMemories().then(({ messages }) => {
          if (messages?.length > 0) setMessages(messages.filter(m => ['user','assistant','system'].includes(m.role)).map((m, i) => ({ id: `h-${i}`, role: m.role as any, content: m.content, timestamp: new Date() })))
        }).catch(console.error)
      }).catch(() => { api.createSession().then(({ session_id }) => { setSessionId(session_id); agentWs.connect(session_id) }) })
    } else {
      api.createSession().then(({ session_id }) => { setSessionId(session_id); agentWs.connect(session_id) })
    }
    return () => agentWs.disconnect()
  }, [routeSessionId])

  // Load HUD
  useEffect(() => {
    if (!sessionId) return
    api.getVersionStatus().then(setVersionStatus).catch(() => {})
    api.getVersionLog(5).then(d => setCheckpoints(d.checkpoints || [])).catch(() => {})
    api.getMcpStatus().then(setMcpStatus).catch(() => {})
    api.getRuntimeStatus().then(setRuntimeStatus).catch(() => {})
  }, [sessionId])

  // WS events
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
        setMessages(prev => prev.map(m => m.toolName === data.tool_name && m.toolStatus === 'running' ? { ...m, toolStatus: 'done' as const, content: `${data.tool_name}: ${data.result || 'done'}` } : m))
        if (sessionId) api.getTodos(sessionId).then(d => setTodos(d.items || [])).catch(() => {})
      }),
      agentWs.on('token', (data) => {
        currentReasoningIdRef.current = null
        const tid = currentAssistantIdRef.current
        if (tid) { setMessages(prev => prev.map(m => m.id === tid ? { ...m, content: m.content + data.content } : m)) }
        else { const nid = `a-${++msgCounterRef.current}`; currentAssistantIdRef.current = nid; setMessages(prev => [...prev, { id: nid, role: 'assistant', content: data.content, timestamp: new Date() }]) }
      }),
      agentWs.on('reasoning', (data) => {
        const tid = currentReasoningIdRef.current
        if (tid) { setMessages(prev => prev.map(m => m.id === tid ? { ...m, content: m.content + data.content } : m)) }
        else { const nid = `r-${++msgCounterRef.current}`; currentReasoningIdRef.current = nid; setMessages(prev => [...prev, { id: nid, role: 'system', content: data.content, timestamp: new Date() }]) }
      }),
      agentWs.on('answer', (data) => {
        const tid = currentAssistantIdRef.current
        if (tid) { setMessages(prev => prev.map(m => m.id === tid ? { ...m, content: data.content } : m)) }
        else { setMessages(prev => { const li = [...prev].reverse().findIndex(m => m.role === 'assistant'); if (li !== -1) { const idx = prev.length - 1 - li; return prev.map((m, i) => i === idx ? { ...m, content: data.content } : m) } return [...prev, { id: `a-${++msgCounterRef.current}`, role: 'assistant' as const, content: data.content, timestamp: new Date() }] }) }
        currentAssistantIdRef.current = null; setIsRunning(false); setCurrentRunId(null)
        api.getVersionStatus().then(setVersionStatus).catch(() => {})
        api.getVersionLog(5).then(d => setCheckpoints(d.checkpoints || [])).catch(() => {})
        api.getRuntimeStatus().then(setRuntimeStatus).catch(() => {})
        processQueue()
      }),
      agentWs.on('error', (data) => { setMessages(prev => [...prev, { id: `e-${++msgCounterRef.current}`, role: 'system', content: `Error: ${data.message}`, timestamp: new Date() }]); setIsRunning(false); setCurrentRunId(null); processQueue() }),
      agentWs.on('done', () => { setIsRunning(false); setCurrentRunId(null); processQueue() }),
      agentWs.on('confirm_request', (data) => { setConfirmRequest({summary: data.summary}) }),
    ]
    return () => unsubs.forEach(u => u())
  }, [sessionId])

  useEffect(scrollToBottom, [messages, scrollToBottom])

  const processQueue = () => { if (messageQueueRef.current.length > 0) { const next = messageQueueRef.current.shift()!; setTimeout(() => sendMessage(next), 100) } }

  const sendMessage = (text: string) => {
    currentAssistantIdRef.current = null; currentReasoningIdRef.current = null
    setMessages(prev => [...prev, { id: `u-${++msgCounterRef.current}`, role: 'user', content: text, timestamp: new Date() }])
    setIsRunning(true); agentWs.sendMessage(text)
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
      case '/clear': setMessages([]); messageQueueRef.current = []; break
      case '/undo': try { const r = await api.versionUndo(); addSys(`Undone: ${r.checkpoint?.id || 'ok'}`); setVersionStatus(await api.getVersionStatus()); const { messages: stm } = await api.listShortMemories(); if (stm) setMessages(stm.filter(m => ['user','assistant','system'].includes(m.role)).map((m, i) => ({ id: `h-${i}`, role: m.role as any, content: m.content, timestamp: new Date() }))) } catch (e: any) { addSys(`Undo failed: ${e.message}`) }; break
      case '/redo': try { const r = await api.versionRedo(); addSys(`Redone: ${r.checkpoint?.id || 'ok'}`); setVersionStatus(await api.getVersionStatus()); const { messages: stm } = await api.listShortMemories(); if (stm) setMessages(stm.filter(m => ['user','assistant','system'].includes(m.role)).map((m, i) => ({ id: `h-${i}`, role: m.role as any, content: m.content, timestamp: new Date() }))) } catch (e: any) { addSys(`Redo failed: ${e.message}`) }; break
      case '/log': try { const { checkpoints: cps } = await api.getVersionLog(10); addSys(cps.length === 0 ? 'No checkpoints.' : cps.map(cp => `${cp.is_head ? '→ ' : '  '}${cp.id}  ${cp.question || ''}`).join('\n')) } catch (e: any) { addSys(`Log failed: ${e.message}`) }; break
      case '/status': try { const s = await api.getVersionStatus(); addSys(`Session: ${s.session_id}\nHEAD: ${s.head?.id || 'none'}\nCan undo: ${s.can_undo}\nCan redo: ${s.can_redo}`) } catch (e: any) { addSys(`Status failed: ${e.message}`) }; break
      case '/compact': addSys('Compressing context...'); try { const r = await api.compactContext(args); addSys(`Compacted: ${r.tokens_saved} tokens saved`) } catch (e: any) { addSys(`Compact failed: ${e.message}`) }; break
      case '/sessions': try { const { sessions } = await api.getRecentSessions(10); addSys(sessions.length === 0 ? 'No recent sessions.' : sessions.map(s => `${s.session_id.slice(0, 12)}  ${s.preview || ''}`).join('\n')) } catch (e: any) { addSys(`Sessions failed: ${e.message}`) }; break
      case '/switch': if (!args) { addSys('Usage: /switch <session_id>'); break }; try { await api.restoreSession(args); window.location.href = `/chat/${args}` } catch (e: any) { addSys(`Switch failed: ${e.message}`) }; break
      default: addSys(`Unknown command: ${cmd}. Type /help for commands.`)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() } }
  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => { const v = e.target.value; setInput(v); setShowPalette(v.startsWith('/') && v.length > 0); setPaletteFilter(v) }
  const handlePaletteSelect = (cmd: string) => { setInput(cmd + ' '); setShowPalette(false); inputRef.current?.focus() }
  const handleCancel = () => { if (currentRunId) agentWs.cancel(currentRunId) }
  const handleConfirm = (approved: boolean) => { agentWs.confirm('', approved); setConfirmRequest(null) }
  const handleUndo = async () => { try { await api.versionUndo(); setVersionStatus(await api.getVersionStatus()); setCheckpoints((await api.getVersionLog(5)).checkpoints || []) } catch {} }
  const handleRedo = async () => { try { await api.versionRedo(); setVersionStatus(await api.getVersionStatus()); setCheckpoints((await api.getVersionLog(5)).checkpoints || []) } catch {} }

  const filteredCommands = COMMAND_DEFS.filter(c => c.cmd.startsWith(paletteFilter))

  return (
    <div className="flex-1 flex overflow-hidden" style={{ background: 'var(--surface-0)' }}>
      {/* Main Chat */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-5 space-y-3">
          {messages.length === 0 && (
            <div className="flex flex-col items-center justify-center h-full gap-4 animate-fade-in">
              <div className="w-16 h-16 rounded-2xl flex items-center justify-center" style={{ background: 'linear-gradient(135deg, var(--accent-muted), var(--cyan-muted))', border: '1px solid var(--border)' }}>
                <Sparkles size={28} style={{ color: 'var(--accent)' }} />
              </div>
              <div className="text-center">
                <p className="text-lg font-semibold" style={{ color: 'var(--fg)' }}>Start a conversation</p>
                <p className="text-sm mt-1" style={{ color: 'var(--fg-muted)' }}>Type <kbd className="px-1.5 py-0.5 rounded text-xs font-mono" style={{ background: 'var(--surface-3)', color: 'var(--fg-secondary)' }}>/help</kbd> for available commands</p>
              </div>
            </div>
          )}
          {messages.map((msg) => (
            <div key={msg.id} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'} animate-slide-up`}>
              <div className={`max-w-[80%] ${msg.role === 'user' ? 'rounded-2xl rounded-br-md' : msg.role === 'tool' ? 'rounded-lg' : 'rounded-2xl rounded-bl-md'} px-4 py-2.5`} style={{
                background: msg.role === 'user' ? 'var(--accent)' : msg.role === 'tool' ? 'var(--surface-2)' : msg.role === 'system' ? 'var(--surface-2)' : 'var(--surface-2)',
                border: msg.role === 'tool' ? '1px solid var(--border)' : msg.role === 'system' ? '1px solid var(--border-subtle)' : 'none',
                color: msg.role === 'user' ? 'white' : 'var(--fg)',
              }}>
                {msg.toolStatus === 'running' && <Loader2 size={12} className="inline mr-1.5 animate-spin" style={{ color: 'var(--accent)' }} />}
                {msg.role === 'tool' && <span className="text-xs font-mono font-medium mr-1.5" style={{ color: 'var(--accent)' }}>{msg.toolName}</span>}
                {msg.role === 'assistant' ? (
                  <div className="prose prose-sm prose-invert max-w-none" style={{ color: 'var(--fg)' }}>
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
                  </div>
                ) : msg.role === 'system' ? (
                  <pre className="whitespace-pre-wrap font-mono text-xs" style={{ color: 'var(--fg-muted)' }}>{msg.content}</pre>
                ) : msg.role === 'tool' ? (
                  <pre className="whitespace-pre-wrap font-mono text-xs" style={{ color: 'var(--fg-secondary)' }}>{msg.content}</pre>
                ) : (
                  <span className="text-sm">{msg.content}</span>
                )}
              </div>
            </div>
          ))}
          <div ref={messagesEndRef} />
        </div>

        {/* Confirm */}
        {confirmRequest && (
          <div className="mx-5 mb-3 rounded-xl p-4 animate-slide-up" style={{ background: 'var(--amber-muted)', border: '1px solid rgba(245,158,11,0.3)' }}>
            <div className="text-sm font-medium mb-2" style={{ color: 'var(--amber)' }}>Confirmation Required</div>
            <pre className="text-xs rounded-lg p-2.5 mb-3 overflow-auto max-h-32" style={{ background: 'var(--surface-1)', color: 'var(--fg-secondary)' }}>{confirmRequest.summary}</pre>
            <div className="flex gap-2">
              <button onClick={() => handleConfirm(true)} className="px-4 py-1.5 text-sm font-medium rounded-lg transition-colors" style={{ background: 'var(--amber)', color: 'var(--surface-0)' }}>Approve</button>
              <button onClick={() => handleConfirm(false)} className="btn-ghost text-sm">Deny</button>
            </div>
          </div>
        )}

        {/* Command Palette */}
        {showPalette && filteredCommands.length > 0 && (
          <div className="mx-5 mb-2 rounded-xl overflow-hidden animate-slide-down" style={{ background: 'var(--surface-3)', border: '1px solid var(--border-strong)', boxShadow: '0 8px 32px rgba(0,0,0,0.4)' }}>
            {filteredCommands.map(c => (
              <button key={c.cmd} onClick={() => handlePaletteSelect(c.cmd)} className="w-full text-left px-4 py-2.5 text-sm flex items-center gap-3 transition-colors" style={{ color: 'var(--fg)' }} onMouseEnter={e => e.currentTarget.style.background = 'var(--surface-4)'} onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
                <span className="font-mono font-medium" style={{ color: 'var(--accent)' }}>{c.cmd}</span>
                <span className="text-xs" style={{ color: 'var(--fg-faint)' }}>{c.desc}</span>
              </button>
            ))}
          </div>
        )}

        {/* Input Area */}
        <div className="p-4 pt-0">
          <div className="rounded-xl p-3" style={{ background: 'var(--surface-1)', border: '1px solid var(--border)' }}>
            <div className="flex items-end gap-2">
              <textarea ref={inputRef} value={input} onChange={handleInputChange} onKeyDown={handleKeyDown} placeholder={isRunning ? "Agent running... messages will be queued" : "Type a message... (/ for commands)"} className="flex-1 bg-transparent text-sm resize-none focus:outline-none max-h-32" style={{ color: 'var(--fg)' }} rows={1} />
              {isRunning ? (
                <button onClick={handleCancel} className="w-8 h-8 flex items-center justify-center rounded-lg transition-all" style={{ background: 'var(--red-muted)', color: 'var(--red)' }}><Square size={14} /></button>
              ) : (
                <button onClick={handleSend} disabled={!input.trim()} className="w-8 h-8 flex items-center justify-center rounded-lg transition-all" style={{ background: input.trim() ? 'var(--accent)' : 'var(--surface-3)', color: input.trim() ? 'white' : 'var(--fg-faint)' }}><Send size={14} /></button>
              )}
            </div>

            {/* HUD */}
            <div className="mt-2.5 flex items-center gap-3 text-xs overflow-x-auto" style={{ color: 'var(--fg-faint)' }}>
              {runtimeStatus && (
                <span className="flex items-center gap-1 shrink-0 font-mono" style={{ color: 'var(--fg-muted)' }}>
                  <Cpu size={11} />
                  {runtimeStatus.model_id?.split('/').pop() || runtimeStatus.model_id}
                </span>
              )}
              {runtimeStatus && (
                <span className="shrink-0" title={`STM: ${runtimeStatus.stm_tokens} / ${runtimeStatus.ctx_max}`}>
                  ctx {Math.round(runtimeStatus.stm_tokens / 1000)}k/{Math.round(runtimeStatus.ctx_max / 1000)}k ({runtimeStatus.ctx_max > 0 ? Math.round(runtimeStatus.stm_tokens / runtimeStatus.ctx_max * 100) : 0}%)
                </span>
              )}
              {runtimeStatus?.total_usage && (
                <span className="shrink-0">in:{(runtimeStatus.total_usage.input_tokens || 0).toLocaleString()} out:{(runtimeStatus.total_usage.output_tokens || 0).toLocaleString()}</span>
              )}
              {versionStatus?.head && (
                <span className="flex items-center gap-1 shrink-0">
                  <span className="w-1.5 h-1.5 rounded-full" style={{ background: 'var(--accent)' }} />
                  {versionStatus.head.id?.slice(0, 8)}
                </span>
              )}
              <div className="flex items-center gap-0.5 shrink-0">
                <button onClick={handleUndo} disabled={!versionStatus?.can_undo} className="p-0.5 rounded transition-colors disabled:opacity-30" style={{ color: 'var(--fg-faint)' }} onMouseEnter={e => e.currentTarget.style.color = 'var(--fg)'} onMouseLeave={e => e.currentTarget.style.color = 'var(--fg-faint)'}><Undo2 size={11} /></button>
                <button onClick={handleRedo} disabled={!versionStatus?.can_redo} className="p-0.5 rounded transition-colors disabled:opacity-30" style={{ color: 'var(--fg-faint)' }} onMouseEnter={e => e.currentTarget.style.color = 'var(--fg)'} onMouseLeave={e => e.currentTarget.style.color = 'var(--fg-faint)'}><Redo2 size={11} /></button>
                <button onClick={() => { setShowCheckpoints(!showCheckpoints); api.getVersionLog(10).then(d => setCheckpoints(d.checkpoints || [])) }} className="p-0.5 rounded transition-colors" style={{ color: 'var(--fg-faint)' }} onMouseEnter={e => e.currentTarget.style.color = 'var(--fg)'} onMouseLeave={e => e.currentTarget.style.color = 'var(--fg-faint)'}><History size={11} /></button>
              </div>
              {messageQueueRef.current.length > 0 && <span className="shrink-0" style={{ color: 'var(--amber)' }}>Queue: {messageQueueRef.current.length}</span>}
              <span className="ml-auto shrink-0 font-mono">{sessionId?.slice(0, 12)}</span>
              <button onClick={() => setShowSidePanel(!showSidePanel)} className="p-0.5 rounded transition-colors shrink-0" style={{ color: 'var(--fg-faint)' }} onMouseEnter={e => e.currentTarget.style.color = 'var(--fg)'} onMouseLeave={e => e.currentTarget.style.color = 'var(--fg-faint)'}>
                {showSidePanel ? <PanelRightClose size={12} /> : <PanelRightOpen size={12} />}
              </button>
            </div>
          </div>
        </div>

        {/* Checkpoint Overlay */}
        {showCheckpoints && (
          <div className="absolute bottom-28 left-5 w-96 rounded-xl overflow-hidden z-50 animate-slide-up" style={{ background: 'var(--surface-3)', border: '1px solid var(--border-strong)', boxShadow: '0 16px 64px rgba(0,0,0,0.5)' }}>
            <div className="px-4 py-3 flex items-center justify-between" style={{ borderBottom: '1px solid var(--border)' }}>
              <span className="text-sm font-medium" style={{ color: 'var(--fg)' }}>Checkpoints</span>
              <button onClick={() => setShowCheckpoints(false)} className="p-1 rounded" style={{ color: 'var(--fg-faint)' }}><Square size={12} /></button>
            </div>
            {checkpoints.length === 0 ? <p className="p-4 text-xs" style={{ color: 'var(--fg-muted)' }}>No checkpoints</p> : checkpoints.map(cp => (
              <div key={cp.id} className="px-4 py-2.5" style={{ borderBottom: '1px solid var(--border-subtle)', background: cp.is_head ? 'var(--accent-subtle)' : 'transparent' }}>
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

      {/* Side Panel */}
      {showSidePanel && (
        <div className="w-72 flex flex-col overflow-hidden shrink-0" style={{ background: 'var(--surface-1)', borderLeft: '1px solid var(--border)' }}>
          <div className="flex" style={{ borderBottom: '1px solid var(--border)' }}>
            {(['tools', 'todo', 'mcp', 'version'] as const).map(tab => (
              <button key={tab} onClick={() => setSidePanelTab(tab)} className="flex-1 py-2.5 text-xs font-medium transition-colors relative" style={{ color: sidePanelTab === tab ? 'var(--accent)' : 'var(--fg-muted)' }}>
                {tab === 'tools' && <Wrench size={11} className="inline mr-1" />}
                {tab === 'todo' && <CheckSquare size={11} className="inline mr-1" />}
                {tab === 'mcp' && <Server size={11} className="inline mr-1" />}
                {tab === 'version' && <History size={11} className="inline mr-1" />}
                {tab.charAt(0).toUpperCase() + tab.slice(1)}
                {sidePanelTab === tab && <div className="absolute bottom-0 left-2 right-2 h-0.5 rounded-full" style={{ background: 'var(--accent)' }} />}
              </button>
            ))}
          </div>

          <div className="flex-1 overflow-y-auto p-3 space-y-1.5">
            {sidePanelTab === 'tools' && (registeredTools.length === 0 ? <p className="text-xs py-4 text-center" style={{ color: 'var(--fg-faint)' }}>Tools registered on agent start</p> : registeredTools.map(tool => (
              <div key={tool.name} className="flex items-center gap-2 text-xs py-1.5 px-2 rounded-md" style={{ color: 'var(--fg-secondary)' }}>
                <span className="w-1.5 h-1.5 rounded-full" style={{ background: tool.risk === 'high' ? 'var(--red)' : tool.risk === 'medium' ? 'var(--amber)' : 'var(--green)' }} />
                <span className="font-mono">{tool.name}</span>
              </div>
            )))}

            {sidePanelTab === 'todo' && (todos.length === 0 ? <p className="text-xs py-4 text-center" style={{ color: 'var(--fg-faint)' }}>No todo items</p> : todos.map(todo => (
              <div key={todo.id} className="flex items-start gap-2 text-xs py-1.5 px-2 rounded-md">
                <span className="mt-0.5" style={{ color: todo.status === 'done' ? 'var(--green)' : todo.status === 'in_progress' ? 'var(--accent)' : 'var(--fg-faint)' }}>{todo.status === 'done' ? '✓' : todo.status === 'in_progress' ? '→' : '·'}</span>
                <span style={{ color: todo.status === 'done' ? 'var(--fg-faint)' : 'var(--fg)', textDecoration: todo.status === 'done' ? 'line-through' : 'none' }}>{todo.description}</span>
              </div>
            )))}

            {sidePanelTab === 'mcp' && (!mcpStatus ? <p className="text-xs py-4 text-center" style={{ color: 'var(--fg-faint)' }}>Loading...</p> : <>
              <div className="flex items-center gap-2 text-xs mb-2 px-2">
                <span className="w-2 h-2 rounded-full" style={{ background: mcpStatus.started ? 'var(--green)' : 'var(--fg-faint)' }} />
                <span style={{ color: 'var(--fg-secondary)' }}>{mcpStatus.started ? 'Running' : 'Not started'}</span>
              </div>
              {(mcpStatus.servers || []).map((s: any) => (
                <div key={s.name} className="flex items-center justify-between text-xs py-1.5 px-2 rounded-md" style={{ color: 'var(--fg-secondary)' }}>
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: s.connected ? 'var(--green)' : 'var(--red)' }} />
                    <span className="truncate">{s.name}</span>
                  </div>
                  <span className="shrink-0 ml-2" style={{ color: 'var(--fg-faint)' }}>{s.tool_names?.length || 0} tools</span>
                </div>
              ))}
            </>)}

            {sidePanelTab === 'version' && versionStatus && (
              <div className="space-y-3 px-2">
                <div className="text-xs"><span style={{ color: 'var(--fg-faint)' }}>HEAD: </span><span className="font-mono" style={{ color: 'var(--accent)' }}>{versionStatus.head?.id || 'none'}</span></div>
                <div className="flex gap-2">
                  <button onClick={handleUndo} disabled={!versionStatus.can_undo} className="btn-ghost text-xs flex items-center gap-1 flex-1 justify-center"><Undo2 size={11} /> Undo</button>
                  <button onClick={handleRedo} disabled={!versionStatus.can_redo} className="btn-ghost text-xs flex items-center gap-1 flex-1 justify-center"><Redo2 size={11} /> Redo</button>
                </div>
                <div style={{ borderTop: '1px solid var(--border)', paddingTop: '8px' }}>
                  <p className="text-xs mb-1.5" style={{ color: 'var(--fg-faint)' }}>Recent:</p>
                  {checkpoints.map(cp => (
                    <div key={cp.id} className="text-xs py-1" style={{ color: cp.is_head ? 'var(--accent)' : 'var(--fg-faint)' }}>{cp.is_head ? '→ ' : '  '}{cp.id} — {cp.question?.slice(0, 30) || '?'}</div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
