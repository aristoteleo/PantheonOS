// Run: node --experimental-vm-modules --test apps/notebook/frontend/control.test.mjs
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'

async function harness({ mismatch = false, refreshError = '', executionError = false } = {}) {
  const actions = new Map(), writes = [], reads = []
  let wrapped, onState, visible, cell = { id: 'new-cell', source: 'initial', cell_type: 'code' }
  const app = {
    async call(method, args) {
      if (method === 'read_notebook') return { success: true, notebook: { cells: [{ ...cell }] } }
      writes.push({ method, args })
      // Match the real backend contract: it accepts content, NOT source.
      if (method === 'add_cell' || method === 'update_cell') cell.source = args.content ?? ''
      if (args.cell_type) cell.cell_type = args.cell_type
      return { success: true, cell_id: cell.id, ...(executionError ? { execution: { success: false, error: 'NameError' } } : {}) }
    },
    onState(fn) { onState = fn; return this }, setState() { return this },
    defineAction(name, fn) { actions.set(name, fn) },
  }
  const context = vm.createContext({ setTimeout, clearTimeout })
  const viewer = new vm.SyntheticModule(['setup'], function () {
    this.setExport('setup', lv => {
      wrapped = lv
      lv.onState(async state => { await lv.call('read_notebook', { notebook_path: state.path }) })
      return { async refresh(path, cellId) {
        reads.push({ path, cellId })
        if (refreshError) throw new Error(refreshError)
        visible = (await lv.call('read_notebook', { notebook_path: path })).notebook.cells[0]
        return { cell_id: visible.id, source: mismatch ? 'local edit' : visible.source, cell_count: 1, cell_type: visible.cell_type }
      } }
    })
  }, { context })
  const controller = new vm.SourceTextModule(await readFile(new URL('./control.js', import.meta.url), 'utf8'), { context })
  await controller.link(() => viewer); await controller.evaluate()
  controller.namespace.setup(app, { querySelector: () => ({}) })
  await onState({ path: '/audit.ipynb' }, {})
  return { actions, writes, reads, onState, visible: () => visible, wrapped, app }
}

test('the reported source payload writes code and refreshes the same window immediately', async () => {
  const h = await harness()
  const source = 'from sklearn.datasets import load_iris\niris = load_iris()'
  const result = await h.actions.get('add_cell')({ source, cell_type: 'code', notebook_path: '/wrong.ipynb' })
  assert.equal(result.success, true)
  assert.equal(result.visible, true)
  assert.equal(result.cell_id, 'new-cell')
  assert.equal(result.source_length, source.length)
  assert.equal(h.writes[0].args.content, source)
  assert.equal(h.writes[0].args.source, undefined)
  assert.equal(h.writes[0].args.notebook_path, '/audit.ipynb')
  assert.equal(h.visible().source, source)
  assert.deepEqual(h.reads, [{ path: '/audit.ipynb', cellId: 'new-cell' }])
})

test('bad parameters fail before creating or executing anything', async () => {
  const h = await harness()
  for (const [method, args] of [
    ['add_cell', {}], ['add_cell', { contnet: 'lost code' }],
    ['add_cell', { source: 'one', content: 'two' }], ['execute_cell', {}],
    ['update_cell', { cell_id: 'cell' }], ['add_cell', { content: '42', execute: 'false' }],
  ]) await assert.rejects(h.actions.get(method)(args))
  assert.equal(h.writes.length, 0)
  assert.equal((await h.actions.get('add_cell')({ content: '' })).success, true, 'explicit empty cells remain supported')
})

for (const options of [{ mismatch: true }, { refreshError: 'Not rendered' }]) {
  test(`an unconfirmed render is not success and retains the already changed cell: ${JSON.stringify(options)}`, async () => {
    const h = await harness(options)
    const result = await h.actions.get('add_cell')({ content: '42' })
    assert.equal(result.success, false)
    assert.equal(result.applied, true)
    assert.equal(result.visible, false)
    assert.equal(result.cell_id, 'new-cell')
    assert.match(result.error, /already changed.*read_cells/)
    assert.equal(h.writes.length, 1)
  })
}

test('execute=true preserves execution failures while rendering the error output', async () => {
  const h = await harness({ executionError: true })
  const result = await h.actions.get('add_cell')({ content: 'missing_name', execute: true })
  assert.equal(result.success, false)
  assert.equal(result.visible, true)
  assert.equal(result.error, 'NameError')
})

test('launcher changes and late reads never move actions to another notebook', async () => {
  const h = await harness()
  let resolveOld
  const realCall = h.app.call
  h.app.call = (method, args) => method === 'read_notebook' && args.notebook_path === '/audit.ipynb'
    ? new Promise(resolve => { resolveOld = resolve }) : realCall(method, args)
  const old = h.wrapped.call('read_notebook', { notebook_path: '/audit.ipynb' })
  await h.onState({ path: '/created.ipynb' }, { reason: 'emit' })
  await h.actions.get('read_cells')({})
  assert.equal(h.writes.at(-1).args.notebook_path, '/created.ipynb')
  resolveOld({ success: true, notebook: { cells: [] } }); await old
  await h.onState({ path: '' }, { reason: 'emit' })
  await assert.rejects(h.actions.get('read_cells')({}), /No notebook is open/)
})

test('a late older poll cannot replace the current viewer snapshot', async () => {
  const h = await harness()
  const pending = []
  h.app.call = () => new Promise(resolve => pending.push(resolve))
  const old = h.wrapped.call('read_notebook', { notebook_path: '/audit.ipynb' })
  const fresh = h.wrapped.call('read_notebook', { notebook_path: '/audit.ipynb' })
  const latest = { success: true, notebook: { cells: [{ source: 'new' }] } }
  pending[1](latest)
  await fresh
  pending[0]({ success: true, notebook: { cells: [{ source: 'old' }] } })
  assert.equal(await old, latest)
})
