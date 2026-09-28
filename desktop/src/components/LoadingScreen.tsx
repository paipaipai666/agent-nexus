import { useEffect, useState, useCallback } from 'react'
import { ThinkingOrb } from 'thinking-orbs'
import { usePrefersReducedMotion } from '../utils/effects'

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
  const reducedMotion = usePrefersReducedMotion()

  const stableOnReady = useCallback(onReady, [])

  useEffect(() => {
    let cancelled = false
    let timeoutId: ReturnType<typeof setTimeout>

    const poll = async () => {
      if (cancelled) return
      const healthy = await checkHealth(backendPort)
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
      data-theme="dark"
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
      <div
        className="metal-heading"
        style={{ fontSize: '2rem', fontWeight: 700, letterSpacing: '-0.02em' }}
      >
        AgentNexus
      </div>
      {!error && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <ThinkingOrb
            state="connecting"
            size={32}
            theme="dark"
            paused={reducedMotion}
            aria-label="Connecting to backend"
          />
          <div style={{ fontSize: '0.875rem', color: '#888' }}>
            Connecting to backend... ({retries})
          </div>
        </div>
      )}
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
    </div>
  )
}
