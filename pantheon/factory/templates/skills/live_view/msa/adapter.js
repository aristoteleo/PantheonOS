/**
 * Multiple Sequence Alignment LiveView adapter.
 *
 * Renders the alignment as a single SVG document. Earlier attempts:
 *   - EBI Nightingale's <nightingale-msa>: required a fragile setup
 *     order (positionToSequence / yPos errors).
 *   - Pure HTML/CSS grid: rendered fine on screen, but the host's
 *     html2canvas snapshot saw a blank image — overflow:auto + inline-
 *     block content didn't capture cleanly.
 *
 * SVG solves both: no layout quirks; serialise + rasterise via a stable
 * <Image> → canvas path for a pixel-perfect snapshot.
 *
 * LiveView "state":
 *   {
 *     "sequences": [{ "name": "seq1", "sequence": "ACGT-..." }, ...],
 *     "color_scheme": "clustal",   // clustal | nucleotide (auto-detected
 *                                  //   if omitted)
 *     "tile_width":   18,          // px per column
 *     "tile_height":  22,          // px per row
 *     "label_width":  140,         // px reserved for sequence names
 *     "show_ruler":   true         // column-number track on top
 *   }
 */

// ClustalX-style residue colors (proteins).
const CLUSTAL_AA = {
  A: '#80a0f0', I: '#80a0f0', L: '#80a0f0', M: '#80a0f0',
  F: '#80a0f0', W: '#80a0f0', V: '#80a0f0', C: '#80a0f0',
  K: '#f01505', R: '#f01505',
  E: '#c048c0', D: '#c048c0',
  N: '#15c015', Q: '#15c015', S: '#15c015', T: '#15c015',
  G: '#f09048',
  H: '#15a4a4', Y: '#15a4a4',
  P: '#c0c000',
}

// Standard nucleotide colors (A-blue, T/U-yellow, G-orange, C-red).
const NUCLEOTIDE = {
  A: '#5050ff', T: '#e6e600', U: '#e6e600', G: '#ffb300', C: '#e02020',
  N: '#cccccc',
}

const SCHEMES = { clustal: CLUSTAL_AA, nucleotide: NUCLEOTIDE }

function detectScheme(sequences) {
  const nucChars = /^[ACGTUNacgtun\-\.]+$/
  const isNuc = sequences.every((s) => nucChars.test(s.sequence))
  return isNuc ? 'nucleotide' : 'clustal'
}

function xmlEscape(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    { '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]
  ))
}

function buildSvg(state) {
  const tileW  = state.tile_width  || 18
  const tileH  = state.tile_height || 22
  const labelW = state.label_width || 140
  const showRuler = state.show_ruler !== false
  const scheme = SCHEMES[state.color_scheme || detectScheme(state.sequences)]
    || SCHEMES.clustal

  const L = state.sequences[0].sequence.length
  const N = state.sequences.length
  const rulerH = showRuler ? tileH : 0
  const W = labelW + L * tileW + 8
  const H = rulerH + N * tileH + 8

  const parts = []
  parts.push(
    `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" `
    + `font-family="monospace" font-size="13">`,
  )
  parts.push(`<rect width="${W}" height="${H}" fill="#ffffff"/>`)

  // Ruler — numbers every 10, dots every 5
  if (showRuler) {
    for (let i = 0; i < L; i++) {
      let txt = ''
      if ((i + 1) % 10 === 0) txt = String(i + 1)
      else if ((i + 1) % 5 === 0) txt = '·'
      if (txt) {
        const x = labelW + i * tileW + tileW / 2
        parts.push(
          `<text x="${x}" y="${tileH * 0.72}" text-anchor="middle" `
          + `font-size="9" fill="#888">${txt}</text>`,
        )
      }
    }
  }

  // Sequence rows
  for (let r = 0; r < N; r++) {
    const seq = state.sequences[r]
    const y = rulerH + r * tileH

    // Name label, right-aligned in the gutter
    parts.push(
      `<text x="${labelW - 8}" y="${y + tileH * 0.7}" `
      + `text-anchor="end" font-weight="600">${xmlEscape(seq.name)}</text>`,
    )

    for (let i = 0; i < L; i++) {
      const ch = seq.sequence[i]
      const bg = scheme[ch.toUpperCase()] || '#ffffff'
      const x = labelW + i * tileW
      parts.push(`<rect x="${x}" y="${y}" width="${tileW}" height="${tileH}" fill="${bg}"/>`)
      if (ch !== '-' && ch !== '.') {
        parts.push(
          `<text x="${x + tileW / 2}" y="${y + tileH * 0.7}" `
          + `text-anchor="middle" font-weight="700">${xmlEscape(ch)}</text>`,
        )
      }
    }
  }

  parts.push('</svg>')
  return { svg: parts.join(''), width: W, height: H }
}

export async function setup(lv, root) {
  root.style.width = '100%'
  root.style.height = '100%'
  root.style.overflow = 'auto'
  root.style.background = '#ffffff'
  root.style.color = '#222'

  let lastKey = null
  let lastDims = null  // { width, height } — last rendered SVG size

  function applyState(state) {
    if (!state || !Array.isArray(state.sequences) || state.sequences.length === 0) {
      lv.fail('MSA: state must include a non-empty `sequences` array of '
        + '{name, sequence}.')
      return
    }
    const L = state.sequences[0].sequence.length
    const bad = state.sequences.find((s) => s.sequence.length !== L)
    if (bad) {
      lv.fail(`MSA: sequences must be equal length; '${bad.name}' is `
        + `${bad.sequence.length} but expected ${L}.`)
      return
    }

    const key = JSON.stringify([state.sequences, state.tile_width,
      state.tile_height, state.color_scheme, state.label_width,
      state.show_ruler])
    if (key === lastKey) return
    lastKey = key

    const { svg, width, height } = buildSvg(state)
    lastDims = { width, height }
    // The SVG itself is inline-block sized; the wrapper handles scrolling.
    root.innerHTML = svg
  }

  lv.onState((state, info) => {
    if (info && info.reason === 'emit') return
    try { applyState(state) }
    catch (e) { lv.fail('MSA: ' + ((e && e.message) || e)) }
  })

  /**
   * Custom snapshot: rasterise the SVG to a PNG at its natural size.
   * The host's default html2canvas path missed scrolled-overflow content;
   * rasterising the serialised SVG sidesteps all DOM-layout uncertainty
   * and gives the agent a pixel-perfect, full-width image.
   */
  lv.onSnapshot(async () => {
    const svgEl = root.querySelector('svg')
    if (!svgEl) return null
    const svgStr = new XMLSerializer().serializeToString(svgEl)
    const dataUrl = 'data:image/svg+xml;charset=utf-8,'
      + encodeURIComponent(svgStr)
    try {
      const img = await new Promise((resolve, reject) => {
        const i = new Image()
        i.onload = () => resolve(i)
        i.onerror = () => reject(new Error('SVG rasterise: image load failed'))
        i.src = dataUrl
      })
      const W = lastDims?.width  || img.naturalWidth  || 800
      const H = lastDims?.height || img.naturalHeight || 200
      const canvas = document.createElement('canvas')
      canvas.width = W
      canvas.height = H
      const ctx = canvas.getContext('2d')
      ctx.fillStyle = '#ffffff'
      ctx.fillRect(0, 0, W, H)
      ctx.drawImage(img, 0, 0)
      return canvas.toDataURL('image/png')
    } catch (_) {
      // Fall back to the host's html2canvas if the SVG → canvas path fails.
      return null
    }
  })
}
