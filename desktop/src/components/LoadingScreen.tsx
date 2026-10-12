import { useEffect, useState } from 'react'

const HEALTH_CHECK_INTERVAL_MS = 1000
const MAX_RETRIES = 30

interface LoadingScreenProps {
  onReady: () => void
  backendPort?: number
}

async function checkHealth(port: number): Promise<boolean> {
  try {
    const res = await fetch(`http://127.0.0.1:${port}/health`, {
      signal: AbortSignal.timeout(2000),
    })
    return res.ok
  } catch {
    return false
  }
}

export default function LoadingScreen({ onReady, backendPort = 18765 }: LoadingScreenProps) {
  const [retries, setRetries] = useState(0)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let cancelled = false
    let timeoutId: ReturnType<typeof setTimeout>

    const poll = async () => {
      if (cancelled) return
      const healthy = await checkHealth(backendPort)
      if (cancelled) return
      if (healthy) {
        onReady()
      } else {
        setRetries((prev) => {
          const next = prev + 1
          if (next >= MAX_RETRIES) {
            setError('Backend failed to start. Ensure AgentNexus is running: nexus serve')
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
            Connecting to backend... ({retries})
          </p>
        </>
      )}
      {error && (
        <p className="startup-error" role="alert">{error}</p>
      )}
      {window.electronAPI && (
        <button className="startup-close" type="button" aria-label="Close AgentNexus" onClick={() => window.electronAPI?.close()}>
          ×
        </button>
      )}
    </div>
  )
}
