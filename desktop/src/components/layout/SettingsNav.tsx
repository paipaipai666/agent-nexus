import { NavLink } from 'react-router-dom'
import { SETTINGS_NAV } from './nav'

/**
 * Context-sidebar content for the Settings section (v2.2): sub-navigation for
 * all non-chat pages. Rendered by AppShell when the active section is Settings.
 */
export default function SettingsNav() {
  return (
    <div className="w-[232px] flex flex-col h-full shrink-0 overflow-y-auto py-3 px-2.5">
      <div
        className="px-2.5 pb-2 text-[11px] font-medium uppercase"
        style={{ color: 'var(--fg-muted)', letterSpacing: '0.06em' }}
      >
        Settings
      </div>
      <div className="flex flex-col" style={{ gap: 2 }}>
        {SETTINGS_NAV.map((item) => {
          const Icon = item.icon
          return (
            <NavLink
              key={item.id}
              to={item.path}
              end={item.path === '/settings'}
              className="flex items-center gap-2.5 px-2.5 rounded-lg transition-all"
              style={({ isActive }) => ({
                height: 32,
                color: isActive ? 'var(--fg)' : 'var(--fg-secondary)',
                background: isActive ? 'var(--accent-muted)' : 'transparent',
                boxShadow: isActive ? 'var(--glow-accent)' : 'none',
                transitionDuration: '150ms',
                transitionTimingFunction: 'var(--ease)',
              })}
            >
              {({ isActive }) => (
                <>
                  <Icon size={14} style={{ color: isActive ? 'var(--accent)' : 'var(--fg-faint)', flexShrink: 0 }} />
                  <span className="text-[12px] font-medium flex-1">{item.label}</span>
                </>
              )}
            </NavLink>
          )
        })}
      </div>
    </div>
  )
}
