import { createApp } from 'vue'
import ImageJApp from './ImageJApp.vue'
import type { AppHost } from './host'

export async function setup(host: AppHost, root: HTMLElement) {
  let started!: () => void
  let failed!: (error: Error) => void
  const ready = new Promise<void>((resolve, reject) => { started = resolve; failed = reject })
  const instance = createApp(ImageJApp, { host, started, failed })
  instance.mount(root)
  window.addEventListener('pagehide', () => instance.unmount(), { once: true })
  await ready
}
