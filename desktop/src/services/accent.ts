/**
 * Accent color presets (v2.3). Selected via Settings → Appearance; applied by
 * setting `data-accent` on <html>, which overrides the accent token family in
 * globals.css for both themes. Default/reset = 'rose'.
 */

export interface AccentPreset {
  id: string
  label: string
  /** dark-theme accent / hover / gradient-deep */
  dark: [string, string, string]
  /** light-theme accent / hover / gradient-from */
  light: [string, string, string]
}

export const ACCENT_PRESETS: AccentPreset[] = [
  { id: 'rose',    label: 'Rose',    dark: ['#f43f5e', '#fb7185', '#e11d48'], light: ['#e11d48', '#f43f5e', '#f43f5e'] },
  { id: 'indigo',  label: 'Indigo',  dark: ['#818cf8', '#a5b4fc', '#4f46e5'], light: ['#4f46e5', '#818cf8', '#818cf8'] },
  { id: 'violet',  label: 'Violet',  dark: ['#a78bfa', '#c4b5fd', '#7c3aed'], light: ['#7c3aed', '#a78bfa', '#a78bfa'] },
  { id: 'blue',    label: 'Blue',    dark: ['#60a5fa', '#93c5fd', '#2563eb'], light: ['#2563eb', '#60a5fa', '#60a5fa'] },
  { id: 'cyan',    label: 'Cyan',    dark: ['#22d3ee', '#67e8f9', '#0891b2'], light: ['#0891b2', '#22d3ee', '#22d3ee'] },
  { id: 'emerald', label: 'Emerald', dark: ['#34d399', '#6ee7b7', '#059669'], light: ['#059669', '#34d399', '#34d399'] },
  { id: 'amber',   label: 'Amber',   dark: ['#fbbf24', '#fcd34d', '#d97706'], light: ['#d97706', '#fbbf24', '#fbbf24'] },
  { id: 'orange',  label: 'Orange',  dark: ['#fb923c', '#fdba74', '#ea580c'], light: ['#ea580c', '#fb923c', '#fb923c'] },
]

const STORAGE_KEY = 'agentnexus-accent'

export function applyAccent(id: string) {
  document.documentElement.dataset.accent = id
  localStorage.setItem(STORAGE_KEY, id)
}

/** Called once at startup (ThemeProvider mount) — restores the saved accent. */
export function initAccent() {
  const saved = localStorage.getItem(STORAGE_KEY)
  document.documentElement.dataset.accent =
    ACCENT_PRESETS.some((p) => p.id === saved) ? saved! : 'rose'
}
