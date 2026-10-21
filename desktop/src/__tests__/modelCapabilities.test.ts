import { expect, it } from 'vitest'
import { parseModelCapabilities } from '../services/modelCapabilities'

const snapshot = {
  model: 'fixture', base_url: 'http://localhost/v1', source: 'unknown', discovery_status: 'probing',
  tool_calling: false, json_mode: false, json_schema: false, thinking: false, vision: false,
  parallel_tool_calls: false, max_context_tokens: 0, max_output_tokens: 32768, session_disabled: [],
}

it('keeps an unconfirmed capability snapshot distinguishable from unsupported capabilities', () => {
  const value = parseModelCapabilities(snapshot)
  expect(value.source).toBe('unknown')
  expect(value.discovery_status).toBe('probing')
  expect(value.max_context_tokens).toBe(0)
})

it.each([
  { tool_calling: 'false' },
  { source: 'probe' },
  { discovery_status: 'ready' },
  { max_context_tokens: Number.NaN },
  { session_disabled: [false] },
])('rejects malformed remote capability data %j', invalid => {
  expect(() => parseModelCapabilities({ ...snapshot, ...invalid })).toThrow()
})
