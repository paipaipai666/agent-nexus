import { useSession } from '../session/SessionProvider'

export default function StatusBar() {
  const { sessionId, modelName, contextUsed, stmTokens, ctxMax, totalInput, totalOutput, stepCount } = useSession()

  return (
    <div
      className="h-7 flex items-center shrink-0 font-mono text-[10px] gap-0.5"
      style={{
        background: 'var(--surface-1)',
        borderTop: '1px dashed var(--border)',
        color: 'var(--fg-secondary)',
        paddingLeft: 14,
        paddingRight: 14,
      }}
    >
      <StatusItem>
        <span
          className="w-[5px] h-[5px] rounded-full"
          style={{ background: sessionId ? 'var(--green)' : 'var(--fg-faint)', boxShadow: sessionId ? '0 0 6px rgba(142,196,122,0.5)' : 'none' }}
        />
        {sessionId ? 'connected' : 'disconnected'}
      </StatusItem>
      <Sep />
      {sessionId && (
        <>
          <StatusItem>session:{sessionId.slice(0, 8)}</StatusItem>
          <Sep />
        </>
      )}
      {stmTokens != null && ctxMax != null && ctxMax > 0 && (
        <>
          <StatusItem>ctx {Math.round(stmTokens / 1000)}k/{Math.round(ctxMax / 1000)}k ({contextUsed ?? Math.round(stmTokens / ctxMax * 100)}%)</StatusItem>
          <Sep />
        </>
      )}
      {modelName && (
        <>
          <StatusItem>{modelName}</StatusItem>
          <Sep />
        </>
      )}
      {(totalInput != null || totalOutput != null) && (
        <>
          <StatusItem>in:{(totalInput ?? 0).toLocaleString()} out:{(totalOutput ?? 0).toLocaleString()}</StatusItem>
          <Sep />
        </>
      )}
      {stepCount != null && (
        <StatusItem>{stepCount} steps</StatusItem>
      )}
    </div>
  )
}

function StatusItem({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-1 px-2 h-full hover:bg-[var(--surface-3)] transition-colors cursor-default">
      {children}
    </div>
  )
}

function Sep() {
  return <div className="w-px h-3.5 opacity-20" style={{ background: 'var(--fg-faint)' }} />
}
