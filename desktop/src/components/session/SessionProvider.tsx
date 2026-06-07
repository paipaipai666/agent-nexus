import { createContext, useContext, useState, useCallback, type ReactNode } from 'react'

interface SessionContextType {
  sessionId: string | null
  modelName: string | null
  contextUsed: number | null
  stmTokens: number | null
  ctxMax: number | null
  totalInput: number | null
  totalOutput: number | null
  stepCount: number | null
  cwd: string | null
  setSessionId: (id: string | null) => void
  setModelName: (name: string | null) => void
  setContextUsed: (pct: number | null) => void
  setRuntimeInfo: (info: { stmTokens?: number; ctxMax?: number; totalInput?: number; totalOutput?: number; stepCount?: number } | null) => void
  setCwd: (cwd: string | null) => void
}

const SessionContext = createContext<SessionContextType>({
  sessionId: null,
  modelName: null,
  contextUsed: null,
  stmTokens: null,
  ctxMax: null,
  totalInput: null,
  totalOutput: null,
  stepCount: null,
  cwd: null,
  setSessionId: () => {},
  setModelName: () => {},
  setContextUsed: () => {},
  setRuntimeInfo: () => {},
  setCwd: () => {},
})

export function useSession() {
  return useContext(SessionContext)
}

export default function SessionProvider({ children }: { children: ReactNode }) {
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [modelName, setModelName] = useState<string | null>(null)
  const [contextUsed, setContextUsed] = useState<number | null>(null)
  const [stmTokens, setStmTokens] = useState<number | null>(null)
  const [ctxMax, setCtxMax] = useState<number | null>(null)
  const [totalInput, setTotalInput] = useState<number | null>(null)
  const [totalOutput, setTotalOutput] = useState<number | null>(null)
  const [stepCount, setStepCount] = useState<number | null>(null)
  const [cwd, setCwd] = useState<string | null>(null)

  const handleSetSessionId = useCallback((id: string | null) => setSessionId(id), [])
  const handleSetModelName = useCallback((name: string | null) => setModelName(name), [])
  const handleSetContextUsed = useCallback((pct: number | null) => setContextUsed(pct), [])
  const handleSetRuntimeInfo = useCallback((info: { stmTokens?: number; ctxMax?: number; totalInput?: number; totalOutput?: number; stepCount?: number } | null) => {
    if (!info) return
    if (info.stmTokens != null) setStmTokens(info.stmTokens)
    if (info.ctxMax != null) setCtxMax(info.ctxMax)
    if (info.totalInput != null) setTotalInput(info.totalInput)
    if (info.totalOutput != null) setTotalOutput(info.totalOutput)
    if (info.stepCount != null) setStepCount(info.stepCount)
  }, [])

  return (
    <SessionContext.Provider value={{
      sessionId, modelName, contextUsed, stmTokens, ctxMax, totalInput, totalOutput, stepCount, cwd,
      setSessionId: handleSetSessionId,
      setModelName: handleSetModelName,
      setContextUsed: handleSetContextUsed,
      setRuntimeInfo: handleSetRuntimeInfo,
      setCwd,
    }}>
      {children}
    </SessionContext.Provider>
  )
}
