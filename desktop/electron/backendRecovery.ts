interface BackendActions {
  healthy: () => Promise<boolean>
  stop: () => Promise<void>
  start: () => Promise<boolean>
}

export class BackendRecovery {
  private pending: Promise<boolean> | null = null
  constructor(private readonly actions: BackendActions) {}

  recover(): Promise<boolean> {
    if (this.pending) return this.pending
    this.pending = this.performRecovery().finally(() => { this.pending = null })
    return this.pending
  }

  private async performRecovery(): Promise<boolean> {
    if (await this.actions.healthy()) return true
    await this.actions.stop()
    return this.actions.start()
  }
}
