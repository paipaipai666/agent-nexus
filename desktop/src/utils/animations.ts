import { gsap } from 'gsap'

// Shared easing — snappy, premium feel
const EASE = 'power3.out'
const EASE_BOUNCE = 'back.out(1.4)'
export const EASE_ELASTIC = 'elastic.out(1, 0.5)'

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

/** Fade + slide down (for dropdowns, palettes) */
export function animateDropDown(elements: Element | Element[]) {
  gsap.fromTo(
    elements,
    { opacity: 0, y: -8, scale: 0.96 },
    { opacity: 1, y: 0, scale: 1, duration: 0.25, ease: EASE, clearProps: 'transform' }
  )
}

/** Scale in (for modals, cards) */
export function animateScaleIn(elements: Element | Element[]) {
  gsap.fromTo(
    elements,
    { opacity: 0, scale: 0.9 },
    { opacity: 1, scale: 1, duration: 0.3, ease: EASE_BOUNCE, clearProps: 'transform' }
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

/** Typing indicator pulse */
export function animatePulse(el: Element) {
  return gsap.to(el, {
    opacity: 0.4,
    duration: 0.6,
    ease: 'sine.inOut',
    repeat: -1,
    yoyo: true,
  })
}

/** Sidebar icon hover effect */
export function animateIconHover(el: Element, entering: boolean) {
  gsap.to(el, {
    scale: entering ? 1.15 : 1,
    duration: 0.2,
    ease: entering ? 'back.out(2)' : 'power2.out',
  })
}

/** Number counter animation (for stats) */
export function animateCounter(el: Element, target: number) {
  const obj = { val: 0 }
  gsap.to(obj, {
    val: target,
    duration: 1,
    ease: 'power2.out',
    onUpdate: () => {
      (el as HTMLElement).textContent = Math.round(obj.val).toLocaleString()
    },
  })
}

/** Page transition — fade out old content, fade in new */
export function animatePageTransition(container: Element, direction: 'in' | 'out'): gsap.core.Tween {
  if (direction === 'out') {
    return gsap.to(container, { opacity: 0, y: -8, duration: 0.15, ease: 'power2.in' })
  }
  return gsap.fromTo(container, { opacity: 0, y: 8 }, { opacity: 1, y: 0, duration: 0.25, ease: EASE, clearProps: 'transform' })
}

/** Glow effect on accent elements */
export function animateGlow(el: Element) {
  gsap.fromTo(
    el,
    { boxShadow: '0 0 0px rgba(139, 92, 246, 0)' },
    {
      boxShadow: '0 0 20px rgba(139, 92, 246, 0.3)',
      duration: 0.6,
      ease: 'sine.inOut',
      repeat: -1,
      yoyo: true,
    }
  )
}

/** Press feedback — quick scale down and back */
export function animatePress(el: Element) {
  gsap.fromTo(
    el,
    { scale: 1 },
    { scale: 0.95, duration: 0.1, ease: 'power2.in', yoyo: true, repeat: 1 }
  )
}

/** Shake animation (for errors) */
export function animateShake(el: Element) {
  gsap.fromTo(
    el,
    { x: 0 },
    { x: 6, duration: 0.08, ease: 'sine.inOut', repeat: 5, yoyo: true, clearProps: 'transform' }
  )
}

/** Smooth height expansion (for expanding panels) */
export function animateExpand(el: Element, expanded: boolean) {
  gsap.to(el, {
    height: expanded ? 'auto' : 0,
    opacity: expanded ? 1 : 0,
    duration: 0.3,
    ease: EASE,
    overflow: 'hidden',
  })
}
