/**
 * spatial3d LiveView adapter — 3D spatial-transcriptomics point cloud.
 * Ported from the Virtual Embryo `SpatialCellViewer.vue` (deck.gl PointCloudLayer
 * + zarrita). Renders up to millions of cells in 3D / 2D / UMAP, coloured by a
 * categorical obs column (cluster) or by a gene's expression.
 *
 * A LiveView component plugin: exports `setup(lv, root)`, loaded by app-host.
 * No build step. deck.gl loads as its standalone UMD bundle (one file → one
 * luma.gl instance, the robust no-build path; `window.deck`); zarrita loads as
 * a browser ES module from esm.sh.
 *
 * Unlike Virtual Embryo this reads cell **positions from `obsm/<spatialKey>`**
 * (a plain Float32 zarr array) rather than a LAZ point cloud — so the agent can
 * produce a dataset straight from an AnnData with no LAZ tooling, and there is
 * no web-worker / loaders.gl dependency to wrangle from a CDN. (For 7M-cell
 * scale a LAZ fast-path could be added later.)
 *
 * ── State (the LiveView state the agent drives) ────────────────────────────
 *   {
 *     url,          spatial zarr root URL (served; holds obsm/, obs/, X_csc/, _spatial.json)
 *     spatialKey,   obsm key for positions (default: manifest.default_spatial_key or "spatial")
 *     mode,         "3d" | "2d" | "umap"            (default "3d"; "2d" if spatial_ndim==2)
 *     colorBy,      "cluster" | "gene"              (default "cluster")
 *     clusterKey,   obs column for cluster colours  (default: manifest.default_color_obs)
 *     gene,         gene symbol (colorBy=="gene")
 *     colormap,     viridis|magma|plasma|inferno|cividis|turbo|blues|reds (default viridis)
 *     threshold,    gene-expression filter: hide cells below this value (default 0)
 *     cluster,      focus a single cluster by NAME (others dimmed out), or null
 *     pointSize,    0.05..3.5 slider (default 1)
 *     opacity,      0..1        (default 1)
 *     camera,       {rotationOrbit, rotationX, zoom} — 3D orbit, round-trips
 *     title,
 *   }
 */
import * as zarr from 'https://esm.sh/zarrita@0.7.2'

const DECK_URL = 'https://unpkg.com/deck.gl@9.3.2/dist.min.js'

// ════════════════════════ spatial-cell zarr helpers ═══════════════════════
// Ported verbatim from virtualembryo-web/utils/spatial-cell.ts (framework-free).

async function loadManifest(url) {
  const r = await fetch(`${url}/_spatial.json`, { cache: 'no-store' })
  if (!r.ok) throw new Error(`No _spatial.json at ${url} (HTTP ${r.status})`)
  return r.json()
}

/** Load an n_cells × dim coordinate matrix from obsm/<key> (one round-trip). */
async function loadCoords(url, obsmKey) {
  const store = new zarr.FetchStore(url)
  const root = await zarr.open(store, { kind: 'group' })
  const arr = await zarr.open(root.resolve(`obsm/${obsmKey}`), { kind: 'array' })
  const chunk = await zarr.get(arr)
  return { data: chunk.data, shape: arr.shape }
}

/** Categorical obs column → {codes:Int32Array, categories:string[]}. */
async function loadCategorical(url, column) {
  const store = new zarr.FetchStore(url)
  const root = await zarr.open(store, { kind: 'group' })
  const arr = await zarr.open(root.resolve(`obs/${column}`), { kind: 'array' })
  const codes = (await zarr.get(arr)).data
  const cats = (arr.attrs && arr.attrs.categories) || []
  return { codes, categories: cats }
}

/** Gene-symbol vocabulary (flat JSON list at the dataset root). */
async function loadGeneSymbols(url) {
  const r = await fetch(`${url}/gene_symbols.json`)
  if (!r.ok) throw new Error(`gene_symbols.json: HTTP ${r.status}`)
  return r.json()
}

/** One gene's expression vector (length nCells) via the CSC sidecar:
 *  indptr[gene]..indptr[gene+1] locate the contiguous data/indices slice,
 *  scattered into a dense vector. Only the needed bytes are range-fetched. */
async function loadGeneExpression(url, geneIdx, nCells) {
  const store = new zarr.FetchStore(url)
  const root = await zarr.open(store, { kind: 'group' })
  const indptr = await zarr.open(root.resolve('X_csc/indptr'), { kind: 'array' })
  const ptr = (await zarr.get(indptr, [zarr.slice(geneIdx, geneIdx + 2)])).data
  const start = Number(ptr[0]); const end = Number(ptr[1])
  const out = new Float32Array(nCells)
  if (end <= start) return out
  const data = await zarr.open(root.resolve('X_csc/data'), { kind: 'array' })
  const idx = await zarr.open(root.resolve('X_csc/indices'), { kind: 'array' })
  const d = (await zarr.get(data, [zarr.slice(start, end)])).data
  const ii = (await zarr.get(idx, [zarr.slice(start, end)])).data
  for (let k = 0; k < d.length; k++) out[ii[k]] = d[k]
  return out
}

