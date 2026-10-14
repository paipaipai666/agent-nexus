import { useEffect, useState } from 'react'
import { api } from './api'
import { wsPool } from './ws'
import { emptyWorkspace, useCollaborationStore } from './collaborationStore'
import { parseSubagentEvent } from './subagentWorkspace'

export function useSubagentWorkspace(sessionId: string, subagentId: string) {
  const workspace = useCollaborationStore(state => state.sessions[sessionId] ?? emptyWorkspace)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [loaded, setLoaded] = useState(false)
  const [retry, setRetry] = useState(0)
  const [enabled, setEnabled] = useState(false)
  useEffect(() => {
    let disposed = false
    let loading = false
    let cursor = 0
    setErrors({})
    const store = useCollaborationStore.getState()
    const fail = (scope: string, error: unknown) => {
      if (!disposed) setErrors(previous => ({ ...previous, [scope]: error instanceof Error ? error.message : '请检查连接' }))
    }
    const clear = (scope: string) => setErrors(previous => {
      const next = { ...previous }
      delete next[scope]
      return next
    })
    const refresh = async () => {
      if (disposed || loading) return
      loading = true
      await Promise.allSettled([
        api.getSubagents(sessionId).then(data => {
          if (disposed) return
          store.reconcile(sessionId, data.subagents)
          setEnabled(data.collaboration_enabled === true)
          clear('metadata')
        }).catch(error => fail('metadata', error)),
        (async () => {
          let count: number
          do {
            const data = await api.getSessionEvents(sessionId, cursor, subagentId, 'trajectory')
            if (disposed) return
            const events = data.events.filter(row => row.event_type === 'subagent_event').map(row => parseSubagentEvent({ ...row.payload, run_id: row.run_id, seq: row.seq, ts: row.ts }))
            store.ingest(sessionId, events)
            if (data.last_id <= cursor) break
            cursor = data.last_id
            count = data.count
          } while (count >= 2000)
          clear('trajectory')
        })().catch(error => fail('trajectory', error)),
        (async () => {
          let after = 0
          while (!disposed) {
            const data = await api.getSubagentMessages(sessionId, subagentId, after)
            if (disposed) return
            store.receive(sessionId, subagentId, data.messages)
            if (data.count < 500 || data.last_id <= after) break
            after = data.last_id
          }
          clear('communication')
        })().catch(error => fail('communication', error)),
      ])
      if (!disposed) setLoaded(true)
      loading = false
    }
    const off = wsPool.on(sessionId, 'subagent_event', (data: unknown) => {
      if (disposed) return
      try { store.ingest(sessionId, [parseSubagentEvent(data)]) } catch (error) { fail('live', error) }
    })
    const reconnect = wsPool.on(sessionId, 'connected', () => { void refresh() })
    void refresh()
    const interval = setInterval(() => { void refresh() }, 2000)
    return () => { disposed = true; off(); reconnect(); clearInterval(interval) }
  }, [sessionId, subagentId, retry])
  return { workspace, errors, loaded, enabled, reload: () => setRetry(value => value + 1) }
}
