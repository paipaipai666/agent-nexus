// Project list — the folders chats can run in (Codex-style). Each chat session
// binds to one project folder at creation; there is no global workspace switch.
// Persisted in the Electron main process (userData/workspace.json) when the
// desktop shell is present, localStorage in plain-browser dev/E2E.

import { useSyncExternalStore } from 'react'

export interface ProjectsState {
  projects: string[]
  selected: string | null
}

const LS_KEY = 'agentnexus.projects'
let state: ProjectsState = { projects: [], selected: null }
const listeners = new Set<() => void>()

function emit(next: ProjectsState) {
  state = next
  listeners.forEach((l) => l())
}

/** Grouping key matching the backend's workspace normalization (Windows paths
 *  are casefolded wholesale, no trailing separators). */
export function workspaceKey(p: string): string {
  const s = p.trim().replace(/[\\/]+$/, '')
  return /^[A-Za-z]:/.test(s) ? s.toLowerCase() : s
}

function persistLocal() {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(state))
  } catch { /* storage full/blocked — non-fatal */ }
}

async function load(): Promise<void> {
  if (window.electronAPI?.getProjects) {
    try {
      const s = await window.electronAPI.getProjects()
      emit({ projects: s.projects, selected: s.lastProject ?? s.projects[0] ?? null })
      return
    } catch { /* fall through to localStorage */ }
  }
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (raw) {
      const s = JSON.parse(raw)
      emit({
        projects: Array.isArray(s.projects) ? s.projects.filter((p: unknown) => typeof p === 'string' && p) : [],
        selected: typeof s.selected === 'string' ? s.selected : null,
      })
    }
  } catch { /* corrupt JSON — start empty */ }
}
void load()

export function useProjects(): ProjectsState {
  return useSyncExternalStore(
    (cb) => { listeners.add(cb); return () => { listeners.delete(cb) } },
    () => state,
  )
}

export function getSelectedProject(): string | null {
  return state.selected
}
/** Sync snapshot for non-React consumers and tests. */
export function getProjectsState(): ProjectsState {
  return state
}

export function selectProject(path: string): void {
  if (!path || state.selected === path) return
  emit({ ...state, selected: path })
  if (window.electronAPI?.setLastProject) {
    window.electronAPI.setLastProject(path).catch(() => {})
  } else {
    persistLocal()
  }
}

export async function addProject(path: string): Promise<void> {
  const trimmed = path.trim()
  if (!trimmed) return
  if (window.electronAPI?.addProject) {
    try {
      const s = await window.electronAPI.addProject(trimmed)
      emit({ projects: s.projects, selected: s.lastProject ?? trimmed })
      return
    } catch { /* fall back to localStorage */ }
  }
  emit({
    projects: [trimmed, ...state.projects.filter((p) => workspaceKey(p) !== workspaceKey(trimmed))],
    selected: trimmed,
  })
  persistLocal()
}

export async function removeProject(path: string): Promise<void> {
  if (window.electronAPI?.removeProject) {
    try {
      const s = await window.electronAPI.removeProject(path)
      emit({ projects: s.projects, selected: s.lastProject ?? s.projects[0] ?? null })
      return
    } catch { /* fall back to localStorage */ }
  }
  const key = workspaceKey(path)
  const projects = state.projects.filter((p) => workspaceKey(p) !== key)
  emit({
    projects,
    selected: state.selected && workspaceKey(state.selected) === key ? projects[0] ?? null : state.selected,
  })
  persistLocal()
}

/** Open the folder picker (Electron dialog, prompt in browser dev) and add the
 *  picked folder as a project. Returns the picked path or null when cancelled. */
export async function pickAndAddProject(promptDefault?: string | null): Promise<string | null> {
  const picked = window.electronAPI
    ? await window.electronAPI.pickDirectory()
    : window.prompt('项目文件夹路径：', promptDefault ?? '')
  if (!picked) return null
  await addProject(picked)
  return picked
}
