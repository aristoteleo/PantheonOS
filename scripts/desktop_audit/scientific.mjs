/** Real scientific app acceptance through the shared server.py and source UI. */
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import fs from 'node:fs/promises'
import { existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const runtime = path.resolve(process.env.RUNTIME_REPO || fileURLToPath(new URL('../../', import.meta.url)))
const ui = path.resolve(process.env.UI_REPO || path.join(path.dirname(runtime), 'pantheon-ui-apps'))
const api = process.env.AUDIT_API || 'http://127.0.0.1:48180'
const uiOrigin = new URL(process.env.UI_URL || 'http://localhost:5173').origin
const meta = await fetch(`${api}/meta`).then(response => response.json())
assert.equal(meta.audit, true, 'Use scripts/desktop_audit/server.py with an isolated scratch workspace')
assert.equal(meta.workspace, path.join(meta.root, 'workspace'))
const work = meta.workspace
const output = path.resolve(process.env.AUDIT_OUTPUT || path.join(meta.root, 'scientific-results'))
await fs.mkdir(path.join(output, 'evidence'), { recursive: true })
const { chromium } = await import(pathToFileURL(path.join(ui, 'node_modules/playwright/index.mjs')).href)
const macChrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const executablePath = process.env.CHROME_EXECUTABLE || (existsSync(macChrome) ? macChrome : undefined)
const browser = await chromium.launch({
  headless: process.env.HEADLESS !== '0', executablePath,
  args: ['--no-first-run', '--disable-features=LocalNetworkAccessChecks', '--use-gl=angle',
    '--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
})
const page = await browser.newPage({ viewport: { width: 1800, height: 1100 } })
const errors = [], result = []
const hash = bytes => createHash('sha256').update(bytes).digest('hex')
const requireOk = value => { assert.equal(value?.success, true, JSON.stringify(value)); return value }
const rpc = (method, args = {}) => page.evaluate(
  ({ method, args }) => window.appAudit.rpc(method, args), { method, args },
)
const serve = async file => requireOk(await rpc('serve_local_data', { path: path.join(work, file) })).url
page.on('pageerror', error => { errors.push(String(error)); console.error('PAGEERROR', String(error)) })
page.on('console', message => { if (message.type() === 'error') console.error('BROWSER', message.text().slice(0, 500)) })

// Keep source Vite transforms and CSS, but prevent unrelated edits elsewhere
// in a shared checkout from hot-reloading this independent acceptance viewport.
await page.route('**/@vite/client', route => route.fulfill({
  contentType: 'text/javascript', body: `
    export function createHotContext(){return {data:{},accept(){},dispose(){},on(){},invalidate(){},prune(){},send(){},acceptExports(){}}}
    export function injectQuery(url){return url}
    export function updateStyle(id,css){let el=document.querySelector('style[data-audit-id="'+CSS.escape(id)+'"]');if(!el){el=document.createElement('style');el.dataset.auditId=id;document.head.append(el)}el.textContent=css}
    export function removeStyle(){}
  `,
}))
const fixtureURL = `${uiOrigin}/__desktop-audit?api=${encodeURIComponent(api)}`
await page.route(fixtureURL, route => route.fulfill({
  contentType: 'text/html', body: '<!doctype html><div id="fixture"></div><script>window.__BUILD_ID__="audit"</script>'
    + '<script type="module" src="/scripts/desktop/fixtures/all-apps-live.ts"></script>',
}))

const cases = [
  { id: 'cytoscape', control: 'circle layout', open: { path: path.join(work, 'audit.cyjs') },
    action: { action: 'setLayout', args: { name: 'circle' } }, bad: { elements: null } },
  { id: 'phylotree', control: 'radial tree', open: { path: path.join(work, 'audit.nwk') },
    action: { action: 'setLayout', args: { layout: 'radial' } }, bad: { newick: '' } },
  { id: 'msa', control: 'hide ruler', open: { path: path.join(work, 'audit.fasta') },
    action: { action: 'toggleRuler' }, bad: { sequences: [] } },
  { id: 'rdkit', control: 'atom indices', open: { path: path.join(work, 'audit.smi') },
    action: { action: 'toggleIndices' }, bad: { molecules: [] } },
  { id: 'molstar', control: 'protein to DNA', open: { path: path.join(work, 'audit.pdb') },
    patch: async () => ({ url: await serve('alternate.pdb'), format: 'pdb' }), bad: { url: `${api}/missing.pdb`, format: 'pdb' } },
  { id: 'gosling', control: 'genomic track color', open: { state: { spec: { title: 'Scientific control audit', tracks: [{
    width: 600, height: 300,
    data: { type: 'json', chromosomeField: 'chr', genomicFields: ['start', 'end'], values: [
      { chr: 'chr1', start: 0, end: 100, value: 2 }, { chr: 'chr1', start: 100, end: 200, value: 5 },
      { chr: 'chr1', start: 200, end: 300, value: 3 },
    ] },
    mark: 'bar', x: { field: 'start', type: 'genomic', domain: { chromosome: 'chr1', interval: [0, 300] } },
    xe: { field: 'end', type: 'genomic' }, y: { field: 'value', type: 'quantitative' }, color: { value: '#7b42bd' },
  }] } } }, patch: state => ({ spec: { tracks: state.spec.tracks.map(track => ({ ...track, color: { value: '#e05235' } })) } }),
  bad: { spec: null } },
  { id: 'igv', control: 'local reference locus', open: async () => ({ state: {
    genome: { id: 'audit', fastaURL: await serve('genome.fa'), indexURL: await serve('genome.fa.fai') },
    locus: 'chrAudit:1-80', tracks: [],
  } }), patch: () => ({ locus: 'chrAudit:121-200' }), bad: state => ({ ...state, locus: 'chrDoesNotExist:1-100' }) },
  { id: 'spatial3d', control: '2D gene colors', open: { path: path.join(work, 'audit.h5ad') },
    patch: () => ({ mode: '2d', colorBy: 'gene', gene: 'Gene_3' }), bad: { url: `${api}/missing-spatial` } },
  { id: 'volume3d', control: 'MIP brightness', open: async () => ({ state: requireOk(await rpc('app_call', {
    app_id: 'volume3d', method: 'load_dataset', args: { id: 'synthetic' },
  })).result.config }), patch: () => ({ mode: 'mip', brightness: 1.8 }), bad: { url: `${api}/missing-volume` } },
  { id: 'vitessce', control: 'toggle heatmap', open: { path: path.join(work, 'audit.h5ad') },
    action: { action: 'toggleView', args: { component: 'heatmap' } }, bad: { version: 'not-a-version', datasets: [], layout: [] } },
  { id: 'viv', control: 'red channel only', open: { path: path.join(work, 'audit.ome.tif') },
    patch: state => ({ channels: state.channels.map((channel, index) => ({
      ...channel, color: index ? [0, 255, 0] : [255, 0, 0], visible: index === 0,
    })) }), bad: { url: `${api}/missing.ome.tif`, channels: [] } },
]
const selected = process.env.ONLY?.split(',').filter(Boolean)
const resultFile = path.join(output, `results${selected ? '-' + selected.join('_') : ''}.json`)

try {
  if (selected) assert.ok(selected.every(id => cases.some(item => item.id === id)), `Unknown ONLY app: ${selected}`)
  await page.goto(fixtureURL)
  await page.waitForFunction(() => window.appAudit?.ready, { timeout: 180000 })
  // The server's explicit audit marker above is required before closing any
  // windows. Do not run two suites concurrently against this same API/workspace.
  for (const window of (await rpc('desktop_windows')).result?.windows ?? []) {
    requireOk(await rpc('desktop_call', { window_id: window.window_id, action: '$close' }))
  }
  for (const test of cases.filter(item => !selected || selected.includes(item.id))) {
    const row = { id: test.id, control: test.control, started: Date.now() }, owned = new Set()
    try {
      console.log('BEGIN', test.id)
      row.opened = await rpc('desktop_open', { app: test.id, ...(typeof test.open === 'function' ? await test.open() : test.open) })
      if (row.opened.result?.window_id) owned.add(row.opened.result.window_id)
      const wid = requireOk(row.opened).result.window_id
      const window = page.locator(`[data-window-id="${wid}"]`)
      const before = requireOk(await rpc('desktop_read', { window_id: wid })); row.before = before
      await window.screenshot({ path: path.join(output, 'evidence', `${test.id}-before.png`) })
      const beforePixels = await window.locator('.app-slot').screenshot()
      const secondOpened = await rpc('desktop_open', { app: test.id, state: before.result.state })
      if (secondOpened.result?.window_id) owned.add(secondOpened.result.window_id)
      const other = requireOk(secondOpened).result.window_id
      assert.notEqual(other, wid, 'second window must be distinct')
      await page.waitForTimeout(500)
      const otherBefore = requireOk(await rpc('desktop_read', { window_id: other }))
      row.mutation = test.action
        ? await rpc('desktop_call', { window_id: wid, ...test.action })
        : await rpc('desktop_update', { window_id: wid, patch: await test.patch(before.result.state) })
      requireOk(row.mutation)
      const after = requireOk(await rpc('desktop_read', { window_id: wid })); row.after = after
      assert.notDeepEqual(after.result.state, before.result.state, 'controlled state did not change')
      const otherAfter = requireOk(await rpc('desktop_read', { window_id: other }))
      assert.deepEqual(otherAfter.result.state, otherBefore.result.state, 'other same-app window changed')
      row.otherUnchanged = true
      requireOk(await rpc('desktop_call', { window_id: other, action: '$close' })); owned.delete(other)
      await window.screenshot({ path: path.join(output, 'evidence', `${test.id}-after.png`) })
      assert.notEqual(hash(await window.locator('.app-slot').screenshot()), hash(beforePixels), 'rendered app pixels did not change')
      row.pixelChanged = true
      const shot = requireOk(await rpc('desktop_screenshot', { window_id: wid }))
      assert.ok(shot.path, 'desktop_screenshot did not return a saved image')
      row.toolScreenshot = { success: true, path: shot.path }
      await fs.copyFile(shot.path, path.join(output, 'evidence', `${test.id}-tool${path.extname(shot.path)}`))
      row.invalid = await rpc('desktop_set', { window_id: wid, state: typeof test.bad === 'function' ? test.bad(after.result.state) : test.bad })
      assert.equal(row.invalid.success, false, 'invalid input reported success')
      row.recovery = requireOk(await rpc('desktop_set', { window_id: wid, state: after.result.state }))
      row.passed = true
    } catch (error) {
      row.error = String(error); row.passed = false
      console.error('FAIL', test.id, row.error)
      for (const wid of owned) await page.locator(`[data-window-id="${wid}"]`).screenshot({
        path: path.join(output, 'evidence', `${test.id}-failure-${wid}.png`), timeout: 4000,
      }).catch(() => {})
    } finally {
      row.ms = Date.now() - row.started
      result.push(row)
      await fs.writeFile(resultFile, JSON.stringify({ workspace: work, result, errors }, null, 2))
      for (const wid of owned) await rpc('desktop_call', { window_id: wid, action: '$close' }).catch(() => {})
      console.log(row.passed ? 'PASS' : 'FAIL', test.id, row.ms + 'ms')
    }
  }
} finally {
  await browser.close()
}
console.log('Results:', resultFile)
if (result.some(row => !row.passed) || errors.length) process.exitCode = 1
