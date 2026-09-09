/** Atrium Text Viewer: a window-local document reader and CodeMirror editor. */
import { Compartment, EditorState } from '@codemirror/state'
import { EditorView, lineNumbers, highlightActiveLine, keymap } from '@codemirror/view'
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands'
import { defaultHighlightStyle, syntaxHighlighting } from '@codemirror/language'
import { search, searchKeymap, openSearchPanel } from '@codemirror/search'
import { oneDark } from '@codemirror/theme-one-dark'
import { languageFor } from './languages.js'
import styles from './styles.css'

const baseName = p => String(p || '').split('/').pop() || ''
const extensionOf = n => { const name = baseName(n).toLowerCase(); const dot = name.lastIndexOf('.'); return dot >= 0 ? name.slice(dot) : '' }
const markdownFile = (name, mime = '') => /\.(md|markdown|mdown|mkd)$/i.test(name) || /^(text\/(markdown|x-markdown))(;|$)/i.test(mime)
const icons = {
  file: '<path d="M14 2H5v20h14V7zM14 2v6h5M8 12h8m-8 4h6"/>',
  preview: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
  edit: '<path d="m16 3 5 5-12 12-6 1 1-6Zm-11 12 5 5M14 5l5 5"/>',
  find: '<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/>',
  wrap: '<path d="M3 5h18M3 10h13a4 4 0 0 1 0 8h-4m3-3-3 3 3 3M3 16h4"/>',
  copy: '<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M15 8V3H3v13h5"/>',
  save: '<path d="M4 3h14l3 3v15H3V3Zm3 0v6h10V3M7 21v-8h10v8"/>',
}
const icon = name => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name]}</svg>`
const button = (name, label, title = label) => `<button type="button" data-action="${name}" title="${title}" aria-label="${title}">${icon(name)}<span class="tv-label">${label}</span></button>`

export function setup(app, root) {
  root.innerHTML = `<style>${styles}</style>
    <section class="tv" aria-label="Text Viewer" tabindex="-1">
      <nav class="tv-toolbar" aria-label="Document controls" hidden>
        <div class="tv-file-info">${icon('file')}<span class="tv-title">Text Viewer</span></div>
        <div class="tv-modes" role="group" aria-label="Markdown view" hidden>${button('preview', 'Preview')}${button('edit', 'Edit')}</div>
        <span class="tv-format"></span>
        <div class="tv-actions">${button('find', 'Find', 'Find in source (⌘/Ctrl F)')}${button('wrap', 'Wrap', 'Toggle line wrapping')}${button('copy', 'Copy', 'Copy source text')}
          <button type="button" class="tv-save" data-action="save" title="Save (⌘/Ctrl S)">${icon('save')}<span>Save</span></button>
        </div>
      </nav>
      <div class="tv-message" role="status" hidden><span></span><button type="button" data-action="dismiss" aria-label="Dismiss message">×</button></div>
      <div class="tv-body">
        <div class="tv-hint"><p>No file open. Open a document from Files.</p><button type="button" data-action="retry" hidden>Try again</button></div>
        <div class="tv-editor" hidden></div>
        <div class="tv-preview" aria-label="Markdown preview" tabindex="0" hidden></div>
      </div>
      <footer class="tv-footer" hidden><span class="tv-kind"></span><span class="tv-lines"></span><span class="tv-words"></span><span class="tv-cursor"></span><span class="tv-status" role="status"></span><span>UTF-8</span></footer>
    </section>`
  const $ = selector => root.querySelector(selector)
  const tv = $('.tv'), editorEl = $('.tv-editor'), previewEl = $('.tv-preview'), hint = $('.tv-hint')
  const toolbar = $('.tv-toolbar'), footer = $('.tv-footer'), modes = $('.tv-modes'), saveBtn = $('.tv-save')
  const status = $('.tv-status'), message = $('.tv-message'), wrapBtn = $('[data-action="wrap"]')
  const themeSlot = new Compartment(), wrapSlot = new Compartment()
  let view = null, path = '', loadedUrl = '', requested = null, savedDoc = null, saving = false, isMarkdown = false
  let mode = 'edit', wrap = true, generation = 0, disposed = false, loadAbort = null, previewTimer = null
  let renderedText = null, previewJob = 0, loading = false
  const dirty = () => !!view && !view.state.doc.eq(savedDoc)
  const describe = () => ({ path, dirty: dirty(), saving, length: view?.state.doc.length ?? 0,
    lines: view?.state.doc.lines ?? 0, loaded: !!view && !loading, mode, markdown: isMarkdown, wrap })
  const publish = () => { if (!disposed) app.setState(describe()) }
  function report(error, tone = 'error') {
    message.querySelector('span').textContent = error instanceof Error ? error.message : String(error)
    message.dataset.tone = tone; message.hidden = false
  }
  function paint() {
    const changed = dirty()
    saveBtn.disabled = !view || !changed || saving || loading
    saveBtn.querySelector('span').textContent = saving ? 'Saving…' : 'Save'
    status.textContent = loading ? 'Loading…' : saving ? 'Saving…' : changed ? 'Unsaved changes' : view ? 'Saved' : ''
    status.dataset.dirty = String(changed)
    modes.hidden = !isMarkdown; $('.tv-format').hidden = isMarkdown
    for (const name of ['preview', 'edit']) $('[data-action="' + name + '"]').setAttribute('aria-pressed', String(mode === name))
    wrapBtn.hidden = mode !== 'edit'; wrapBtn.setAttribute('aria-pressed', String(wrap))
    if (view) {
      $('.tv-lines').textContent = `${view.state.doc.lines.toLocaleString()} lines`
      $('.tv-words').hidden = !isMarkdown
      $('.tv-cursor').hidden = mode !== 'edit'
      if (mode === 'edit') {
        const head = view.state.selection.main.head, line = view.state.doc.lineAt(head)
        $('.tv-cursor').textContent = `Ln ${line.number}, Col ${head - line.from + 1}`
      }
    }
  }
  function updateWords() {
    if (!view || !isMarkdown) return
    const text = view.state.doc.toString().trim()
    $('.tv-words').textContent = `${text ? text.split(/\s+/u).length.toLocaleString() : 0} words`
  }
  function themeExtension(theme) {
    return theme === 'dark' ? oneDark : [syntaxHighlighting(defaultHighlightStyle), EditorView.theme({
      '&': { color: '#242936', backgroundColor: '#fff' },
      '.cm-gutters': { color: '#8b93a1', backgroundColor: '#fff', border: 'none' },
      '.cm-activeLine': { backgroundColor: '#f3f3fa' },
      '&.cm-focused .cm-selectionBackground, .cm-selectionBackground': { backgroundColor: '#ded5f6' },
      '.cm-cursor': { borderLeftColor: '#6750bd' },
    })]
  }
  async function renderPreview() {
    if (!view || !isMarkdown || disposed) return
    const text = view.state.doc.toString()
    if (text === renderedText) return
    const job = ++previewJob
    const { renderMarkdown } = await import('./markdown.js')
    if (disposed || job !== previewJob || !view || text !== view.state.doc.toString()) return
    const scroll = previewEl.scrollTop
    renderMarkdown(text, previewEl, loadedUrl)
    renderedText = text; previewEl.scrollTop = scroll
  }
  async function setMode(next) {
    if (!view || loading || disposed) return
    mode = isMarkdown && next === 'preview' ? 'preview' : 'edit'
    editorEl.hidden = mode !== 'edit'; previewEl.hidden = mode !== 'preview'
    paint()
    if (mode === 'preview') await renderPreview()
    else { view.requestMeasure(); view.focus() }
    publish()
  }
  async function save() {
    if (!path || !view || loading) throw new Error('No file is loaded')
    if (saving) throw new Error('A save is already in progress')
    if (!dirty()) return describe()
    const targetView = view, targetPath = path, doc = view.state.doc, text = doc.toString()
    saving = true; message.hidden = true; paint(); publish()
    try {
      await app.fs.write(targetPath, text)
      if (view === targetView && path === targetPath) savedDoc = doc
    } catch (error) { report(error); throw error }
    finally { saving = false; paint(); publish() }
    return describe()
  }
  async function load(s) {
    const turn = ++generation
    loadAbort?.abort(); loadAbort = new AbortController()
    requested = s; loading = true; message.hidden = true
    hint.hidden = false; hint.querySelector('p').textContent = 'Loading document…'
    $('[data-action="retry"]').hidden = true; toolbar.hidden = footer.hidden = editorEl.hidden = previewEl.hidden = true
    paint()
    try {
      const res = await fetch(s.url, { signal: loadAbort.signal })
      if (!res.ok) throw new Error(`Could not open ${baseName(s.name || s.path) || 'this file'} (HTTP ${res.status}).`)
      const text = await res.text(), name = baseName(s.name || s.path || new URL(s.url, location.href).pathname)
      const markdown = markdownFile(name, s.mime || s.mimeType || res.headers?.get('content-type') || '')
      const language = await languageFor(name, markdown ? '.md' : extensionOf(name))
      if (disposed || turn !== generation) return
      view?.destroy(); view = null; previewJob++; renderedText = null; previewEl.replaceChildren(); previewEl.scrollTop = 0
      path = s.path || ''; loadedUrl = s.url; isMarkdown = markdown
      mode = markdown ? (s.mode === 'edit' ? 'edit' : 'preview') : 'edit'; wrap = s.wrap !== false
      view = new EditorView({ parent: editorEl, state: EditorState.create({ doc: text, extensions: [
        lineNumbers(), highlightActiveLine(), history(), search({ top: true }),
        keymap.of([...searchKeymap, ...defaultKeymap, ...historyKeymap]),
        wrapSlot.of(wrap ? EditorView.lineWrapping : []), themeSlot.of(themeExtension(tv.dataset.theme)),
        ...(language ? [language] : []),
        EditorView.updateListener.of(update => {
          if (update.docChanged) {
            updateWords(); paint(); publish()
            if (mode === 'preview') {
              clearTimeout(previewTimer)
              previewTimer = setTimeout(() => { void renderPreview().catch(report) }, 100)
            }
          } else if (update.selectionSet) paint()
        }),
      ] }) })
      savedDoc = view.state.doc; updateWords()
      $('.tv-title').textContent = name || 'Untitled'; $('.tv-title').title = name
      $('.tv-file-info').title = s.path || name
      $('.tv-format').textContent = $('.tv-kind').textContent = markdown ? 'Markdown' : extensionOf(name).slice(1).toUpperCase() || 'Plain text'
      if (name) app.window.setTitle(name)
      loading = false; hint.hidden = true; toolbar.hidden = footer.hidden = false
      await setMode(mode)
    } catch (error) {
      if (disposed || turn !== generation) return
      loading = false
      hint.hidden = false; hint.querySelector('p').textContent = error.message || String(error)
      $('[data-action="retry"]').hidden = false; paint()
      throw error
    }
  }
  const click = event => {
    const action = event.target.closest('[data-action]')?.dataset.action
    if (action === 'preview' || action === 'edit') void setMode(action).catch(report)
    if (action === 'save') void save().catch(() => {})
    if (action === 'dismiss') message.hidden = true
    if (action === 'retry' && requested) void load(requested).catch(() => {})
    if (action === 'wrap' && view) { wrap = !wrap; view.dispatch({ effects: wrapSlot.reconfigure(wrap ? EditorView.lineWrapping : []) }); paint(); publish() }
    if (action === 'find') void setMode('edit').then(() => { if (view) openSearchPanel(view) }).catch(report)
    if (action === 'copy' && view) {
      if (!navigator.clipboard?.writeText) report('Clipboard is unavailable in this browser. Switch to Edit to select and copy text.')
      else void navigator.clipboard.writeText(view.state.doc.toString()).then(() => report('Source text copied.', 'success')).catch(report)
    }
  }
  tv.addEventListener('click', click)
  tv.addEventListener('keydown', event => {
    if (!(event.metaKey || event.ctrlKey)) return
    if (event.key.toLowerCase() === 's') { event.preventDefault(); void save().catch(report) }
    if (event.key.toLowerCase() === 'f' && mode === 'preview') {
      event.preventDefault(); void setMode('edit').then(() => openSearchPanel(view)).catch(report)
    }
  })
  previewEl.addEventListener('click', event => {
    const link = event.target.closest('a[href^="#"]')
    if (link) {
      event.preventDefault()
      let id = link.getAttribute('href').slice(1)
      try { id = decodeURIComponent(id) } catch { /* literal anchor */ }
      const heading = [...previewEl.querySelectorAll('[id]')].find(el => el.id === id)
      heading?.scrollIntoView({ block: 'start' })
    }
    const image = event.target.closest('img[src]')
    if (image && !event.target.closest('a') && app.window.openImage) void app.window.openImage(image.src).catch(report)
  })
  app.onTheme(theme => { tv.dataset.theme = theme; if (view) view.dispatch({ effects: themeSlot.reconfigure(themeExtension(theme)) }) })
  app.defineAction('getText', ({ offset = 0, limit = 100000 } = {}) => {
    if (!view || loading) throw new Error('No file is loaded')
    if (!Number.isInteger(offset) || offset < 0 || !Number.isInteger(limit) || limit < 1 || limit > 100000) throw new Error('offset must be nonnegative and limit between 1 and 100000')
    const text = view.state.doc.sliceString(offset, offset + limit)
    return { ...describe(), offset, text, truncated: offset + text.length < view.state.doc.length }
  })
  app.defineAction('replaceText', async ({ text, expectedText } = {}) => {
    if (!view || loading) throw new Error('No file is loaded')
    if (typeof text !== 'string') throw new Error('replaceText requires a text string')
    const before = view.state.doc.toString()
    if (expectedText !== undefined && expectedText !== before) throw new Error('The document changed; read it again before replacing text')
    if (text !== before) view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: text } })
    if (mode === 'preview') await renderPreview()
    return describe()
  })
  app.defineAction('save', save)
  app.onState(async (s, info) => {
    if (disposed || info?.reason === 'emit') return
    if (Object.prototype.hasOwnProperty.call(s || {}, 'content')) throw new Error('Use replaceText to edit the document, then save to persist it')
    if (!s?.url) return
    if (s.url !== loadedUrl) {
      if (dirty() || saving) throw new Error('Save the current document before opening another file in this window')
      await load(s)
    }
  })
  paint()
  return { dispose() { disposed = true; generation++; previewJob++; loadAbort?.abort(); clearTimeout(previewTimer); view?.destroy() } }
}
