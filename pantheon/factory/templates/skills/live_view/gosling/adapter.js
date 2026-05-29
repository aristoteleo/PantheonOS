/**
 * Gosling.js LiveView adapter — grammar-based genomic visualisation.
 *
 * Where IGV is the classical "open a BAM/VCF at this locus" browser,
 * Gosling is "write a JSON spec for a designed genomic figure" — circular
 * ideograms, multi-track linear views, comparative dual-genome plots,
 * sample-faceted layouts. It is to genomics what Vega-Lite is to charts.
 *
 * Loaded as an ordinary setup(lv, root) plugin module. Gosling.js is
 * imported by full URL from esm.sh with react kept external (single React
 * instance with app-host); everything else (pixi.js, HiGlass, d3-*) is
 * bundled by esm.sh.
 *
 * LiveView "state":
 *   {
 *     "spec": { ... a Gosling specification ... },
 *     "options": { ... embed options, optional ... }
 *   }
 *
 * The agent's job here is to author the Gosling `spec` — that JSON IS the
 * driving interface. See https://gosling-lang.org for the grammar.
 */

import { embed } from 'https://esm.sh/gosling.js@1.0.7?external=react,react-dom'

export async function setup(lv, root) {
  root.style.width = '100%'
  root.style.height = '100%'
  root.style.overflow = 'auto'
  root.style.background = '#ffffff'  // Gosling is designed for a light background
  root.style.color = '#222'

  let api = null
  let lastKey = null

  async function render(spec, options) {
    const key = JSON.stringify([spec, options || null])
    if (key === lastKey) return
    lastKey = key
    // Gosling's embed mounts a HiGlass container; clear any previous one.
    root.innerHTML = ''
    api = await embed(root, spec, options || {})
  }

  lv.onState((state, info) => {
    if (info && info.reason === 'emit') return
    if (!state || !state.spec || typeof state.spec !== 'object') {
      lv.fail('Gosling: state must include a `spec` object (a Gosling spec).')
      return
    }
    render(state.spec, state.options).catch((e) =>
      lv.fail('Gosling: ' + ((e && e.message) || e)),
    )
  })

  // Snapshot — Gosling renders via HiGlass + PixiJS (WebGL). Capture the
  // largest canvas; if its drawing buffer is blank (preserveDrawingBuffer
  // = false), the host's html2canvas fallback at least catches the DOM.
  lv.onSnapshot(() => {
    const canvases = Array.from(root.querySelectorAll('canvas'))
    if (canvases.length === 0) return null
    const c = canvases.reduce((a, b) =>
      a.width * a.height >= b.width * b.height ? a : b,
    )
    try {
      return c.toDataURL('image/jpeg', 0.85)
    } catch (e) {
      return null  // fall back to the host's html2canvas
    }
  })
}
