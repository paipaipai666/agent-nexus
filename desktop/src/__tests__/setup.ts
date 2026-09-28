import '@testing-library/jest-dom/vitest'

// jsdom ships without matchMedia; UI effects (border-beam / thinking-orbs / metal-fx)
// and reduced-motion checks need it.
if (typeof window !== 'undefined' && typeof window.matchMedia !== 'function') {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia
}
