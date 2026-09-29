/** Thinking-effort "armed" intent for the pre-session window.

New chat defers session creation until the first message; the HUD control
stores an override here and the session-creation path applies it before the
first message is sent. null means "follow Settings default" (not armed).
 */

let armed: string | null = null

export const thinkingEffortArm = {
  get: () => armed,
  set: (v: string | null) => {
    armed = v
  },
  /** Read-and-clear: applies the armed override exactly once. */
  consume: () => {
    const v = armed
    armed = null
    return v
  },
}
