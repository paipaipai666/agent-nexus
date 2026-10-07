import { History } from 'lucide-react'

/** HUD action — quiet 28px icon button (v3). The label moves to the tooltip;
 *  at HUD density, text competed with the model/plan chips for attention. */
function HudAction({ icon: Icon, label, onClick, disabled, title }: {
  icon: typeof History
  label: string
  onClick: () => void
  disabled?: boolean
  title?: string
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title ?? label}
      aria-label={label}
      className="flex items-center justify-center w-7 h-7 rounded-lg transition-colors hover:bg-[var(--surface-3)] hover:text-[var(--fg)] disabled:opacity-30 disabled:hover:bg-transparent disabled:hover:text-[var(--fg-muted)]"
      style={{ color: 'var(--fg-muted)' }}
    >
      <Icon size={14} style={{ flexShrink: 0 }} />
    </button>
  )
}

export default HudAction
