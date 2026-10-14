import { expect, it } from 'vitest'
import { mergeMessages, parseCommunication, parseSubagentEvent, mergeEvents } from '../services/subagentWorkspace'
import { useCollaborationStore } from '../services/collaborationStore'

const message = { id: 1, session_id: 's', run_id: 'p', request_id: 'r', subagent_id: 'a', child_run_id: 'c', sender: 'a', recipient: 'parent', kind: 'question', content: 'Format?', question_id: '', delivery: 'queued', step_id: 0, question_state: 'open', reason: '', revision: 1, ts: 1 }

it('keeps newer receipts when a delayed history response arrives', () => {
  const applied = parseCommunication({ ...message, delivery: 'applied', revision: 2, step_id: 3 })
  const history = parseCommunication(message)
  expect(mergeMessages([applied], [history])).toEqual([applied])
  expect(mergeMessages([history], [applied, applied])).toEqual([applied])
})

it('merges realtime and replay by stable event identity and original order', () => {
  const first = parseSubagentEvent({ subagent_id: 'a', run_id: 'p', seq: 1, kind: 'started', task: 'Read', role: 'explorer', name: 'Reader', status: 'thinking' })
  const finished = parseSubagentEvent({ subagent_id: 'a', run_id: 'p', seq: 3, kind: 'finished', status: 'completed', summary: 'Done' })
  expect(mergeEvents([finished], [first, finished]).map(e => e.kind)).toEqual(['started', 'finished'])
})

it('rejects malformed communication at the boundary', () => {
  expect(() => parseCommunication({ ...message, kind: 'unknown' })).toThrow()
  expect(() => parseCommunication({ ...message, id: '1' })).toThrow()
  expect(() => parseSubagentEvent({ kind: 'started' })).toThrow()
})

it('REST corrects a missed completion and older events cannot reverse it', () => {
  const store = useCollaborationStore.getState()
  store.ingest('correction', [parseSubagentEvent({ subagent_id: 'a', run_id: 'p', seq: 1, ts: 1, kind: 'started', status: 'thinking', task: 'Read', name: 'Reader' })])
  store.reconcile('correction', [{ subagent_id: 'a', name: 'Reader', role: 'explorer', task: 'Read', status: 'completed', current_tool: '', started_at: 1, finished_at: 3, updated_at: 3, steps_used: 2, error: '' }])
  store.ingest('correction', [parseSubagentEvent({ subagent_id: 'a', run_id: 'p', seq: 2, ts: 2, kind: 'thinking', status: 'thinking' })])
  expect(useCollaborationStore.getState().sessions.correction.agents.get('a')?.status).toBe('completed')
})

it('coalesces streaming output and ignores duplicate websocket chunks', () => {
  const first = parseSubagentEvent({ subagent_id: 'a', run_id: 'p', child_run_id: 'c', seq: 2, step_id: 1, kind: 'token', content: 'Hello ' })
  const next = parseSubagentEvent({ subagent_id: 'a', run_id: 'p', child_run_id: 'c', seq: 3, step_id: 1, kind: 'token', content: 'world' })
  const merged = mergeEvents([first], [first, next, next])
  expect(merged).toHaveLength(1)
  expect(merged[0].kind === 'token' ? merged[0].content : '').toBe('Hello world')
})
