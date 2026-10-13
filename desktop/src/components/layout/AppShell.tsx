import { type ReactNode } from 'react'
import { useLocation } from 'react-router-dom'
import Sidebar from './Sidebar'
import SettingsNav from './SettingsNav'
import StatusBar from './StatusBar'
import Titlebar from '../titlebar/Titlebar'
import CommandPalette from '../palette/CommandPalette'
import BackendRecoveryBanner from '../BackendRecoveryBanner'
import { useUIStore } from '../../services/ui'

interface AppShellProps {
  children: ReactNode
}

export default function AppShell({ children }: AppShellProps) {
  const location = useLocation()
  const sidebarCollapsed = useUIStore((s) => s.sidebarCollapsed)
  const isChat = location.pathname === '/' || location.pathname.startsWith('/chat')
  const isSettings = location.pathname === '/settings' || location.pathname.startsWith('/settings/')
  const showSidebar = (isChat || isSettings) && !sidebarCollapsed

  return (
    <div className="flex flex-col h-screen" style={{ background: 'var(--surface-0)' }}>
      <Titlebar />
      <BackendRecoveryBanner />
      <div className="flex flex-1 overflow-hidden">
        <div
          id="navigation-sidebar"
          aria-hidden={!showSidebar}
          inert={!showSidebar}
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
