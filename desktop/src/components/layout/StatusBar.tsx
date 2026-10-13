import { useSession } from '../session/SessionProvider'
import { Folder, Wrench, ListTodo } from 'lucide-react'
import ContextMeter from './ContextMeter'

export default function StatusBar() {
  const { sessionId, connectionState, runtimeStatus, cwd, toolCount, todoCount } = useSession()
  const usage = runtimeStatus?.total_usage

  return (
    <div
      className="h-6 flex items-center shrink-0 min-w-0 font-mono text-[12px] gap-0.5"
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
          style={{ background: connectionState === 'connected' ? 'var(--green)' : 'var(--fg-faint)' }}
        />
        {connectionState === 'connected' ? '已连接' : sessionId && connectionState === 'connecting' ? '正在连接' : '未连接'}
      </StatusItem>
      <Sep />

      {cwd && (
        <div className="hidden md:flex items-center min-w-0 h-full">
          <StatusItem>
            <Folder size={10} />
            <span className="truncate max-w-[200px]" title={cwd}>{cwd}</span>
          </StatusItem>
          <Sep />
        </div>
      )}
      {toolCount > 0 && (
        <div className="hidden sm:flex items-center h-full">
          <StatusItem>
            <Wrench size={10} />
            {toolCount} tools
          </StatusItem>
          <Sep />
        </div>
      )}
      {todoCount > 0 && (
        <div className="hidden sm:flex items-center h-full">
          <StatusItem>
            <ListTodo size={10} />
            {todoCount} todos
          </StatusItem>
          <Sep />
        </div>
      )}
      <div className="ml-auto flex shrink-0 items-center gap-3">
        {usage && (
          <span className="hidden lg:inline">in:{usage.input_tokens.toLocaleString()} out:{usage.output_tokens.toLocaleString()}</span>
        )}
        <ContextMeter key={sessionId} status={runtimeStatus} />
      </div>
    </div>
  )
}

function StatusItem({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-1 px-2.5 h-full hover:bg-[var(--surface-2)] transition-colors cursor-default rounded">
      {children}
    </div>
  )
}

function Sep() {
  return <div className="w-px h-3 opacity-20" style={{ background: 'var(--fg-faint)' }} />
}
