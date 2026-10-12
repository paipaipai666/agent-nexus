import { useState, useEffect, useMemo, useRef } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { MessageSquare, Search, Plus, FolderOpen, FolderPlus, X, ChevronDown, ChevronRight, Settings } from 'lucide-react'
import { BorderBeam } from 'border-beam'
import { api } from '../../services/api'
import { useSession } from '../session/SessionProvider'
import { useProjects, selectProject, addProject, removeProject, pickAndAddProject, workspaceKey } from '../../services/projects'
import { useTheme } from '../theme/ThemeProvider'
import { usePrefersReducedMotion } from '../../utils/effects'
import InlineNotice from '../InlineNotice'

interface RecentSession {
  session_id: string
  created_at: string
  updated_at: string
  last_message_at: string
  preview: string
  profile: string | null
  workspace_path: string
}

interface ProjectGroup {
  key: string
  path: string
  explicit: boolean
  sessions: RecentSession[]
}
type SidebarError = { kind: 'load'; message: string } | { kind: 'delete'; message: string; sessionId: string }
const parseTime = (dateStr: string) => new Date(dateStr + 'Z').getTime()

function sortByRecency(list: RecentSession[]) {
  return [...list].sort((a, b) => parseTime(b.last_message_at) - parseTime(a.last_message_at))
}

const formatTime = (dateStr: string) => {
  const diffMs = Date.now() - parseTime(dateStr)
  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMs / 3600000)
  const diffDays = Math.floor(diffMs / 86400000)
  if (diffMins < 1) return '刚刚'
  if (diffMins < 60) return `${diffMins}m`
  if (diffHours < 24) return `${diffHours}h`
  if (diffDays < 7) return `${diffDays}d`
  return new Date(dateStr + 'Z').toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

