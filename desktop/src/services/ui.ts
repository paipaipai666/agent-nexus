import { create } from 'zustand'

interface UIState {
  /** context sidebar collapsed (Ctrl+B) */
  sidebarCollapsed: boolean
  toggleSidebar: () => void
  /** command palette */
  paletteOpen: boolean
  setPaletteOpen: (open: boolean) => void
  togglePalette: () => void
  /** floating session info card on the chat page */
  infoPanelCollapsed: boolean
  toggleInfoPanel: () => void
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
  infoPanelCollapsed: localStorage.getItem('agentnexus-infopanel') === 'collapsed',
  toggleInfoPanel: () =>
    set((s) => {
      const next = !s.infoPanelCollapsed
      localStorage.setItem('agentnexus-infopanel', next ? 'collapsed' : 'expanded')
      return { infoPanelCollapsed: next }
    }),
}))
