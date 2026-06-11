/**
 * Gosling.js LiveView adapter — grammar-based genomic visualisation, wrapped
 * in a lightweight genome-browser shell.
 *
 * Where IGV is the classical "open a BAM/VCF at this locus" browser, Gosling is
 * "write a JSON spec for a designed genomic figure" — circular ideograms,
 * multi-track linear views, comparative dual-genome plots, sample-faceted
 * layouts, and Hi-C contact matrices. It is to genomics what Vega-Lite is to
 * charts.
 *
 * Loaded as an ordinary setup(lv, root) plugin module. Gosling.js is imported
 * by full URL from esm.sh with react kept external (single React instance with
 * app-host); everything else (pixi.js, HiGlass, d3-*) is bundled by esm.sh.
 *
 * LiveView "state":
 *   {
 *     "spec": { ... a Gosling specification ... },
 *     "options": { ... embed options, optional ... }
 *   }
 *
 * The agent authors the Gosling `spec` — that JSON IS the driving interface.
 * See https://gosling-lang.org for the grammar.
 *
 * On top of the raw embed the adapter adds the genome-browser experience the
 * spec cannot express on its own:
 *   1. a centred, framed card layout (not a figure dumped in the top-left);
 *   2. a gene / locus SEARCH BAR — type a gene symbol (with autocomplete) or a
 *      locus and the view navigates there (Gosling's zoomToGene / zoomTo);
 *   3. responsive sizing — every track is fitted to the same panel width (so
 *      stacked tracks stay aligned and nothing is clipped in a narrow panel),
 *      re-fitted on resize;
 *   4. a viridis colormap safety net for contact matrices (HiGlass renders an
 *      array / unknown range as viridis anyway, and the only other honoured
 *      name is the magenta "warm" — which reads poorly, so we drop it).
 */

import { embed } from 'https://esm.sh/gosling.js@1.0.7?external=react,react-dom'

const VIRIDIS = 'viridis'
// HiGlass honours almost no matrix colormap name except "warm" (a poor magenta
// ramp) and "viridis"; arrays/other names silently fall back to viridis. So we
// only need to drop the rainbow/magenta family back to the viridis default.
const BAD_RANGE = new Set(['warm', 'cool', 'rainbow', 'sinebow'])

const clone = (o) => JSON.parse(JSON.stringify(o))

function walk(node, fn) {
  if (!node || typeof node !== 'object') return
  fn(node)
  for (const k of Object.keys(node)) {
    const v = node[k]
    if (v && typeof v === 'object') walk(v, fn)
  }
}

const isMatrixTrack = (n) => !!(n && n.data && n.data.type === 'matrix')
// Gosling gene-annotation data: a beddb tileset carrying exon intervals.
const isGeneAnno = (n) =>
  !!(n && n.data && n.data.type === 'beddb' && Array.isArray(n.data.exonIntervalFields))

function specHasMatrix(spec) {
  let m = false
  walk(spec, (n) => { if (isMatrixTrack(n)) m = true })
  return m
}
function specIsGenomic(spec) {
  let g = false
  walk(spec, (n) => { if (n && n.type === 'genomic') g = true })
  return g
}

function normaliseMatrixColors(spec) {
  walk(spec, (n) => {
    if (!isMatrixTrack(n)) return
    const c = n.color && typeof n.color === 'object' ? n.color : {}
    if (typeof c.range === 'string' && BAD_RANGE.has(c.range.toLowerCase())) c.range = VIRIDIS
    if (c.field == null) c.field = 'value'
    if (c.type == null) c.type = 'quantitative'
    if (c.legend == null) c.legend = true
    n.color = c
  })
}

