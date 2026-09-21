/**
 * History message transform — maps the durable journal format
 * (conversation_messages: user / assistant thoughts / tool rows /
 * `[思考过程]` / `[最终答案]` markers) to desktop display Messages.
 *
 * Extracted from ChatPage.loadAndDisplayMessages as a pure function so the
 * mapping is unit-testable. Invariants (locked by history-transform tests):
 * - blank journal rows never render (empty thinking cards);
 * - express_reaction tool rows never become tool cards — the emoji attaches
 *   to the triggering user message, mirroring the live WS path where the
 *   server rewrites tool_start into a user_reaction event;
 * - `[思考过程]` → thinking card, `[最终答案]` → assistant answer,
 *   plain `assistant` rows → thinking cards (display_only thoughts).
 */
import type { Message } from '../components/session/SessionManager'

/** Mirrors the backend REACTION_EMOJI table (agentnexus/tools/user_reaction.py). */
const REACTION_EMOJI: Record<string, string> = {
  like: '👍',
  dislike: '👎',
  love: '🤩',
  sad: '😔',
  thinking: '🤔',
  speechless: '😑',
  surprised: '😲',
  sleepy: '🥱',
}

const REACTION_TOOL = 'express_reaction'

const cleanToolContent = (content: string): { name: string; display: string } => {
  const actionMatch = content.match(/^Action:\s*(\w+)\[/)
  const name = actionMatch ? actionMatch[1] : 'tool'
  const obsIdx = content.indexOf('\nObservation:')
  let display = obsIdx >= 0 ? content.slice(obsIdx + 13).trim() : content
  if (display.length > 500) display = display.slice(0, 500) + '\n...(truncated)'
  return { name, display }
}

/** Parse express_reaction arguments out of a journal tool row, if present. */
function parseReactionTool(content: string): { emoji: string; comment: string } | null {
  const argsMatch = content.match(/^Action:\s*express_reaction\[(\{.*\})\]/)
  if (!argsMatch) return null
  try {
    const args = JSON.parse(argsMatch[1]) as { reaction?: string; comment?: string }
    if (!args.reaction) return null
    return { emoji: REACTION_EMOJI[args.reaction] || '', comment: args.comment || '' }
  } catch {
    return null
  }
}

export function transformHistoryMessages(
  stm: Array<{ role: string; content: string; ts?: number }>,
): Message[] {
  const transformed: Message[] = []
  let idx = 0
  let pendingTools: Message[] = []

  const flushPendingTools = () => {
    for (const t of pendingTools) { t.id = `h-${idx++}`; transformed.push(t) }
    pendingTools = []
  }

  for (const m of stm) {
    const role = m.role
    const content = (m.content || '').trim()
    const ts = new Date(m.ts || Date.now())

    // Blank rows (e.g. empty assistant appends) never render.
    if (!content) continue
    if (role === 'system' && content.startsWith('[上下文已裁剪]')) continue
    if (role === 'system' && content.startsWith('[恢复文件]')) continue

    if (role === 'system' && content.startsWith('[思考过程]')) {
      const reasoning = content.replace(/^\[思考过程\]\s*/, '').trim()
      if (reasoning) {
        flushPendingTools()
        transformed.push({ id: `h-${idx++}`, role: 'system', content: reasoning, timestamp: ts })
      }
      continue
    }

    if (role === 'system' && content.startsWith('[最终答案]')) {
      const answer = content.replace(/^\[最终答案\]\s*/, '').trim()
      if (answer) { flushPendingTools(); transformed.push({ id: `h-${idx++}`, role: 'assistant', content: answer, timestamp: ts }) }
      continue
    }

    if (role === 'system' && content.startsWith('[会话摘要]')) {
      const summary = content.replace(/^\[会话摘要\]\s*/, '').trim()
      if (summary) {
        flushPendingTools()
        const display = summary.length > 300 ? summary.slice(0, 300) + '…' : summary
        transformed.push({ id: `h-${idx++}`, role: 'system', content: `[Context compacted] ${display}`, timestamp: ts })
      }
      continue
    }

    if (role === 'user') {
      flushPendingTools()
      transformed.push({ id: `h-${idx++}`, role: 'user', content, timestamp: ts })
      continue
    }

    if (role === 'tool') {
      // express_reaction is rewritten server-side on the live path
      // (tool_start → user_reaction event, no tool card). Mirror that here:
      // attach the emoji under the user message instead of showing a card.
      if (content.startsWith(`Action: ${REACTION_TOOL}[`)) {
        const reaction = parseReactionTool(content)
        if (reaction) {
          for (let i = transformed.length - 1; i >= 0; i--) {
            if (transformed[i].role === 'user') {
              transformed[i] = { ...transformed[i], reaction }
              break
            }
          }
        }
        continue
      }
      const { name, display } = cleanToolContent(content)
      pendingTools.push({ id: '', role: 'tool', content: display, toolName: name, toolStatus: 'done', timestamp: ts })
      continue
    }

    // Plain assistant rows are display_only thoughts — render as thinking cards.
    if (role === 'assistant') { flushPendingTools(); transformed.push({ id: `h-${idx++}`, role: 'system', content, timestamp: ts }); continue }

    if (role === 'system') { flushPendingTools(); transformed.push({ id: `h-${idx++}`, role: 'system', content, timestamp: ts }) }
  }

  flushPendingTools()
  return transformed
}
