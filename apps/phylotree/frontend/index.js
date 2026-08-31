/**
 * phylotree.js LiveView adapter — phylogenetic-tree drawing.
 *
 * phylotree.js is a D3-based phylogenetic-tree library — Newick string
 * in, interactive SVG out. Linear or radial layouts; rerooting, ladderise,
 * clade collapse, etc.
 *
 * LiveView "state":
 *   {
 *     "newick":        "(A:0.1,B:0.2,(C:0.3,D:0.4):0.5);",
 *     "layout":        "linear",    // linear | radial (optional)
 *     "show_labels":   true,        // tip labels visible (optional)
 *     "show_scale":    true,        // scale bar (optional)
 *     "align_tips":    true,        // align tips at the same x (optional)
 *     "width":         700,         // SVG width  px (optional)
 *     "height":        null         // SVG height px; default scales to tips
 *   }
 */
import { phylotree } from 'https://esm.sh/phylotree@2.6.0'

// Phylotree's render() draws an SVG, but the stroke/fill on .branch /
// .node etc. comes from a separate stylesheet that ships with the
// library. Without it the SVG renders but is invisible (no stroke on
// branches, transparent node circles). Inject the upstream CSS once on
// first setup.
const PHYLOTREE_CSS = 'https://unpkg.com/phylotree@2.6.0/dist/phylotree.css'
function ensurePhylotreeCss() {
  if (document.querySelector('link[data-lv="phylotree-css"]')) return
  const link = document.createElement('link')
  link.rel = 'stylesheet'
  link.href = PHYLOTREE_CSS
  link.setAttribute('data-lv', 'phylotree-css')
  document.head.appendChild(link)
}

async function __viewerSetup(lv, root) {
  root.style.width = '100%'
  root.style.height = '100%'
  root.style.overflow = 'auto'
  // WHITE in both themes, deliberately. This viewer draws fixed dark ink —
  // black labels, dark edges — that the package does not control, so a
  // ground that followed the appearance setting would be unreadable in
  // dark. It is paper, and paper is white. An app whose ink it DOES
  // control should use var(--app-bg) instead (docs/app-spec.md).
  root.style.background = '#ffffff'
  root.style.color = '#222'

  ensurePhylotreeCss()

  // phylotree's render uses d3.select(container); a stable selector is
  // safest. The id is scoped to this iframe document, so collisions with
  // other live views are impossible.
  if (!root.id) root.id = 'phylotree-host'

  let lastKey = null
  let tree = null

  // Crude count of tips in a newick — used to size the canvas sensibly
  // when `height` is not given.
  function countTips(newick) {
    // tips are tokens followed by ':' or ',' or ')' and not preceded by ')'
    const cleaned = newick.replace(/\s/g, '')
    let count = 0, depth = 0, in_token = false
    for (let i = 0; i < cleaned.length; i++) {
      const c = cleaned[i]
      if (c === '(') { depth++; in_token = false }
      else if (c === ')') { if (in_token) count++; in_token = false; depth-- }
      else if (c === ',') { if (in_token) count++; in_token = false }
      else if (c === ':' || c === ';') { if (in_token) count++; in_token = false }
      else { in_token = true }
    }
    return Math.max(count, 4)
  }

  function applyState(state) {
    if (!state || typeof state.newick !== 'string' || !state.newick.trim()) {
      lv.fail('Phylotree: state must include a `newick` string.')
      return
    }
    const key = JSON.stringify([state.newick, state.layout, state.show_labels,
      state.show_scale, state.align_tips, state.width, state.height])
    if (key === lastKey) return
    lastKey = key

    root.innerHTML = ''
    try {
      tree = new phylotree(state.newick)
      const tipCount = countTips(state.newick)
      const opts = {
        container: '#' + root.id,
        width:  state.width  || Math.max(500, (root.clientWidth || 700) - 24),
        height: state.height || Math.max(300, tipCount * 18 + 40),
        'is-radial':   state.layout === 'radial',
        'show-scale':  state.show_scale !== false,
        'show-labels': state.show_labels !== false,
        'align-tips':  state.align_tips !== false,
        'left-right-spacing': 'fit-to-size',
        'top-bottom-spacing': 'fit-to-size',
        zoom: true,
      }
      // v2.x quirk: tree.render() returns a renderer but does NOT
      // auto-append the SVG. The library's own update_layout_and_view
      // does: render -> update -> container.appendChild(show()). Mirror
      // that here so the tree actually appears.
      const renderer = tree.render(opts)
      if (renderer && typeof renderer.update === 'function') {
        renderer.update()
      }
      if (renderer && typeof renderer.show === 'function') {
        const svgNode = renderer.show()
        if (svgNode instanceof Element) {
          root.appendChild(svgNode)
        }
      }
    } catch (e) {
      lv.fail('Phylotree: render failed — ' + ((e && e.message) || e))
    }
  }

  lv.onState((state, info) => {
    if (info && info.reason === 'emit') return
    applyState(state)
  })

  // SVG output — html2canvas captures it cleanly. No custom provider.
}


