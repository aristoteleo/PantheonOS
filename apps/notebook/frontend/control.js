/** Generic Desktop actions over the same notebook backend and visible viewer. */
import { setup as setupViewer } from './index.js'
export function setup(app, root) {
  let path = '', observed = null, readSequence = 0, observedSequence = 0
  const wrapped = Object.create(app)
  const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
  const loaded = () => observed?.success && observed.notebook && root.querySelector('.notebook-panel')
  const describe = () => ({ path, loaded: !!loaded(), cellCount: observed?.notebook?.cells?.length ?? 0 })
  wrapped.call = async (method, args, options) => {
    const sequence = method === 'read_notebook' ? ++readSequence : 0
    const result = await app.call(method, args, options)
    if (method === 'read_notebook' && args?.notebook_path === path) {
      // An old in-flight poll is not proof that the viewer observed a write.
      // Do not let a late older reply replace a newer snapshot in its store.
      if (sequence < observedSequence) return observed
      observed = result; observedSequence = sequence
    }
    return result
  }
  async function waitForViewer(after = 0) {
    const deadline = Date.now() + 20000
    while (Date.now() < deadline) {
      if (observed && observedSequence >= after) {
        if (observed.success === false) throw new Error(observed.error || 'Notebook load failed')
        if (loaded()) {
          // The store applies the backend reply after call() resolves. Wait
          // for Vue/Monaco to commit it, including in a background viewport.
          await delay(50)
          app.setState(describe())
          return
        }
      }
      await delay(50)
    }
    throw new Error('The visible notebook has not refreshed; read this same window again')
  }
  wrapped.onState = cb => app.onState(async (state, info) => {
    if (info?.reason === 'emit') return
    const next = state?.path || ''
    if (next !== path) { path = next; observed = null; observedSequence = 0 }
    await cb(state, info)
    if (path) await waitForViewer()
  })
  for (const method of ['read_cells', 'add_cell', 'update_cell', 'execute_cell']) {
    app.defineAction(method, async (args = {}) => {
      if (!path) throw new Error('No notebook is open in this window')
      const result = await app.call(method, { ...args, notebook_path: path }, { timeoutMs: 120000 })
      if (result?.success === false) throw new Error(result.error || `${method} failed`)
      // Its normal poll updates the existing Vue document; do not remount it
      // or replace the kernel merely to make an agent's edit visible.
      if (method !== 'read_cells') await waitForViewer(readSequence + 1)
      if (result.execution?.success === false) throw new Error(result.execution.error || 'Cell execution failed; read_cells includes its error output')
      return result
    })
  }
  return setupViewer(wrapped, root)
}
