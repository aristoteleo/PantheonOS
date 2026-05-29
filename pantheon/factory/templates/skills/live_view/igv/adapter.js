/**
 * IGV.js LiveView adapter — genome-browser viewer plugin.
 *
 * Embeds the IGV (Integrative Genomics Viewer) JavaScript port: a track-
 * based browser on a reference genome (hg38, mm10, custom FASTA, …) that
 * renders BAM/CRAM alignments, VCF variants, BED/GFF annotations, bigWig
 * coverage, and more.
 *
 * IGV.js ships as a UMD bundle; this adapter injects it from a CDN and
 * drives `igv.createBrowser`.
 *
 * LiveView "state":
 *   {
 *     "genome": "hg38",                       // built-in id OR a custom
 *                                             //   {id, fastaURL, indexURL, ...}
 *     "locus": "chr8:127,736,588-127,739,371",// initial position; symbol works
 *                                             //   only when a searchable
 *                                             //   annotation is loaded
 *     "tracks": [                             // optional
 *       {
 *         "name": "HG00103",
 *         "url": "https://.../sample.bam",
 *         "indexURL": "https://.../sample.bam.bai",
 *         "format": "bam"                     // bam|cram|vcf|bed|gff|bigwig|wig
 *       }
 *     ]
 *   }
 *
 * To navigate later: `live_view_update(view_id, {"locus": "BRCA1"})` —
 * only the locus changes, IGV's existing tracks stay (no rebuild).
 * Changing `genome` or `tracks` rebuilds the browser.
 */

const IGV_BUNDLE = 'https://cdn.jsdelivr.net/npm/igv@3.8.0/dist/igv.min.js'

function loadIgv() {
  return new Promise((resolve, reject) => {
    if (window.igv && window.igv.createBrowser) {
      resolve()
      return
    }
    const js = document.createElement('script')
    js.src = IGV_BUNDLE
    js.onload = () => resolve()
    js.onerror = () => reject(new Error('failed to load igv.js from the CDN'))
    document.head.appendChild(js)
  })
}

export async function setup(lv, root) {
  root.style.width = '100%'
  root.style.height = '100%'
  // IGV is designed for a light background; force white inside our dark host.
  root.style.background = '#ffffff'
  root.style.color = '#222'

  await loadIgv()

  let browser = null
  let lastConfigKey = null  // (genome + tracks); `locus` is mutable in-place

  const configKey = (state) =>
    JSON.stringify([state.genome, state.tracks || []])

  async function applyState(state) {
    if (!state || !state.genome) {
      root.innerHTML =
        '<div style="padding:24px;color:#666;font:13px system-ui">' +
        'No genome — set state.genome (e.g. "hg38").</div>'
      return
    }
    const key = configKey(state)
    // Same genome + tracks: only locus may differ — navigate in place.
    if (browser && key === lastConfigKey) {
      if (state.locus) {
        try { await browser.search(state.locus) } catch (e) { /* unknown symbol → silent */ }
      }
      return
    }
    // Genome or tracks changed: tear down and rebuild.
    if (browser) {
      try { window.igv.removeBrowser(browser) } catch (e) { /* ignore */ }
      browser = null
    }
    root.innerHTML = ''
    browser = await window.igv.createBrowser(root, {
      genome: state.genome,
      locus: state.locus,
      tracks: state.tracks || [],
    })
    lastConfigKey = key
  }

  lv.onState((state, info) => {
    if (info && info.reason === 'emit') return
    applyState(state).catch((e) =>
      lv.fail('IGV: ' + ((e && e.message) || e)),
    )
  })

  // No custom snapshot provider — IGV renders tracks to 2D <canvas>
  // elements (no preserveDrawingBuffer issue), so the host's html2canvas
  // fallback captures them. If a high-fidelity export is needed later,
  // browser.toSVG() returns an SVG of the current view.
}
