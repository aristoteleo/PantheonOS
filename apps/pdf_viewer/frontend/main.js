/**
 * PDF Viewer — a bundled headed App on the app-host bridge.
 *
 * The opened file's served URL arrives in the init state
 * (PackagedApp.fileState) and is handed to the browser's own PDF engine in
 * an iframe. Shipping pdf.js would mean carrying a renderer to duplicate
 * something every target browser already has. No backend, no shared state.
 */

export function setup(app, root) {
  root.innerHTML = `
    <style>
      .pv { display: flex; height: 100vh; background: #1b1f27;
        font: 12.5px -apple-system, system-ui, sans-serif; }
      .pv[data-theme="light"] { background: #f4f4f7; }
      .pv iframe { flex: 1; border: 0; }
      .hint { margin: auto; padding: 18px; color: #9aa0ab; }
      .hint.error { color: #f0a3a2; }
    </style>
    <div class="pv">
      <p class="hint" hidden></p>
      <iframe hidden title=""></iframe>
    </div>`

  const pv = root.querySelector('.pv')
  const hint = root.querySelector('.hint')
  const frame = root.querySelector('iframe')

  const show = (message, isError) => {
    hint.hidden = message === undefined
    frame.hidden = message !== undefined
    if (message !== undefined) {
      hint.textContent = message
      hint.classList.toggle('error', !!isError)
    }
  }

  app.onTheme((t) => pv.setAttribute('data-theme', t))

  app.onState(() => {
    const s = app.state || {}
    if (!s.url) { show('No file open — use File ▸ Open…'); return }
    if (frame.src === s.url) return
    frame.title = s.name || 'PDF'
    frame.src = s.url
    show(undefined)
    if (s.name) app.window.setTitle(s.name)
  })
}
