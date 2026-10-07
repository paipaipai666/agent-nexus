import React, { useState, useRef, useEffect } from 'react'
import { ChevronRight, Puzzle } from 'lucide-react'
import { ThinkingOrb } from 'thinking-orbs'
import type { Message } from '../session/SessionManager'
import { usePrefersReducedMotion, toolOrbState } from '../../utils/effects'
import { toolIcon } from '../../utils/toolIcons'
import {
  toolArgSummary, formatDuration, toolBodyKind, parseDiffLines, parseSearchHits,
  shellCommandFromArgs, toolBodyText, toolPathArg,
} from '../../utils/toolDisplay'

/* ─── Tool Row — one quiet line inside a shared group card ───
 * Collapsed: icon · name · arg summary chip · duration · 7px status dot.
 * Expanded: typed body (diff / search / shell / file meta / plain).
 * Consecutive tool messages share one bordered card (see thread renderer). */
const ToolCard = React.memo(function ToolCard({ msg, isMcp }: { msg: Message; isMcp?: boolean }) {
  const [expanded, setExpanded] = useState(msg.toolStatus === 'running')
  const userToggled = useRef(false)
  const reducedMotion = usePrefersReducedMotion()
  useEffect(() => {
    // auto-expand while running; collapse again when done unless the user
    // explicitly opened the row
    if (msg.toolStatus === 'running') setExpanded(true)
    else if (!userToggled.current) setExpanded(false)
  }, [msg.toolStatus])

  const status = msg.toolStatus ?? 'done'
  const statusColor = status === 'running' ? 'var(--amber)' : status === 'error' ? 'var(--red)' : 'var(--green)'
  const Icon = toolIcon(msg.toolName)
  const argSummary = toolArgSummary(msg)
  const duration = formatDuration(msg.toolDurationMs)
  const body = toolBodyText(msg)
  const kind = toolBodyKind(msg.toolName)

  return (
    <div className="group/tool">
      <button
        onClick={() => { userToggled.current = true; setExpanded(!expanded) }}
        className="w-full flex items-center gap-2.5 px-3.5 py-2 text-left transition-colors hover:bg-[var(--surface-2)]"
        style={{ minHeight: 38 }}
      >
        <span
          className="w-5 h-5 rounded-md grid place-items-center shrink-0"
          style={{ background: 'var(--surface-2)', color: 'var(--fg-muted)' }}
        >
          {Icon ? <Icon size={11} strokeWidth={2} /> : isMcp
            ? <Puzzle size={11} strokeWidth={2} />
            : <span className="font-mono text-[9.5px] font-semibold">{(msg.toolName || 'T')[0].toUpperCase()}</span>}
        </span>
        <span className="font-mono text-[12.5px] shrink-0 font-medium" style={{ color: 'var(--fg-secondary)' }}>
          {msg.toolName || 'tool'}
        </span>
        {argSummary && (
          <span
            className="font-mono text-[11px] truncate max-w-[280px] px-2 py-px rounded-full"
            style={{
              color: 'var(--fg-muted)',
              background: 'var(--surface-2)',
              border: '1px solid var(--border)',
            }}
            title={argSummary}
          >
            {argSummary}
          </span>
        )}
        <span className="ml-auto" />
        {duration && (
          <span className="font-mono text-[11px] shrink-0 tabular-nums" style={{ color: 'var(--fg-faint)' }}>
            {duration}
          </span>
        )}
        <span className="flex items-center gap-1.5 shrink-0">
          {status === 'running' && !reducedMotion ? (
            <ThinkingOrb
              state={toolOrbState(msg.toolName)}
              size={20}
              paused={reducedMotion}
              aria-label={`${msg.toolName || 'tool'} running`}
              style={{ width: 14, height: 14 }}
            />
          ) : (
            <span
              className="w-[7px] h-[7px] rounded-full"
              style={{
                background: statusColor,
                animation: status === 'running' && !reducedMotion ? 'tool-dot-pulse 1.2s ease-in-out infinite' : undefined,
              }}
              aria-label={status}
            />
          )}
        </span>
        <ChevronRight
          size={13}
          className="shrink-0 transition-transform duration-200"
          style={{ color: 'var(--fg-faint)', transform: expanded ? 'rotate(90deg)' : 'none' }}
        />
      </button>
      {expanded && (
        <div className="pl-11 pr-3.5 pb-3">
          <ToolBody msg={msg} kind={kind} body={body} />
        </div>
      )}
    </div>
  )
})

