import { useEffect, useState } from 'react'
import type { OrbState } from 'thinking-orbs'

function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

/** Mirror the user's motion preference; beams/orbs that ship without
 *  reduced-motion handling get gated off through this. */
export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(prefersReducedMotion)
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    const onChange = (e: MediaQueryListEvent) => setReduced(e.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])
  return reduced
}

/** Tool name → ThinkingOrb state. One animation per activity kind. */
export function toolOrbState(name: string | undefined | null): OrbState {
  const n = name ?? ''
  if (/search|grep|kb_search|web_fetch|history_search|snapshot|browser_read|computer_snapshot/.test(n)) {
    return 'searching'
  }
  if (/python_execute|shell_exec|browser_evaluate|computer_(click|type|key|launch|select|toggle|scroll)/.test(n)) {
    return 'solving'
  }
  if (/file_write|memory_save|browser_type/.test(n)) {
    return 'composing'
  }
  if (n === 'exit_plan_mode') return 'weaving'
  if (/connect|mcp|reload/.test(n)) return 'connecting'
  return 'working'
}
