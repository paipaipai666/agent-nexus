export default function InlineNotice({ message, tone = 'error', onDismiss, onRetry }: {
  message: string | null
  tone?: 'error' | 'success' | 'info'
  onDismiss?: () => void
  onRetry?: () => void
}) {
  if (!message) return null
  const color = tone === 'error' ? 'var(--red)' : tone === 'success' ? 'var(--green)' : 'var(--fg-secondary)'
  return (
    <div role={tone === 'error' ? 'alert' : 'status'} className="flex items-start gap-3 rounded-lg px-3 py-2 text-sm"
      style={{ background: 'var(--surface-2)', color, border: '1px solid var(--border)' }}>
      <span className="flex-1 whitespace-pre-wrap break-words">{message}</span>
      {onRetry && <button type="button" onClick={onRetry} className="shrink-0 underline">重试</button>}
      {onDismiss && <button type="button" onClick={onDismiss} className="shrink-0" aria-label="关闭提示">关闭</button>}
    </div>
  )
}
