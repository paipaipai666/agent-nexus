import { describe, it, expect } from 'vitest'
import {
  toolArgSummary, formatDuration, toolBodyKind,
  parseDiffLines, parseSearchHits, shellCommandFromArgs, toolBodyText, toolPathArg,
} from '../utils/toolDisplay'

describe('toolArgSummary', () => {
  it('prefers path-like keys', () => {
    expect(toolArgSummary({ toolArgs: { file_path: 'src/a.ts', content: 'x' } })).toBe('src/a.ts')
  })
  it('falls back to query / command', () => {
    expect(toolArgSummary({ toolArgs: { query: 'hook rules' } })).toBe('hook rules')
    expect(toolArgSummary({ toolArgs: { command: 'npm test' } })).toBe('npm test')
  })
  it('collapses whitespace and truncates long values', () => {
    const s = toolArgSummary({ toolArgs: { query: 'a\n\n  b  ' } })
    expect(s).toBe('a b')
    const long = toolArgSummary({ toolArgs: { command: 'x'.repeat(100) } })
    expect(long.length).toBeLessThanOrEqual(64)
    expect(long.endsWith('…')).toBe(true)
  })
  it('returns empty when no usable args', () => {
    expect(toolArgSummary({ toolArgs: {} })).toBe('')
    expect(toolArgSummary({})).toBe('')
  })
})

describe('formatDuration', () => {
  it('formats ms / s / m', () => {
    expect(formatDuration(48)).toBe('48ms')
    expect(formatDuration(1200)).toBe('1.2s')
    expect(formatDuration(2100)).toBe('2.1s')
    expect(formatDuration(15000)).toBe('15s')
    expect(formatDuration(125000)).toBe('2m5s')
    expect(formatDuration(undefined)).toBe('')
    expect(formatDuration(-1)).toBe('')
  })
})

describe('toolBodyKind', () => {
  it('maps tool families', () => {
    expect(toolBodyKind('file_write')).toBe('diff')
    expect(toolBodyKind('shell_exec')).toBe('shell')
    expect(toolBodyKind('web_search')).toBe('search')
    expect(toolBodyKind('file_read')).toBe('file')
    expect(toolBodyKind('todo_add')).toBe('plain')
    expect(toolBodyKind(undefined)).toBe('plain')
  })
})

describe('parseDiffLines', () => {
  it('parses unified-diff-like bodies', () => {
    const lines = parseDiffLines('@@ -1,2 +1,2 @@\n-old\n+new\n ctx')
    expect(lines?.map(l => l.kind)).toEqual(['hunk', 'del', 'add', 'ctx'])
  })
  it('rejects non-diff text even if it starts with +', () => {
    expect(parseDiffLines('+ just one plus line')).toBeNull()
    expect(parseDiffLines('hello')).toBeNull()
  })
})

describe('parseSearchHits', () => {
  it('parses condensed web_search output', () => {
    const hits = parseSearchHits(
      '[1] Title A (today) [相关度: 9.0]\nURL: https://a.example\nSnippet body\n\n[2] Title B\nURL: https://b.example',
    )
    expect(hits).toHaveLength(2)
    expect(hits![0].title).toContain('Title A')
    expect(hits![0].url).toBe('https://a.example')
    expect(hits![0].snippet).toBe('Snippet body')
    expect(hits![1].title).toContain('Title B')
  })
  it('returns null when no [N] entries', () => {
    expect(parseSearchHits('just text')).toBeNull()
  })
})

describe('shellCommandFromArgs / toolPathArg / toolBodyText', () => {
  it('extracts command and path', () => {
    expect(shellCommandFromArgs({ command: 'ls -la' })).toBe('ls -la')
    expect(shellCommandFromArgs({})).toBe('')
    expect(toolPathArg({ file_path: 'a/b.ts', other: 1 })).toBe('a/b.ts')
  })
  it('prefers toolResult over content', () => {
    expect(toolBodyText({ toolResult: 'R', content: 'C' })).toBe('R')
    expect(toolBodyText({ content: 'C' })).toBe('C')
    expect(toolBodyText({})).toBe('')
  })
})
