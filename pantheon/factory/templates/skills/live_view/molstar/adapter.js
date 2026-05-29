/**
 * Mol* (molstar) LiveView adapter — a viewer plugin for 3D molecular
 * structures (proteins, nucleic acids, complexes).
 *
 * Like every viewer it is an ordinary setup(lv, root) module loaded by the
 * generic host. Mol* ships as a self-contained UMD bundle + CSS; this
 * adapter injects them from a CDN and drives `molstar.Viewer`.
 *
 * LiveView "state" — provide exactly one source:
 *   {
 *     "pdbId": "1CBS",                     // an RCSB PDB entry
 *     "alphafold": "P00533",               // AlphaFold DB, by UniProt accession
 *     "url": "http://.../structure.cif",   // a structure file (e.g. served
 *                                          //   via serve_local_data)
 *     "format": "mmcif"                    // for `url`: "mmcif" | "pdb"
 *   }
 */

const MOLSTAR = 'https://unpkg.com/molstar@5.9.0/build/viewer/molstar'

/** Inject the Mol* UMD bundle + stylesheet once; resolve when ready. */
function loadMolstar() {
  return new Promise((resolve, reject) => {
    if (window.molstar && window.molstar.Viewer) {
      resolve()
      return
    }
    const css = document.createElement('link')
    css.rel = 'stylesheet'
    css.href = MOLSTAR + '.css'
    document.head.appendChild(css)
    const js = document.createElement('script')
    js.src = MOLSTAR + '.js'
    js.onload = () => resolve()
    js.onerror = () => reject(new Error('failed to load Mol* from the CDN'))
    document.head.appendChild(js)
  })
}

export async function setup(lv, root) {
  if (!root.id) root.id = 'molstar-root'
  root.style.position = 'relative'
  root.style.width = '100%'
  root.style.height = '100%'

  await loadMolstar()  // throws → app-host reports the failure

  const viewer = await window.molstar.Viewer.create(root.id, {
    layoutIsExpanded: false,
    layoutShowControls: false,
    layoutShowSequence: false,
    layoutShowLog: false,
    viewportShowExpand: true,
    viewportShowSelectionMode: false,
    pdbProvider: 'rcsb',
  })

  let loadedKey = null

  async function loadStructure(state) {
    const key = JSON.stringify([
      state.pdbId, state.alphafold, state.url, state.format,
    ])
    if (key === loadedKey) return  // already showing this structure
    loadedKey = key

    // Replace any previously loaded structure.
    try {
      if (viewer.plugin && typeof viewer.plugin.clear === 'function') {
        await viewer.plugin.clear()
      }
    } catch (e) { /* best-effort */ }

    if (state.alphafold) {
      // Mol* fetches the AlphaFold DB model and colours it by pLDDT.
      await viewer.loadAlphaFoldDb(state.alphafold)
    } else if (state.pdbId) {
      await viewer.loadPdb(String(state.pdbId).toLowerCase())
    } else if (state.url) {
      await viewer.loadStructureFromUrl(state.url, state.format || 'mmcif')
    }
  }

  lv.onState((state, info) => {
    if (info && info.reason === 'emit') return
    if (!state || (!state.pdbId && !state.alphafold && !state.url)) {
      lv.fail('Mol*: state needs one of pdbId / alphafold / url.')
      return
    }
    loadStructure(state).catch((e) =>
      lv.fail('Mol* failed to load the structure: ' + ((e && e.message) || e)),
    )
  })

  // Snapshot — use Mol*'s own screenshot helper. It handles the WebGL
  // drawing-buffer issues internally (a plain canvas.toDataURL would be
  // blank because Mol*'s context has preserveDrawingBuffer = false).
  lv.onSnapshot(async () => {
    try {
      const helper = viewer.plugin?.helpers?.viewportScreenshot
      if (helper && typeof helper.getImageDataUri === 'function') {
        const url = await helper.getImageDataUri()
        if (url) return url
      }
    } catch (e) { /* fall through to the raw-canvas attempt */ }
    const canvas = root.querySelector('canvas')
    if (!canvas) return null
    try {
      return canvas.toDataURL('image/jpeg', 0.85)
    } catch (e) {
      return null  // fall back to the host's html2canvas
    }
  })
}
