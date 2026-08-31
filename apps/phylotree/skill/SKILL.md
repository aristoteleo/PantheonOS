---
id: phylotree
name: Phylogenetic Trees with phylotree.js
description: |
  Open and drive a phylotree.js view on the desktop — interactive
  phylogenetic-tree rendering from a Newick string. Linear or radial
  layouts, branch-length-scaled, with rerooting / ladderise / clade
  collapse built in.
tags: [phylotree, phylogeny, phylogenetics, newick, tree, evolution]
---

# Phylogenetic Trees with phylotree.js

phylotree.js is the D3-based phylogenetics library originally built for
HyPhy. State is a **Newick string** plus a few display options.

## When to use

- Show a phylogeny you just built (IQ-TREE, RAxML, FastTree, MrBayes,
  BEAST, ...) — interactive instead of a static `ape::plot.phylo` PNG.
- Inspect a clade — collapse / expand, reroot live.
- Pair with the `msa` viewer: tree on the left, alignment on the right
  (separate windows).

For a *static* tree image inside a paper figure → matplotlib + ete3 or
ggtree gives finer control. This app is for *exploring* the tree.

## Quick demo — built in

```
desktop_open(app="phylotree", title="Primate phylogeny")
```

No `state` → opens the bundled `demo.json` (a small primate phylogeny,
radial layout).

## The state — Newick + display options

```jsonc
{
  // REQUIRED — a valid Newick (or HyPhy-extended Newick with {category}
  // suffixes on labels)
  "newick": "(((A:0.1,B:0.2):0.1,C:0.4):0.05,D:0.5);",

  // optional display knobs
  "layout":       "linear",   // linear | radial
  "show_labels":  true,
  "show_scale":   true,
  "align_tips":   true,
  "width":        700,        // SVG width  px
  "height":       null        // SVG height px; defaults to ~ #tips * 18
}
```

## Where the Newick comes from

### From a Bio.Phylo Tree

```python
from Bio import Phylo
import io
t = Phylo.read("tree.nwk", "newick")
buf = io.StringIO(); Phylo.write(t, buf, "newick")
desktop_open(app="phylotree", title="My tree",
               state={"newick": buf.getvalue().strip()})
```

### From a string already in hand

```python
desktop_open(app="phylotree", title="quick tree", state={
    "newick": "(Human:0.05,(Chimp:0.02,Gorilla:0.025):0.01);",
    "layout": "linear",
})
```

### Convert from a different format

If you have a Nexus or PhyloXML file, convert with Bio.Phylo or
ete3:

```python
from Bio import Phylo
trees = list(Phylo.parse("trees.nexus", "nexus"))
buf = io.StringIO(); Phylo.write(trees[0], buf, "newick")
nwk = buf.getvalue().strip()
```

## Driving the view

Newick is the whole story — replace it to swap trees:

```python
# show the bootstrapped consensus instead of the ML tree
desktop_set(window_id, {"newick": consensus_newick,
                              "layout": "radial"})

# flip to a linear layout for publication
desktop_set(window_id, {**current, "layout": "linear"})
```

## Verify it

`desktop_read` — `status: ready`, empty `diagnostics`. The most
common failure is a malformed Newick (unmatched parens, missing semicolon)
— the adapter reports it via `lv.fail`. `desktop_screenshot` uses
html2canvas to capture the SVG.

## Tips

- For trees with **bootstrap / support values** on internal nodes, the
  standard Newick form is `(...)support_value:branch_length` — phylotree
  handles this directly.
- **Very large trees** (> 1000 tips): consider linear layout with a tall
  height (e.g. `tip_count * 14` px) so labels don't collide. Radial layout
  becomes unreadable past a few hundred tips.
- Tip **colors** require post-render DOM manipulation (out of scope for
  the simple state above); for that, look at building a custom view with
  the Atrium app SDK directly.

---

## Desktop runtime (Atrium)

This viewer is installed as the desktop app `phylotree` ("PhyloTree"). Everything above drives it through the `desktop` tools, which reach every window of it — including ones the USER opened. What follows is what is specific to this app.

- **Open a file**: `desktop_open(path="/path/to/file")` — `.nwk`, `.newick`, `.tree` route here through the app's own open pipeline (format conversion, backend prepare) — no serve_local_data needed. Returns `window_id`.
- **Open by state**: `desktop_open(app="phylotree", state={...})` with the state contract documented above.
- **Force this viewer** for a file another app also claims: `desktop_open(app="phylotree", path=...)`. `desktop_apps()` lists every installed app with its id and file claims.
- **Drive any window** (yours or the user's): `desktop_windows()` lists them; `desktop_read(window_id)` returns the current state; `desktop_update(window_id, patch)` deep-merges; `desktop_call(window_id, action, args)` runs the same handlers the app's menus trigger. `desktop_call(w, "$close")` closes.
- **Fix in place**: when a view comes out wrong, correct THAT window (update/set/call, or `desktop_open(path=..., window_id=...)` for a different file) — do not open another window.

### Actions

- `setLayout(layout: linear | radial)` — Tree layout
- `toggleLabels()` — Show/hide tip labels
- `toggleAlign()` — Align tips at the same depth
- `toggleScale()` — Show/hide the scale bar