/* Typed expanded body for a tool row. */
function ToolBody({ msg, kind, body }: { msg: Message; kind: ReturnType<typeof toolBodyKind>; body: string }) {
  const mono = 'font-mono text-[12px] leading-[1.7]'
  const codeBg = { background: 'var(--surface-2)', border: '1px solid var(--border)' }

  if (!body && msg.toolStatus === 'running') {
    return (
      <div className={`${mono} px-3 py-2 rounded-md`} style={{ ...codeBg, color: 'var(--fg-muted)' }}>
        执行中…
      </div>
    )
  }

  if (kind === 'diff' || (kind === 'plain' && parseDiffLines(body))) {
    const lines = parseDiffLines(body)
    if (lines) {
      return (
        <div className="rounded-md overflow-hidden" style={codeBg}>
          {lines.map((l, i) => (
            <div
              key={i}
              className={`${mono} px-3 whitespace-pre-wrap break-words`}
              style={{
                color: l.kind === 'hunk' ? 'var(--blue)'
                  : l.kind === 'add' ? 'var(--green)'
                  : l.kind === 'del' ? 'var(--red)'
                  : 'var(--fg-muted)',
                background: l.kind === 'add' ? 'var(--green-muted)'
                  : l.kind === 'del' ? 'var(--red-muted)'
                  : l.kind === 'hunk' ? 'var(--blue-muted)'
                  : 'transparent',
              }}
            >
              {l.text || ' '}
            </div>
          ))}
        </div>
      )
    }
  }

  if (kind === 'search') {
    const hits = parseSearchHits(body)
    if (hits) {
      return (
        <div>
          <div className="font-mono text-[11px] mb-1.5" style={{ color: 'var(--fg-faint)' }}>
            {hits.length} 条结果 · 正文默认收起
          </div>
          {hits.map((h, i) => (
            <div key={i} className="py-2" style={{ borderTop: i ? '1px solid var(--border-subtle)' : 'none' }}>
              <div className="text-[13px] font-medium mb-0.5" style={{ color: 'var(--fg)' }}>{h.title}</div>
              {h.url && (
                <div className="font-mono text-[11px] break-all" style={{ color: 'var(--blue)' }}>{h.url}</div>
              )}
              {h.snippet && (
                <div className="text-[12px] mt-1 leading-snug" style={{ color: 'var(--fg-muted)' }}>{h.snippet}</div>
              )}
            </div>
          ))}
        </div>
      )
    }
  }

  if (kind === 'shell') {
    const cmd = shellCommandFromArgs(msg.toolArgs)
    const isErr = msg.toolStatus === 'error'
    return (
      <div>
        {cmd && (
          <div
            className={`${mono} px-3 py-2 rounded-t-md flex items-start gap-2`}
            style={{ ...codeBg, borderBottom: 'none', borderRadius: '10px 10px 0 0', color: 'var(--fg-secondary)' }}
          >
            <span style={{ color: 'var(--green)' }}>›</span>
            <span className="break-all">{cmd}</span>
          </div>
        )}
        {body && (
          <div
            className={`${mono} px-3 py-2 whitespace-pre-wrap break-words max-h-[180px] overflow-auto`}
            style={{
              ...codeBg,
              borderRadius: cmd ? '0 0 10px 10px' : '10px',
              borderTop: cmd ? 'none' : undefined,
              borderLeft: isErr ? '2px solid var(--red)' : undefined,
              color: 'var(--fg-muted)',
            }}
          >
            {isErr && (
              <span className="block text-[11.5px] mb-1" style={{ color: 'var(--red)' }}>
                error{msg.toolDurationMs != null ? ` · ${formatDuration(msg.toolDurationMs)}` : ''}
              </span>
            )}
            {body}
          </div>
        )}
      </div>
    )
  }

  if (kind === 'file') {
    const path = toolPathArg(msg.toolArgs)
    return (
      <div>
        {(path || body) && (
          <div className="flex flex-wrap gap-x-3.5 gap-y-1 mb-2 font-mono text-[11px]" style={{ color: 'var(--fg-muted)' }}>
            {path && (
              <span>
                路径 <b style={{ color: 'var(--fg-secondary)', fontWeight: 500 }}>{path}</b>
              </span>
            )}
            {body && (
              <span>
                <b style={{ color: 'var(--fg-secondary)', fontWeight: 500 }}>{body.split('\n').length}</b> 行
              </span>
            )}
          </div>
        )}
        {body && (
          <pre
            className={`${mono} px-3 py-2 rounded-md whitespace-pre-wrap break-words max-h-[220px] overflow-auto`}
            style={{ ...codeBg, color: 'var(--fg-muted)' }}
          >
            {body}
          </pre>
        )}
      </div>
    )
  }

  // plain
  return body ? (
    <pre
      className={`${mono} px-3 py-2 rounded-md whitespace-pre-wrap break-words max-h-[220px] overflow-auto`}
      style={{ ...codeBg, color: 'var(--fg-muted)' }}
    >
      {body}
    </pre>
  ) : null
}

export default ToolCard
