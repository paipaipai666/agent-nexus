import { useState, useCallback, useEffect } from 'react'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import AppShell from './components/layout/AppShell'
import ThemeProvider from './components/theme/ThemeProvider'
import SessionProvider from './components/session/SessionProvider'
import SettingsLayout from './components/settings/SettingsLayout'
import LoadingScreen from './components/LoadingScreen'
import ChatPage from './pages/ChatPage'
import KnowledgePage from './pages/KnowledgePage'
import SkillsPage from './pages/SkillsPage'
import MCPPage from './pages/MCPPage'
import MemoryPage from './pages/MemoryPage'
import PluginsPage from './pages/PluginsPage'
import SettingsPage from './pages/SettingsPage'
import StatsPage from './pages/StatsPage'
import HealthPage from './pages/HealthPage'
import AlertsPage from './pages/AlertsPage'
import AuditPage from './pages/AuditPage'
import EvalPage from './pages/EvalPage'
import WikiPage from './pages/WikiPage'

const DEFAULT_BACKEND_PORT = 18765

export default function App() {
  const [backendReady, setBackendReady] = useState(false)
  const [backendPort, setBackendPort] = useState(DEFAULT_BACKEND_PORT)

  useEffect(() => {
    // In Electron, get the actual port from the main process
    window.electronAPI?.getBackendStatus().then((status) => {
      if (status.port) setBackendPort(status.port)
    })
  }, [])

  const handleReady = useCallback(() => {
    setBackendReady(true)
  }, [])

  if (!backendReady) {
    return (
      <ThemeProvider>
        <LoadingScreen onReady={handleReady} backendPort={backendPort} />
      </ThemeProvider>
    )
  }

  return (
    <ThemeProvider>
      <SessionProvider>
        <BrowserRouter>
          <AppShell>
          <Routes>
            <Route path="/" element={<ChatPage />} />
            <Route path="/chat/:sessionId" element={<ChatPage />} />
            <Route path="/settings" element={<SettingsLayout />}>
              <Route index element={<SettingsPage />} />
              <Route path="general" element={<SettingsPage />} />
              <Route path="knowledge" element={<KnowledgePage />} />
              <Route path="wiki" element={<WikiPage />} />
              <Route path="skills" element={<SkillsPage />} />
              <Route path="mcp" element={<MCPPage />} />
              <Route path="memory" element={<MemoryPage />} />
              <Route path="plugins" element={<PluginsPage />} />
              <Route path="stats" element={<StatsPage />} />
              <Route path="health" element={<HealthPage />} />
              <Route path="alerts" element={<AlertsPage />} />
              <Route path="audit" element={<AuditPage />} />
              <Route path="eval" element={<EvalPage />} />
            </Route>
          </Routes>
          </AppShell>
        </BrowserRouter>
      </SessionProvider>
    </ThemeProvider>
  )
}