// ── palettes / colormaps (ported from spatial-cell.ts) ──
function hslToRgb(h, s, l) {
  if (s === 0) return [l, l, l]
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s
  const p = 2 * l - q
  const f = (t) => {
    if (t < 0) t += 1; if (t > 1) t -= 1
    if (t < 1 / 6) return p + (q - p) * 6 * t
    if (t < 1 / 2) return q
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6
    return p
  }
  return [f(h + 1 / 3), f(h), f(h - 1 / 3)]
}
function categoricalPalette(n) {
  const base = [
    [0.31, 0.48, 0.65], [1.00, 0.50, 0.05], [0.17, 0.63, 0.17],
    [0.84, 0.15, 0.16], [0.58, 0.40, 0.74], [0.55, 0.34, 0.29],
    [0.89, 0.47, 0.76], [0.50, 0.50, 0.50], [0.74, 0.74, 0.13],
    [0.09, 0.75, 0.81], [0.65, 0.81, 0.89], [1.00, 0.73, 0.47],
    [0.60, 0.87, 0.54], [1.00, 0.60, 0.59], [0.77, 0.69, 0.84],
    [0.77, 0.61, 0.49], [0.97, 0.71, 0.82], [0.78, 0.78, 0.78],
    [0.86, 0.86, 0.55], [0.62, 0.85, 0.90],
  ]
  if (n <= base.length) return base.slice(0, n)
  const out = []
  for (let i = 0; i < n; i++) out.push(hslToRgb((i * 360) / n / 360, 0.65, 0.55))
  return out
}
const COLORMAP_STOPS = {
  viridis: [[0, [0.267, 0.005, 0.329]], [0.143, [0.282, 0.140, 0.458]], [0.286, [0.254, 0.265, 0.530]], [0.429, [0.207, 0.372, 0.553]], [0.571, [0.164, 0.471, 0.558]], [0.714, [0.128, 0.567, 0.551]], [0.857, [0.135, 0.659, 0.518]], [1, [0.993, 0.906, 0.144]]],
  magma: [[0, [0.001, 0, 0.014]], [0.143, [0.116, 0.063, 0.272]], [0.286, [0.316, 0.072, 0.485]], [0.429, [0.522, 0.118, 0.493]], [0.571, [0.717, 0.215, 0.475]], [0.714, [0.890, 0.378, 0.392]], [0.857, [0.984, 0.611, 0.398]], [1, [0.987, 0.991, 0.749]]],
  plasma: [[0, [0.050, 0.030, 0.528]], [0.143, [0.281, 0.013, 0.629]], [0.286, [0.493, 0.012, 0.658]], [0.429, [0.673, 0.150, 0.604]], [0.571, [0.823, 0.318, 0.490]], [0.714, [0.929, 0.512, 0.357]], [0.857, [0.987, 0.748, 0.207]], [1, [0.940, 0.975, 0.131]]],
  inferno: [[0, [0.001, 0, 0.014]], [0.143, [0.139, 0.046, 0.309]], [0.286, [0.337, 0.061, 0.430]], [0.429, [0.532, 0.126, 0.426]], [0.571, [0.719, 0.215, 0.337]], [0.714, [0.881, 0.349, 0.196]], [0.857, [0.984, 0.612, 0.080]], [1, [0.988, 0.998, 0.645]]],
  cividis: [[0, [0, 0.135, 0.305]], [0.143, [0.131, 0.211, 0.408]], [0.286, [0.245, 0.286, 0.430]], [0.429, [0.366, 0.361, 0.430]], [0.571, [0.495, 0.442, 0.413]], [0.714, [0.643, 0.526, 0.371]], [0.857, [0.806, 0.621, 0.286]], [1, [0.995, 0.741, 0.156]]],
  turbo: [[0, [0.190, 0.072, 0.232]], [0.143, [0.213, 0.388, 0.880]], [0.286, [0.180, 0.667, 0.968]], [0.429, [0.156, 0.890, 0.778]], [0.571, [0.426, 0.997, 0.448]], [0.714, [0.860, 0.946, 0.213]], [0.857, [0.996, 0.638, 0.151]], [1, [0.620, 0.071, 0.012]]],
  blues: [[0, [0.969, 0.984, 1]], [0.143, [0.871, 0.929, 0.969]], [0.286, [0.776, 0.859, 0.937]], [0.429, [0.620, 0.792, 0.882]], [0.571, [0.420, 0.682, 0.839]], [0.714, [0.259, 0.573, 0.776]], [0.857, [0.129, 0.443, 0.710]], [1, [0.031, 0.318, 0.612]]],
  reds: [[0, [1, 0.961, 0.941]], [0.143, [0.996, 0.878, 0.824]], [0.286, [0.988, 0.733, 0.631]], [0.429, [0.988, 0.573, 0.447]], [0.571, [0.984, 0.416, 0.290]], [0.714, [0.937, 0.231, 0.173]], [0.857, [0.796, 0.094, 0.114]], [1, [0.404, 0, 0.051]]],
}
const COLORMAP_NAMES = ['viridis', 'magma', 'plasma', 'inferno', 'cividis', 'turbo', 'blues', 'reds']
function colormapColor(name, t) {
  const stops = COLORMAP_STOPS[name] || COLORMAP_STOPS.viridis
  const u = Math.max(0, Math.min(1, t))
  for (let i = 1; i < stops.length; i++) {
    if (u <= stops[i][0]) {
      const [a, ca] = stops[i - 1]; const [b, cb] = stops[i]
      const f = (u - a) / (b - a || 1)
      return [ca[0] + (cb[0] - ca[0]) * f, ca[1] + (cb[1] - ca[1]) * f, ca[2] + (cb[2] - ca[2]) * f]
    }
  }
  return stops[stops.length - 1][1].slice()
}
function colormapCSS(name) {
  const stops = COLORMAP_STOPS[name] || COLORMAP_STOPS.viridis
  return 'linear-gradient(to right,' + stops.map(([t, [r, g, b]]) =>
    `rgb(${Math.round(r * 255)},${Math.round(g * 255)},${Math.round(b * 255)}) ${(t * 100).toFixed(0)}%`).join(',') + ')'
}

// Load the deck.gl UMD bundle once (shared across all instances).
let _deckPromise = null
function loadDeck() {
  if (window.deck) return Promise.resolve(window.deck)
  if (_deckPromise) return _deckPromise
  _deckPromise = new Promise((res, rej) => {
    const s = document.createElement('script')
    s.src = DECK_URL
    s.onload = () => res(window.deck)
    s.onerror = () => rej(new Error('failed to load deck.gl bundle'))
    document.head.appendChild(s)
  })
  return _deckPromise
}

