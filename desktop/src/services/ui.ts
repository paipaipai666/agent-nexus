import { create } from 'zustand'

interface UIState {
  sidebarCollapsed: boolean
  toggleSidebar: () => void
  paletteOpen: boolean
  setPaletteOpen: (open: boolean) => void
  togglePalette: () => void
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