// ── file-open shim (generated by convert-viewers.py) ─────────────────────────
// The desktop opens files with generic state {url, path, name, type}; the
// viewer's own contract is documented at the top of this file. File-shaped
// state (a `path`, none of the viewer's keys) is mapped before the stock
// adapter sees it; everything else — agent states, demos — passes through.
const __FILE_KEYS = ["newick"]
// What the desktop itself puts in a file-open state; everything else in it
// came from the caller and is theirs to keep.
const __FILE_STATE_KEYS = ['url', 'path', 'name', 'type']
async function __fromFile(state) {
  const res = await fetch(state.url)
  if (!res.ok) throw new Error('fetch failed: ' + res.status)
  const text = (await res.text()).trim()
  if (!text) throw new Error('the file is empty')
  return { newick: text }
}
export async function setup(lv, root) {
  const __cbs = []
  let __lastFile = null
  let __cur = null
  const wrapped = Object.create(lv)
  // Adapters read lv.state on their own re-renders (Vitessce does on window
  // resize). The SDK's store still holds the RAW state — file-shaped, or
  // stale after menu-driven changes that bypass it — so point the accessor
  // at what the adapter actually saw last.
  Object.defineProperty(wrapped, 'state', { get: () => __cur })
  wrapped.onState = (cb) => {
    __cbs.push(cb)
    lv.onState((state, info) => {
      // A file open is a file open. This used to also require that the state
      // carried NONE of the viewer's own keys — but those keys are exactly
      // what this app declares as `sync` state, so the first time anyone
      // toggled a mode or picked a colour the window could never run its
      // backend `prepare` again. It then fed the raw file envelope to the
      // viewer: spatial3d fetched `_spatial.json` from inside an .h5ad (404),
      // Vitessce reported "Missing version" on an object that was never a
      // config. The mapping is also what re-mints served URLs, so skipping it
      // left those windows pointing at a dead tunnel after every restart.
      const fileShaped = !!(state && state.path && state.url)
      if (!fileShaped) { __cur = state; cb(state, info); return }
      __lastFile = state
      Promise.resolve(__fromFile(state, lv)).then((mapped) => {
        // Anything the caller asked for beyond the file itself — a layout, a
        // colour scheme, desktop_open(path=…, state={…}) — must survive the
        // mapping. Dropping it silently is why "open it radial" came out
        // linear and sent an agent hunting for a bug that was ours.
        // __FILE_KEYS belong to the FILE: they are what the mapping produces,
        // and they carry served URLs only the current pod can mint. Anything
        // else in the state — a layout, a camera, a colour scheme — is the
        // caller's or the session's, and rides on top.
        const extra = {}
        for (const k of Object.keys(state)) {
          if (!__FILE_STATE_KEYS.includes(k) && !__FILE_KEYS.includes(k)) extra[k] = state[k]
        }
        __emitToApp(Object.assign({}, mapped, extra), info)
      }).catch((e) =>
        lv.fail('Could not open ' + (state.name || state.path) + ': ' + ((e && e.message) || e)))
    })
  }
  // Every shim delivery goes through here — and through the SDK's own store.
  // Adapters build their next state from lv.state (mode toggles, sliders); a
  // delivery that bypassed the store left it holding the bare init `{}`, and
  // the first toolbar click re-rendered from nothing ("Provide state.url").
  const __emitToApp = (state, info) => {
    __cur = state
    if (typeof lv.setState === 'function') lv.setState(state)
    for (const cb of __cbs) cb(state, info || { reason: 'set' })
  }
  // Menu actions patch the CURRENT viewer state — the adapter re-renders the
  // way it would for any set.
  const __patch = (p) => {
    if (!__cur) throw new Error('nothing is loaded yet')
    __emitToApp(Object.assign({}, __cur, p))
  }
  void __patch
  if (typeof lv.defineAction === 'function') {
    lv.defineAction('setLayout', (a) => {
      __patch({ layout: a && a.layout === 'radial' ? 'radial' : 'linear' }); return 'ok'
    })
    lv.defineAction('toggleLabels', () => {
      const on = !__cur || __cur.show_labels !== false
      __patch({ show_labels: !on }); return 'ok'
    })
    lv.defineAction('toggleAlign', () => {
      const on = !__cur || __cur.align_tips !== false
      __patch({ align_tips: !on }); return 'ok'
    })
    lv.defineAction('toggleScale', () => {
      const on = !__cur || __cur.show_scale !== false
      __patch({ show_scale: !on }); return 'ok'
    })
  }
  return __viewerSetup(wrapped, root)
}
