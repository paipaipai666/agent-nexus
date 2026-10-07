import { useEffect, useState } from 'react'
import type { Arm } from '../services/arm'

/** Session-override state machine shared by the chat HUD controls
 *  (PlanModeToggle, ThinkEffortControl), keyed on sessionId:
 *  - no session: reflect the armed intent locally
 *  - session + armed intent: consume it and apply server-side before any run
 *    starts; failure re-arms so the next session attach retries
 *  - session + no intent: read the server-side value
 *  commit() (user toggle) mirrors the same arm-or-send split.
 *  Differences between controls — the arm, the local<->API value mapping, and
 *  the server accessors — are passed in via cfg. */
export interface SessionOverrideConfig<TLocal, TApi> {
  arm: Arm<TApi>
  initial: TLocal
  /** Local UI value -> API payload ('follow'/off maps to the arm's initial). */
  toApi: (v: TLocal) => TApi
  /** API payload -> local UI value (null/undefined = follow default). */
  fromApi: (v: TApi | null | undefined) => TLocal
  getServerValue: (sessionId: string) => Promise<TApi | null | undefined>
  setServerValue: (sessionId: string, v: TApi) => Promise<TApi | null | undefined>
}

export function useSessionOverride<TLocal, TApi>(
  sessionId: string | null,
  cfg: SessionOverrideConfig<TLocal, TApi>,
) {
  const [value, setValue] = useState<TLocal>(cfg.initial)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!sessionId) {
      // No session yet — reflect the armed intent locally.
      setValue(cfg.fromApi(cfg.arm.get()))
      return
    }
    if (cfg.arm.isArmed()) {
      // Armed before the session existed — apply now, before any run starts.
      const pending = cfg.arm.consume()
      cfg.setServerValue(sessionId, pending)
        .then(res => setValue(cfg.fromApi(res)))
        .catch(() => {
          cfg.arm.set(pending) // retry on the next session attach
          setValue(cfg.initial)
        })
      return
    }
    cfg.getServerValue(sessionId)
      .then(res => setValue(cfg.fromApi(res)))
      .catch(() => {})
    // cfg is a module-level constant at every call site
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId])

  /** Live preview during drag; commit() persists. */
  const preview = setValue

  const commit = async (next: TLocal) => {
    if (busy) return
    if (!sessionId) {
      cfg.arm.set(cfg.toApi(next))
      setValue(next)
      return
    }
    setBusy(true)
    try {
      const res = await cfg.setServerValue(sessionId, cfg.toApi(next))
      setValue(cfg.fromApi(res))
      cfg.arm.set(cfg.toApi(cfg.fromApi(res)))
    } catch {
      // keep previous state; transient backend errors surface on next toggle
    } finally {
      setBusy(false)
    }
  }

  return { value, busy, preview, commit }
}
