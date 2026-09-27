import { useEffect, useState } from 'react'
import { ClipboardList } from 'lucide-react'
import { api } from '../../services/api'

interface PlanModeToggleProps {
  sessionId: string | null
}

/** Plan-mode switch in the chat input's HUD row. Reflects the per-session
 *  server-side switch; the gate itself is enforced backend-side per tool
 *  call, so toggling works even mid-run. */
export default function PlanModeToggle({ sessionId }: PlanModeToggleProps) {
  const [enabled, setEnabled] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    setEnabled(false)
    if (!sessionId) return
    api.getSession(sessionId)
      .then(d => setEnabled(!!d.plan_mode))
      .catch(() => {})
  }, [sessionId])

  const handleToggle = async () => {
    if (!sessionId || busy) return
    setBusy(true)
    try {
      const res = await api.setPlanMode(sessionId, !enabled)
      setEnabled(!!res.plan_mode)
    } catch {
      // keep previous state; transient backend errors surface on next toggle
    } finally {
      setBusy(false)
    }
  }

  return (
    <button
      onClick={handleToggle}
      disabled={!sessionId || busy}
      aria-pressed={enabled}
      className="flex items-center gap-1 px-1.5 py-0.5 rounded transition-colors hover:bg-[var(--surface-2)] disabled:opacity-40"
      style={{ color: enabled ? 'var(--accent)' : 'var(--fg-muted)' }}
      title="计划模式：开启后 agent 只能只读调研，提交计划经你批准后退出"
    >
      <ClipboardList size={10} style={{ flexShrink: 0 }} />
      <span>plan</span>
      {enabled && (
        <span className="w-1.5 h-1.5 rounded-full" style={{ background: 'var(--accent)' }} />
      )}
    </button>
  )
}
