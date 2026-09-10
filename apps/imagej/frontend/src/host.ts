export interface AppHost {
  state: Record<string, unknown> | null
  onState(cb: (state: Record<string, unknown>) => void | Promise<void>): AppHost
  setState(patch: Record<string, unknown>): AppHost
  defineAction(name: string, fn: (args: Record<string, unknown>) => Promise<unknown>): AppHost
  onSnapshot(fn: () => Promise<unknown>): AppHost
  call(method: string, args: Record<string, unknown>, opts?: { timeoutMs: number }): Promise<{
    path: string; url: string; converted: boolean; reason?: string; downsampled?: number
  }>
  fs: { ls(path: string): Promise<Array<{ name: string; type: string }>> }
}
