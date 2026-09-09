// Run with NODE_PATH pointing to pantheon-ui/node_modules (jsdom).
import { createRequire } from 'node:module'
import { test, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { setup } from './main.js'
const { JSDOM } = createRequire(import.meta.url)('jsdom')
let pending, observer
beforeEach(() => {
  const dom = new JSDOM('<body></body>', { url: 'https://viewer.test/' })
  globalThis.window = dom.window; globalThis.document = dom.window.document; pending = []
  globalThis.Image = class { naturalWidth = 1600; naturalHeight = 1000; set src(value) { this.url = value; pending.push(this) } }
  globalThis.ResizeObserver = class { constructor(fn) { observer = fn } observe() {} disconnect() {} }
})
async function viewer(url = 'https://viewer.test/image.png') {
  const root = document.createElement('div'); document.body.append(root)
  let receive, state = {}, actions = {}, max = 0
  const app = { window: { setTitle() {}, toggleMaximize() { max++ } }, onTheme(fn) { fn('light') }, onState(fn) { receive = fn },
    setState(patch) { state = { ...state, ...patch }; receive(state) }, defineAction(name, fn) { actions[name] = fn } }
  const control = setup(app, root)
  const canvas = root.querySelector('.canvas')
  Object.defineProperty(canvas, 'clientWidth', { value: 824, configurable: true })
  Object.defineProperty(canvas, 'clientHeight', { value: 524, configurable: true })
  canvas.setPointerCapture = () => {}
  state = { url, name: 'figure.png' }
  const loaded = receive(state); pending.shift().onload(); await loaded
  return { root, canvas, actions, receive: next => { state = next; return receive(next) }, control, state: () => state, maximized: () => max,
    click: name => root.querySelector(`[data-action="${name}"]`).click() }
}
test('fit uses natural pixels, actual size is 100%, rotated fit accounts for swapped dimensions', async () => {
  const v = await viewer()
  assert.equal(v.state().zoom, .5)
  assert.equal(v.root.querySelector('.dimensions').textContent, '1,600 × 1,000 px')
  v.click('actual'); assert.equal(v.state().zoom, 1)
  v.click('zoom_in'); assert.equal(v.state().zoom, 1.25)
  v.click('fit'); v.click('rotate'); assert.equal(v.state().rotation, 90)
  assert.equal(v.state().zoom, 500 / 1600)
  v.click('maximize'); assert.equal(v.maximized(), 1)
})
test('window resize refits while another viewer retains its own zoom and rotation', async () => {
  const a = await viewer(), b = await viewer()
  a.click('actual'); a.click('rotate')
  assert.equal(b.state().rotation, 0); assert.equal(b.state().zoom, .5)
  Object.defineProperty(b.canvas, 'clientWidth', { value: 424 })
  observer(); assert.equal(b.root.querySelector('.zoom').textContent, '25%')
  assert.equal(a.state().zoom, 1); assert.equal(a.state().rotation, 90)
})
test('pointer panning, keyboard reset and downloads retain original bytes/name', async () => {
  const v = await viewer(); v.click('actual')
  const pointer = (type, x, y) => { const event = new window.Event(type); Object.assign(event, { button: 0, pointerId: 1, clientX: x, clientY: y }); v.canvas.dispatchEvent(event) }
  pointer('pointerdown', 100, 100); pointer('pointermove', 150, 120)
  assert.match(v.root.querySelector('img').style.transform, /translate\(50px, 20px\)/)
  pointer('pointerup', 150, 120)
  v.canvas.dispatchEvent(new window.KeyboardEvent('keydown', { key: '0', bubbles: true }))
  assert.equal(v.state().fit, true)
  assert.match(v.root.querySelector('img').style.transform, /translate\(0px, 0px\)/)
  let download
  globalThis.fetch = async () => ({ ok: true, blob: async () => 'original bytes' })
  globalThis.URL.createObjectURL = blob => { assert.equal(blob, 'original bytes'); return 'blob:test' }
  globalThis.URL.revokeObjectURL = () => {}
  window.HTMLAnchorElement.prototype.click = function () { download = [this.download, this.href] }
  v.click('download'); await new Promise(resolve => setTimeout(resolve, 0))
  assert.deepEqual(download, ['figure.png', 'blob:test'])
})
test('failed image loads allow retry and a late old load cannot replace a newer image', async () => {
  const v = await viewer()
  const old = v.receive({ url: 'https://viewer.test/old.png' })
  const next = v.receive({ url: 'https://viewer.test/new.png' })
  pending[1].onload(); await next; pending[0].onload(); await old
  assert.equal(v.root.querySelector('img').src, 'https://viewer.test/new.png')
  pending = []
  const failed = v.receive({ url: 'https://viewer.test/broken.png' })
  pending[0].onerror(); await assert.rejects(failed, /Could not load/)
  assert.equal(v.root.querySelector('[data-action="retry"]').hidden, false)
  pending = []; v.click('retry'); pending[0].onload(); await new Promise(resolve => setTimeout(resolve, 0))
  assert.equal(v.root.querySelector('img').hidden, false)
})