// In a genome-browser spec — a Hi-C matrix stacked with a linear track (a gene
// annotation) — the matrix's left (y) axis offsets its plot horizontally, so it
// no longer lines up with the linear track below (the exact misalignment users
// hit). Drop the matrix's left axis and zero the inter-view spacing so both
// tracks share one left edge and the same x-axis. A standalone matrix (no track
// to align with) keeps its axes.
function alignGenomeBrowser(spec) {
  let hasMatrix = false
  let hasLinear = false
  walk(spec, (n) => {
    if (isMatrixTrack(n)) hasMatrix = true
    else if (n && n.mark && n.x && n.x.type === 'genomic') hasLinear = true
  })
  if (!hasMatrix || !hasLinear) return
  spec.spacing = 0
  walk(spec, (n) => {
    if (isMatrixTrack(n) && n.y && n.y.axis && n.y.axis !== 'none') n.y.axis = 'none'
  })
}

// Below this matrix width Gosling's genomic axis track fails to instantiate
// ("invalid format: ,.NaN" → an "Unknown track type: axis-track" red box), and
// the tick labels would be unreadable anyway — so we drop the axis there.
const MATRIX_AXIS_MIN = 420

// Fit every track to the SAME target width — so stacked / linked tracks stay
// aligned — fitted to the panel and re-fitted on resize, so a hardcoded width
// is never clipped. Matrix tracks become square; others keep their aspect.
function fitSpec(spec, availW) {
  const hasMatrix = specHasMatrix(spec)
  let target
  if (hasMatrix) {
    // The legend overlays the matrix (top-right), so once the left axis is gone
    // (genome-browser layout) the matrix can fill nearly the full width — no
    // upper cap, so it tops out the panel. Reserve a left margin only when a
    // left axis is actually drawn (a standalone matrix).
    let leftAxis = false
    walk(spec, (n) => {
      if (isMatrixTrack(n) && n.y && n.y.axis && n.y.axis !== 'none') leftAxis = true
    })
    target = Math.max(220, availW - (leftAxis ? 76 : 12))
  } else {
    target = Math.max(180, Math.min(availW - 24, 1040))
  }
  const dropAxes = hasMatrix && target < MATRIX_AXIS_MIN
  walk(spec, (n) => {
    // Below the threshold the HiGlass genomic axis track fails to instantiate,
    // so suppress every genomic axis (its labels would be illegible anyway).
    if (dropAxes && n && (n.mark || n.tracks)) {
      if (n.x && n.x.type === 'genomic') n.x.axis = 'none'
      if (n.y && n.y.type === 'genomic') n.y.axis = 'none'
    }
    if (typeof n.width !== 'number') return
    const orig = n.width
    n.width = target
    if (isMatrixTrack(n)) n.height = target
    else if (typeof n.height === 'number' && orig > 0)
      n.height = Math.max(1, Math.round(n.height * (target / orig))) // keep aspect
  })
}

// Gosling's zoomTo / zoomToGene / suggestGene are keyed by TRACK id (not view
// id). Give every track a stable id and pick the one the search bar drives:
// prefer the matrix — its x/y linking keeps the Hi-C square on navigation — and
// otherwise the first genomic track. Gene search resolves globally as long as
// the spec contains a gene-annotation track, so any track id works for it.
function ensureTrackIdsAndPickDriver(spec) {
  const tracks = []
  walk(spec, (n) => {
    if (Array.isArray(n.tracks)) n.tracks.forEach((t) => { if (t && typeof t === 'object') tracks.push(t) })
  })
  if (tracks.length === 0) return null
  tracks.forEach((t, i) => { if (!t.id) t.id = 'gos-track-' + i })
  const matrix = tracks.find(isMatrixTrack)
  if (matrix) {
    // Keep navigation square: mirror the x linkingId onto y if it is missing,
    // so zooming to a gene/locus moves both matrix axes together.
    if (matrix.x && matrix.x.linkingId && matrix.y && !matrix.y.linkingId)
      matrix.y.linkingId = matrix.x.linkingId
    return matrix.id
  }
  const genomic = tracks.find((t) => t.x && t.x.type === 'genomic')
  return (genomic || tracks[0]).id
}

