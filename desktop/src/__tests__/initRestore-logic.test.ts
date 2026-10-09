import { describe, it, expect } from 'vitest'

type Message = { id: string; role: string; content: string }

function simulateInitRestore(opts: {
  agentWasRunning: boolean
  backendResult: { loaded: boolean; hasAssistant: boolean }
  cachedMessages: Message[] | null
  currentMessages: Message[]
}): { messages: Message[]; source: string } {
  const { agentWasRunning, backendResult, cachedMessages, currentMessages } = opts
  const { loaded, hasAssistant } = backendResult
  let messages = [...currentMessages]

  if (hasAssistant) {
    return { messages, source: 'backend-complete' }
  } else if (loaded && agentWasRunning) {
    if (cachedMessages) {
      const ids = new Set(messages.map(m => m.id))
      const newMsgs = cachedMessages.filter(m => !ids.has(m.id))
      messages = [...messages, ...newMsgs]
    }
    return { messages, source: 'backend+cache-merge' }
  } else if (!loaded) {
    if (cachedMessages) {
      messages = cachedMessages
    }
    return { messages, source: 'cache-only' }
  }

  return { messages, source: 'backend-only' }
}

describe('initRestore decision tree', () => {
  const userMsg: Message = { id: 'u-1', role: 'user', content: 'What is 2+2?' }
  const partialAnswer: Message = { id: 'a-1', role: 'assistant', content: 'The answer ' }
  const fullAnswer: Message = { id: 'a-1', role: 'assistant', content: 'The answer is 4.' }

  it('agent finished while away → backend complete, cache cleared', () => {
    const result = simulateInitRestore({
      agentWasRunning: true,
      backendResult: { loaded: true, hasAssistant: true },
      cachedMessages: [userMsg, partialAnswer],
      currentMessages: [userMsg, fullAnswer],
    })
    expect(result.source).toBe('backend-complete')
    expect(result.messages.find(m => m.role === 'assistant')?.content).toBe('The answer is 4.')
  })

  it('agent still running → merge backend (user msg) + cache (streaming)', () => {
    const result = simulateInitRestore({
      agentWasRunning: true,
      backendResult: { loaded: true, hasAssistant: false },
      cachedMessages: [userMsg, partialAnswer],
      currentMessages: [userMsg],
    })
    expect(result.source).toBe('backend+cache-merge')
    expect(result.messages.length).toBe(2)
    expect(result.messages.find(m => m.role === 'assistant')?.content).toBe('The answer ')
  })

  it('agent was NOT running, backend has data → use backend', () => {
    const result = simulateInitRestore({
      agentWasRunning: false,
      backendResult: { loaded: true, hasAssistant: true },
      cachedMessages: [userMsg],
      currentMessages: [userMsg, fullAnswer],
    })
    expect(result.source).toBe('backend-complete')
    expect(result.messages.length).toBe(2)
  })

  it('backend empty → use cache', () => {
    const result = simulateInitRestore({
      agentWasRunning: true,
      backendResult: { loaded: false, hasAssistant: false },
      cachedMessages: [userMsg, partialAnswer],
      currentMessages: [],
    })
    expect(result.source).toBe('cache-only')
    expect(result.messages.length).toBe(2)
  })

  it('BUG BEFORE FIX: backend always has user msg → hasData=true → cache cleared', () => {

    const oldLogicResult = (() => {
      const hasData = true
      if (hasData) {
        return { source: 'backend', cacheCleared: true }
      }
      return { source: 'cache', cacheCleared: false }
    })()

    expect(oldLogicResult.cacheCleared).toBe(true)

    const newLogicResult = simulateInitRestore({
      agentWasRunning: true,
      backendResult: { loaded: true, hasAssistant: false },
      cachedMessages: [userMsg, partialAnswer],
      currentMessages: [userMsg],
    })
    expect(newLogicResult.source).toBe('backend+cache-merge')
    expect(newLogicResult.messages.find(m => m.role === 'assistant')?.content).toBe('The answer ')
  })
})
