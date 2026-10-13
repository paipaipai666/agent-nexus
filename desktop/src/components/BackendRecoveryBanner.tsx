import { useEffect, useState } from 'react'
import { checkBackendHealth } from '../services/backend'
import BackendRecoveryControls from './BackendRecoveryControls'

export default function BackendRecoveryBanner() {
  const [error, setError] = useState<string | null>(null)
  const [port, setPort] = useState(18765)
  useEffect(() => {
    let cancelled = false
    let pending = false
    const unsubscribe = window.electronAPI?.onBackendError?.(message => { if (!cancelled) setError(message) })
    window.electronAPI?.getBackendStatus().then(status => { if (!cancelled && status.port) setPort(status.port) }).catch(() => {})
    const check = async () => {
      if (pending) return
      pending = true
      const ready = await checkBackendHealth(port)
      pending = false
      if (!cancelled) setError(ready ? null : '后端连接已断开。当前页面和草稿已保留，请恢复连接后核对任务状态。')
    }
    const timer = setInterval(() => void check(), 5000)
    return () => { cancelled = true; clearInterval(timer); unsubscribe?.() }
  }, [port])
  if (!error) return null
  return <div role="alert" className="px-4 py-3 text-sm space-y-2 shrink-0" style={{ background: 'var(--surface-2)', color: 'var(--fg-secondary)' }}>
    <p>{error}</p>
    <BackendRecoveryControls port={port} onRecovered={() => setError(null)} />
  </div>
}
