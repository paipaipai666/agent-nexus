import { type ReactNode } from 'react'
import { useLocation } from 'react-router-dom'
import ActivityBar from './ActivityBar'
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
 * v2.2 app shell — designs/mockups/v2-ui.yaml
 * Titlebar (36) / [ActivityBar 48 | ContextSidebar 232 | main] / StatusBar (24).
 * The activity bar carries two sections only: Chat and Settings. The context
 * sidebar switches content by section (projects/sessions vs settings sub-nav).
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
        <ActivityBar />
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
