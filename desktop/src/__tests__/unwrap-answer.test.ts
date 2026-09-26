import { describe, it, expect } from 'vitest'
import { unwrapStreamingAnswer } from '../utils/unwrapAnswer'

describe('unwrapStreamingAnswer', () => {
  it('passes through plain text', () => {
    expect(unwrapStreamingAnswer('这是普通回答')).toBe('这是普通回答')
    expect(unwrapStreamingAnswer('')).toBe('')
  })

  it('unwraps a complete answer envelope', () => {
    expect(unwrapStreamingAnswer('{"answer":"完整答案"}')).toBe('完整答案')
    expect(unwrapStreamingAnswer('{"answer": "多行\\n答案"}')).toBe('多行\n答案')
  })

  it('unwraps a partial stream without showing the JSON shell', () => {
    const partial = '{"answer":"报告第一章写到一半，\\n接下来是'
    const out = unwrapStreamingAnswer(partial)
    expect(out).not.toContain('{"answer"')
    expect(out).toContain('报告第一章写到一半')
    expect(out).toContain('\n')
  })

  it('hides the prefix while still typing the key', () => {
    expect(unwrapStreamingAnswer('{"ans')).toBe('')
    expect(unwrapStreamingAnswer('{"answer"')).toBe('')
  })

  it('does not mangle other JSON objects', () => {
    expect(unwrapStreamingAnswer('{"foo":1}')).toBe('{"foo":1}')
  })
})
