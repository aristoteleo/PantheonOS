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

  const describe = () => ({ path, dirty, saving, length: view?.state.doc.length ?? 0,
    lines: view?.state.doc.lines ?? 0, loaded: !!view })
  const publish = () => app.setState(describe())
  async function save() {
    if (!path || !view) throw new Error('No file is loaded')
    if (saving) throw new Error('A save is already in progress')
    if (!dirty) return describe()
    saving = true
    warnEl.hidden = true
    paintSave()
    try {
      const savedText = view.state.doc.toString()
      await app.fs.write(path, savedText)
      dirty = view.state.doc.toString() !== savedText
      savedEl.hidden = dirty
      setTimeout(() => { savedEl.hidden = true }, 1500)
    } catch (e) {
      warnEl.textContent = e instanceof Error ? e.message : String(e)
      warnEl.hidden = false
      throw e
    } finally {
      saving = false
      paintSave()
      publish()
    }
    return describe()
  }

  saveBtn.addEventListener('click', () => { void save().catch(() => {}) })
  root.ownerDocument.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 's') {
      e.preventDefault()
      void save().catch(() => {})
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
          if (u.docChanged) { dirty = true; paintSave(); linesEl.textContent = `${u.state.doc.lines.toLocaleString()} lines`; publish() }
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
      loadedUrl = s.url
      path = s.path || ''
      publish()
      if (name) app.window.setTitle(name)
    } catch (e) {
      show('hint', e instanceof Error ? e.message : String(e), true)
      throw e
    }
  }

  function replaceText({ text, expectedText } = {}) {
    if (!view) throw new Error('No file is loaded')
    if (typeof text !== 'string') throw new Error('replaceText requires a text string')
    const before = view.state.doc.toString()
    if (expectedText !== undefined && expectedText !== before) throw new Error('The document changed; read it again before replacing text')
    if (text !== before) view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: text } })
    return describe()
  }
  app.defineAction('getText', ({ offset = 0, limit = 100000 } = {}) => {
    if (!view) throw new Error('No file is loaded')
    if (!Number.isInteger(offset) || offset < 0 || !Number.isInteger(limit) || limit < 1 || limit > 100000) throw new Error('offset must be nonnegative and limit between 1 and 100000')
    const text = view.state.doc.sliceString(offset, offset + limit)
    return { ...describe(), offset, text, truncated: offset + text.length < view.state.doc.length }
  })
  app.defineAction('replaceText', replaceText)
  app.defineAction('save', save)
  app.onState(async (s, info) => {
    if (info?.reason === 'emit') return
    if (Object.prototype.hasOwnProperty.call(s || {}, 'content')) {
      throw new Error('Use replaceText to edit the document, then save to persist it')
    }
    if (!s?.url) { show('hint', 'No file open — use File ▸ Open…'); return }
    if (s.url !== loadedUrl) await load(s)
    else show('editor')
  })
}
