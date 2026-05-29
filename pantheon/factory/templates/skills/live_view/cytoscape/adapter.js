/**
 * Cytoscape.js LiveView adapter — biological networks & pathways.
 *
 * Cytoscape.js is the standard JavaScript library for interactive network
 * visualisation: nodes + edges as JSON elements, built-in layout algorithms
 * (cose, breadthfirst, circle, dagre, ...), CSS-like stylesheet by selectors.
 *
 * LiveView "state":
 *   {
 *     "elements": [
 *       { "data": { "id": "geneA", "label": "GeneA" } },
 *       { "data": { "id": "geneB", "label": "GeneB" } },
 *       { "data": { "id": "e1", "source": "geneA", "target": "geneB" } }
 *     ],
 *     "layout":  { "name": "cose", "animate": false },   // optional
 *     "style":   [ ... cytoscape stylesheet array ... ]  // optional
 *   }
 */
import cytoscape from 'https://esm.sh/cytoscape@3.30.0'

// A neutral default stylesheet — readable on a light background, gives the
// agent a working view even with no `style` set.
const DEFAULT_STYLE = [
  { selector: 'node', style: {
      label: 'data(label)',
      'background-color': '#58a6ff',
      color: '#222',
      'font-size': '11px',
      'text-valign': 'center',
      'text-halign': 'center',
      width: 36, height: 36,
  } },
  { selector: 'edge', style: {
      'line-color': '#aaa',
      width: 1.5,
      'curve-style': 'bezier',
      'target-arrow-shape': 'triangle',
      'target-arrow-color': '#aaa',
  } },
]

export async function setup(lv, root) {
  root.style.width = '100%'
  root.style.height = '100%'
  root.style.background = '#ffffff'  // Cytoscape is designed for light bg
  root.style.color = '#222'

  let cy = null
  let lastKey = null

  function applyState(state) {
    if (!state || !Array.isArray(state.elements)) {
      lv.fail('Cytoscape: state must include an `elements` array '
        + '(nodes + edges in Cytoscape JSON).')
      return
    }
    const key = JSON.stringify([state.elements, state.layout, state.style])
    if (cy && key === lastKey) return
    lastKey = key

    // Tear down previous instance — Cytoscape doesn't auto-rebuild on
    // wholesale element changes; cleaner to destroy + recreate.
    if (cy) { try { cy.destroy() } catch (_) {} cy = null }
    root.innerHTML = ''
    // Note: `wheelSensitivity` would be nice (slower wheel-zoom) but
    // Cytoscape now console.warns on it as a deprecated/non-standard
    // option, which feeds the LiveView diagnostics channel as noise.
    cy = cytoscape({
      container: root,
      elements: state.elements,
      layout: state.layout || { name: 'cose', animate: false },
      style: state.style || DEFAULT_STYLE,
    })
  }

  lv.onState((state, info) => {
    if (info && info.reason === 'emit') return
    try { applyState(state) }
    catch (e) { lv.fail('Cytoscape: ' + ((e && e.message) || e)) }
  })

  // Cytoscape has its own PNG exporter — it understands its canvas and
  // scales cleanly, better than html2canvas.
  lv.onSnapshot(() => {
    if (!cy) return null
    try {
      return cy.png({ output: 'base64uri', scale: 1.5, full: true, bg: '#ffffff' })
    } catch (_) { return null }
  })
}
