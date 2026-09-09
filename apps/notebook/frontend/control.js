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
    const expectedPath = path
    const deadline = Date.now() + 20000
    while (Date.now() < deadline) {
      if (path !== expectedPath) throw new Error('The notebook in this window changed while the operation was running')
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
    const next = state?.path || ''
    // The launcher uses setState to open/create a document. Forward those
    // user changes too; only skip our own same-path status publication.
    if (info?.reason === 'emit' && next === path) return
    if (next !== path) { path = next; observed = null; observedSequence = 0 }
    await cb(state, info)
    if (path) await waitForViewer()
  })
  const viewer = setupViewer(wrapped, root)
  const parameters = {
    read_cells: ['include_details', 'cell_ids'],
    add_cell: ['content', 'cell_type', 'cell_id', 'position', 'execute'],
    update_cell: ['cell_id', 'content', 'old_content', 'execute'],
    execute_cell: ['cell_id'],
  }
  for (const method of Object.keys(parameters)) {
    app.defineAction(method, async (input = {}) => {
      if (!path) throw new Error('No notebook is open in this window')
      if (!input || typeof input !== 'object' || Array.isArray(input)) throw new Error('Action arguments must be an object')
      const args = { ...input }
      delete args.notebook_path // this action always belongs to its own window
      if ('source' in args && ['add_cell', 'update_cell'].includes(method)) {
        if ('content' in args && args.content !== args.source) throw new Error('Conflicting content and source')
        args.content = args.source
        delete args.source
      }
      const unknown = Object.keys(args).filter(key => !parameters[method].includes(key))
      if (unknown.length) throw new Error(`Unknown ${method} parameter(s): ${unknown.join(', ')}. Use: ${parameters[method].join(', ')}`)
      if (['add_cell', 'update_cell'].includes(method) && typeof args.content !== 'string') {
        throw new Error(`${method} requires content (string); use content: "" only for an intentionally empty cell`)
      }
      if (['update_cell', 'execute_cell'].includes(method) && (typeof args.cell_id !== 'string' || !args.cell_id)) {
        throw new Error(`${method} requires cell_id from read_cells or add_cell`)
      }
      if ('execute' in args && typeof args.execute !== 'boolean') throw new Error('execute must be a boolean')
      if ('cell_type' in args && !['code', 'markdown', 'raw'].includes(args.cell_type)) throw new Error('cell_type must be code, markdown or raw')
      const expectedPath = path
      const result = await app.call(method, { ...args, notebook_path: expectedPath }, { timeoutMs: 120000 })
      if (result?.success === false) throw new Error(result.error || `${method} failed`)
      if (method === 'read_cells') return result
      const cellId = result.cell_id || args.cell_id
      // Mutation succeeded. A refresh failure must never invite an add retry
      // that duplicates the cell: return its identity and an explicit outcome.
      try {
        if (path !== expectedPath) throw new Error('The notebook in this window changed during the operation')
        const visible = await viewer.refresh(expectedPath, cellId)
        if ('content' in args && !args.old_content && visible.source !== args.content) {
          throw new Error('The visible cell content differs from the requested content; it may have a local edit')
        }
        if ('cell_type' in args && visible.cell_type !== args.cell_type) throw new Error('The visible cell type differs from the requested type')
        app.setState(describe())
        return { ...result, success: result.execution?.success !== false,
          ...(result.execution?.success === false ? { error: result.execution.error || 'Cell execution failed' } : {}),
          applied: true, visible: true, cell_id: cellId, cell_count: visible.cell_count,
          source_preview: visible.source.slice(0, 160), source_length: visible.source.length }
      } catch (error) {
        return { ...result, success: false, applied: true, visible: false, cell_id: cellId,
          error: `${error.message}. The cell was already changed; use read_cells with this cell_id before retrying.` }
      }
    })
  }
  return viewer
}
