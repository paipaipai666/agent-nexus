import { useEffect, useState } from 'react'
import { checkBackendHealth } from '../services/backend'
import BackendRecoveryControls from './BackendRecoveryControls'

const HEALTH_CHECK_INTERVAL_MS = 1000
const MAX_RETRIES = 30

interface LoadingScreenProps {
  onReady: () => void
  backendPort?: number
}

export default function LoadingScreen({ onReady, backendPort = 18765 }: LoadingScreenProps) {
  const [retries, setRetries] = useState(0)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let cancelled = false
    let timeoutId: ReturnType<typeof setTimeout>

    const poll = async () => {
      if (cancelled) return
      const healthy = await checkBackendHealth(backendPort)
      if (cancelled) return
      if (healthy) {
        onReady()
      } else {
        setRetries((prev) => {
          const next = prev + 1
          if (next >= MAX_RETRIES) {
            setError('后端尚未启动，可以重试连接或重启后端。')
          }
          return next
        })
        if (!cancelled) {
          timeoutId = setTimeout(poll, HEALTH_CHECK_INTERVAL_MS)
        }
      }
    }

    poll()

    return () => {
      cancelled = true
      clearTimeout(timeoutId)
    }
  }, [onReady, backendPort])
  useEffect(() => window.electronAPI?.onBackendError?.(setError), [])

  return (
    <div className="startup-screen" aria-label="Starting AgentNexus">
      <div className="startup-brand">
        <img className="startup-mark" src={`${import.meta.env.BASE_URL}agentnexus-mark.png`} width={72} height={72} alt="AgentNexus logo" />
        <h1 className="startup-title">AgentNexus</h1>
      </div>
      {!error && (
        <>
          <div className="startup-spinner" aria-hidden="true" />
          <p className="startup-status" role="status">
            正在连接后端… ({retries})
          </p>
        </>
      )}
      {error && (
        <div className="space-y-3 text-center"><p className="startup-error" role="alert">{error}</p><BackendRecoveryControls port={backendPort} onRecovered={onReady} /></div>
      )}
      {window.electronAPI && (
        <button className="startup-close" type="button" aria-label="Close AgentNexus" onClick={() => window.electronAPI?.close()}>
          ×
        </button>
      )}
    </div>
  )
}
