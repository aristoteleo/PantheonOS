/**
 * Text Viewer — syntax-highlighted editing, saved back to the pod.
 *
 * A bundled headed App on the app-host bridge (built by esbuild from
 * frontend-src/, committed as frontend/ — see build.sh). CodeMirror rather
 * than a textarea: it renders only the viewport, so a 100k-line file
 * scrolls instead of locking the tab, and it brings the grammars, each in
 * its own lazy chunk (languages.js).
 *
 * The file's bytes come from the served URL in the init state — the data
 * server streams the whole file, so there is no read-limit truncation and
 * no read-only mode. Saving writes through the bridge's fs broker
 * (caps.fs in the manifest is the contract).
 */

import { EditorState } from '@codemirror/state'
import { EditorView, lineNumbers, highlightActiveLine, keymap } from '@codemirror/view'
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands'
import { oneDark } from '@codemirror/theme-one-dark'
import { languageFor } from './languages.js'

const baseName = (p) => String(p || '').split('/').pop() || ''
const extensionOf = (n) => {
  const name = baseName(n).toLowerCase()
  const dot = name.lastIndexOf('.')
  return dot >= 0 ? name.slice(dot) : ''
}

export function setup(app, root) {
  root.innerHTML = `
    <style>
      .tv { display: flex; flex-direction: column; height: 100vh; background: #14171d;
        font: 12.5px -apple-system, system-ui, sans-serif; color: #c8cdd4; }
      .editor { flex: 1; min-height: 0; overflow: hidden; }
      .editor .cm-editor { height: 100%; font-size: 12.5px; }
      .editor .cm-scroller { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
        line-height: 1.55; }
      .hint { margin: 0; padding: 18px; color: #9aa0ab; }
      .hint.error { color: #f0a3a2; }
      .bar { display: flex; align-items: center; gap: 10px; height: 22px; padding: 0 10px;
        border-top: 1px solid rgba(255,255,255,.07); background: #1b1f27;
        color: #9aa0ab; font-size: 11px; flex: none; }
      .warn { color: #d0a029; }
      .spacer { flex: 1; }
      .saved { color: #3fb950; }
      .save { padding: 2px 12px; border-radius: 6px; border: 1px solid rgba(255,255,255,.16);
        background: rgba(255,255,255,.06); color: #c8cdd4; font-size: 11.5px; cursor: pointer; }
      .save:disabled { opacity: .45; cursor: default; }
      .save:not(:disabled):hover { background: rgba(123,104,238,.25);
        border-color: rgba(123,104,238,.5); }
    </style>
    <div class="tv">
      <p class="hint" hidden></p>
      <div class="editor" hidden></div>
      <footer class="bar" hidden>
        <span class="lines"></span>
        <span class="warn" hidden></span>
        <span class="spacer"></span>
        <span class="saved" hidden>Saved</span>
        <button class="save">Saved</button>
      </footer>
    </div>`

  const hint = root.querySelector('.hint')
  const editorEl = root.querySelector('.editor')
  const bar = root.querySelector('.bar')
  const linesEl = root.querySelector('.lines')
  const warnEl = root.querySelector('.warn')
  const savedEl = root.querySelector('.saved')
  const saveBtn = root.querySelector('.save')

  let view = null
  let loadedUrl = ''
  let path = ''
  let dirty = false
  let saving = false

  const show = (which, message, isError) => {
    hint.hidden = which !== 'hint'
    editorEl.hidden = which !== 'editor'
    bar.hidden = which !== 'editor'
    if (which === 'hint') {
      hint.textContent = message
      hint.classList.toggle('error', !!isError)
    }
  }

  const paintSave = () => {
    saveBtn.disabled = !dirty || saving
    saveBtn.textContent = saving ? 'Saving…' : dirty ? 'Save' : 'Saved'
    saveBtn.title = dirty ? 'Save (⌘S)' : 'No changes'
  }

  async function save() {
    if (!path || !view || !dirty || saving) return
    saving = true
    warnEl.hidden = true
    paintSave()
    try {
      await app.fs.write(path, view.state.doc.toString())
      dirty = false
      savedEl.hidden = false
      setTimeout(() => { savedEl.hidden = true }, 1500)
    } catch (e) {
      warnEl.textContent = e instanceof Error ? e.message : String(e)
      warnEl.hidden = false
    } finally {
      saving = false
      paintSave()
    }
  }

  saveBtn.addEventListener('click', save)
  root.ownerDocument.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 's') {
      e.preventDefault()
      void save()
    }
  })

  async function load(s) {
    show('hint', 'Loading…')
    try {
      const res = await fetch(s.url)
      if (!res.ok) throw new Error(`could not fetch ${s.name || s.path}: HTTP ${res.status}`)
      const text = await res.text()
      const name = baseName(s.name || s.path)
      const language = await languageFor(name, extensionOf(name))

      const extensions = [
        lineNumbers(),
        highlightActiveLine(),
        history(),
        keymap.of([...defaultKeymap, ...historyKeymap]),
        EditorView.updateListener.of((u) => {
          if (u.docChanged && !dirty) { dirty = true; paintSave() }
        }),
        EditorView.lineWrapping,
        oneDark,
        ...(language ? [language] : []),
      ]

      view?.destroy()
      view = new EditorView({
        state: EditorState.create({ doc: text, extensions }),
        parent: editorEl,
      })
      dirty = false
      linesEl.textContent = `${view.state.doc.lines.toLocaleString()} lines`
      paintSave()
      show('editor')
      if (name) app.window.setTitle(name)
    } catch (e) {
      show('hint', e instanceof Error ? e.message : String(e), true)
    }
  }

  app.onState(() => {
    const s = app.state || {}
    if (!s.url) { show('hint', 'No file open — use File ▸ Open…'); return }
    if (s.url === loadedUrl) return
    loadedUrl = s.url
    path = s.path || ''
    void load(s)
  })
}
