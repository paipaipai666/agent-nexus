export interface Arm<T> {
  get: () => T
  set: (v: T) => void
  isArmed: () => boolean
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
