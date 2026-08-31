---
id: cytoscape
name: Biological Networks with Cytoscape.js
description: |
  Open and drive a Cytoscape.js view on the desktop — interactive
  network / pathway visualisation. Nodes + edges as JSON elements,
  built-in layouts (cose, breadthfirst, circle, dagre, ...), CSS-like
  stylesheet by selectors. Use for protein–protein interaction networks,
  signalling/metabolic pathways, gene-regulatory networks, ontologies.
tags: [cytoscape, network, pathway, graph, ppi, signalling, regulatory]
---

# Biological Networks with Cytoscape.js

Cytoscape.js is the standard JavaScript port of the Cytoscape network
visualisation toolkit. State is a list of `elements` (nodes + edges in
Cytoscape's JSON shape) plus a `layout` and an optional `style`.

## When to use

- **Protein–protein interaction** networks (STRING, BioGRID, IntAct
  exports → Cytoscape JSON).
- **Signalling / metabolic pathways** (Reactome / KEGG / WikiPathways
  → adapt to elements).
- **Gene-regulatory networks** — TF → target edges.
- **Ontology** subtrees (GO terms parent/child).
- Any graph where the user benefits from *interactive layout* (drag nodes,
  hover edges, zoom).

For *3D* molecular structures → use the `molstar` viewer. For
*genome-track* style data → `igv` / `gosling`. For a static plot of a
small network → matplotlib NetworkX is fine.

## Quick demo — built in

```
desktop_open(app="cytoscape", title="Network Demo")
```

No `state` → opens the bundled `demo.json` (the p53 DNA-damage response
pathway, 9 nodes / 9 edges, cose layout).

## The state — Cytoscape JSON

```jsonc
{
  // REQUIRED — nodes and edges as Cytoscape elements
  "elements": [
    { "data": { "id": "a", "label": "Gene A" } },
    { "data": { "id": "b", "label": "Gene B" } },
    // edge: needs `source` and `target` referencing node ids
    { "data": { "id": "e1", "source": "a", "target": "b" } }
  ],
  // optional layout — default: cose (force-directed, animation off)
  "layout": { "name": "cose", "animate": false, "padding": 30 },
  // optional stylesheet — default gives sensible blue nodes / grey arrows
  "style": [
    { "selector": "node", "style": { "label": "data(label)" } },
    { "selector": "edge", "style": { "target-arrow-shape": "triangle" } }
  ]
}
```

### Built-in layouts

| Layout         | Use case |
|----------------|---|
| `cose`         | force-directed, default; small-to-medium networks (≤ a few hundred nodes) |
| `breadthfirst` | rooted trees / hierarchies — pass `roots: ["id"]` |
| `circle`       | small networks, all nodes on a circle |
| `concentric`   | layered networks (e.g. by degree); pass `concentric: ele => ele.degree()` |
| `grid`         | predictable rectangular grid |
| `random`       | quick layout for debugging |

For more (cose-bilkent, dagre, klay, fcose), they need a separate
extension import — out of scope for the default adapter.

### Styling

Cytoscape's stylesheet is a list of `{ selector, style }`. Selectors
match nodes/edges by class, data, state:

```jsonc
[
  { "selector": "node",            "style": { "background-color": "#58a6ff" } },
  { "selector": "node[type='TF']", "style": { "shape": "diamond" } },
  { "selector": ":selected",       "style": { "background-color": "#f85149" } },
  { "selector": "edge[weight>0.8]","style": { "width": 4 } }
]
```

Common properties: `background-color`, `border-color`, `border-width`,
`shape` (ellipse | rectangle | diamond | star | triangle | ...), `width`,
`height`, `label`, `font-size`, `color`, `line-color`, `line-style`
(solid | dashed | dotted), `target-arrow-shape`, `curve-style` (bezier |
straight | taxi | unbundled-bezier).

## From data → elements

### STRING / BioGRID PPI

```python
import requests, json
r = requests.get(
    "https://string-db.org/api/json/network",
    params={"identifiers": "TP53%0dMDM2%0dATM%0dCHEK2",
            "species": "9606", "required_score": "700"},
    timeout=30,
).json()

elements = []
seen = set()
for e in r:
    a, b = e["preferredName_A"], e["preferredName_B"]
    for n in (a, b):
        if n not in seen:
            elements.append({"data": {"id": n, "label": n}})
            seen.add(n)
    elements.append({"data":
        {"id": f"{a}_{b}", "source": a, "target": b,
         "weight": float(e["score"])}})

desktop_open(app="cytoscape", title="PPI: p53 module", state={
    "elements": elements,
    "layout": {"name": "cose", "animate": False, "padding": 30},
})
```

### Pathway from a local TSV

If you have `edges.tsv` with `source\ttarget` columns:

```python
import pandas as pd
df = pd.read_csv("edges.tsv", sep="\t")
nodes = {n for col in ("source","target") for n in df[col]}
elements = (
    [{"data": {"id": n, "label": n}} for n in nodes]
    + [{"data": {"id": f"{r.source}__{r.target}",
                 "source": r.source, "target": r.target}}
       for r in df.itertuples()]
)
desktop_open(app="cytoscape", title="My pathway",
               state={"elements": elements})
```

### Reactome / KEGG

These usually export pathway XML/SBML. Pre-process to a node list +
edge list, then format as Cytoscape elements as above.

## Driving the view

Replace the whole element set with `desktop_set` (deep-merging
an array patch corrupts it):

```python
# colour a subset of nodes after a calculation
get = desktop_read(window_id)
els = [
    {**el, "data": {**el["data"], "highlight": el["data"]["id"] in hot_set}}
    for el in get["state"]["elements"]
]
desktop_set(window_id, {"elements": els,
    "style": [
        # ...same as before, plus:
        {"selector": "node[highlight]",
         "style": {"background-color": "#f85149", "color": "#fff"}}
    ]})
```

## Verify it

`desktop_read` — `status: ready`, empty `diagnostics`. A diagnostic
typically means a malformed element (edge whose `source`/`target` doesn't
match any node id) or an unknown layout name. `desktop_screenshot` uses
Cytoscape's built-in `cy.png()` exporter (clean, scaled).

---

## Desktop runtime (Atrium)

This viewer is installed as the desktop app `cytoscape` ("Cytoscape"). Everything above drives it through the `desktop` tools, which reach every window of it — including ones the USER opened. What follows is what is specific to this app.

- **Open a file**: `desktop_open(path="/path/to/file")` — `.cyjs` route here through the app's own open pipeline (format conversion, backend prepare) — no serve_local_data needed. Returns `window_id`.
- **Open by state**: `desktop_open(app="cytoscape", state={...})` with the state contract documented above.
- **Force this viewer** for a file another app also claims: `desktop_open(app="cytoscape", path=...)`. `desktop_apps()` lists every installed app with its id and file claims.
- **Drive any window** (yours or the user's): `desktop_windows()` lists them; `desktop_read(window_id)` returns the current state; `desktop_update(window_id, patch)` deep-merges; `desktop_call(window_id, action, args)` runs the same handlers the app's menus trigger. `desktop_call(w, "$close")` closes.
- **Fix in place**: when a view comes out wrong, correct THAT window (update/set/call, or `desktop_open(path=..., window_id=...)` for a different file) — do not open another window.

### Actions

- `setLayout(name: cose | circle | grid | breadthfirst | concentric)` — Re-run the graph layout
