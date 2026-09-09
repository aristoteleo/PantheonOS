// NODE_PATH=/path/to/pantheon-ui/node_modules node --test apps/text_viewer/frontend-src/main.test.mjs
import { createRequire } from 'node:module'
import { test, afterEach } from 'node:test'
import assert from 'node:assert/strict'
const { JSDOM } = createRequire(import.meta.url)('jsdom')
const dom = new JSDOM('<body></body>', { url: 'https://viewer.test/', pretendToBeVisual: true })
for (const name of ['window', 'document', 'location', 'MutationObserver', 'HTMLElement', 'Window']) globalThis[name] = dom.window[name]
Object.defineProperty(globalThis, 'navigator', { value: dom.window.navigator, configurable: true })
globalThis.requestAnimationFrame = dom.window.requestAnimationFrame.bind(dom.window)
globalThis.cancelAnimationFrame = dom.window.cancelAnimationFrame.bind(dom.window)
dom.window.Range.prototype.getClientRects = () => []
dom.window.Range.prototype.getBoundingClientRect = () => ({ left: 0, top: 0, bottom: 0, right: 0, width: 0, height: 0 })
const { setup } = await import('../frontend/main.js')
let viewers = []
afterEach(() => { for (const v of viewers) { v.control.dispose(); v.root.remove() }; viewers = [] })
async function viewer(text = '# Document\n\nHello **world**.', name = 'readme.md', mime = 'text/plain') {
  let receive, theme, state = {}, write = async () => {}, actions = {}, writes = []
  globalThis.fetch = async () => ({ ok: true, text: async () => text, headers: { get: () => mime } })
  const root = document.createElement('div'); document.body.append(root)
  const app = { window: { setTitle() {} }, fs: { async write(path, text) { writes.push([path, text]); await write(path, text) } },
    onTheme(fn) { theme = fn; fn('light') }, onState(fn) { receive = fn },
    setState(patch) { state = { ...state, ...patch }; void receive?.(state, { reason: 'emit' }) }, defineAction(name, fn) { actions[name] = fn } }
  const control = setup(app, root)
  const v = { root, actions, control, writes, state: () => state, theme, setWrite: fn => { write = fn }, receive,
    click: name => root.querySelector(`[data-action="${name}"]`).click(), tick: () => new Promise(resolve => setTimeout(resolve, 20)) }
  viewers.push(v)
  state = { url: 'https://viewer.test/files/' + name, name, path: 'Desktop/' + name }
  await receive(state, { reason: 'init' })
  return v
}
test('Markdown defaults to preview with collapsible frontmatter, tables and safe relative links', async () => {
  const text = '---\nname: sample\n---\n# Title\n\n| A | B |\n| - | - |\n| 1 | 2 |\n\n[Next](next.md)\n\n[Heading](#title)'
  const v = await viewer(text)
  assert.equal(v.state().mode, 'preview')
  assert.equal(v.root.querySelector('.tv-editor').hidden, true)
  assert.equal(v.root.querySelector('.tv-preview h1').textContent, 'Title')
  assert.equal(v.root.querySelector('.tv-metadata').open, false)
  assert.equal(v.root.querySelector('.tv-metadata pre').textContent, 'name: sample')
  assert.equal(v.root.querySelectorAll('td').length, 2)
  assert.equal(v.root.querySelector('a').href, 'https://viewer.test/files/next.md')
  assert.equal(v.root.querySelector('a').rel, 'noopener noreferrer')
  assert.equal(v.actions.getText().text, text)
})
test('untrusted Markdown cannot inject scripts, events, CSS or executable links', async () => {
  const v = await viewer('# Safe\n<script>alert(1)</script><style>body{display:none}</style><img src="x" onerror="alert(1)"><a href="javascript:alert(1)" style="position:fixed" data-action="save">bad</a><iframe src="https://evil.test"></iframe>')
  const preview = v.root.querySelector('.tv-preview')
  assert.equal(preview.querySelector('script,style,iframe,[onerror],[style],[data-action]'), null)
  assert.equal(preview.querySelector('a').hasAttribute('href'), false)
})
test('Preview/Edit retains unsaved source; failed saves preserve edits and successful saves persist exact text', async () => {
  const v = await viewer()
  v.click('edit'); await v.tick()
  assert.equal(v.root.querySelector('.tv-editor').hidden, false)
  await v.actions.replaceText({ text: '# Changed\n\nNew text.' })
  assert.equal(v.state().dirty, true)
  v.click('preview'); await v.tick()
  assert.equal(v.root.querySelector('h1').textContent, 'Changed')
  assert.equal(v.root.querySelector('.tv-status').textContent, 'Unsaved changes')
  v.setWrite(async () => { throw new Error('Disconnected') })
  await assert.rejects(v.actions.save(), /Disconnected/)
  assert.equal(v.state().dirty, true)
  assert.match(v.root.querySelector('.tv-message').textContent, /Disconnected/)
  v.setWrite(async () => {})
  await v.actions.save()
  assert.equal(v.state().dirty, false)
  assert.deepEqual(v.writes.at(-1), ['Desktop/readme.md', '# Changed\n\nNew text.'])
  await assert.rejects(v.actions.replaceText({ text: 'oops', expectedText: 'outdated' }), /document changed/)
})
test('concurrent editing during save remains unsaved, and replacing a dirty document is refused', async () => {
  const v = await viewer(); await v.actions.replaceText({ text: '# First edit' })
  let finish
  v.setWrite(() => new Promise(resolve => { finish = resolve }))
  const saving = v.actions.save()
  await v.actions.replaceText({ text: '# Second edit' })
  finish(); await saving
  assert.equal(v.state().dirty, true)
  assert.equal(v.actions.getText().text, '# Second edit')
  assert.equal(v.writes[0][1], '# First edit')
  await assert.rejects(v.receive({ url: 'https://viewer.test/other.md' }), /Save the current document/)
})
test('plain files stay in editor; MIME detection and each window mode/theme/wrap stay independent', async () => {
  const a = await viewer(), b = await viewer(), code = await viewer('print(1)', 'demo.py')
  const byMime = await viewer('# Markdown', 'document', 'text/markdown; charset=utf-8')
  assert.equal(byMime.state().markdown, true)
  assert.equal(code.state().mode, 'edit'); assert.equal(code.root.querySelector('.tv-modes').hidden, true)
  a.click('edit'); await a.tick(); a.click('wrap'); a.theme('dark')
  assert.equal(a.state().wrap, false); assert.equal(a.root.querySelector('.tv').dataset.theme, 'dark')
  assert.equal(b.state().mode, 'preview'); assert.equal(b.state().wrap, true)
  assert.equal(b.root.querySelector('.tv').dataset.theme, 'light')
  a.click('find'); await a.tick()
  assert.ok(a.root.querySelector('.cm-search'))
  assert.equal(b.root.querySelector('.cm-search'), null)
})
test('an older fetch cannot replace a newer document, failed loads can retry', async () => {
  const v = await viewer()
  let resolveOld
  globalThis.fetch = url => url.endsWith('old.md') ? new Promise(resolve => { resolveOld = resolve }) :
    Promise.resolve({ ok: true, text: async () => '# New', headers: { get: () => 'text/plain' } })
  const old = v.receive({ url: 'https://viewer.test/old.md', name: 'old.md', path: 'old.md' })
  await v.receive({ url: 'https://viewer.test/new.md', name: 'new.md', path: 'new.md' })
  resolveOld({ ok: true, text: async () => '# Old', headers: { get: () => '' } }); await old
  assert.equal(v.root.querySelector('h1').textContent, 'New')
  globalThis.fetch = async () => ({ ok: false, status: 503 })
  await assert.rejects(v.receive({ url: 'https://viewer.test/fail.md', name: 'fail.md' }), /HTTP 503/)
  assert.equal(v.root.querySelector('[data-action="retry"]').hidden, false)
  globalThis.fetch = async () => ({ ok: true, text: async () => '# Recovered', headers: { get: () => '' } })
  v.click('retry'); await v.tick()
  assert.equal(v.root.querySelector('h1').textContent, 'Recovered')
})
