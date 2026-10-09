import { createArm } from './arm'

const arm = createArm(false)

export const planModeArm = { ...arm, setArmed: arm.set }
