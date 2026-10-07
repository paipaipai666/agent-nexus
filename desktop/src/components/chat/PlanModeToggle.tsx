import { ClipboardList } from 'lucide-react'
import { api } from '../../services/api'
import { planModeArm } from '../../services/planModeArm'
import { useSessionOverride, type SessionOverrideConfig } from '../../hooks/useSessionOverride'
import { useSession } from '../session/SessionManager'

const PLAN_MODE_OVERRIDE: SessionOverrideConfig<boolean, boolean> = {
  arm: planModeArm,
  initial: false,
  toApi: (v: boolean) => v,
  fromApi: (v: boolean | null | undefined) => !!v,
  getServerValue: (sessionId) => api.getSession(sessionId).then(d => d.plan_mode),
  setServerValue: (sessionId, v) => api.setPlanMode(sessionId, v).then(d => d.plan_mode),
}

/** Plan-mode switch in the chat input's HUD row. Reflects the per-session
 *  server-side switch; the gate itself is enforced backend-side per tool
 *  call, so toggling works even mid-run.
 *
 *  Pre-session window (new chat defers session creation until the first
 *  message): the toggle stays clickable and stores an "armed" intent, which
 *  the session-creation path applies before the first message is sent. */
export default function PlanModeToggle() {
  const { sessionId } = useSession()
  const { value: enabled, busy, commit } = useSessionOverride(sessionId, PLAN_MODE_OVERRIDE)

  const handleToggle = () => {
    void commit(!enabled)
  }

  return (
    <button
      onClick={handleToggle}
      disabled={busy}
      aria-pressed={enabled}
      className="flex items-center gap-1.5 h-7 px-3 rounded-full transition-colors hover:bg-[var(--surface-3)] disabled:opacity-40"
      style={{
        color: enabled ? 'var(--accent)' : 'var(--fg-muted)',
        background: enabled ? 'var(--accent-muted)' : 'transparent',
      }}
      title={sessionId
        ? '计划模式：开启后 agent 只能只读调研，提交计划经你批准后退出'
        : '计划模式：将在新会话创建后生效（首条消息发送前应用）'}
    >
      <ClipboardList size={13} style={{ flexShrink: 0 }} />
      <span className="text-[12.5px]">plan</span>
      {enabled && (
        <span className="w-1.5 h-1.5 rounded-full" style={{ background: 'var(--accent)' }} />
      )}
    </button>
  )
}
