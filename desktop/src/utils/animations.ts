import { gsap } from 'gsap'

// Shared easing — snappy, premium feel
const EASE = 'power3.out'

/** Fade + slide up entrance */
export function animateEntrance(elements: Element | Element[], opts?: { stagger?: number; delay?: number; y?: number }) {
  const targets = Array.isArray(elements) ? elements : [elements]
  if (targets.length === 0) return
  gsap.fromTo(
    targets,
    { opacity: 0, y: opts?.y ?? 12 },
    {
      opacity: 1,
      y: 0,
      duration: 0.4,
      ease: EASE,
      stagger: opts?.stagger ?? 0.04,
      delay: opts?.delay ?? 0,
      clearProps: 'transform',
    }
  )
}

/** Staggered card entrance for grid layouts */
export function animateCardGrid(cards: Element[]) {
  if (cards.length === 0) return
  gsap.fromTo(
    cards,
    { opacity: 0, y: 20, scale: 0.95 },
    {
      opacity: 1,
      y: 0,
      scale: 1,
      duration: 0.35,
      ease: EASE,
      stagger: 0.06,
      clearProps: 'transform',
    }
  )
}

/** Chat message entrance — slide from side based on role */
export function animateMessage(el: Element, role: 'user' | 'assistant' | 'system' | 'tool') {
  const x = role === 'user' ? 20 : -20
  gsap.fromTo(
    el,
    { opacity: 0, x, scale: 0.97 },
    { opacity: 1, x: 0, scale: 1, duration: 0.3, ease: EASE, clearProps: 'transform' }
  )
}