const LOCUS_RE = /^(chr[\w]+)[\s:]+([\d,]+)\s*[-–—]\s*([\d,]+)$/i

export async function setup(lv, root) {
  // ── Page chrome: DARK, to match the Pantheon UI (a light card in the dark
  // app looked out of place). Built from semi-transparent whites so it adapts
  // to the panel's exact dark background. A branded header (title + locus) sits
  // above a slim sticky search bar; Gosling's own plain title is lifted into the
  // header (see render) so there's no big gap above the figure. ──
  root.style.cssText =
    'width:100%;height:100%;overflow:auto;scrollbar-gutter:stable;box-sizing:border-box;' +
    'color:#c9d1d9;background:transparent;font:13px/1.45 -apple-system,BlinkMacSystemFont,' +
    '"Segoe UI",Roboto,Helvetica,Arial,sans-serif;'
  const card = document.createElement('div')
  card.style.cssText =
    'margin:6px;background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.09);' +
    'border-radius:12px;box-shadow:0 1px 2px rgba(0,0,0,.3);overflow:hidden;box-sizing:border-box;'

  // Sticky top bar = branded header (title) + search toolbar.
  const topbar = document.createElement('div')
  topbar.style.cssText =
    'position:sticky;top:0;z-index:5;background:rgba(255,255,255,.03);' +
    'border-bottom:1px solid rgba(255,255,255,.08);backdrop-filter:blur(6px);'

  const header = document.createElement('div')
  header.style.cssText = 'display:flex;align-items:center;gap:10px;padding:8px 12px 6px;'
  header.innerHTML =
    '<div style="flex:none;width:26px;height:26px;border-radius:7px;display:flex;' +
    'align-items:center;justify-content:center;' +
    'background:linear-gradient(135deg,#2f81f7,#1aab9b);' +
    'box-shadow:0 1px 2px rgba(0,0,0,.35)">' +
    '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#fff" ' +
    'stroke-width="1.7" stroke-linecap="round">' +
    '<path d="M8 3c0 4 8 5 8 9s-8 5-8 9"/>' +
    '<path d="M16 3c0 4-8 5-8 9s8 5 8 9"/>' +
    '<line x1="9.2" y1="6.2" x2="14.8" y2="6.2"/>' +
    '<line x1="9.2" y1="17.8" x2="14.8" y2="17.8"/></svg></div>' +
    '<div style="min-width:0">' +
    '<div data-role="title" style="font-size:13.5px;font-weight:600;color:#e6edf3;' +
    'line-height:1.25;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">Genome Browser</div>' +
    '<div data-role="subtitle" style="font-size:11px;color:#8b949e;line-height:1.3;' +
    'white-space:nowrap;overflow:hidden;text-overflow:ellipsis"></div></div>'

  const toolbar = document.createElement('div')
  toolbar.style.cssText =
    'display:flex;gap:6px;align-items:center;flex-wrap:wrap;padding:0 12px 8px;'
  toolbar.innerHTML =
    '<input data-role="q" list="gos-genes" autocomplete="off" spellcheck="false" ' +
    'placeholder="Search gene (TP53) or locus (chr9:5,450,000-5,470,000)" ' +
    'style="flex:1 1 200px;min-width:0;padding:5px 10px;border:1px solid rgba(255,255,255,.12);' +
    'border-radius:8px;font:12.5px ui-monospace,SFMono-Regular,Menlo,monospace;' +
    'color:#c9d1d9;background:rgba(0,0,0,.25);outline:none" />' +
    '<datalist id="gos-genes"></datalist>' +
    '<button data-role="go" style="' + btnCss('#2f81f7', '#fff') + '">Go</button>' +
    '<button data-role="reset" title="Zoom out to the whole genome" style="' +
    btnCss('rgba(255,255,255,.06)', '#c9d1d9', 'rgba(255,255,255,.14)') + '">Whole genome</button>' +
    '<span data-role="hint" style="flex-basis:100%;color:#ff7b72;font-size:12px;display:none;margin-top:2px"></span>'

  topbar.appendChild(header)
  topbar.appendChild(toolbar)
  const mount = document.createElement('div')
  mount.style.cssText = 'padding:4px 6px 8px;box-sizing:border-box;overflow:auto;'
  card.appendChild(topbar)
  card.appendChild(mount)
  root.appendChild(card)

  const titleEl = header.querySelector('[data-role=title]')
  const subtitleEl = header.querySelector('[data-role=subtitle]')
  const input = toolbar.querySelector('[data-role=q]')
  const genes = toolbar.querySelector('#gos-genes')
  const hintEl = toolbar.querySelector('[data-role=hint]')
  const setHint = (t) => { hintEl.textContent = t || ''; hintEl.style.display = t ? 'block' : 'none' }

  let api = null
  let driveTrackId = null
  let rawSpec = null
  let rawOptions = null
  let lastSpecKey = null
  let lastRenderedW = -1

  function navigate(q) {
    q = (q || '').trim()
    if (!q || !api || !driveTrackId) return
    const m = q.match(LOCUS_RE)
    try {
      if (m) {
        const s = +m[2].replace(/,/g, ''), e = +m[3].replace(/,/g, '')
        api.zoomTo(driveTrackId, m[1] + ':' + s + '-' + e, 0, 1000)
      } else {
        api.zoomToGene(driveTrackId, q, 5000, 1000) // ±5 kb padding
      }
      setHint('')
    } catch (err) {
      setHint('Could not navigate to "' + q + '".')
    }
  }

  toolbar.querySelector('[data-role=go]').addEventListener('click', () => navigate(input.value))
  toolbar.querySelector('[data-role=reset]').addEventListener('click', () => {
    if (api && driveTrackId) try { api.zoomToExtent(driveTrackId, 1000) } catch (e) { /* noop */ }
  })
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); navigate(input.value) } })

  // Gene-symbol autocomplete via Gosling's suggestGene (queries the gene
  // annotation track's tileset). Debounced; silent on failure.
  let sugTimer = null
  input.addEventListener('input', () => {
    clearTimeout(sugTimer)
    const kw = input.value.trim()
    if (kw.length < 2 || LOCUS_RE.test(kw) || !api || !driveTrackId) return
    sugTimer = setTimeout(() => {
      try {
        api.suggestGene(driveTrackId, kw, (sug) => {
          genes.innerHTML = (sug || []).slice(0, 8)
            .map((s) => '<option value="' + String(s.geneName || s.name || '').replace(/"/g, '') + '">')
            .join('')
        })
      } catch (e) { /* gene search unavailable (e.g. no gene track) */ }
    }, 180)
  })

  function availWidth() {
    const cs = getComputedStyle(mount)
    const padX = parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight)
    const w = Math.max(240, mount.clientWidth - padX)
    return Math.round(w / 8) * 8 // quantise so resize jitter doesn't re-embed
  }

  async function render() {
    if (!rawSpec) return
    const availW = availWidth()
    const specKey = JSON.stringify([rawSpec, rawOptions || null])
    // Re-embed on a spec change or a REAL width change only. Ignore sub-scrollbar
    // width jitter: a tall figure makes a vertical scrollbar appear, which shrinks
    // the width, which would re-embed a shorter figure, which drops the scrollbar…
    // a flicker loop. The 24px threshold (> a scrollbar) breaks it.
    if (specKey === lastSpecKey && Math.abs(availW - lastRenderedW) < 24) return
    lastSpecKey = specKey
    lastRenderedW = availW
    const spec = clone(rawSpec)
    // Lift title/subtitle into our styled header and drop them from the spec so
    // Gosling doesn't render its own plain title (which leaves a big gap).
    titleEl.textContent = (typeof spec.title === 'string' && spec.title) || 'Genome Browser'
    subtitleEl.textContent = typeof spec.subtitle === 'string' ? spec.subtitle : ''
    subtitleEl.style.display = subtitleEl.textContent ? 'block' : 'none'
    delete spec.title
    delete spec.subtitle
    normaliseMatrixColors(spec)
    alignGenomeBrowser(spec)
    fitSpec(spec, availW)
    driveTrackId = ensureTrackIdsAndPickDriver(spec)
    toolbar.style.display = specIsGenomic(spec) ? 'flex' : 'none'
    mount.innerHTML = ''
    // Dark theme to match the Pantheon UI; padding:6 instead of Gosling's huge
    // 60px default so the figure fills the card (no fat left/top margin). Caller
    // can override via options.
    api = await embed(mount, spec, { theme: 'dark', padding: 6, margin: 0, ...(rawOptions || {}) })
  }

  // Serialise renders — a state change and a resize must not interleave their
  // async embed()s into the same mount.
  let rendering = Promise.resolve()
  function requestRender() {
    rendering = rendering
      .then(render)
      .catch((e) => lv.fail('Gosling: ' + ((e && e.message) || e)))
  }

  lv.onState((state, info) => {
    if (info && info.reason === 'emit') return
    if (!state || !state.spec || typeof state.spec !== 'object') {
      lv.fail('Gosling: state must include a `spec` object (a Gosling spec).')
      return
    }
    rawSpec = state.spec
    rawOptions = state.options
    requestRender()
  })

  // Re-fit when the panel is resized (debounced); the key check skips the
  // re-embed when the quantised width is unchanged.
  let resizeTimer = null
  const ro = new ResizeObserver(() => {
    clearTimeout(resizeTimer)
    resizeTimer = setTimeout(requestRender, 150)
  })
  ro.observe(card)

  // ── Snapshot ──
  // Gosling renders via HiGlass + PixiJS (WebGL), whose live canvas reads back
  // BLANK with toDataURL (preserveDrawingBuffer = false). Gosling's own
  // api.getCanvas() re-renders into a fresh, readable canvas — use that so the
  // screenshot is the real figure, not a black rectangle. Fall back to the
  // largest live canvas only if the API is unavailable.
  lv.onSnapshot(() => {
    let src = null
    try {
      if (api && typeof api.getCanvas === 'function') {
        // resolution 1 (on-screen size) — higher res triggers a slow WebGL
        // ReadPixels stall that can time out the host's snapshot request.
        const r = api.getCanvas({ resolution: 1, transparentBackground: false })
        if (r && r.canvas) src = r.canvas
      }
    } catch (e) {
      src = null
    }
    if (!src) {
      const canvases = Array.from(root.querySelectorAll('canvas'))
      if (canvases.length === 0) return null
      src = canvases.reduce((a, b) => (a.width * a.height >= b.width * b.height ? a : b))
    }
    try {
      const scale = Math.min(1, 1280 / Math.max(src.width, 1))
      const out = document.createElement('canvas')
      out.width = Math.max(1, Math.round(src.width * scale))
      out.height = Math.max(1, Math.round(src.height * scale))
      const ctx = out.getContext('2d')
      ctx.fillStyle = '#ffffff'
      ctx.fillRect(0, 0, out.width, out.height)
      ctx.drawImage(src, 0, 0, out.width, out.height)
      return out.toDataURL('image/jpeg', 0.9)
    } catch (e) {
      return null // fall back to the host's html2canvas
    }
  })
}

function btnCss(bg, fg, border) {
  return (
    'padding:5px 11px;border-radius:7px;cursor:pointer;font:12.5px system-ui;' +
    'font-weight:500;white-space:nowrap;background:' + bg + ';color:' + fg +
    ';border:1px solid ' + (border || bg) + ';'
  )
}
