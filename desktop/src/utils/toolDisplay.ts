/**
 * Tool-card display helpers — collapsed-row arg summary and typed body kind.
 * Pure functions; no React so they stay unit-testable.
 */

/** Keys preferred for the collapsed-row summary, in priority order. */
const ARG_SUMMARY_KEYS = [
  'file_path', 'filepath', 'path', 'file', 'filename',
  'query', 'q', 'pattern', 'search', 'regex',
  'command', 'cmd', 'code',
  'url', 'uri', 'link',
  'text', 'content', 'message', 'prompt', 'task', 'question',
]

/** One-line argument summary for the collapsed tool row. */
export function toolArgSummary(msg: { toolArgs?: Record<string, unknown> }): string {
  const args = msg.toolArgs
  if (!args || typeof args !== 'object') return ''
  for (const key of ARG_SUMMARY_KEYS) {
    const v = args[key]
    if (typeof v === 'string' && v.trim()) return truncateOneLine(v.trim())
    if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  }
  // First string-ish value as fallback
  for (const v of Object.values(args)) {
    if (typeof v === 'string' && v.trim()) return truncateOneLine(v.trim())
    if (typeof v === 'number') return String(v)
  }
  return ''
}

function truncateOneLine(s: string, max = 64): string {
  const one = s.replace(/\s+/g, ' ')
  return one.length > max ? one.slice(0, max - 1) + '…' : one
}

/** 48ms / 1.2s / 2m3s — compact duration for the row. */
export function formatDuration(ms?: number): string {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return ''
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)}s`
  const m = Math.floor(ms / 60_000)
  const s = Math.round((ms % 60_000) / 1000)
  return s ? `${m}m${s}s` : `${m}m`
}

export type ToolBodyKind = 'diff' | 'search' | 'shell' | 'file' | 'plain'

const DIFF_TOOLS = new Set(['file_write', 'file_edit', 'edit_file', 'write_file', 'apply_patch'])
const SEARCH_TOOLS = new Set(['web_search', 'web_fetch', 'kb_search', 'grep_search', 'history_search', 'memory_search'])
const SHELL_TOOLS = new Set(['shell_exec', 'python_execute', 'bash', 'execute_command', 'run_command'])
const FILE_TOOLS = new Set(['file_read', 'read_file', 'file_list', 'file_read_text'])

/** Result body renderer class for a tool message. */
export function toolBodyKind(toolName?: string): ToolBodyKind {
  const n = (toolName || '').toLowerCase()
  if (DIFF_TOOLS.has(n)) return 'diff'
  if (SHELL_TOOLS.has(n)) return 'shell'
  if (SEARCH_TOOLS.has(n)) return 'search'
  if (FILE_TOOLS.has(n)) return 'file'
  return 'plain'
}

export interface DiffLine { kind: 'hunk' | 'add' | 'del' | 'ctx'; text: string }

/** Split a unified-diff-like body into lines. Avoids the old first-char heuristic
 *  on non-diff text: only treats as diff when hunk/add/del lines actually appear. */
export function parseDiffLines(text: string): DiffLine[] | null {
  const lines = text.split('\n')
  let score = 0
  for (const l of lines) {
    if (l.startsWith('@@')) score += 2
    else if (l.startsWith('+') && !l.startsWith('+++')) score += 1
    else if (l.startsWith('-') && !l.startsWith('---')) score += 1
  }
  if (score < 2) return null
  return lines.map(l => {
    if (l.startsWith('@@')) return { kind: 'hunk' as const, text: l }
    if (l.startsWith('+') && !l.startsWith('+++')) return { kind: 'add' as const, text: l }
    if (l.startsWith('-') && !l.startsWith('---')) return { kind: 'del' as const, text: l }
    return { kind: 'ctx' as const, text: l }
  })
}

export interface SearchHit { title: string; url: string; snippet: string }

/** Parse condensed web_search / web_fetch style output into hit cards. */
export function parseSearchHits(text: string): SearchHit[] | null {
  const hits: SearchHit[] = []
  const lines = text.split('\n')
  let cur: SearchHit | null = null
  for (const line of lines) {
    const titleMatch = line.match(/^\[(\d+)\]\s*(.+)$/)
    const urlMatch = line.match(/^URL:\s*(\S+)/)
    if (titleMatch) {
      if (cur) hits.push(cur)
      cur = { title: titleMatch[2].trim(), url: '', snippet: '' }
      continue
    }
    if (urlMatch && cur) {
      cur.url = urlMatch[1]
      continue
    }
    if (cur && line.trim() && !cur.snippet) {
      cur.snippet = line.trim()
    }
  }
  if (cur) hits.push(cur)
  return hits.length ? hits : null
}

/** Extract shell command from args for the expanded shell body. */
export function shellCommandFromArgs(args?: Record<string, unknown>): string {
  if (!args) return ''
  for (const k of ['command', 'cmd', 'code', 'script']) {
    const v = args[k]
    if (typeof v === 'string' && v.trim()) return v.trim()
  }
  return ''
}

/** Result body text: toolResult first, content as fallback (history path). */
export function toolBodyText(msg: { toolResult?: string; content?: string }): string {
  return (msg.toolResult ?? msg.content ?? '').trim()
}

/** First path-ish arg for file tools — used in the expanded meta strip. */
export function toolPathArg(args?: Record<string, unknown>): string {
  if (!args) return ''
  for (const k of ARG_SUMMARY_KEYS) {
    const v = args[k]
    if (typeof v === 'string' && v.trim() && (k.includes('path') || k.includes('file') || k === 'filename')) {
      return v.trim()
    }
  }
  return ''
}