// ════════════════════════════ DOM chrome ══════════════════════════════════
const CSS = `
.sp3-root{position:absolute;inset:0;display:flex;flex-direction:column;background:transparent;
  color:#c9d1d9;font-family:system-ui,-apple-system,sans-serif;overflow:hidden}
.sp3-bar{display:flex;align-items:center;gap:9px;padding:8px 12px;flex:0 0 auto;flex-wrap:wrap;
  background:rgba(20,24,30,.72);backdrop-filter:blur(8px);border-bottom:1px solid rgba(255,255,255,.07);z-index:4}
.sp3-badge{width:26px;height:26px;border-radius:7px;flex:0 0 auto;display:grid;place-items:center;
  background:linear-gradient(135deg,#2f7d6b,#6b54a8)}
.sp3-badge svg{width:15px;height:15px;display:block}
.sp3-title{font-size:13px;font-weight:600;color:#e6edf3;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis;max-width:30vw}
.sp3-spacer{flex:1 1 auto;min-width:6px}
.sp3-seg{display:inline-flex;border:1px solid rgba(255,255,255,.14);border-radius:7px;overflow:hidden}
.sp3-seg button{appearance:none;border:0;background:transparent;color:#9aa4b2;font-size:11px;
  font-weight:600;padding:4px 9px;cursor:pointer;letter-spacing:.02em}
.sp3-seg button.on{background:rgba(107,84,168,.34);color:#f0ecf7}
.sp3-ctl{display:flex;align-items:center;gap:6px;font-size:11px;color:#9aa4b2}
.sp3-ctl input[type=range]{width:74px;accent-color:#6b54a8}
.sp3-ctl input[type=text]{width:96px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.14);
  border-radius:6px;color:#e6edf3;font-size:11px;padding:3px 7px}
.sp3-ctl select{background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.14);border-radius:6px;
  color:#e6edf3;font-size:11px;padding:3px 6px}
.sp3-view{position:relative;flex:1 1 auto;min-height:0}
.sp3-legend{position:absolute;left:10px;bottom:10px;width:208px;border-radius:8px;z-index:4;
  background:rgba(0,0,0,.58);backdrop-filter:blur(7px);border:1px solid rgba(255,255,255,.1);
  font-size:10.5px;overflow:hidden}
.sp3-leghead{display:flex;align-items:center;gap:7px;padding:5px 9px;cursor:pointer;
  color:#cfd6df;font-weight:600;user-select:none}
.sp3-leghead:hover{background:rgba(255,255,255,.06)}
.sp3-chev{flex:0 0 auto;width:8px;height:8px;border-right:1.6px solid #9aa4b2;border-bottom:1.6px solid #9aa4b2;
  transform:rotate(45deg);transition:transform .15s}
.sp3-legend.collapsed .sp3-chev{transform:rotate(-45deg)}
.sp3-legend.collapsed .sp3-legbody{display:none}
.sp3-legtitle{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sp3-legbody{max-height:min(46vh,300px);overflow-y:auto;overflow-x:hidden;padding:2px 9px 8px;
  scrollbar-width:thin;scrollbar-color:rgba(255,255,255,.24) transparent}
.sp3-legbody::-webkit-scrollbar{width:7px}
.sp3-legbody::-webkit-scrollbar-track{background:transparent;margin:3px 0}
.sp3-legbody::-webkit-scrollbar-thumb{background:rgba(255,255,255,.22);border-radius:4px}
.sp3-legbody::-webkit-scrollbar-thumb:hover{background:rgba(255,255,255,.36)}
.sp3-legrow{display:flex;align-items:center;gap:7px;padding:1.5px 3px;border-radius:5px;cursor:pointer}
.sp3-legrow:hover{background:rgba(255,255,255,.08)}
.sp3-legrow.dim{opacity:.4}
.sp3-lbl{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
.sp3-sw{width:11px;height:11px;border-radius:3px;flex:0 0 auto}
.sp3-bar2{height:11px;border-radius:3px;width:180px;margin:3px 0}
.sp3-slice{position:absolute;bottom:12px;left:50%;transform:translateX(-50%);z-index:4;display:flex;
  align-items:center;gap:3px;padding:4px 6px;border-radius:9px;background:rgba(20,24,30,.84);
  backdrop-filter:blur(7px);border:1px solid rgba(255,255,255,.12);font-size:11px;color:#cfd6df}
.sp3-slice button{appearance:none;border:0;background:transparent;color:#cfd6df;cursor:pointer;
  padding:2px 7px;border-radius:6px;line-height:1;font-size:14px}
.sp3-slice button:hover{background:rgba(255,255,255,.1)}
.sp3-sl-label{min-width:78px;text-align:center;font-variant-numeric:tabular-nums}
.sp3-slice-all{font-size:10.5px!important;font-weight:600;margin-left:2px}
.sp3-slice-all.on{background:rgba(107,84,168,.42)!important;color:#f0ecf7}
.sp3-tip{position:absolute;pointer-events:none;z-index:6;background:rgba(10,14,20,.92);border:1px solid rgba(255,255,255,.12);
  border-radius:6px;padding:5px 8px;font:11px/1.4 ui-monospace,monospace;color:#e6edf3;white-space:nowrap;display:none}
.sp3-overlay{position:absolute;inset:0;display:grid;place-items:center;background:rgba(8,12,20,.8);
  backdrop-filter:blur(3px);font-size:13px;color:#8b949e;text-align:center;padding:24px;z-index:5}
.sp3-overlay.err{color:#f0857d}
.sp3-overlay.hidden{display:none}
.sp3-dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#2f7d6b;margin-right:8px;
  animation:sp3pulse 1.1s ease-in-out infinite}
@keyframes sp3pulse{0%,100%{opacity:.35}50%{opacity:1}}
.sp3-hud{position:absolute;right:10px;bottom:10px;padding:3px 8px;border-radius:6px;background:rgba(0,0,0,.42);
  font:11px/1.3 ui-monospace,monospace;color:#9aa4b2;border:1px solid rgba(255,255,255,.08);pointer-events:none}
`
const CELLS_SVG = `<svg viewBox="0 0 24 24" fill="#eaf2ff"><circle cx="7" cy="8" r="2.3"/><circle cx="15" cy="6" r="1.7"/>
  <circle cx="17" cy="13" r="2.1"/><circle cx="9" cy="15" r="1.9"/><circle cx="13" cy="17.5" r="1.5"/>
  <circle cx="5.5" cy="15.5" r="1.3"/></svg>`

