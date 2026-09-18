import { useLocation, useNavigate } from 'react-router-dom'
import { NAV_GROUPS } from './nav'

/**
 * VS Code-style activity bar: 48px icon rail, groups separated by hairlines,
 * settings pinned to the bottom. Active item = surface-3 block + accent icon.
 */
export default function ActivityBar() {
  const location = useLocation()
  const navigate = useNavigate()

  return (
    <nav
      aria-label="Primary"
      className="w-12 shrink-0 flex flex-col items-center py-1.5"
      style={{ background: 'var(--surface-0)', borderRight: '1px solid var(--border-subtle)' }}
    >
      {NAV_GROUPS.map((group, gi) => (
        <div key={group.id} className={`flex flex-col items-center gap-0.5 ${group.id === 'system' ? 'mt-auto' : ''}`}>
          {gi > 0 && group.id !== 'system' && (
            <div className="w-5 h-px my-1.5" style={{ background: 'var(--border)' }} />
          )}
          {group.id === 'system' && (
            <div className="w-5 h-px my-1.5" style={{ background: 'var(--border)' }} />
          )}
          {group.items.map((item) => {
            const active = item.match ? item.match(location.pathname) : location.pathname.startsWith(item.path)
            const Icon = item.icon
            return (
              <button
                key={item.id}
                onClick={() => navigate(item.path)}
                aria-label={item.label}
                aria-current={active ? 'page' : undefined}
                title={item.label}
                className="group/ab relative w-10 h-10 grid place-items-center rounded-lg transition-all"
                style={{
                  color: active ? 'var(--accent)' : 'var(--fg)',
                  opacity: active ? 1 : undefined,
                  background: active ? 'var(--surface-3)' : 'transparent',
                  transitionDuration: '150ms',
                  transitionTimingFunction: 'var(--ease)',
                }}
              >
                {active && (
                  <span
                    className="absolute left-0 top-1/2 -translate-y-1/2 w-[2px] h-4 rounded-full"
                    style={{ background: 'var(--accent)' }}
                  />
                )}
                <Icon
                  size={16}
                  className="transition-opacity"
                  style={{ opacity: active ? 1 : 0.35 }}
                />
                {/* hover lift via opacity on non-active */}
                {!active && (
                  <span
                    className="absolute inset-0 rounded-lg opacity-0 group-hover/ab:opacity-100 transition-opacity"
                    style={{ background: 'var(--surface-3)', transitionDuration: '150ms' }}
                  />
                )}
              </button>
            )
          })}
        </div>
      ))}
    </nav>
  )
}
