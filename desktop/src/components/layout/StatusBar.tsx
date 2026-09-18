import { useSession } from '../session/SessionProvider'
import { Folder, Wrench, ListTodo } from 'lucide-react'

export default function StatusBar() {
  const { sessionId, contextUsed, stmTokens, ctxMax, totalInput, totalOutput, stepCount, cwd, toolCount, todoCount } = useSession()

  const ctxPct = stmTokens != null && ctxMax ? Math.min(100, Math.round((stmTokens / ctxMax) * 100)) : (contextUsed ?? null)

  return (
    <div
      className="h-6 flex items-center shrink-0 font-mono text-[11px] gap-0.5"
      style={{
        background: 'var(--surface-1)',
        borderTop: '1px solid var(--border-subtle)',
        color: 'var(--fg-muted)',
        paddingLeft: 12,
        paddingRight: 12,
      }}
    >
      <StatusItem>
        <span
          className="w-[6px] h-[6px] rounded-full animate-pulse"
          style={{ background: sessionId ? 'var(--green)' : 'var(--fg-faint)' }}
        />
        {sessionId ? 'connected' : 'disconnected'}
      </StatusItem>
      <Sep />

      {cwd && (
        <>
          <StatusItem>
            <Folder size={10} />
            <span className="truncate max-w-[200px]" title={cwd}>{cwd}</span>
          </StatusItem>
          <Sep />
        </>
      )}
      {toolCount > 0 && (
        <>
          <StatusItem>
            <Wrench size={10} />
            {toolCount} tools
          </StatusItem>
          <Sep />
        </>
      )}
      {todoCount > 0 && (
        <>
          <StatusItem>
            <ListTodo size={10} />
            {todoCount} todos
          </StatusItem>
          <Sep />
        </>
      )}
      {stepCount != null && (
        <StatusItem>{stepCount} steps</StatusItem>
      )}
      {/* Right: context usage + token totals */}
      <div className="ml-auto flex items-center gap-3">
        {(totalInput != null || totalOutput != null) && (
          <span>in:{(totalInput ?? 0).toLocaleString()} out:{(totalOutput ?? 0).toLocaleString()}</span>
        )}
        {ctxPct != null && (
          <>
            <span>ctx {ctxPct}%</span>
            <span
              className="w-24 h-[3px] rounded-full overflow-hidden"
              style={{ background: 'var(--surface-3)' }}
            >
              <span
                className="block h-full rounded-full transition-all"
                style={{ width: `${ctxPct}%`, background: 'var(--accent)', transitionDuration: '400ms', transitionTimingFunction: 'var(--ease)' }}
              />
            </span>
          </>
        )}
      </div>
    </div>
  )
}

function StatusItem({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-1 px-2 h-full hover:bg-[var(--surface-2)] transition-colors cursor-default rounded">
      {children}
    </div>
  )
}

function Sep() {
  return <div className="w-px h-3 opacity-20" style={{ background: 'var(--fg-faint)' }} />
}
