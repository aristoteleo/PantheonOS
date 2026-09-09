import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import assert from 'node:assert/strict'

// Exercise the shipped file/catalog adapter. Only the WebGL renderer and RPC
// are replaced: renderer-originated camera updates must use the canonical SDK state.
const source = readFileSync(new URL('./index.js', import.meta.url), 'utf8')
const shim = source.slice(source.indexOf('// ── file-open shim'))
const config = id => ({ url: `https://data.invalid/${id}.zarr`, title: id, mode: '3d', colorBy: 'cluster' })
async function harness({ cameraDuringRender = false } = {}) {
  let state = null, receive
  const actions = new Map(), renders = [], calls = []
  const lv = {
    get state() { return state },
    onState(cb) { receive = cb },
    emitState(next) { state = next; receive(state, { reason: 'emit' }); return this },
    setState(patch) { return this.emitState({ ...state, ...patch }) },
    defineAction(name, fn) { actions.set(name, fn) },
    window: { setMenus() {} },
    fail(message) { throw new Error(message) },
    async call(method, args) {
      calls.push({ method, args })
      if (method === 'datasets') return { datasets: [] }
      const id = method === 'example' ? 'digiembryo_e8_0' : args.id
      return { id, config: config(id) }
    },
  }
  const viewer = async wrapped => {
    let reported = false
    wrapped.onState(async current => {
      renders.push(structuredClone(current))
      if (current.url && cameraDuringRender && !reported) {
        reported = true
        // deck.gl reports camera state while asynchronous data rendering is
        // still in progress, before the adapter's delivery has returned.
        wrapped.setState({ camera: { zoom: 2 } })
        await Promise.resolve()
      }
    })
  }
  const setup = new Function('__viewerSetup', `${shim.replace('export async function setup', 'async function setup')}; return setup`)(viewer)
  await setup(lv, {})
  return { lv, renders, calls, actions, async boot(initial) { state = initial; await receive(state, { reason: 'init' }) } }
}

test('camera reports during initial rendering retain the dataset and the camera', async () => {
  const h = await harness({ cameraDuringRender: true })
  await h.boot({ mode: '3d', colorBy: 'cluster' })
  assert.ok(h.renders.length >= 2)
  assert.ok(h.renders.every(s => s.url), 'an in-flight render lost the data URL')
  assert.equal(h.lv.state.url, config('digiembryo_e8_0').url)
  assert.deepEqual(h.lv.state.camera, { zoom: 2 })
})

test('restoring a catalog selection loads it directly and preserves view settings', async () => {
  const h = await harness()
  await h.boot({ datasetId: 'e11_5_embryo', mode: '2d', camera: { zoom: 1 }, opacity: 0.6 })
  assert.deepEqual(h.calls.filter(c => c.method !== 'datasets'), [{ method: 'load_dataset', args: { id: 'e11_5_embryo' } }])
  assert.equal(h.lv.state.url, config('e11_5_embryo').url)
  assert.equal(h.lv.state.datasetId, 'e11_5_embryo')
  assert.equal(h.lv.state.mode, '2d')
  assert.equal(h.lv.state.opacity, 0.6)
})

test('a menu selection survives a new viewport without storing its URL', async () => {
  const manifest = JSON.parse(readFileSync(new URL('../app.json', import.meta.url)))
  const h = await harness()
  await h.boot({})
  await h.actions.get('loadDataset')({ id: 'e11_5_embryo' })
  const saved = Object.fromEntries(manifest.persistState.filter(k => h.lv.state[k] !== undefined).map(k => [k, h.lv.state[k]]))
  assert.equal(saved.datasetId, 'e11_5_embryo')
  assert.equal(saved.url, undefined)
  const restored = await harness()
  await restored.boot(saved)
  assert.equal(restored.lv.state.url, config('e11_5_embryo').url)
  // A later shared selection must change the actual data, too.
  await restored.boot({ ...restored.lv.state, datasetId: 'e11_5_heart' })
  assert.equal(restored.lv.state.url, config('e11_5_heart').url)
})
