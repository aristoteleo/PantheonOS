---
id: phylotree_live_view
name: Phylogenetic Trees with phylotree.js
description: |
  Open and drive a phylotree.js view in the Pantheon sidebar — interactive
  phylogenetic-tree rendering from a Newick string. Linear or radial
  layouts, branch-length-scaled, with rerooting / ladderise / clade
  collapse built in.
tags: [phylotree, phylogeny, phylogenetics, newick, tree, evolution, live-view]
---

# Phylogenetic Trees with phylotree.js

phylotree.js is the D3-based phylogenetics library originally built for
HyPhy. State is a **Newick string** plus a few display options.

## When to use

- Show a phylogeny you just built (IQ-TREE, RAxML, FastTree, MrBayes,
  BEAST, ...) — interactive instead of a static `ape::plot.phylo` PNG.
- Inspect a clade — collapse / expand, reroot live.
- Pair with the `msa` viewer: tree on the left, alignment on the right
  (separate LiveViews).

For a *static* tree image inside a paper figure → matplotlib + ete3 or
ggtree gives finer control. This LiveView is for *exploring* the tree.

## Quick demo — built in

```
open_live_view(view_type="phylotree", title="Primate phylogeny")
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
open_live_view(view_type="phylotree", title="My tree",
               state={"newick": buf.getvalue().strip()})
```

### From a string already in hand

```python
open_live_view(view_type="phylotree", title="quick tree", state={
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
live_view_set_state(view_id, {"newick": consensus_newick,
                              "layout": "radial"})

# flip to a linear layout for publication
live_view_set_state(view_id, {**current, "layout": "linear"})
```

## Verify it

`live_view_get_state` — `status: ready`, empty `diagnostics`. The most
common failure is a malformed Newick (unmatched parens, missing semicolon)
— the adapter reports it via `lv.fail`. `live_view_screenshot` uses
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
  the LiveView SDK directly.
