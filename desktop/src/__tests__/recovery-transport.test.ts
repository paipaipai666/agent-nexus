import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { wsPool } from '../services/ws'

class Socket {
  static OPEN = 1
  static CONNECTING = 0
  static CLOSED = 3
  static instances: Socket[] = []
  readyState = Socket.OPEN
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  send = vi.fn()
  close() { this.readyState = Socket.CLOSED }
  constructor(public url: string) { Socket.instances.push(this) }
  receive(data: unknown) { this.onmessage?.({ data: JSON.stringify(data) }) }
  drop() { this.close(); this.onclose?.() }
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.stubGlobal('WebSocket', Socket)
  Socket.instances = []
})

afterEach(() => {
  wsPool.disconnectAll()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

it('reconnects from the latest run cursor after two runs', () => {
  wsPool.connect('recovery')
  const socket = Socket.instances[0]
  socket.receive({ type: 'run_started', run_id: 'first', seq: 0 })
  socket.receive({ type: 'done', run_id: 'first', seq: 70 })
  socket.receive({ type: 'run_started', run_id: 'second', seq: 0 })
  socket.receive({ type: 'thinking', run_id: 'second', seq: 5 })
  socket.drop()
  vi.advanceTimersByTime(3000)
  const url = new URL(Socket.instances[1].url)
  expect(url.searchParams.get('resumeEventsFrom')).toBe('5')
  expect(url.searchParams.get('resumeRunId')).toBe('second')
})

it('ignores late events from a previous run and reports transport errors separately', () => {
  wsPool.connect('recovery')
  const answers: string[] = []
  const connectionErrors: string[] = []
  wsPool.on('recovery', 'answer', data => answers.push(data.content))
  wsPool.on('recovery', 'connection_error', data => connectionErrors.push(data.message))
  const socket = Socket.instances[0]
  socket.receive({ type: 'run_started', run_id: 'first', seq: 0 })
  socket.receive({ type: 'run_started', run_id: 'second', seq: 0 })
  socket.receive({ type: 'answer', run_id: 'first', content: 'stale', seq: 90 })
  socket.receive({ type: 'answer', run_id: 'second', content: 'current', seq: 1 })
  socket.onerror?.()
  expect(answers).toEqual(['current'])
  expect(connectionErrors).toEqual(['连接失败，正在尝试重新连接'])
})
