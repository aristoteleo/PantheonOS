/** Atrium Image Viewer. A normal desktop window; no page-wide overlay. */
export function setup(app, root) {
  const icons = {
    out: '<circle cx="10" cy="10" r="6"/><path d="m15 15 5 5M7 10h6"/>',
    in: '<circle cx="10" cy="10" r="6"/><path d="m15 15 5 5M7 10h6m-3-3v6"/>',
    fit: '<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5M7 7h10v10H7z"/>',
    rotate: '<path d="M3 10a9 9 0 1 1 2 8M3 3v7h7"/>',
    download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
    maximize: '<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/>',
  }
  const button = (action, label, icon, text = '') => `<button type="button" data-action="${action}" aria-label="${label}" title="${label}">${icon ? `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[icon]}</svg>` : ''}${text}</button>`
  root.innerHTML = `
    <style>
      .iv { --bg:#171b23; --fg:#e7eaf0; --muted:#a1a9b8; --border:#343b49; --hover:#2b3343; --accent:#b7a4ff;
        display:flex; flex-direction:column; height:100vh; overflow:hidden; color:var(--fg); background:var(--bg); font:13px -apple-system,system-ui,sans-serif; }
      .iv[data-theme="light"] { --bg:#fafafa; --fg:#232936; --muted:#626c7a; --border:#dce0e7; --hover:#ececf3; --accent:#6850b5; }
      .iv *, .iv *::before, .iv *::after { box-sizing:border-box; }
      .iv [hidden] { display:none !important; }
      .iv .toolbar { display:flex; flex-wrap:wrap; align-items:center; gap:4px; padding:6px 8px; border-bottom:1px solid var(--border); flex:none; }
      .iv button { display:inline-flex; align-items:center; justify-content:center; gap:6px; height:30px; min-width:30px; padding:4px 7px; border:1px solid transparent; border-radius:6px; background:transparent; color:var(--fg); font:inherit; cursor:pointer; }
      .iv button:hover { background:var(--hover); }
      .iv button:focus-visible, .iv .canvas:focus-visible { outline:2px solid var(--accent); outline-offset:-2px; }
      .iv button[aria-pressed="true"] { color:var(--accent); background:var(--hover); }
      .iv button:disabled { opacity:.4; cursor:default; }
      .iv svg { width:18px; height:18px; }
      .iv .zoom { min-width:54px; text-align:center; font-variant-numeric:tabular-nums; }
      .iv .sep { width:1px; height:18px; background:var(--border); margin:0 3px; }
      .iv .trailing { margin-left:auto; display:flex; gap:4px; }
      .iv .canvas { position:relative; flex:1; min-height:0; overflow:hidden; touch-action:none; user-select:none;
        background:repeating-conic-gradient(#1c212b 0% 25%,#232a36 0% 50%) 50% / 20px 20px; }
      .iv[data-theme="light"] .canvas { background:repeating-conic-gradient(#eceef2 0% 25%,#f7f8fa 0% 50%) 50% / 20px 20px; }
      .iv .canvas img { position:absolute; left:50%; top:50%; max-width:none; max-height:none; transform-origin:center; pointer-events:none; }
      .iv .hint { position:absolute; inset:0; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:10px; padding:24px; text-align:center; color:var(--muted); }
      .iv .bar { display:flex; align-items:center; gap:12px; padding:5px 10px; min-height:28px; flex:none; border-top:1px solid var(--border); color:var(--muted); font-size:11px; }
      .iv .name { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; flex:1; }
      .iv .dimensions { flex:none; font-variant-numeric:tabular-nums; }
      .iv .message { color:var(--fg); margin:0; padding:8px 12px; border-top:1px solid var(--border); }
    </style>
    <section class="iv" aria-label="Image Viewer">
      <nav class="toolbar" aria-label="Image controls">
        ${button('zoom_out', 'Zoom out (−)', 'out')}<span class="zoom">100%</span>${button('zoom_in', 'Zoom in (+)', 'in')}
        <span class="sep"></span>${button('fit', 'Fit to window (0)', 'fit', 'Fit')}${button('actual', 'Actual size (1)', '', '100%')}
        <span class="sep"></span>${button('rotate', 'Rotate clockwise (R)', 'rotate')}
        <div class="trailing">${button('download', 'Download original image', 'download')}${button('maximize', 'Maximize / restore window', 'maximize')}</div>
      </nav>
      <div class="canvas" tabindex="0" aria-label="Image canvas. Scroll to zoom; drag to pan.">
        <img alt="" draggable="false" hidden>
        <div class="hint" role="status"><span>No image open. Open an image from Files or a notebook.</span><button data-action="retry" hidden>Try again</button></div>
      </div>
      <p class="message" role="status" hidden></p>
      <footer class="bar"><span class="name"></span><span class="dimensions"></span></footer>
    </section>`
  const iv = root.querySelector('.iv'), canvas = root.querySelector('.canvas'), img = root.querySelector('img')
  const hint = root.querySelector('.hint'), hintText = hint.querySelector('span'), retry = root.querySelector('[data-action="retry"]')
  const message = root.querySelector('.message'), nameEl = root.querySelector('.name')
  const zoomLabel = root.querySelector('.zoom'), dimensions = root.querySelector('.dimensions')
  let url = '', name = '', width = 0, height = 0, zoom = 1, fit = true, rotation = 0, x = 0, y = 0, drag = null, generation = 0
  const ready = () => width > 0 && height > 0
  const bounds = () => rotation % 180 ? [height, width] : [width, height]
  const fitZoom = () => { const [w, h] = bounds(); return Math.min(1, Math.max(.001, (canvas.clientWidth - 24) / w), Math.max(.001, (canvas.clientHeight - 24) / h)) }
  const state = () => ({ loaded: ready(), width, height, zoom, fit, rotation })
  const publish = () => app.setState(state())
  function paint() {
    if (ready()) {
      if (fit) { zoom = fitZoom(); x = 0; y = 0 }
      const [w, h] = bounds()
      const maxX = Math.max(0, (w * zoom - canvas.clientWidth) / 2 + 12)
      const maxY = Math.max(0, (h * zoom - canvas.clientHeight) / 2 + 12)
      x = Math.max(-maxX, Math.min(maxX, x)); y = Math.max(-maxY, Math.min(maxY, y))
      img.style.width = `${width}px`; img.style.height = `${height}px`
      img.style.transform = `translate(-50%, -50%) translate(${x}px, ${y}px) rotate(${rotation}deg) scale(${zoom})`
      canvas.style.cursor = drag ? 'grabbing' : maxX || maxY ? 'grab' : 'default'
    }
    zoomLabel.textContent = `${Math.round(zoom * 1000) / 10}%`
    dimensions.textContent = ready() ? `${width.toLocaleString()} × ${height.toLocaleString()} px` : ''
    root.querySelectorAll('.toolbar button').forEach(el => { el.disabled = !ready() && el.dataset.action !== 'maximize' })
    root.querySelector('[data-action="fit"]').setAttribute('aria-pressed', String(fit))
    root.querySelector('[data-action="actual"]').setAttribute('aria-pressed', String(!fit && zoom === 1))
  }
  function scale(next, anchor) {
    if (!ready()) return
    const previous = zoom
    zoom = Math.max(.01, Math.min(16, next)); fit = false
    const ax = anchor?.x || 0, ay = anchor?.y || 0
    x = ax - (ax - x) * zoom / previous; y = ay - (ay - y) * zoom / previous
    paint(); publish()
  }
  function applyView(action) {
    if (!ready()) return
    if (action === 'zoom_in') return scale(zoom * 1.25)
    if (action === 'zoom_out') return scale(zoom / 1.25)
    if (action === 'fit' || action === 'reset') { fit = true; x = 0; y = 0; if (action === 'reset') rotation = 0 }
    if (action === 'actual') { fit = false; zoom = 1; x = 0; y = 0 }
    if (action === 'rotate') rotation = (rotation + 90) % 360
    paint(); publish()
  }
  async function load(nextUrl) {
    const turn = ++generation
    width = height = 0; img.hidden = true; hint.hidden = false; retry.hidden = true; message.hidden = true
    hintText.textContent = 'Loading image…'; paint()
    try {
      const loaded = await new Promise((resolve, reject) => {
        const image = new Image(); image.crossOrigin = 'anonymous'
        image.onload = () => resolve(image)
        image.onerror = () => reject(new Error('Could not load this image. Check the connection and try again.'))
        image.src = nextUrl
      })
      if (turn !== generation) return
      width = loaded.naturalWidth; height = loaded.naturalHeight
      img.src = nextUrl; img.alt = name || 'Image'; img.hidden = false; hint.hidden = true
      paint(); publish()
    } catch (error) {
      if (turn !== generation) return
      hintText.textContent = error.message; retry.hidden = false
      throw error
    }
  }
  async function download() {
    const response = await fetch(url)
    if (!response.ok) throw new Error('Could not download the image. Try again.')
    const blob = await response.blob(), objectUrl = URL.createObjectURL(blob)
    const a = document.createElement('a'); a.href = objectUrl; a.download = name || 'image.png'
    document.body.append(a); a.click(); a.remove()
    setTimeout(() => URL.revokeObjectURL(objectUrl), 1000)
  }
  function report(error) { message.textContent = error.message || String(error); message.hidden = false }
  const click = event => {
    const action = event.target.closest('[data-action]')?.dataset.action
    if (action === 'download') void download().catch(report)
    else if (action === 'retry') void load(url).catch(report)
    else if (action === 'maximize') app.window.toggleMaximize?.()
    else applyView(action)
  }
  iv.addEventListener('click', click)
  canvas.addEventListener('dblclick', () => applyView(fit ? 'actual' : 'fit'))
  canvas.addEventListener('wheel', event => {
    event.preventDefault()
    if (!event.deltaY) return
    const rect = canvas.getBoundingClientRect()
    scale(zoom * Math.exp(-Math.max(-80, Math.min(80, event.deltaY)) * .006), { x: event.clientX - rect.left - rect.width / 2, y: event.clientY - rect.top - rect.height / 2 })
  }, { passive: false })
  canvas.addEventListener('pointerdown', event => {
    if (event.button !== 0 || !ready() || event.target.closest('button')) return
    canvas.focus(); drag = { id: event.pointerId, x: event.clientX - x, y: event.clientY - y }
    canvas.setPointerCapture(event.pointerId); paint()
  })
  canvas.addEventListener('pointermove', event => {
    if (!drag || drag.id !== event.pointerId) return
    x = event.clientX - drag.x; y = event.clientY - drag.y; paint()
  })
  const endDrag = () => { drag = null; paint() }
  canvas.addEventListener('pointerup', endDrag); canvas.addEventListener('pointercancel', endDrag); canvas.addEventListener('lostpointercapture', endDrag)
  iv.addEventListener('keydown', event => {
    if (event.ctrlKey || event.metaKey || event.altKey) return
    const action = { '+': 'zoom_in', '=': 'zoom_in', '-': 'zoom_out', '0': 'fit', '1': 'actual', r: 'rotate', R: 'rotate' }[event.key]
    if (action) { event.preventDefault(); applyView(action) }
    if (event.key === 'Escape') { event.preventDefault(); applyView('reset') }
  })
  const resize = new ResizeObserver(() => paint()); resize.observe(canvas)
  app.onTheme(theme => iv.setAttribute('data-theme', theme))
  root.querySelector('[data-action="maximize"]').hidden = !app.window.toggleMaximize
  app.onState(async s => {
    if (typeof s?.fit === 'boolean') fit = s.fit
    else if (typeof s?.zoomed === 'boolean') { fit = !s.zoomed; if (s.zoomed) zoom = 1 }
    if (typeof s?.zoom === 'number' && Number.isFinite(s.zoom) && s.zoom > 0) zoom = Math.max(.001, Math.min(16, s.zoom))
    if (typeof s?.rotation === 'number' && Number.isFinite(s.rotation)) rotation = ((Math.round(s.rotation / 90) * 90) % 360 + 360) % 360
    name = s?.name || ''; nameEl.textContent = name; nameEl.title = name
    if (name) app.window.setTitle(name)
    if (!s?.url) { paint(); return }
    if (url === s.url) { paint(); return }
    url = s.url; x = 0; y = 0
    await load(url)
  })
  for (const action of ['zoom_in', 'zoom_out', 'fit', 'actual', 'rotate', 'reset']) {
    app.defineAction(action, () => {
      if (!ready()) throw new Error('Wait for the image to load before changing its view')
      applyView(action); return state()
    })
  }
  paint()
  // Primarily for embedded hosts/tests; iframe teardown also releases observers.
  return { dispose() { generation++; resize.disconnect() } }
}
