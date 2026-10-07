/** Pre-session "armed intent" holder.
 *
 * New chat defers session creation until the first message send, but HUD
 * controls must be able to store intent BEFORE that (plan mode on, thinking
 * effort override); the session-creation path consumes it and applies the
 * server-side switch before the first message is sent. Single-client app, so
 * a module-level value is sufficient.
 */
export interface Arm<T> {
  get: () => T
  set: (v: T) => void
  /** True when a non-initial (armed) value is stored. */
  isArmed: () => boolean
  /** Read-and-clear: applies the armed intent exactly once, to the next session. */
  consume: () => T
}

export function createArm<T>(initial: T): Arm<T> {
  let value = initial
  return {
    get: () => value,
    set: (v: T) => {
      value = v
    },
    isArmed: () => !Object.is(value, initial),
    consume: () => {
      const v = value
      value = initial
      return v
    },
  }
}
