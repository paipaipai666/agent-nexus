import { create } from 'zustand'

interface UIState {
  /** context sidebar collapsed (Ctrl+B) */
  sidebarCollapsed: boolean
  toggleSidebar: () => void
  /** command palette */
  paletteOpen: boolean
  setPaletteOpen: (open: boolean) => void
  togglePalette: () => void
}

export const useUIStore = create<UIState>((set) => ({
  sidebarCollapsed: localStorage.getItem('agentnexus-sidebar') === 'collapsed',
  toggleSidebar: () =>
    set((s) => {
      const next = !s.sidebarCollapsed
      localStorage.setItem('agentnexus-sidebar', next ? 'collapsed' : 'expanded')
      return { sidebarCollapsed: next }
    }),
  paletteOpen: false,
  setPaletteOpen: (open) => set({ paletteOpen: open }),
  togglePalette: () => set((s) => ({ paletteOpen: !s.paletteOpen })),
}))
