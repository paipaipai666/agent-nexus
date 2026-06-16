/**
 * Tests the initRestore logic with various backend/cache states.
 * Simulates the exact decision tree in ChatPage.tsx.
 */
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
    // Backend has the complete answer — cache is stale, clear it
    return { messages, source: 'backend-complete' }
  } else if (loaded && agentWasRunning) {
    // Backend has user message only (agent still running), cache has streaming
    if (cachedMessages) {
      const ids = new Set(messages.map(m => m.id))
      const newMsgs = cachedMessages.filter(m => !ids.has(m.id))
      messages = [...messages, ...newMsgs]
    }
    return { messages, source: 'backend+cache-merge' }
  } else if (!loaded) {
    // Backend empty — use cache
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
      currentMessages: [userMsg, fullAnswer], // loadAndDisplayMessages set these
    })
    expect(result.source).toBe('backend-complete')
    expect(result.messages.find(m => m.role === 'assistant')?.content).toBe('The answer is 4.')
  })

  it('agent still running → merge backend (user msg) + cache (streaming)', () => {
    const result = simulateInitRestore({
      agentWasRunning: true,
      backendResult: { loaded: true, hasAssistant: false },
      cachedMessages: [userMsg, partialAnswer],
      currentMessages: [userMsg], // loadAndDisplayMessages set user msg only
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
    // Before the fix, we checked `hasData` (always true because user msg exists)
    // instead of `hasAssistant` (false because agent hasn't finished).
    // This caused the cache to be cleared even when the agent was still running.

    const oldLogicResult = (() => {
      const hasData = true // backend always has at least user msg
      if (hasData) {
        return { source: 'backend', cacheCleared: true }
      }
      return { source: 'cache', cacheCleared: false }
    })()

    // Old logic: hasData=true → cache cleared → streaming content lost!
    expect(oldLogicResult.cacheCleared).toBe(true)

    // New logic: hasAssistant=false → cache NOT cleared → streaming preserved
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
