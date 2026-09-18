import { useRef, useCallback, type RefObject } from 'react'

const MAX_TILT_DEG = 6
const motionReduced = () =>
  typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches

/**
 * Pointer-follow 3D tilt (v2.1). Rotates the element up to ±6° around X/Y
 * based on cursor position, springing back flat on leave. No-op under
 * prefers-reduced-motion. Attach the returned ref to the tilting element and
 * render its parent with `perspective` (see `.perspective-3d` in globals.css).
 */
export function useTilt<T extends HTMLElement>(): RefObject<T | null> {
  const ref = useRef<T>(null)

  const onMove = useCallback((e: PointerEvent) => {
    const el = ref.current
    if (!el || motionReduced() || e.pointerType === 'touch') return
    const rect = el.getBoundingClientRect()
    const px = (e.clientX - rect.left) / rect.width - 0.5
    const py = (e.clientY - rect.top) / rect.height - 0.5
    el.style.transition = 'transform 80ms linear'
    el.style.transform = `rotateX(${(-py * MAX_TILT_DEG).toFixed(2)}deg) rotateY(${(px * MAX_TILT_DEG).toFixed(2)}deg) translateZ(0)`
  }, [])

  const onLeave = useCallback(() => {
    const el = ref.current
    if (!el) return
    el.style.transition = 'transform 350ms cubic-bezier(0.16,1,0.3,1)'
    el.style.transform = 'rotateX(0deg) rotateY(0deg)'
  }, [])

  const setRef = useCallback((node: T | null) => {
    const prev = ref.current
    if (prev) {
      prev.removeEventListener('pointermove', onMove)
      prev.removeEventListener('pointerleave', onLeave)
    }
    ref.current = node
    if (node && !motionReduced()) {
      node.addEventListener('pointermove', onMove)
      node.addEventListener('pointerleave', onLeave)
    }
  }, [onMove, onLeave])

  return setRef as unknown as RefObject<T | null>
}
