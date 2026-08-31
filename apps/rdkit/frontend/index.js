/**
 * RDKit-JS LiveView adapter — 2D small-molecule depiction.
 *
 * Complements the molstar 3D viewer: 2D drawing of SMILES / MOL block via
 * RDKit's C++ → WebAssembly build. The first call downloads ~3 MB of
 * WASM; subsequent renders are instant.
 *
 * IMPORTANT: We load the OFFICIAL UMD bundle from unpkg, NOT the esm.sh
 * ESM build. esm.sh polyfills `node:fs` with an `unenv` stub that throws
 * on `fs.readFileSync`, which RDKit's Emscripten init hits when it tries
 * to detect its environment (`__Process$.versions?.node` is truthy under
 * the polyfill). The official UMD has no such Node-style branches.
 *
 * LiveView "state":
 *   {
 *     "molecules": [
 *       { "smiles":   "CCO",                             "name": "Ethanol" },
 *       { "smiles":   "CN1C=NC2=C1C(=O)N(C(=O)N2C)C",    "name": "Caffeine" },
 *       { "molblock": "<MOL file string>",               "name": "From MOL" }
 *     ],
 *     "draw_options": {                                  // optional
 *       "width": 320, "height": 220,
 *       "addAtomIndices": false,
 *       "highlightAtoms": [],         // 0-based indices to highlight
 *       "highlightBonds": []
 *     }
 *   }
 */
const RDKIT_BUNDLE = 'https://unpkg.com/@rdkit/rdkit@2025.3.4-1.0.0/dist/RDKit_minimal.js'

function loadRDKit() {
  if (window.initRDKitModule) return Promise.resolve(window.initRDKitModule)
  return new Promise((resolve, reject) => {
    const s = document.createElement('script')
    s.src = RDKIT_BUNDLE
    s.onload = () => {
      if (window.initRDKitModule) resolve(window.initRDKitModule)
      else reject(new Error('RDKit script loaded but initRDKitModule global missing'))
    }
    s.onerror = () => reject(new Error('Failed to load RDKit script from ' + RDKIT_BUNDLE))
    document.head.appendChild(s)
  })
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
  root.style.padding = '12px'
  root.style.fontFamily = 'system-ui, -apple-system, sans-serif'

  root.innerHTML = '<div style="padding:24px;color:#666;font-size:13px">'
    + 'Loading RDKit (WASM, ~3 MB)…</div>'

  let RDKit
  try {
    const init = await loadRDKit()
    RDKit = await init()
  } catch (e) {
    lv.fail('RDKit: failed to load WASM — ' + ((e && e.message) || e))
    return
  }

  let lastKey = null

  function applyState(state) {
    if (!state || !Array.isArray(state.molecules) || state.molecules.length === 0) {
      lv.fail('RDKit: state must include a non-empty `molecules` array '
        + 'of {smiles, name?} or {molblock, name?}.')
      return
    }
    const key = JSON.stringify([state.molecules, state.draw_options])
    if (key === lastKey) return
    lastKey = key

    const opts = state.draw_options || {}
    const W = opts.width  || 320
    const H = opts.height || 220

    // RDKit's drawing-options JSON — see RDKit::MinimalLib::draw_to_string.
    const drawDetails = JSON.stringify({
      width: W, height: H,
      addAtomIndices: !!opts.addAtomIndices,
      atoms: opts.highlightAtoms || [],
      bonds: opts.highlightBonds || [],
    })

    root.innerHTML = ''
    const grid = document.createElement('div')
    grid.style.display = 'grid'
    grid.style.gridTemplateColumns = `repeat(auto-fit, minmax(${W + 8}px, 1fr))`
    grid.style.gap = '16px'

    for (const m of state.molecules) {
      const cell = document.createElement('div')
      cell.style.display = 'flex'
      cell.style.flexDirection = 'column'
      cell.style.alignItems = 'center'
      cell.style.gap = '6px'

      let html = ''
      let mol = null
      try {
        mol = m.molblock
          ? RDKit.get_mol(m.molblock)
          : RDKit.get_mol(String(m.smiles || ''))
        if (mol && mol.is_valid()) {
          html = mol.get_svg_with_highlights(drawDetails)
        } else {
          html = '<div style="color:#f85149;font-size:12px;padding:8px">'
            + 'invalid input: ' + (m.smiles || '(molblock)') + '</div>'
        }
      } catch (e) {
        html = '<div style="color:#f85149;font-size:12px;padding:8px">'
          + 'RDKit error: ' + ((e && e.message) || e) + '</div>'
      } finally {
        // WASM-allocated objects must be deleted to avoid leaks.
        if (mol) { try { mol.delete() } catch (_) {} }
      }

      const svgBox = document.createElement('div')
      svgBox.innerHTML = html
      cell.appendChild(svgBox)

      const label = document.createElement('div')
      label.textContent = m.name || m.smiles || ''
      label.style.fontSize = '12px'
      label.style.color = '#444'
      label.style.maxWidth = W + 'px'
      label.style.textAlign = 'center'
      label.style.overflow = 'hidden'
      label.style.textOverflow = 'ellipsis'
      label.style.whiteSpace = 'nowrap'
      cell.appendChild(label)

      grid.appendChild(cell)
    }
    root.appendChild(grid)
  }

  lv.onState((state, info) => {
    if (info && info.reason === 'emit') return
    try { applyState(state) }
    catch (e) { lv.fail('RDKit: ' + ((e && e.message) || e)) }
  })
}


// ── file-open shim (generated by convert-viewers.py) ─────────────────────────
// The desktop opens files with generic state {url, path, name, type}; the
// viewer's own contract is documented at the top of this file. File-shaped
// state (a `path`, none of the viewer's keys) is mapped before the stock
// adapter sees it; everything else — agent states, demos — passes through.
const __FILE_KEYS = ["molecules"]
// What the desktop itself puts in a file-open state; everything else in it
// came from the caller and is theirs to keep.
const __FILE_STATE_KEYS = ['url', 'path', 'name', 'type']
async function __fromFile(state) {
  const res = await fetch(state.url)
  if (!res.ok) throw new Error('fetch failed: ' + res.status)
  const text = await res.text()
  if (/\.smi$/i.test(String(state.name || state.path))) {
    const molecules = text.split(/\r?\n/)
      .map((l) => l.trim()).filter((l) => l && !l.startsWith('#')).slice(0, 24)
      .map((l) => {
        const parts = l.split(/\s+/)
        return { smiles: parts[0], name: parts.slice(1).join(' ') || parts[0] }
      })
    if (!molecules.length) throw new Error('no SMILES lines in the file')
    return { molecules }
  }
  // .mol is one block; .sdf may be many, delimited by $$$$.
  const blocks = text.split(/\$\$\$\$[^\S\r\n]*\r?\n?/).filter((b) => b.trim()).slice(0, 24)
  const molecules = blocks.map((b, i) => {
    const block = b.replace(/^[\r\n]+/, '')
    const first = (block.split(/\r?\n/)[0] || '').trim()
    return { molblock: block, name: first || 'molecule ' + (i + 1) }
  })
  if (!molecules.length) throw new Error('no MOL blocks in the file')
  return { molecules }
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
    lv.defineAction('toggleIndices', () => {
      const d = (__cur && __cur.draw_options) || {}
      __patch({ draw_options: Object.assign({}, d, { addAtomIndices: !d.addAtomIndices }) })
      return 'ok'
    })
  }
  return __viewerSetup(wrapped, root)
}
