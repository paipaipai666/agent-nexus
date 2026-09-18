import { type ReactNode } from 'react'
import { useLocation } from 'react-router-dom'
import Sidebar from './Sidebar'
import SettingsNav from './SettingsNav'
import StatusBar from './StatusBar'
import Titlebar from '../titlebar/Titlebar'
import CommandPalette from '../palette/CommandPalette'
import { useUIStore } from '../../services/ui'

interface AppShellProps {
  children: ReactNode
}

/**
 * v2.3 app shell — no activity bar. Titlebar (36) / [ContextSidebar 232 | main]
 * / StatusBar (24). The single sidebar switches content by section: chat shows
 * projects/sessions (+ Settings at the bottom), settings shows the sub-nav.
 */
export default function AppShell({ children }: AppShellProps) {
  const location = useLocation()
  const sidebarCollapsed = useUIStore((s) => s.sidebarCollapsed)
  const isChat = location.pathname === '/' || location.pathname.startsWith('/chat')
  const isSettings = location.pathname === '/settings' || location.pathname.startsWith('/settings/')
  const showSidebar = (isChat || isSettings) && !sidebarCollapsed

  return (
    <div className="flex flex-col h-screen" style={{ background: 'var(--surface-0)' }}>
      <Titlebar />
      <div className="flex flex-1 overflow-hidden">
        <div
          className="shrink-0 overflow-hidden"
          style={{
            width: showSidebar ? 232 : 0,
            background: 'var(--surface-1)',
            borderRight: showSidebar ? '1px solid var(--border-subtle)' : 'none',
            transition: 'width 0.25s var(--ease)',
          }}
        >
          {isChat && <Sidebar />}
          {isSettings && <SettingsNav />}
        </div>
        <main className="flex-1 flex flex-col overflow-hidden">
          {children}
        </main>
      </div>
      <StatusBar />
      <CommandPalette />
    </div>
  )
}
