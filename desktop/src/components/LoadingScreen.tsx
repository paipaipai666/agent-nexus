import { useEffect, useState, useCallback } from 'react'

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

  const stableOnReady = useCallback(onReady, [])

  useEffect(() => {
    let cancelled = false
    let timeoutId: ReturnType<typeof setTimeout>

    const poll = async () => {
      if (cancelled) return
      const healthy = await checkHealth(backendPort)
      if (cancelled) return

      if (healthy) {
        stableOnReady()
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
  }, [stableOnReady, backendPort])

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100vh',
        background: 'var(--color-bg, #111318)',
        color: 'var(--color-text, #e0e0e0)',
        fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
        gap: '1.5rem',
      }}
    >
      <div style={{ fontSize: '2rem', fontWeight: 700, letterSpacing: '-0.02em' }}>
        AgentNexus
      </div>
      {!error && (
        <div
          style={{
            width: 40,
            height: 40,
            border: '3px solid rgba(255,255,255,0.1)',
            borderTopColor: '#6366f1',
            borderRadius: '50%',
            animation: 'spin 0.8s linear infinite',
          }}
        />
      )}
      <div style={{ fontSize: '0.875rem', color: '#888' }}>
        {error ? 'Connection failed' : `Connecting to backend... (${retries})`}
      </div>
      {error && (
        <div
          style={{
            fontSize: '0.875rem',
            color: '#ef4444',
            maxWidth: 400,
            textAlign: 'center',
          }}
        >
          {error}
        </div>
      )}
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  )
}
