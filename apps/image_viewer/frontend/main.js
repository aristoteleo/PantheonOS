/**
 * Image Viewer — the first shell builtin rebuilt as a packaged-app bundle.
 *
 * Runs in the app-host iframe on the standard bridge: `setup(app, root)`.
 * The shell hands the opened file's served URL in the init state
 * (PackagedApp.fileState), so showing an image needs no backend at all.
 * Zoom is shared state (two viewports of one window see one zoom), same as
 * the old in-shell component's appSync spec.
 */

export function setup(app, root) {
  root.innerHTML = `
    <style>
      .iv { display: flex; flex-direction: column; height: 100vh;
        background: repeating-conic-gradient(#171a21 0% 25%, #1b1f27 0% 50%) 50% / 18px 18px;
        font: 12.5px -apple-system, system-ui, sans-serif; }
      .iv[data-theme="light"] {
        background: repeating-conic-gradient(#e8e8ec 0% 25%, #f4f4f7 0% 50%) 50% / 18px 18px; }
      .canvas { flex: 1; min-height: 0; display: grid; place-items: center;
        overflow: auto; padding: 12px; cursor: zoom-in; }
      .canvas.zoomed { cursor: zoom-out; place-items: start; }
      .canvas img { max-width: 100%; max-height: 100%; object-fit: contain; }
      .canvas.zoomed img { max-width: none; max-height: none; }
      .bar { display: flex; justify-content: space-between; align-items: center;
        height: 24px; padding: 0 10px; flex: none; font-size: 11px;
        border-top: 1px solid rgba(128,128,128,.35); color: #9aa0ab;
        background: rgba(0,0,0,.25); }
      .iv[data-theme="light"] .bar { color: #5a6068; background: rgba(255,255,255,.5); }
      .hint { margin: auto; padding: 18px; color: #9aa0ab; }
      .hint.error { color: #f0a3a2; }
      .hintlet { opacity: .7; }
    </style>
    <div class="iv">
      <p class="hint" hidden></p>
      <div class="canvas" hidden><img alt=""></div>
      <footer class="bar" hidden>
        <span class="name"></span><span class="hintlet"></span>
      </footer>
    </div>`

  const iv = root.querySelector('.iv')
  const hint = root.querySelector('.hint')
  const canvas = root.querySelector('.canvas')
  const img = root.querySelector('img')
  const bar = root.querySelector('.bar')
  const nameEl = root.querySelector('.name')
  const hintlet = root.querySelector('.hintlet')

  let zoomed = false
  const paint = () => {
    canvas.classList.toggle('zoomed', zoomed)
    hintlet.textContent = zoomed ? 'Click to fit' : 'Click to zoom'
  }
  canvas.addEventListener('click', () => {
    zoomed = !zoomed
    paint()
    app.setState({ zoomed })
  })

  const show = (which, text) => {
    hint.hidden = which !== 'hint'
    canvas.hidden = which !== 'img'
    bar.hidden = which !== 'img'
    if (text !== undefined) { hint.textContent = text; hint.classList.toggle('error', which === 'hint' && !!text.error) }
  }
  const fail = (message) => {
    hint.hidden = false; canvas.hidden = true; bar.hidden = true
    hint.textContent = message; hint.classList.add('error')
  }

  app.onTheme((t) => iv.setAttribute('data-theme', t))

  app.onState(() => {
    const s = app.state || {}
    if (typeof s.zoomed === 'boolean' && s.zoomed !== zoomed) { zoomed = s.zoomed; paint() }
    if (!s.url) { show('hint', 'No file open — use File ▸ Open…'); return }
    if (img.src === s.url) return
    show('hint', 'Loading…')
    img.onload = () => { show('img'); paint() }
    img.onerror = () => fail(`Could not load ${s.name || s.path || 'image'}`)
    img.src = s.url
    nameEl.textContent = s.name || ''
    if (s.name) app.window.setTitle(s.name)
  })
}
