import { useRef, useEffect } from 'react'
import { gsap } from 'gsap'

/**
 * Hook that returns a ref and auto-animates the element on mount.
 * Usage: const ref = useGsapEntrance({ y: 12, delay: 0.1 })
 */
export function useGsapEntrance(opts?: { y?: number; delay?: number; duration?: number }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!ref.current) return
    gsap.fromTo(
      ref.current,
      { opacity: 0, y: opts?.y ?? 12 },
      { opacity: 1, y: 0, duration: opts?.duration ?? 0.35, ease: 'power3.out', delay: opts?.delay ?? 0, clearProps: 'transform' }
    )
  }, [])
  return ref
}

/**
 * Hook that returns a ref for staggered children animation.
 * Usage: const ref = useGsapStagger({ selector: '.card', stagger: 0.06 })
 */
export function useGsapStagger(opts?: { selector?: string; stagger?: number; y?: number }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!ref.current) return
    const targets = opts?.selector
      ? ref.current.querySelectorAll(opts.selector)
      : ref.current.children
    if (targets.length === 0) return
    gsap.fromTo(
      targets,
      { opacity: 0, y: opts?.y ?? 16, scale: 0.97 },
      {
        opacity: 1, y: 0, scale: 1,
        duration: 0.35, ease: 'power3.out',
        stagger: opts?.stagger ?? 0.05,
        clearProps: 'transform',
      }
    )
  }, [])
  return ref
}

/**
 * Hook that animates a list of items when they change.
 * Usage: useGsapList(itemsRef, [deps])
 */
export function useGsapList(containerRef: React.RefObject<HTMLElement | null>, deps: unknown[]) {
  useEffect(() => {
    if (!containerRef.current) return
    const children = Array.from(containerRef.current.children)
    if (children.length === 0) return
    gsap.fromTo(
      children,
      { opacity: 0, y: 10 },
      { opacity: 1, y: 0, duration: 0.3, ease: 'power3.out', stagger: 0.03, clearProps: 'transform' }
    )
  }, deps)
}

/**
 * Returns a callback ref that triggers a scale-in animation.
 * Usage: const ref = useGsapScaleIn()
 */
export function useGsapScaleIn() {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!ref.current) return
    gsap.fromTo(
      ref.current,
      { opacity: 0, scale: 0.9, y: -4 },
      { opacity: 1, scale: 1, y: 0, duration: 0.25, ease: 'back.out(1.4)', clearProps: 'transform' }
    )
  }, [])
  return ref
}
