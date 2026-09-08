// Run: node --experimental-vm-modules --test apps/notebook/frontend/control.test.mjs
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'

const pause = ms => new Promise(resolve => setTimeout(resolve, ms))
const snapshot = text => ({ success: true, notebook: { cells: [{ source: text }] } })

async function harness() {
  const actions = new Map(), pendingReads = [], writes = []
  let wrapped, onState, visible
  const app = {
    call(method, args) {
      if (method === 'read_notebook') {
        return new Promise(resolve => pendingReads.push(resolve))
      }
      writes.push({ method, args })
      return Promise.resolve({ success: true })
    },
    onState(fn) { onState = fn; return this },
    setState() { return this },
    defineAction(name, fn) { actions.set(name, fn) },
  }
  // Stub only the large Vue viewer dependency. The controller under test is
  // loaded unchanged; replies update `visible` exactly where the viewer's
  // notebookDocumentStore applies the result of its transport call.
  const context = vm.createContext({ setTimeout, clearTimeout })
  const viewer = new vm.SyntheticModule(['setup'], function () {
    this.setExport('setup', lv => { wrapped = lv; lv.onState(() => {}) })
  }, { context })
  const controller = new vm.SourceTextModule(
    await readFile(new URL('./control.js', import.meta.url), 'utf8'), { context },
  )
  await controller.link(specifier => {
    assert.equal(specifier, './index.js')
    return viewer
  })
  await controller.evaluate()
  controller.namespace.setup(app, { querySelector: () => ({}) })

  function poll() {
    const result = wrapped.call('read_notebook', { notebook_path: '/audit.ipynb' })
      .then(reply => { visible = reply; return reply })
    const resolve = pendingReads.shift()
    assert.ok(resolve)
    return { result, resolve }
  }
  const initializing = onState({ path: '/audit.ipynb' }, {})
  const initial = poll()
  initial.resolve(snapshot('initial'))
  await initial.result
  await initializing
  return { actions, poll, writes, visible: () => visible }
}

test('a poll started before a write cannot acknowledge that write', async () => {
  const h = await harness()
  const stale = h.poll()
  let completed = false
  const action = h.actions.get('update_cell')({ cell_id: 'cell', source: 'new' })
    .then(value => { completed = true; return value })
  await pause(5) // let the backend write finish before delivering the old poll
  stale.resolve(snapshot('old'))
  await stale.result
  await pause(120) // longer than the controller's render/commit delay
  assert.equal(completed, false, 'old contents must not report the write as visible')
  assert.equal(h.writes[0].args.notebook_path, '/audit.ipynb')

  const fresh = h.poll()
  fresh.resolve(snapshot('new'))
  await fresh.result
  assert.equal((await action).success, true)
  assert.equal(h.visible().notebook.cells[0].source, 'new')
})

test('a late older response cannot overwrite the new visible snapshot', async () => {
  const h = await harness()
  const stale = h.poll()
  const action = h.actions.get('add_cell')({ source: 'new', notebook_path: '/other.ipynb' })
  await pause(5)
  const fresh = h.poll()
  const latest = snapshot('new')
  fresh.resolve(latest)
  await fresh.result
  assert.equal((await action).success, true)
  assert.equal(h.writes[0].args.notebook_path, '/audit.ipynb', 'actions must stay in their own window')

  stale.resolve(snapshot('old'))
  assert.equal(await stale.result, latest, 'return the newer snapshot to the viewer store')
  assert.equal(h.visible().notebook.cells[0].source, 'new')
})
