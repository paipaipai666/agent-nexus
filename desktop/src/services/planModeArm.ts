/** Plan-mode "armed" intent for the pre-session window.

New chat defers session creation until the first message send, but users
must be able to arm plan mode BEFORE that — otherwise the agent starts
executing (with writes) before plan mode can be enabled. The toggle stores
the intent here; the session-creation path consumes it and applies the
server-side switch before the first message is sent. Single-client app, so a
module-level flag is sufficient.
 */

let armed = false

export const planModeArm = {
  isArmed: () => armed,
  setArmed: (v: boolean) => {
    armed = v
  },
  /** Read-and-clear: applies the armed intent exactly once, to the next session. */
  consume: () => {
    const v = armed
    armed = false
    return v
  },
}
