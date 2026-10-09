import { useEffect, useState } from 'react'
import type { Arm } from '../services/arm'

export interface SessionOverrideConfig<TLocal, TApi> {
  arm: Arm<TApi>
  initial: TLocal
  toApi: (v: TLocal) => TApi
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
      setValue(cfg.fromApi(cfg.arm.get()))
      return
    }
    if (cfg.arm.isArmed()) {
      const pending = cfg.arm.consume()
      cfg.setServerValue(sessionId, pending)
        .then(res => setValue(cfg.fromApi(res)))
        .catch(() => {
          cfg.arm.set(pending)
          setValue(cfg.initial)
        })
      return
    }
    cfg.getServerValue(sessionId)
      .then(res => setValue(cfg.fromApi(res)))
      .catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId])

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
    } finally {
      setBusy(false)
    }
  }

  return { value, busy, preview, commit }
}
