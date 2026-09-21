/**
 * Tests for the history message transform (desktop ChatPage history view).
 *
 * Locks the three defects reported against restored sessions:
 * 1. Assistant entries missing from the journal after restart-boundary turns
 *    (backend fix) — transform must map [思考过程]/[最终答案]/assistant rows
 *    to the same bubbles the live WS path produces.
 * 2. Empty assistant journal rows rendered as blank thinking cards.
 * 3. express_reaction tool rows rendered as tool cards — live path rewrites
 *    them to user_reaction (emoji under the user message); history must match.
 */
import { describe, it, expect } from 'vitest'
import { transformHistoryMessages } from '../utils/historyTransform'

const T = 1789980000

const stm = (rows: Array<[string, string]>) =>
  rows.map(([role, content], i) => ({ role, content, ts: T + i }))

describe('transformHistoryMessages', () => {
  it('maps a complete turn to the same shape the live path renders', () => {
    const out = transformHistoryMessages(stm([
      ['user', 'Do you like me?'],
      ['system', '[思考过程] 这是用户的第三个问题'],
      ['system', '[最终答案] 我没有情感，但我会认真对待你的每个问题'],
    ]))
    expect(out.map(m => m.role)).toEqual(['user', 'system', 'assistant'])
    expect(out[1].content).toBe('这是用户的第三个问题')
    expect(out[2].content).toBe('我没有情感，但我会认真对待你的每个问题')
  })

  it('drops blank journal rows instead of rendering empty thinking cards', () => {
    const out = transformHistoryMessages(stm([
      ['user', '用表情包工具来回复我的这个问题'],
      ['assistant', ''],
      ['assistant', '   '],
      ['assistant', '总结性思考'],
      ['system', '[最终答案] 当然喜欢！'],
    ]))
    expect(out.map(m => m.role)).toEqual(['user', 'system', 'assistant'])
    expect(out.every(m => m.content.trim().length > 0)).toBe(true)
  })

  it('never renders an express_reaction tool card — emoji attaches to the user message', () => {
    const out = transformHistoryMessages(stm([
      ['user', 'Do you like me?'],
      ['assistant', '想表达 👍'],
      ['tool', 'Action: express_reaction[{"reaction": "like", "comment": "当然喜欢"}]\nObservation: [express_reaction] 已表达 👍'],
      ['system', '[最终答案] 当然喜欢！'],
    ]))
    expect(out.some(m => m.role === 'tool')).toBe(false)
    const user = out.find(m => m.role === 'user')
    expect(user?.reaction?.emoji).toBe('👍')
    expect(user?.reaction?.comment).toBe('当然喜欢')
  })

  it('renders normal tool rows as done tool cards between thinking and answer', () => {
    const out = transformHistoryMessages(stm([
      ['user', '查一下天气'],
      ['system', '[思考过程] 需要工具'],
      ['tool', 'Action: web_search[{"q": "天气"}]\nObservation: 晴'],
      ['system', '[最终答案] 今天晴'],
    ]))
    expect(out.map(m => m.role)).toEqual(['user', 'system', 'tool', 'assistant'])
    expect(out[2].toolName).toBe('web_search')
    expect(out[2].toolStatus).toBe('done')
  })

  it('plain assistant rows become thinking cards (display_only thoughts)', () => {
    const out = transformHistoryMessages(stm([
      ['user', 'hello'],
      ['assistant', 'Thought: 用户只是打招呼'],
    ]))
    expect(out.map(m => m.role)).toEqual(['user', 'system'])
    expect(out[1].content).toContain('用户只是打招呼')
  })

  it('keeps multiple turns in order with per-turn tool grouping', () => {
    const out = transformHistoryMessages(stm([
      ['user', 'q1'],
      ['system', '[最终答案] a1'],
      ['user', 'q2'],
      ['system', '[思考过程] t2'],
      ['tool', 'Action: file_read[{"p": "x"}]\nObservation: ok'],
      ['system', '[最终答案] a2'],
    ]))
    expect(out.map(m => [m.role, m.content.slice(0, 6)])).toEqual([
      ['user', 'q1'],
      ['assistant', 'a1'],
      ['user', 'q2'],
      ['system', 't2'],
      ['tool', 'ok'],
      ['assistant', 'a2'],
    ])
  })
})
