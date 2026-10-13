import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import SessionManager, { useSession } from '../components/session/SessionManager'
import { wsPool } from '../services/ws'
import { api } from '../services/api'

class Socket {
  static OPEN = 1
  static CONNECTING = 0
  static CLOSED = 3
  static instances: Socket[] = []
  readyState = Socket.CONNECTING
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  send = vi.fn()
  close() { this.readyState = Socket.CLOSED }
  constructor(public url: string) { Socket.instances.push(this) }
  receive(data: unknown) { this.onmessage?.({ data: JSON.stringify(data) }) }
  open() { this.readyState = Socket.OPEN; this.onopen?.() }
}

let latest: ReturnType<typeof useSession>
function Probe() {
  const session = useSession()
  latest = session
  return <>
    <button onClick={() => session.setSessionId('pending-session')}>activate</button>
    <button onClick={() => session.sendMessage('original prompt', [{ path: 'D:/note.txt', name: 'note.txt', size: 7, mime: 'text/plain' }])}>send</button>
    <button onClick={session.cancelRun}>stop</button>
    <button onClick={() => session.confirmToolCall(true)}>approve</button>
  </>
}

beforeEach(() => {
  localStorage.clear()
  vi.useFakeTimers()
  Socket.instances = []
  vi.stubGlobal('WebSocket', Socket)
})
afterEach(() => {
  cleanup()
  wsPool.disconnectAll()
  vi.unstubAllGlobals()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

it('does not claim execution before a disconnected send is acknowledged', async () => {
  render(<SessionManager><Probe /></SessionManager>)
  await act(async () => { screen.getByText('activate').click() })
  await act(async () => { screen.getByText('send').click() })
  expect(latest.isRunning).toBe(false)
  await act(async () => { vi.advanceTimersByTime(15000) })
  await act(async () => { Socket.instances[0].open(); vi.advanceTimersByTime(100) })
  expect(Socket.instances[0].send.mock.calls).toEqual([])
})

it('retains the interrupted task outcome and unsaved answer through a remount', async () => {
  render(<SessionManager><Probe /></SessionManager>)
  await act(async () => { screen.getByText('activate').click(); })
  await act(async () => { Socket.instances[0].open(); screen.getByText('send').click() })
  await act(async () => {
    Socket.instances[0].receive({ type: 'run_started', run_id: 'partial-run', seq: 0 })
    Socket.instances[0].receive({ type: 'error', run_id: 'partial-run', session_id: 'pending-session', question: 'original prompt', status: 'interrupted', answer: 'retained output', reason: 'client_disconnected', journal: ['tool done: file_write -> saved'], persisted: false, execution_active: false, seq: 1 })
    Socket.instances[0].receive({ type: 'save_failed', run_id: 'partial-run', status: 'interrupted', persisted: false, save_error: 'disk full', seq: 2 })
  })
  expect(latest.recovery?.reason).toBe('client_disconnected')
  expect(latest.recovery?.answer).toBe('retained output')
  cleanup()
  render(<SessionManager><Probe /></SessionManager>)
  await act(async () => { screen.getByText('activate').click() })
  expect(latest.recovery?.answer).toBe('retained output')
  expect(latest.recovery?.save_error).toBe('disk full')
})

it('cancels an unsent prompt without waiting for a run id or sending after reconnect', async () => {
  render(<SessionManager><Probe /></SessionManager>)
  await act(async () => { screen.getByText('activate').click() })
  await act(async () => { screen.getByText('send').click() })
  await act(async () => { screen.getByText('stop').click() })
  expect(latest.pendingSubmission).toBe(null)
  await act(async () => { Socket.instances[0].open(); vi.advanceTimersByTime(20000) })
  expect(Socket.instances[0].send.mock.calls).toEqual([])
  expect(latest.messages[0].content).toBe('original prompt')
})

it('keeps approval visible until its matching acknowledgement and exposes expiry', async () => {
  render(<SessionManager><Probe /></SessionManager>)
  await act(async () => { screen.getByText('activate').click() })
  await act(async () => { Socket.instances[0].open() })
  await act(async () => {
    Socket.instances[0].receive({ type: 'run_started', run_id: 'confirm-run', seq: 0 })
    Socket.instances[0].receive({ type: 'confirm_request', run_id: 'confirm-run', confirmation_id: 'confirm-one', summary: 'write file', expires_at: Date.now() + 300000 })
  })
  await act(async () => { screen.getByText('approve').click() })
  expect(latest.confirmRequest?.summary).toBe('write file')
  await act(async () => { Socket.instances[0].receive({ type: 'confirm_result', run_id: 'confirm-run', confirmation_id: 'confirm-one', status: 'accepted' }) })
  expect(latest.confirmRequest).toBe(null)
  await act(async () => {
    Socket.instances[0].receive({ type: 'confirm_request', run_id: 'confirm-run', confirmation_id: 'confirm-two', summary: 'second write', expires_at: Date.now() + 300000 })
    Socket.instances[0].receive({ type: 'confirm_timeout', run_id: 'confirm-run', confirmation_id: 'confirm-two' })
  })
  expect(latest.getSessionState('pending-session')?.confirmRequest?.status).toBe('timed-out')
})

it('replaces a partial streamed answer with the recovered final answer', async () => {
  const recovery = vi.spyOn(api, 'getSessionRecovery').mockResolvedValue({ recovery: null })
  render(<SessionManager><Probe /></SessionManager>)
  await act(async () => { screen.getByText('activate').click() })
  await act(async () => { Socket.instances[0].open(); screen.getByText('send').click() })
  await act(async () => {
    Socket.instances[0].receive({ type: 'run_started', run_id: 'recover-final', seq: 0 })
    Socket.instances[0].receive({ type: 'token', run_id: 'recover-final', content: 'partial', seq: 1 })
  })
  recovery.mockResolvedValue({ recovery: { run_id: 'recover-final', session_id: 'pending-session', question: 'original prompt', status: 'finished', answer: 'complete recovered answer', persisted: true, execution_active: false } })
  await act(async () => { latest.refreshRecovery(); await Promise.resolve() })
  expect(latest.messages.filter(message => message.role === 'assistant').map(message => message.content)).toEqual(['complete recovered answer'])
  expect(latest.isRunning).toBe(false)
})
