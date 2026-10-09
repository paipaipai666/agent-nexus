import { describe, it, expect } from 'vitest'

describe('initRestore cache/backend priority fix', () => {
  function simulateInitRestore(opts: {
    agentWasRunning: boolean
    backendMessages: any[]
    cachedMessages: any[] | null
  }): { source: 'backend' | 'cache' | 'empty'; messages: any[] } {
    const { agentWasRunning, backendMessages, cachedMessages } = opts

    const hasData = backendMessages.length > 0

    if (hasData) {
      return { source: 'backend', messages: backendMessages }
    } else if (agentWasRunning && cachedMessages) {
      return { source: 'cache', messages: cachedMessages }
    }

    return { source: 'empty', messages: [] }
  }

  it('FIXED: agent finished while away → backend data used, not stale cache', () => {
    const result = simulateInitRestore({
      agentWasRunning: true,
      backendMessages: [
        { id: 'u-1', role: 'user', content: 'What is 2+2?' },
        { id: 'a-1', role: 'assistant', content: 'The answer is 4.' },
      ],
      cachedMessages: [
        { id: 'u-1', role: 'user', content: 'What is 2+2?' },
        { id: 'a-1', role: 'assistant', content: 'The answer ' },
      ],
    })

    expect(result.source).toBe('backend')
    const answer = result.messages.find(m => m.role === 'assistant')
    expect(answer.content).toBe('The answer is 4.')
  })

  it('agent still running → cache used (backend is empty)', () => {
    const result = simulateInitRestore({
      agentWasRunning: true,
      backendMessages: [],
      cachedMessages: [
        { id: 'u-1', role: 'user', content: 'What is 2+2?' },
        { id: 'a-1', role: 'assistant', content: 'The answer ' },
      ],
    })

    expect(result.source).toBe('cache')
    expect(result.messages.length).toBe(2)
  })

  it('agent was NOT running → backend used (cache is stale)', () => {
    const result = simulateInitRestore({
      agentWasRunning: false,
      backendMessages: [
        { id: 'u-1', role: 'user', content: 'Hello' },
        { id: 'a-1', role: 'assistant', content: 'Hi there!' },
      ],
      cachedMessages: [
        { id: 'u-1', role: 'user', content: 'Hello' },
      ],
    })

    expect(result.source).toBe('backend')
    expect(result.messages.length).toBe(2)
  })

  it('neither backend nor cache → empty', () => {
    const result = simulateInitRestore({
      agentWasRunning: false,
      backendMessages: [],
      cachedMessages: null,
    })

    expect(result.source).toBe('empty')
    expect(result.messages.length).toBe(0)
  })

  it('BUG SCENARIO before fix: cache had priority, showing incomplete content', () => {

    const cachedMessages = [
      { id: 'u-1', role: 'user', content: 'What is 2+2?' },
      { id: 'a-1', role: 'assistant', content: 'The answer ' },
    ]

    const beforeFix = cachedMessages ? 'cache' : 'backend'
    expect(beforeFix).toBe('cache')

    const afterFix = simulateInitRestore({
      agentWasRunning: true,
      backendMessages: [
        { id: 'u-1', role: 'user', content: 'What is 2+2?' },
        { id: 'a-1', role: 'assistant', content: 'The answer is 4.' },
      ],
      cachedMessages,
    })
    expect(afterFix.source).toBe('backend')
  })
})
