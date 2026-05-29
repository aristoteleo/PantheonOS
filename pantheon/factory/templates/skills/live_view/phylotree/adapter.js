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

export async function setup(lv, root) {
  root.style.width = '100%'
  root.style.height = '100%'
  root.style.overflow = 'auto'
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