// ════════════════════════════ component ═══════════════════════════════════
export function setup(lv, root) {
  root.innerHTML = ''
  const style = document.createElement('style'); style.textContent = CSS; root.appendChild(style)
  const el = document.createElement('div'); el.className = 'sp3-root'
  el.innerHTML = `
    <div class="sp3-bar">
      <div class="sp3-badge">${CELLS_SVG}</div>
      <div class="sp3-title" data-title>Spatial cells</div>
      <div class="sp3-spacer"></div>
      <div class="sp3-seg" data-view-seg>
        <button data-vm="3d">3D</button><button data-vm="2d">2D</button><button data-vm="umap">UMAP</button>
      </div>
      <div class="sp3-seg" data-cb-seg>
        <button data-cb="cluster">Cluster</button><button data-cb="gene">Gene</button>
      </div>
      <label class="sp3-ctl" data-gene-wrap style="display:none">gene
        <input type="text" list="sp3-genes" data-gene placeholder="e.g. Sox2"><datalist id="sp3-genes"></datalist></label>
      <label class="sp3-ctl" data-cmap-wrap style="display:none">cmap
        <select data-cmap></select></label>
      <label class="sp3-ctl">size<input type="range" min="0.05" max="3.5" step="0.05" data-psize></label>
      <label class="sp3-ctl">opacity<input type="range" min="0.1" max="1" step="0.05" data-opacity></label>
    </div>
    <div class="sp3-view" data-view>
      <div class="sp3-legend" data-legend style="display:none">
        <div class="sp3-leghead" data-legtoggle><span class="sp3-chev"></span><span class="sp3-legtitle" data-legtitle>Legend</span></div>
        <div class="sp3-legbody" data-legbody></div>
      </div>
      <div class="sp3-slice" data-slice style="display:none">
        <button data-sl="prev" title="previous layer">‹</button>
        <span class="sp3-sl-label" data-sl-label>All layers</span>
        <button data-sl="next" title="next layer">›</button>
        <button class="sp3-slice-all" data-sl="all">All</button>
      </div>
      <div class="sp3-hud" data-hud style="display:none"></div>
      <div class="sp3-tip" data-tip></div>
      <div class="sp3-overlay" data-overlay><span><span class="sp3-dot"></span>Loading…</span></div>
    </div>`
  root.appendChild(el)

  const view = el.querySelector('[data-view]')
  const overlay = el.querySelector('[data-overlay]')
  const legendEl = el.querySelector('[data-legend]')
  const legBody = el.querySelector('[data-legbody]')
  const legTitle = el.querySelector('[data-legtitle]')
  el.querySelector('[data-legtoggle]').addEventListener('click', () => legendEl.classList.toggle('collapsed'))
  const sliceEl = el.querySelector('[data-slice]')
  const slLabel = el.querySelector('[data-sl-label]')
  sliceEl.addEventListener('click', (e) => {
    const b = e.target.closest('button[data-sl]'); if (!b) return
    const n = sliceLabels.length; if (!n) return
    const act = b.getAttribute('data-sl'); const s = cur.slice()
    if (act === 'all') lv.setState({ slice: null })
    else if (act === 'prev') lv.setState({ slice: s == null ? 0 : Math.max(0, s - 1) })
    else if (act === 'next') lv.setState({ slice: s == null ? 0 : Math.min(n - 1, s + 1) })
  })
  const hud = el.querySelector('[data-hud]')
  const tip = el.querySelector('[data-tip]')
  const titleEl = el.querySelector('[data-title]')
  const viewSeg = el.querySelector('[data-view-seg]')
  const cbSeg = el.querySelector('[data-cb-seg]')
  const geneWrap = el.querySelector('[data-gene-wrap]')
  const geneInput = el.querySelector('[data-gene]')
  const geneList = el.querySelector('#sp3-genes')
  const cmapWrap = el.querySelector('[data-cmap-wrap]')
  const cmapSel = el.querySelector('[data-cmap]')
  const pSize = el.querySelector('[data-psize]')
  const opac = el.querySelector('[data-opacity]')
  COLORMAP_NAMES.forEach((n) => { const o = document.createElement('option'); o.value = n; o.textContent = n; cmapSel.appendChild(o) })

  const showOverlay = (m, isErr) => { overlay.className = 'sp3-overlay' + (isErr ? ' err' : ''); overlay.innerHTML = isErr ? `<span>${m}</span>` : `<span><span class="sp3-dot"></span>${m}</span>` }
  const hideOverlay = () => { overlay.className = 'sp3-overlay hidden' }

  let Deck, OrbitView, OrthographicView, COORDINATE_SYSTEM, PointCloudLayer, DataFilterExtension

  // ── data + render state ──
  let deck = null
  let manifest = null
  let cellCount = 0
  let positions = null         // live GPU-bound buffer
  let positionsSpatial = null  // immutable spatial copy
  let positionsFlat = null     // spatial with z=0 (2D)
  let positionsUmap = null     // UMAP, rescaled, z=0
  let colors = null            // Uint8ClampedArray RGBA
  let filterValues = null      // Float32 0/1 mask
  let clusterCodes = null
  let clusterCategories = []
  let geneExpr = null
  let geneExprMax = 0
  let geneSymbols = []
  let sliceCodes = null
  let sliceLabels = []
  let centroid = [0, 0, 0]
  let sphereRadius = 1
  let pointSizeScale = 1
  let initialViewState = {}
  let currentViewState = {}

  let applied = null
  let loadToken = 0
  let camTimer = null
  let lastCamKey = ''

  // ── helpers reading the merged state with defaults ──
  const S = () => applied || {}
  const cur = {
    mode: () => S().mode || (manifest && manifest.spatial_ndim === 2 ? '2d' : '3d'),
    colorBy: () => S().colorBy || 'cluster',
    gene: () => S().gene || null,
    clusterKey: () => S().clusterKey || (manifest && manifest.default_color_obs) || null,
    colormap: () => S().colormap || 'viridis',
    threshold: () => Number(S().threshold || 0),
    cluster: () => (S().cluster == null ? null : S().cluster),
    pointSize: () => (S().pointSize == null ? 1 : Number(S().pointSize)),
    opacity: () => (S().opacity == null ? 1 : Number(S().opacity)),
    slice: () => (S().slice == null ? null : Number(S().slice)),
    detailStride: () => Math.max(1, Math.round(cellCount / 500000)),
  }

  // ── view-state + views ──
  function viewForMode(mode) {
    if (mode === '3d') return new OrbitView({ fov: 50, near: 0.001, far: 10000 })
    return new OrthographicView({ id: 'flat', flipY: true })
  }
  function build3DViewState() {
    const [cx, cy, cz] = centroid
    const fov = 50
    const z = Math.log2((view.clientHeight || 600) / (sphereRadius * 2.4 * Math.tan((fov * Math.PI) / 360) * 2))
    return { target: [cx, cy, cz], zoom: isFinite(z) ? z : 0, rotationX: 20, rotationOrbit: 30, minZoom: -10, maxZoom: 30 }
  }
  function build2DViewState() {
    if (!positions) return {}
    const slice = cur.mode() === '2d' ? cur.slice() : null  // frame the active layer
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
    for (let i = 0; i < cellCount; i++) {
      if (slice != null && sliceCodes && sliceCodes[i] !== slice) continue
      const x = positions[i * 3]; const y = positions[i * 3 + 1]
      if (x < minX) minX = x; if (x > maxX) maxX = x
      if (y < minY) minY = y; if (y > maxY) maxY = y
    }
    if (!isFinite(minX)) {  // empty/degenerate layer → frame the whole footprint
      for (let i = 0; i < cellCount; i++) {
        const x = positions[i * 3]; const y = positions[i * 3 + 1]
        if (x < minX) minX = x; if (x > maxX) maxX = x; if (y < minY) minY = y; if (y > maxY) maxY = y
      }
    }
    if (!isFinite(minX)) return {}
    const cx = (minX + maxX) / 2; const cy = (minY + maxY) / 2
    const aspect = (view.clientWidth || 800) / (view.clientHeight || 600)
    const fitSpan = Math.max(Math.max(maxX - minX, 1e-6) / aspect, Math.max(maxY - minY, 1e-6)) * 1.15
    const z = Math.log2((view.clientHeight || 600) / fitSpan)
    return { target: [cx, cy, 0], zoom: isFinite(z) ? z : 0, minZoom: -10, maxZoom: 30 }
  }

  function ensureFlatPositions() {
    const src = positionsSpatial || positions
    if (positionsFlat || !src) return
    positionsFlat = new Float32Array(src.length)
    for (let i = 0; i < src.length; i += 3) { positionsFlat[i] = src[i]; positionsFlat[i + 1] = src[i + 1]; positionsFlat[i + 2] = 0 }
  }
  async function ensureUmapPositions() {
    if (positionsUmap) return true
    if (!manifest || !manifest.obsm || !manifest.obsm.X_umap) return false
    try {
      const { data: raw } = await loadCoords(S().url, 'X_umap')
      if (raw.length < cellCount * 2) return false
      let uMin = Infinity, uMax = -Infinity, vMin = Infinity, vMax = -Infinity
      for (let i = 0; i < cellCount; i++) {
        const u = raw[i * 2]; const v = raw[i * 2 + 1]
        if (u < uMin) uMin = u; if (u > uMax) uMax = u
        if (v < vMin) vMin = v; if (v > vMax) vMax = v
      }
      const uC = (uMin + uMax) / 2; const vC = (vMin + vMax) / 2
      const scale = (sphereRadius * 1.6) / Math.max(uMax - uMin, vMax - vMin, 1e-6)
      positionsUmap = new Float32Array(cellCount * 3)
      for (let i = 0; i < cellCount; i++) {
        positionsUmap[i * 3] = centroid[0] + (raw[i * 2] - uC) * scale
        positionsUmap[i * 3 + 1] = centroid[1] + (raw[i * 2 + 1] - vC) * scale
        positionsUmap[i * 3 + 2] = 0
      }
      return true
    } catch { return false }
  }

  // ── layers ──
  function buildLayers() {
    if (!positions || !colors || !filterValues) return []
    const mode = cur.mode()
    const pos = mode === '2d' && positionsFlat ? positionsFlat : positions
    return [new PointCloudLayer({
      id: 'cells',
      data: { length: cellCount, attributes: {
        getPosition: { value: pos, size: 3 },
        getColor: { value: colors, size: 4 },
        getFilterValue: { value: filterValues, size: 1 },
      } },
      coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
      pointSize: cur.pointSize() * pointSizeScale,
      opacity: cur.opacity(),
      sizeUnits: 'common',
      extensions: [new DataFilterExtension({ filterSize: 1 })],
      filterRange: [0.5, 1.5], filterEnabled: true,
      pickable: true,
      material: { unlit: true }, // deck v9: `material:false` is ignored; unlit kills the specular streak
      parameters: mode === '3d' ? { depthMask: true, depthTest: true } : { depthMask: false, depthTest: false },
      updateTriggers: { getColor: colorEpoch, getFilterValue: filterEpoch, getPosition: posEpoch },
    })]
  }
  let colorEpoch = 0; let filterEpoch = 0; let posEpoch = 0
  function refreshLayer() { if (deck) deck.setProps({ layers: buildLayers() }) }

  // ── filter mask (GPU-dropped via DataFilterExtension) ──
  function rebuildFilter() {
    if (!filterValues) return
    const mode = cur.mode()
    const stride = mode === '2d' ? 1 : cur.detailStride()
    const focusName = cur.cluster()
    const focused = focusName != null ? clusterCategories.indexOf(String(focusName)) : -1
    const expr = geneExpr
    const threshold = cur.colorBy() === 'gene' && expr && geneExprMax > 0 ? cur.threshold() : null
    const slice = mode === '2d' ? cur.slice() : null   // Z-layer filter only in 2D
    for (let i = 0; i < cellCount; i++) {
      let keep = (i % stride === 0)
      if (keep && clusterCodes && focused >= 0 && clusterCodes[i] !== focused) keep = false
      if (keep && threshold !== null && expr && expr[i] < threshold) keep = false
      if (keep && slice != null && sliceCodes && sliceCodes[i] !== slice) keep = false
      filterValues[i] = keep ? 1 : 0
    }
    filterEpoch++; refreshLayer()
  }

  // ── colouring ──
  function repaintCluster() {
    if (!colors || !clusterCodes) return
    const palette = categoricalPalette(clusterCategories.length)
    for (let i = 0; i < cellCount; i++) {
      const c = clusterCodes[i]
      if (c < 0 || c >= palette.length) { colors[i * 4] = 102; colors[i * 4 + 1] = 102; colors[i * 4 + 2] = 102 }
      else { const [r, g, b] = palette[c]; colors[i * 4] = Math.round(r * 255); colors[i * 4 + 1] = Math.round(g * 255); colors[i * 4 + 2] = Math.round(b * 255) }
      colors[i * 4 + 3] = 255
    }
    colorEpoch++; refreshLayer()
  }
  function recolorByExpression() {
    if (!colors || !geneExpr) return
    const lo = 0; const hi = geneExprMax; const span = Math.max(hi - lo, 1e-9)
    const cmap = cur.colormap()
    for (let i = 0; i < cellCount; i++) {
      const [r, g, b] = colormapColor(cmap, (geneExpr[i] - lo) / span)
      colors[i * 4] = Math.round(r * 255); colors[i * 4 + 1] = Math.round(g * 255); colors[i * 4 + 2] = Math.round(b * 255); colors[i * 4 + 3] = 255
    }
    colorEpoch++; refreshLayer()
  }
  async function applyClusterColor(column) {
    if (!column) { showStatus('No cluster column'); return }
    try {
      const { codes, categories } = await loadCategorical(S().url, column)
      clusterCodes = codes; clusterCategories = categories
      repaintCluster(); rebuildFilter(); renderLegendCluster()
    } catch (e) { lv.fail('spatial3d cluster: ' + ((e && e.message) || e)) }
  }
  async function applyGeneColor(gene) {
    const idx = geneSymbols.indexOf(gene)
    if (idx < 0) { showStatus(`gene ${gene} not found`); return }
    try {
      const expr = await loadGeneExpression(S().url, idx, cellCount)
      geneExpr = expr
      let max = 0; let nzCount = 0
      for (let i = 0; i < cellCount; i++) { const v = expr[i]; if (v > max) max = v; if (v > 0) nzCount++ }
      // Robust upper bound: 99th percentile of EXPRESSING cells, so a handful of
      // outlier-hot cells don't crush the whole colormap to its low end (common
      // with real genes — e.g. one cell at 111 while the bulk sit under 20).
      let vmax = max
      if (nzCount > 50) {
        const nz = new Float32Array(nzCount); let j = 0
        for (let i = 0; i < cellCount; i++) if (expr[i] > 0) nz[j++] = expr[i]
        nz.sort()
        vmax = nz[Math.floor(nzCount * 0.99)] || max
      }
      geneExprMax = vmax
      if (max === 0) { for (let i = 0; i < cellCount; i++) { colors[i * 4] = 90; colors[i * 4 + 1] = 90; colors[i * 4 + 2] = 90; colors[i * 4 + 3] = 255 } colorEpoch++; refreshLayer(); renderLegendGene(gene, 0) }
      else { recolorByExpression(); renderLegendGene(gene, vmax) }
      rebuildFilter()
    } catch (e) { lv.fail('spatial3d gene: ' + ((e && e.message) || e)) }
  }
  function applyColoring() {
    if (cur.colorBy() === 'gene' && cur.gene()) return applyGeneColor(cur.gene())
    return applyClusterColor(cur.clusterKey())
  }

  // ── legend ──
  function renderLegendCluster() {
    const palette = categoricalPalette(clusterCategories.length)
    const focus = cur.cluster()
    legendEl.style.display = clusterCategories.length ? '' : 'none'
    legTitle.textContent = `${clusterCategories.length} cell types`
    legBody.innerHTML = clusterCategories.map((label, i) => {
      const [r, g, b] = palette[i]
      const dim = focus != null && String(focus) !== String(label) ? ' dim' : ''
      return `<div class="sp3-legrow${dim}" data-cat="${encodeURIComponent(label)}"><span class="sp3-sw" style="background:rgb(${Math.round(r * 255)},${Math.round(g * 255)},${Math.round(b * 255)})"></span><span class="sp3-lbl">${String(label)}</span></div>`
    }).join('')
  }
  function renderLegendGene(gene, max) {
    legendEl.style.display = ''
    legTitle.textContent = gene
    legBody.innerHTML = `<div class="sp3-bar2" style="background:${colormapCSS(cur.colormap())}"></div>
      <div style="display:flex;justify-content:space-between;color:#9aa4b2"><span>0</span><span>${max.toFixed(2)}</span></div>`
  }

  function showStatus(t) { hud.textContent = t; hud.style.display = '' }

  // ── camera ──
  function setAgentCamera(camO) {
    if (!deck || cur.mode() !== '3d' || !camO) return
    const next = { ...currentViewState }
    if (typeof camO.rotationOrbit === 'number') next.rotationOrbit = camO.rotationOrbit
    if (typeof camO.rotationX === 'number') next.rotationX = camO.rotationX
    if (typeof camO.zoom === 'number') next.zoom = camO.zoom
    currentViewState = next; lastCamKey = camKey(next)
    deck.setProps({ initialViewState: { ...currentViewState } })
  }
  function camKey(v) { return `${Math.round(v.rotationOrbit || 0)},${Math.round(v.rotationX || 0)},${(v.zoom || 0).toFixed ? (v.zoom || 0).toFixed(2) : v.zoom}` }
  function scheduleCameraReport() {
    if (camTimer) clearTimeout(camTimer)
    camTimer = setTimeout(() => {
      camTimer = null
      if (cur.mode() !== '3d') return
      const cam = { rotationOrbit: Math.round(currentViewState.rotationOrbit || 0), rotationX: Math.round(currentViewState.rotationX || 0), zoom: currentViewState.zoom }
      const k = camKey(currentViewState)
      if (k === lastCamKey) return
      lastCamKey = k
      lv.setState({ camera: cam })
    }, 450)
  }

  // ── view mode switch ──
  async function setViewMode(mode) {
    if (!deck) return
    if (mode === 'umap') { if (!(await ensureUmapPositions())) { showStatus('no UMAP in dataset'); return } }
    if (mode === '2d') ensureFlatPositions()
    const wasUmap = positions && positionsUmap && positions === positionsUmap
    if (positions) {
      if (mode === 'umap' && positionsUmap) positions.set(positionsUmap)
      else if (mode !== 'umap' && positionsSpatial) positions.set(positionsSpatial)
    }
    posEpoch++
    currentViewState = mode === '3d' ? { ...initialViewState } : build2DViewState()
    deck.setProps({ views: viewForMode(mode), initialViewState: { ...currentViewState }, layers: buildLayers() })
    rebuildFilter()
  }

  // ════════════════════════ full (re)load ════════════════════════
  async function reload() {
    if (!Deck) return // deck.gl bundle still loading; the boot .then() re-invokes reload()
    const token = ++loadToken
    showOverlay('Loading manifest…', false)
    legendEl.style.display = 'none'
    try {
      manifest = await loadManifest(S().url)
      if (token !== loadToken) return
      cellCount = manifest.n_cells
      const spatialKey = S().spatialKey || manifest.default_spatial_key || 'spatial'

      showOverlay(`Loading ${cellCount.toLocaleString()} cells…`, false)
      const { data: coords, shape } = await loadCoords(S().url, spatialKey)
      if (token !== loadToken) return
      const ndim = shape && shape.length > 1 ? shape[1] : (manifest.spatial_ndim || 3)
      // Expand to N×3 (z=0 for 2D data).
      positionsSpatial = new Float32Array(cellCount * 3)
      if (ndim >= 3) { for (let i = 0; i < cellCount; i++) { positionsSpatial[i * 3] = coords[i * ndim]; positionsSpatial[i * 3 + 1] = coords[i * ndim + 1]; positionsSpatial[i * 3 + 2] = coords[i * ndim + 2] } }
      else { for (let i = 0; i < cellCount; i++) { positionsSpatial[i * 3] = coords[i * 2]; positionsSpatial[i * 3 + 1] = coords[i * 2 + 1]; positionsSpatial[i * 3 + 2] = 0 } }
      positions = new Float32Array(positionsSpatial)
      positionsFlat = null; positionsUmap = null

      // centroid + sphere radius
      let mnx = Infinity, mny = Infinity, mnz = Infinity, mxx = -Infinity, mxy = -Infinity, mxz = -Infinity
      for (let i = 0; i < cellCount; i++) {
        const x = positions[i * 3]; const y = positions[i * 3 + 1]; const z = positions[i * 3 + 2]
        if (x < mnx) mnx = x; if (x > mxx) mxx = x; if (y < mny) mny = y; if (y > mxy) mxy = y; if (z < mnz) mnz = z; if (z > mxz) mxz = z
      }
      centroid = [(mnx + mxx) / 2, (mny + mxy) / 2, (mnz + mxz) / 2]
      const dx = mxx - mnx, dy = mxy - mny, dz = mxz - mnz
      sphereRadius = Math.sqrt(dx * dx + dy * dy + dz * dz) / 2 || 1
      pointSizeScale = sphereRadius * 0.0025

      colors = new Uint8ClampedArray(cellCount * 4)
      for (let i = 0; i < cellCount; i++) { colors[i * 4] = 153; colors[i * 4 + 1] = 153; colors[i * 4 + 2] = 153; colors[i * 4 + 3] = 255 }
      filterValues = new Float32Array(cellCount)
      for (let i = 0; i < cellCount; i++) filterValues[i] = 1

      // Z-section codes for 2D "layer" stepping: discrete physical sections if
      // the data has few unique Z values, else bin the continuous Z into slabs.
      sliceCodes = null; sliceLabels = []
      {
        const zs = new Set()
        for (let i = 0; i < cellCount; i++) { zs.add(positionsSpatial[i * 3 + 2]); if (zs.size > 4000) break }
        const uniq = [...zs].sort((a, b) => a - b)
        if (uniq.length > 1) {
          let codeOf, nSlices
          if (uniq.length <= 60) {            // discrete sections — use them directly
            const mp = new Map(uniq.map((z, i) => [z, i]))
            nSlices = uniq.length; codeOf = (z) => (mp.get(z) ?? -1)
            sliceLabels = uniq.map((z) => Number.isInteger(z) ? `z=${z}` : `z=${z.toFixed(1)}`)
          } else {                            // continuous Z — bin into ~18 slabs
            nSlices = 18
            const z0 = uniq[0]; const span = (uniq[uniq.length - 1] - z0) || 1
            codeOf = (z) => Math.min(nSlices - 1, Math.max(0, Math.floor(((z - z0) / span) * nSlices)))
            sliceLabels = Array.from({ length: nSlices }, (_, i) => String(i + 1))
          }
          sliceCodes = new Int32Array(cellCount)
          for (let i = 0; i < cellCount; i++) sliceCodes[i] = codeOf(positionsSpatial[i * 3 + 2])
        }
      }

      initialViewState = build3DViewState()
      currentViewState = { ...initialViewState }
      const mode0 = cur.mode()

      if (deck) { deck.finalize(); deck = null }
      deck = new Deck({
        parent: view,
        style: { position: 'absolute', inset: '0' },
        deviceProps: { webgl: { preserveDrawingBuffer: true } }, // so onSnapshot can read the canvas
        views: viewForMode(mode0),
        initialViewState: mode0 === '3d' ? initialViewState : build2DViewState(),
        controller: true,
        getCursor: ({ isDragging, isHovering }) => isDragging ? 'grabbing' : isHovering ? 'crosshair' : 'default',
        onViewStateChange: ({ viewState }) => { currentViewState = viewState; scheduleCameraReport() },
        onHover: onHover,
        layers: [],
      })
      if (mode0 === '2d') ensureFlatPositions()

      rebuildFilter()

      // gene vocab
      try { geneSymbols = await loadGeneSymbols(S().url) } catch { geneSymbols = [] }
      if (token !== loadToken) return
      geneList.innerHTML = geneSymbols.slice(0, 4000).map((g) => `<option value="${g}">`).join('')

      await applyColoring()
      if (token !== loadToken) return
      hud.textContent = `${cellCount.toLocaleString()} cells`; hud.style.display = ''
      syncUI()   // sliceCodes now known → reveal the 2D layer stepper if applicable
      hideOverlay()
    } catch (e) {
      if (token !== loadToken) return
      const msg = (e && e.message) || String(e)
      showOverlay(msg, true); lv.fail('spatial3d: ' + msg)
    }
  }

  function onHover({ index, x, y }) {
    if (index == null || index === -1) { tip.style.display = 'none'; return }
    const code = clusterCodes ? clusterCodes[index] : -1
    const clusterName = code >= 0 && code < clusterCategories.length ? clusterCategories[code] : null
    const expr = cur.colorBy() === 'gene' && geneExpr ? geneExpr[index] : null
    let html = `cell #${index}`
    if (clusterName) html += `<br>${clusterName}`
    if (expr != null) html += `<br>${cur.gene()}: ${expr.toFixed(3)}`
    tip.innerHTML = html; tip.style.left = (x + 14) + 'px'; tip.style.top = (y + 14) + 'px'; tip.style.display = ''
  }

  // ── reflect state into controls ──
  function syncUI() {
    const s = S()
    titleEl.textContent = s.title || 'Spatial cells'
    const mode = cur.mode(); const cb = cur.colorBy()
    viewSeg.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b.getAttribute('data-vm') === mode))
    cbSeg.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b.getAttribute('data-cb') === cb))
    geneWrap.style.display = cb === 'gene' ? '' : 'none'
    cmapWrap.style.display = cb === 'gene' ? '' : 'none'
    if (document.activeElement !== geneInput) geneInput.value = cur.gene() || ''
    cmapSel.value = cur.colormap()
    if (document.activeElement !== pSize) pSize.value = String(cur.pointSize())
    if (document.activeElement !== opac) opac.value = String(cur.opacity())
    // 2D layer (Z-section) stepper — only meaningful in 2D with section codes
    const showSlice = mode === '2d' && sliceCodes && sliceLabels.length > 1
    sliceEl.style.display = showSlice ? '' : 'none'
    if (showSlice) {
      const sl = cur.slice()
      slLabel.textContent = sl == null ? 'All layers' : `Layer ${sl + 1} / ${sliceLabels.length}`
      const allBtn = sliceEl.querySelector('[data-sl="all"]')
      if (allBtn) allBtn.classList.toggle('on', sl == null)
    }
  }

  // ── control interactions → single state path ──
  viewSeg.addEventListener('click', (e) => { const b = e.target.closest('button[data-vm]'); if (b) lv.setState({ mode: b.getAttribute('data-vm') }) })
  cbSeg.addEventListener('click', (e) => { const b = e.target.closest('button[data-cb]'); if (b) lv.setState({ colorBy: b.getAttribute('data-cb') }) })
  geneInput.addEventListener('change', () => { const g = geneInput.value.trim(); if (g) lv.setState({ colorBy: 'gene', gene: g }) })
  cmapSel.addEventListener('change', () => lv.setState({ colormap: cmapSel.value }))
  pSize.addEventListener('input', () => lv.setState({ pointSize: Number(pSize.value) }))
  opac.addEventListener('input', () => lv.setState({ opacity: Number(opac.value) }))
  legendEl.addEventListener('click', (e) => {
    const row = e.target.closest('[data-cat]'); if (!row) return
    const name = decodeURIComponent(row.getAttribute('data-cat'))
    lv.setState({ cluster: String(cur.cluster()) === name ? null : name })
  })

  // ── the one render path ──
  lv.onState((state) => {
    if (!state) return
    if (!state.url) {
      overlay.className = 'sp3-overlay'
      overlay.innerHTML = '<span>Provide <b>state.url</b> — a spatial <code>.zarr</code> (obsm/spatial + obs/&lt;cluster&gt; + X_csc). See spatial3d.md.</span>'
      return
    }
    const prev = applied; applied = state; syncUI()
    if (!prev || state.url !== prev.url || state.spatialKey !== prev.spatialKey) { reload(); return }
    if (!deck) return
    if (state.mode !== prev.mode) setViewMode(cur.mode())
    if (state.colorBy !== prev.colorBy || state.clusterKey !== prev.clusterKey || state.gene !== prev.gene) applyColoring()
    else if (state.colormap !== prev.colormap && cur.colorBy() === 'gene') { recolorByExpression(); renderLegendGene(cur.gene(), geneExprMax) }
    if (String(state.cluster) !== String(prev.cluster)) { rebuildFilter(); if (cur.colorBy() === 'cluster') renderLegendCluster() }
    if (state.threshold !== prev.threshold) rebuildFilter()
    if (state.slice !== prev.slice) {
      rebuildFilter()
      if (cur.mode() === '2d') { currentViewState = build2DViewState(); deck.setProps({ initialViewState: { ...currentViewState } }) }
    }
    if (state.pointSize !== prev.pointSize || state.opacity !== prev.opacity) refreshLayer()
    if (state.camera && camKey({ ...currentViewState, ...state.camera }) !== lastCamKey) setAgentCamera(state.camera)
  })

  // ── WebGL snapshot (deck canvas; preserveDrawingBuffer is on) ──
  lv.onSnapshot(() => {
    if (!deck) return null
    try { deck.redraw('snapshot') } catch { /* deck draws on rAF anyway */ }
    const c = deck.getCanvas ? deck.getCanvas() : view.querySelector('canvas')
    return c ? c.toDataURL('image/png') : null
  })

  // ── boot: load the deck.gl bundle, then we're ready for state ──
  loadDeck().then((d) => {
    Deck = d.Deck; OrbitView = d.OrbitView; OrthographicView = d.OrthographicView
    COORDINATE_SYSTEM = d.COORDINATE_SYSTEM; PointCloudLayer = d.PointCloudLayer; DataFilterExtension = d.DataFilterExtension
    if (applied && applied.url && !deck) reload() // state arrived before deck finished loading
  }).catch((e) => { showOverlay('deck.gl failed to load: ' + ((e && e.message) || e), true); lv.fail('spatial3d: deck.gl load failed') })

  // app-host calls lv.ready() once setup() resolves.
}
