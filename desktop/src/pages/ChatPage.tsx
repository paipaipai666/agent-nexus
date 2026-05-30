import { useState, useRef, useEffect, useCallback } from 'react'
import { useParams } from 'react-router-dom'
import { Send, Square, Loader2 } from 'lucide-react'
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

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [])

  // Initialize session
  useEffect(() => {
    if (routeSessionId) {
      // Restore existing session
      api.restoreSession(routeSessionId).then(({ session_id }) => {
        setSessionId(session_id)
        agentWs.connect(session_id)
        // Load history messages from STM
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
        }).catch(err => console.error('Failed to load history:', err))
      }).catch((error) => {
        console.error('Failed to restore session:', error)
        // Fallback to creating new session
        api.createSession().then(({ session_id }) => {
          setSessionId(session_id)
          agentWs.connect(session_id)
        })
      })
    } else {
      // Create new session
      api.createSession().then(({ session_id }) => {
        setSessionId(session_id)
        agentWs.connect(session_id)
      })
    }
    return () => agentWs.disconnect()
  }, [routeSessionId])

  // Listen for WS events
  useEffect(() => {
    const unsubs = [
      agentWs.on('thinking', (data) => {
        currentAssistantIdRef.current = null
        currentReasoningIdRef.current = null
        const id = data.seq != null ? `thinking-${data.seq}` : `thinking-${++msgCounterRef.current}`
        setMessages(prev => [...prev, {
          id,
          role: 'system',
          content: data.content || 'Thinking...',
          timestamp: new Date(),
        }])
      }),
      agentWs.on('tool_call', (data) => {
        currentAssistantIdRef.current = null
        const id = data.seq != null ? `tool-${data.seq}` : `tool-${++msgCounterRef.current}`
        setMessages(prev => [...prev, {
          id,
          role: 'tool',
          content: `Calling: ${data.tool_name}`,
          toolName: data.tool_name,
          toolStatus: 'running',
          timestamp: new Date(),
        }])
      }),
      agentWs.on('tool_result', (data) => {
        setMessages(prev =>
          prev.map(m =>
            m.toolName === data.tool_name && m.toolStatus === 'running'
              ? { ...m, toolStatus: 'done' as const, content: `${data.tool_name}: ${data.result || 'done'}` }
              : m
          )
        )
      }),
      agentWs.on('token', (data) => {
        // When final answer starts, clear reasoning ref
        currentReasoningIdRef.current = null
        const targetId = currentAssistantIdRef.current
        if (targetId) {
          setMessages(prev =>
            prev.map(m => m.id === targetId ? { ...m, content: m.content + data.content } : m)
          )
        } else {
          const newId = data.seq != null ? `assistant-${data.seq}` : `assistant-${++msgCounterRef.current}`
          currentAssistantIdRef.current = newId
          setMessages(prev => [...prev, {
            id: newId,
            role: 'assistant',
            content: data.content,
            timestamp: new Date(),
          }])
        }
      }),
      agentWs.on('reasoning', (data) => {
        const targetId = currentReasoningIdRef.current
        if (targetId) {
          setMessages(prev =>
            prev.map(m => m.id === targetId ? { ...m, content: m.content + data.content } : m)
          )
        } else {
          const newId = data.seq != null ? `reasoning-${data.seq}` : `reasoning-${++msgCounterRef.current}`
          currentReasoningIdRef.current = newId
          setMessages(prev => [...prev, {
            id: newId,
            role: 'system',
            content: data.content,
            timestamp: new Date(),
          }])
        }
      }),
      agentWs.on('answer', (data) => {
        const targetId = currentAssistantIdRef.current
        if (targetId) {
          setMessages(prev =>
            prev.map(m => m.id === targetId ? { ...m, content: data.content } : m)
          )
        } else {
          // Find the last assistant message in the array and update it,
          // instead of creating a new message at the end
          setMessages(prev => {
            const lastAssistantIdx = [...prev].reverse().findIndex(m => m.role === 'assistant')
            if (lastAssistantIdx !== -1) {
              const idx = prev.length - 1 - lastAssistantIdx
              return prev.map((m, i) => i === idx ? { ...m, content: data.content } : m)
            }
            // No existing assistant message found, create new one
            const newId = data.seq != null ? `assistant-${data.seq}` : `assistant-${++msgCounterRef.current}`
            return [...prev, {
              id: newId,
              role: 'assistant' as const,
              content: data.content,
              timestamp: new Date(),
            }]
          })
        }
        currentAssistantIdRef.current = null
        setIsRunning(false)
        setCurrentRunId(null)
      }),
      agentWs.on('error', (data) => {
        const id = `error-${++msgCounterRef.current}`
        setMessages(prev => [...prev, {
          id,
          role: 'system',
          content: `Error: ${data.message}`,
          timestamp: new Date(),
        }])
        setIsRunning(false)
        setCurrentRunId(null)
      }),
      agentWs.on('done', () => {
        setIsRunning(false)
        setCurrentRunId(null)
      }),
      agentWs.on('confirm_request', (data) => {
        setConfirmRequest({summary: data.summary})
      }),
    ]
    return () => unsubs.forEach(u => u())
  }, [])

  useEffect(scrollToBottom, [messages, scrollToBottom])

  const handleSend = () => {
    const text = input.trim()
    if (!text || !sessionId || isRunning) return

    currentAssistantIdRef.current = null
    currentReasoningIdRef.current = null

    setMessages(prev => [...prev, {
      id: `user-${++msgCounterRef.current}`,
      role: 'user',
      content: text,
      timestamp: new Date(),
    }])
    setInput('')
    setIsRunning(true)

    // Send via WS for streaming, or fallback to REST
    agentWs.sendMessage(text)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleCancel = () => {
    if (currentRunId) {
      agentWs.cancel(currentRunId)
    }
  }

  const handleConfirm = (approved: boolean) => {
    agentWs.confirm('', approved)
    setConfirmRequest(null)
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {messages.length === 0 && (
          <div className="flex items-center justify-center h-full text-text-muted">
            <p>Start a conversation</p>
          </div>
        )}
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className={`max-w-[80%] rounded-lg px-3 py-2 text-sm ${
                msg.role === 'user'
                  ? 'bg-accent-primary/20 text-text-primary'
                  : msg.role === 'tool'
                  ? 'bg-accent-purple/10 text-accent-purple text-xs font-mono'
                  : msg.role === 'system'
                  ? 'bg-bg-tertiary text-text-muted text-xs italic'
                  : 'bg-bg-secondary text-text-primary'
              }`}
            >
              {msg.toolStatus === 'running' && (
                <Loader2 size={12} className="inline mr-1 animate-spin" />
              )}
              {msg.role === 'assistant' ? (
                <div className="prose prose-sm prose-invert max-w-none">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {msg.content}
                  </ReactMarkdown>
                </div>
              ) : (
                msg.content
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
          <pre className="text-xs text-text-secondary bg-bg-tertiary p-2 rounded mb-3 overflow-auto max-h-32">
            {confirmRequest.summary}
          </pre>
          <div className="flex gap-2">
            <button
              onClick={() => handleConfirm(true)}
              className="px-3 py-1.5 text-sm bg-status-warning text-bg-primary rounded hover:opacity-90"
            >
              Approve
            </button>
            <button
              onClick={() => handleConfirm(false)}
              className="px-3 py-1.5 text-sm bg-bg-tertiary text-text-secondary rounded hover:bg-bg-secondary"
            >
              Deny
            </button>
          </div>
        </div>
      )}

      {/* Input */}
      <div className="border-t border-border-default p-3 bg-bg-secondary">
        <div className="flex items-end gap-2">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Type a message... (Enter to send, Shift+Enter for newline)"
            className="flex-1 bg-bg-tertiary text-text-primary rounded-lg px-3 py-2 text-sm resize-none border border-border-default focus:border-accent-primary focus:outline-none max-h-32"
            rows={1}
            disabled={isRunning}
          />
          {isRunning ? (
            <button
              onClick={handleCancel}
              className="w-9 h-9 flex items-center justify-center rounded-lg bg-status-error/20 text-status-error hover:bg-status-error/30 transition-colors"
              title="Cancel"
            >
              <Square size={16} />
            </button>
          ) : (
            <button
              onClick={handleSend}
              disabled={!input.trim()}
              className="w-9 h-9 flex items-center justify-center rounded-lg bg-accent-primary text-bg-primary hover:opacity-90 transition-opacity disabled:opacity-30"
              title="Send"
            >
              <Send size={16} />
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