export default function Sidebar() {
  const location = useLocation()
  const navigate = useNavigate()
  const { theme } = useTheme()
  const reducedMotion = usePrefersReducedMotion()
  const { isSessionRunning, activateSession, sessions } = useSession()
  const [recentSessions, setRecentSessions] = useState<RecentSession[]>([])
  const [loading, setLoading] = useState(false)
  const [query, setQuery] = useState('')
  const queryRef = useRef(query)
  queryRef.current = query
  const requestId = useRef(0)
  const loadedCount = useRef(0)
  const [hasMore, setHasMore] = useState(false)
  const [error, setError] = useState<SidebarError | null>(null)
  const [deleting, setDeleting] = useState<string | null>(null)
  const { projects, selected } = useProjects()
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})

  const isChatActive = location.pathname === '/' || location.pathname.startsWith('/chat/')
  const isSettingsActive = location.pathname === '/settings' || location.pathname.startsWith('/settings/')

  useEffect(() => {
    requestId.current++
    const timer = window.setTimeout(() => void loadRecentSessions(), query ? 200 : 0)
    return () => { window.clearTimeout(timer); requestId.current++ }
  }, [query])

  useEffect(() => {
    if (isChatActive) loadRecentSessions()
  }, [location.pathname])

  useEffect(() => {
    const handleSessionUpdated = () => loadRecentSessions()
    window.addEventListener('session-updated', handleSessionUpdated)
    return () => window.removeEventListener('session-updated', handleSessionUpdated)
  }, [])

  const loadRecentSessions = async (append = false) => {
    const id = ++requestId.current
    const offset = append ? loadedCount.current : 0
    setLoading(true)
    setError(previous => previous?.kind === 'delete' ? previous : null)
    try {
      const response = await api.getRecentSessions(50, queryRef.current, offset)
      if (id !== requestId.current) return
      const incoming = response.sessions.filter(s => s.preview && s.preview.trim())
      loadedCount.current = offset + response.sessions.length
      setRecentSessions(previous => append ? [...new Map([...previous, ...incoming].map(s => [s.session_id, s])).values()] : incoming)
      setHasMore(response.has_more === true)
    } catch (error) {
      if (id === requestId.current) setError({ kind: 'load', message: `会话加载失败：${error instanceof Error ? error.message : '请重试'}` })
    } finally {
      if (id === requestId.current) setLoading(false)
    }
  }

  const groups = useMemo<ProjectGroup[]>(() => {
    const byKey = new Map<string, RecentSession[]>()
    const q = query.trim().toLowerCase()
    for (const s of recentSessions) {
      if (!s.workspace_path) continue
      const key = workspaceKey(s.workspace_path)
      const list = byKey.get(key)
      if (list) list.push(s)
      else byKey.set(key, [s])
    }
    const claimed = new Set<string>()
    const explicit: ProjectGroup[] = projects.map((p) => {
      const key = workspaceKey(p)
      claimed.add(key)
      return { key, path: p, explicit: true, sessions: sortByRecency(byKey.get(key) ?? []) }
    }).filter(group => !q || group.sessions.length > 0)
    const orphans: ProjectGroup[] = [...byKey.entries()]
      .filter(([key]) => !claimed.has(key))
      .map(([key, list]) => ({ key, path: list[0].workspace_path, explicit: false, sessions: sortByRecency(list) }))
      .sort((a, b) => parseTime(b.sessions[0].last_message_at) - parseTime(a.sessions[0].last_message_at))
    return [...explicit, ...orphans]
  }, [recentSessions, projects, query])

  const handleNewChat = () => {
    window.dispatchEvent(new Event('new-chat'))
    navigate('/')
  }

  const draftSession = useMemo<{ id: string; preview: string; isNew: boolean } | null>(() => {
    if (location.pathname === '/') {
      return { id: '', preview: '', isNew: true }
    }
    if (location.pathname.startsWith('/chat/')) {
      const sid = location.pathname.slice('/chat/'.length)
      if (!sid || recentSessions.some(s => s.session_id === sid)) return null
      const firstUser = sessions.get(sid)?.messages.find(m => m.role === 'user')
      return { id: sid, preview: firstUser?.content ?? '', isNew: false }
    }
    return null
  }, [location.pathname, recentSessions, sessions])
  const handleAddProject = async () => {
    await pickAndAddProject(selected)
  }
  const handleSessionClick = (sessionId: string) => {
    activateSession(sessionId)
    navigate(`/chat/${sessionId}`)
  }

  const deleteSession = async (sessionId: string) => {
    if (deleting) return
    setDeleting(sessionId)
    setError(null)
    try {
      const res = await api.deleteSessions([sessionId])
      if (res.skipped.includes(sessionId)) {
        window.alert('该会话正在运行中，请先等待或取消当前任务。')
        return
      }
      setRecentSessions(prev => prev.filter(s => s.session_id !== sessionId))
      if (location.pathname === `/chat/${sessionId}`) navigate('/')
    } catch (err) {
      setError({ kind: 'delete', sessionId, message: `删除会话失败：${err instanceof Error ? err.message : '请重试'}` })
    }
    finally { setDeleting(null) }
  }
  const handleDeleteSession = async (e: React.MouseEvent, sessionId: string, preview: string) => {
    e.stopPropagation()
    if (!window.confirm(`删除会话「${preview.slice(0, 40)}」？\n该会话的消息历史将被永久删除，不可恢复。`)) return
    await deleteSession(sessionId)
  }

  const isActiveSession = (sid: string) => location.pathname === `/chat/${sid}`
  const selectedKey = selected ? workspaceKey(selected) : null

  return (
    <div className="w-[232px] flex flex-col h-full shrink-0" role="complementary" aria-label="Chat sessions">
      {}
      <div className="px-3 pt-3 pb-2 shrink-0">
        <BorderBeam
          size="pulse-inner"
          theme={theme}
          active={!reducedMotion}
          strength={0.75}
          borderRadius={8}
          style={{ display: 'block' }}
        >
          <button
            onClick={handleNewChat}
            className="btn-primary w-full flex items-center justify-center gap-1.5 text-[13.5px]"
            style={{ height: 36 }}
          >
            <Plus size={13} />
            新建聊天
          </button>
        </BorderBeam>
        <div
          className="mt-2.5 flex items-center gap-1.5 h-[34px] px-2.5 rounded-lg transition-all"
          style={{
            background: 'var(--surface-2)',
            border: '1px solid var(--border-subtle)',
            transitionDuration: '200ms',
            transitionTimingFunction: 'var(--ease)',
          }}
          onFocusCapture={(e) => {
            e.currentTarget.style.borderColor = 'var(--accent-ring)'
            e.currentTarget.style.boxShadow = '0 0 0 3px var(--accent-glow)'
          }}
          onBlurCapture={(e) => {
            e.currentTarget.style.borderColor = 'var(--border-subtle)'
            e.currentTarget.style.boxShadow = 'none'
          }}
        >
          <Search size={12} style={{ color: 'var(--fg-faint)', flexShrink: 0 }} />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索会话内容…"
            aria-label="搜索会话内容"
            maxLength={200}
            className="flex-1 min-w-0 bg-transparent outline-none text-[13px]"
            style={{ color: 'var(--fg)' }}
          />
        </div>
      </div>

      {}
      <div className="flex-1 overflow-y-auto px-3 pb-3">
        <InlineNotice message={error?.message ?? null} onDismiss={() => setError(null)} onRetry={() => error?.kind === 'delete' ? void deleteSession(error.sessionId) : void loadRecentSessions()} />
        {loading && <p role="status" className="px-3 py-2 text-xs" style={{ color: 'var(--fg-secondary)' }}>{query ? '正在搜索…' : '正在加载会话…'}</p>}
        {
}
        {draftSession && (
          <button
            onClick={() => (draftSession.isNew ? navigate('/') : handleSessionClick(draftSession.id))}
            className="relative w-full flex items-center gap-2 px-2.5 py-2.5 mb-1 rounded-[9px] transition-all text-left"
            style={{
              color: 'var(--fg)',
              background: 'var(--surface-2)',
              transitionDuration: '150ms',
              transitionTimingFunction: 'var(--ease)',
            }}
          >
            <span
              className="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-3.5 rounded-full"
              style={{ background: 'var(--accent)' }}
            />
            <MessageSquare size={12} style={{ color: 'var(--accent)', flexShrink: 0 }} />
            <span className="text-[13.5px] truncate flex-1" title={draftSession.preview || '新会话'}>
              {draftSession.preview || '新会话'}
            </span>
            {!draftSession.isNew && isSessionRunning(draftSession.id) && (
              <span
                className="w-1.5 h-1.5 rounded-full shrink-0 animate-pulse"
                style={{ background: 'var(--green)' }}
                title="Running"
              />
            )}
            <span className="text-[12px] shrink-0" style={{ color: 'var(--fg-faint)', fontFamily: 'var(--font-mono)' }}>
              刚刚
            </span>
          </button>
        )}
        <div className="flex items-center justify-between pr-1 pt-3 pb-1.5">
          <span
            className="px-1 text-[12px] font-medium"
            style={{ color: 'var(--fg-muted)' }}
          >
            项目
          </span>
          <button
            onClick={handleAddProject}
            className="w-7 h-7 grid place-items-center rounded-md transition-colors -my-2"
            style={{ color: 'var(--fg-faint)' }}
            onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--surface-3)'; e.currentTarget.style.color = 'var(--fg)' }}
            onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--fg-faint)' }}
            title="添加项目文件夹"
            aria-label="添加项目文件夹"
          >
            <FolderPlus size={12} />
          </button>
        </div>

        {loading && recentSessions.length === 0 ? (
          <div className="px-3 py-3 flex justify-center">
            <div className="w-4 h-4 border-2 border-t-transparent rounded-full animate-spin" style={{ borderColor: 'var(--fg-faint)', borderTopColor: 'transparent' }} />
          </div>
        ) : groups.length === 0 ? (
          query.trim() ? <p role="status" className="px-3 py-3 text-sm" style={{ color: 'var(--fg-secondary)' }}>没有匹配的会话，请尝试其他关键词。</p> :
          <button
            onClick={handleAddProject}
            className="w-full flex items-center gap-2 px-3 py-2 rounded-[9px] transition-colors text-left"
            style={{ color: 'var(--fg-faint)' }}
            onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--surface-3)' }}
            onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
          >
            <FolderPlus size={13} style={{ flexShrink: 0 }} />
            <span className="text-[13.5px]">添加项目文件夹…</span>
          </button>
        ) : (
          <div className="space-y-1.5">
            {groups.map((group) => {
              const isSelected = selectedKey === group.key
              const isCollapsed = !!collapsed[group.key]
              return (
                <div key={group.key}>
                  {}
                  <div
                    className="group/proj flex items-center gap-1 px-1.5 py-1.5 rounded-lg transition-colors"
                    style={{ background: isSelected ? 'var(--surface-3)' : 'transparent', transitionDuration: '150ms' }}
                  >
                    <button
                      onClick={() => setCollapsed(c => ({ ...c, [group.key]: !c[group.key] }))}
                      className="w-7 h-7 grid place-items-center shrink-0 transition-opacity -my-2"
                      style={{ color: 'var(--fg-faint)' }}
                      title={isCollapsed ? '展开项目' : '折叠项目'}
                      aria-label={isCollapsed ? '展开项目' : '折叠项目'}
                    >
                      {isCollapsed ? <ChevronRight size={11} /> : <ChevronDown size={11} />}
                    </button>
                    <button
                      onClick={() => selectProject(group.path)}
                      className="flex items-center gap-1.5 flex-1 min-w-0 text-left"
                      title={`${group.path}\n新聊天将在此文件夹中创建`}
                    >
                      <FolderOpen size={12} style={{ color: isSelected ? 'var(--accent)' : 'var(--fg-faint)', flexShrink: 0 }} />
                      <span
                        className="text-[11.5px] font-medium truncate"
                        style={{ color: isSelected ? 'var(--fg-secondary)' : 'var(--fg-faint)', fontFamily: 'var(--font-mono)' }}
                      >
                        {group.path.split(/[\\/]/).filter(Boolean).pop() ?? group.path}
                      </span>
                    </button>
                    {group.explicit ? (
                      <button
                        onClick={() => removeProject(group.path)}
                        className="w-7 h-7 grid place-items-center shrink-0 rounded opacity-0 group-hover/proj:opacity-100 transition-opacity -my-2"
                        style={{ color: 'var(--fg-faint)' }}
                        title="移除项目，会话仍会保留"
                        aria-label="移除项目"
                      >
                        <X size={11} />
                      </button>
                    ) : (
                      <button
                        onClick={() => addProject(group.path)}
                        className="w-7 h-7 grid place-items-center shrink-0 rounded opacity-0 group-hover/proj:opacity-100 transition-opacity -my-2"
                        style={{ color: 'var(--fg-faint)' }}
                        title="添加为项目"
                        aria-label="添加为项目"
                      >
                        <Plus size={11} />
                      </button>
                    )}
                  </div>

                  {}
                  {!isCollapsed && (
                    <div className="ml-4 space-y-1">
                      {group.sessions.length === 0 ? (
                        <div className="px-3 py-1 text-[12px]" style={{ color: 'var(--fg-faint)' }}>{query.trim() ? '该项目没有匹配的会话' : '暂无会话'}</div>
                      ) : group.sessions.map((session) => {
                        const active = isActiveSession(session.session_id)
                        return (
                          <div key={session.session_id} className="relative group/session">
                            <button
                              onClick={() => handleSessionClick(session.session_id)}
                              className="relative w-full flex items-center gap-2 px-2.5 py-2.5 rounded-[9px] transition-all text-left"
                              style={{
                                color: active ? 'var(--fg)' : 'var(--fg-secondary)',
                                background: active ? 'var(--surface-2)' : 'transparent',
                                transitionDuration: '150ms',
                                transitionTimingFunction: 'var(--ease)',
                              }}
                            >
                              {active && (
                                <span
                                  className="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-3.5 rounded-full"
                                  style={{ background: 'var(--accent)' }}
                                />
                              )}
                              <MessageSquare size={12} style={{ color: active ? 'var(--accent)' : 'var(--fg-faint)', flexShrink: 0 }} />
                              <span className="text-[13.5px] truncate flex-1" title={session.preview || '新会话'}>
                                {session.preview || '新会话'}
                              </span>
                              {isSessionRunning(session.session_id) && (
                                <span
                                  className="w-1.5 h-1.5 rounded-full shrink-0 animate-pulse"
                                  style={{ background: 'var(--green)' }}
                                  title="Running"
                                />
                              )}
                              {}
                              {sessions.get(session.session_id)?.pendingConfirm && (
                                <span
                                  className="text-[12px] font-bold px-1.5 py-0.5 rounded-full shrink-0 animate-pulse"
                                  style={{ background: 'var(--amber)', color: '#0a0b0d' }}
                                  title="Waiting for tool confirmation"
                                >
                                  !
                                </span>
                              )}
                              {}
                            {sessions.get(session.session_id)?.unreadCount ? (
                              <span
                                className="text-[12px] px-1 py-0.5 rounded-full shrink-0"
                                style={{ background: 'var(--accent)', color: '#fff' }}
                              >
                                {sessions.get(session.session_id)!.unreadCount}
                              </span>
                            ) : null}
                            <span className="text-[12px] shrink-0" style={{ color: 'var(--fg-faint)', fontFamily: 'var(--font-mono)' }}>
                              {formatTime(session.last_message_at)}
                            </span>
                            </button>
                            <button
                              onClick={(e) => handleDeleteSession(e, session.session_id, session.preview || '')}
                              disabled={deleting !== null}
                              className="absolute right-1.5 top-1/2 -translate-y-1/2 w-6 h-6 grid place-items-center rounded-md opacity-0 group-hover/session:opacity-100 transition-opacity"
                              style={{ color: 'var(--fg-faint)', background: 'var(--surface-3)' }}
                              title="删除会话，消息历史将永久移除"
                              aria-label="删除会话"
                            >
                              <X size={11} />
                            </button>
                          </div>
                        )
                      })}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
        {hasMore && <button type="button" className="btn-ghost w-full text-sm mt-3" disabled={loading} onClick={() => void loadRecentSessions(true)}>加载更多会话</button>}
      </div>

      {}
      <div className="shrink-0 px-3 py-2.5" style={{ borderTop: '1px solid var(--border-subtle)' }}>
        <button
          onClick={() => navigate('/settings')}
          className="relative w-full flex items-center gap-2 px-2.5 py-2.5 rounded-[9px] transition-all text-left"
          style={{
            color: isSettingsActive ? 'var(--fg)' : 'var(--fg-secondary)',
            background: isSettingsActive ? 'var(--surface-2)' : 'transparent',
            transitionDuration: '150ms',
            transitionTimingFunction: 'var(--ease)',
          }}
        >
          {isSettingsActive && (
            <span
              className="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-3.5 rounded-full"
              style={{ background: 'var(--accent)' }}
            />
          )}
          <Settings size={14} style={{ color: isSettingsActive ? 'var(--accent)' : 'var(--fg-faint)', flexShrink: 0 }} />
          <span className="text-[13.5px] font-medium flex-1">设置</span>
          <kbd
            className="font-mono text-[12px] px-1 rounded"
            style={{ color: 'var(--fg-faint)', background: 'var(--surface-2)' }}
          >
            Ctrl K
          </kbd>
        </button>
      </div>
    </div>
  )
}
