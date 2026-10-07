import React, { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ChevronDown, ChevronRight, Paperclip } from 'lucide-react'
import type { Message } from '../session/SessionManager'
import { humanSize } from '../../services/api'
import { animateMessage } from '../../utils/animations'
import { unwrapStreamingAnswer } from '../../utils/unwrapAnswer'
import ToolCard from './ToolCard'

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
              {msg.attachments && msg.attachments.length > 0 && (
                <div className="flex flex-wrap justify-end gap-1 mb-1.5">
                  {msg.attachments.map(a => (
                    <span
                      key={a.path}
                      title={a.path}
                      className="flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] max-w-[280px]"
                      style={{ background: 'var(--surface-1)', border: '1px solid var(--border)', color: 'var(--fg-muted)' }}
                    >
                      <Paperclip size={10} style={{ flexShrink: 0 }} />
                      <span className="truncate">{a.name}</span>
                      <span style={{ color: 'var(--fg-faint)' }}>{humanSize(a.size)}</span>
                    </span>
                  ))}
                </div>
              )}
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

export default MessageBubble
