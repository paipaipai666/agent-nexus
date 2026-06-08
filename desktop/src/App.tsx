import { BrowserRouter, Routes, Route } from 'react-router-dom'
import AppShell from './components/layout/AppShell'
import ThemeProvider from './components/theme/ThemeProvider'
import SessionProvider from './components/session/SessionProvider'
import SettingsLayout from './components/settings/SettingsLayout'
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

export default function App() {
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
