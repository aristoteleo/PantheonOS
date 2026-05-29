---
id: gosling_live_view
name: Designed Genomic Figures with Gosling.js
description: |
  Open and drive a Gosling.js view in the Pantheon sidebar — a grammar-based
  genomic visualisation (circular ideograms, multi-track linear, comparative
  dual-genome, sample-faceted layouts). For looking at BAM/VCF files in a
  standard browser use IGV; for designed genomic figures use Gosling.
tags: [gosling, genome, genomics, visualisation, ideogram, multi-track, circular, hi-glass, live-view]
---

# Designed Genomic Figures with Gosling.js

Gosling.js is "Vega-Lite for genomics" — a declarative grammar over a
WebGL/HiGlass renderer. You author a JSON **spec** that describes tracks,
encodings, layouts (linear / circular / faceted), and Gosling renders it.

## Gosling vs IGV — pick the right one

- **IGV** — when the task is "look at this BAM / VCF / BED in a genome
  browser at this locus". Pipeline data, pile-ups, peaks, variants. Use the
  `igv` viewer skill.
- **Gosling** — when the task is "make a designed genomic figure" —
  circular chromosome ideograms, multi-track / multi-sample layouts,
  comparative views, custom encodings. Hard to build with matplotlib; a
  single Gosling spec covers it.

These do not overlap.

## Quick demo — built in

```
open_live_view(view_type="gosling", title="Gosling Demo")
```

No `state` → opens the bundled `demo.json` (a circular hg38 cytogenetic
ideogram). Visually distinct from anything IGV / matplotlib would produce.

## The state — a Gosling spec

```jsonc
{
  "spec": { ...a valid Gosling specification... },
  "options": { ...embed options, optional... }
}
```

`spec` is the entire Gosling JSON. The grammar's shape:

```jsonc
{
  // optional metadata
  "title": "...", "subtitle": "...",
  // top-level layout: "linear" (default) or "circular"
  "layout": "linear",
  // a single root track, OR a list of tracks, OR composed views
  "tracks": [ {Track}, {Track}, ... ],
  // composition: views, arrangement, alignment
  "arrangement": "horizontal",
  "views": [{tracks: [...], ...}, ...]
}
```

A `Track` has: `data` (URL + type: csv | json | bed | beddb | multivec | ...),
`mark` (rect | line | point | text | bar | area | rule | link | triangle),
encoding channels (`x`, `xe`, `y`, `color`, `size`, `stroke`, `text`, …),
and visual properties (`width`, `height`). See
https://gosling-lang.org/docs/ for the full grammar.

## Data sources

- **Public CSV / JSON / BED over HTTP** — `data.type: "csv"` etc. with a
  `url`. Best for small annotation / cytoband / region tables.
- **Tiled HiGlass servers** (e.g. `gosling-lang.org/api/v1/...`) — for
  pre-tiled genome-scale data (multivec, beddb, vector). Gosling reuses
  HiGlass tilesets.
- **Local files** — serve them and use the URL:
  ```
  serve_local_data("peaks.bed")        # -> { url }
  open_live_view(view_type="gosling", title="...", state={"spec": {
      "tracks": [{
          "data": {"url": <that url>, "type": "bed",
                   "chromosomeField": "Chromosome",
                   "genomicFields": ["start","end"]},
          "mark": "rect",
          "x":  {"field":"start","type":"genomic"},
          "xe": {"field":"end","type":"genomic"},
          "color": {"value":"steelblue"},
          "width": 700, "height": 30
      }]
  }})
  ```

## Driving

The spec **is** the interface — change it with `live_view_set_state(view_id,
{"spec": <new spec>})`. For small edits, deep-merging an arbitrary patch
into the spec can corrupt it; always set the full new spec.

```python
# colour the cytobands by Giemsa-stain category
live_view_set_state(view_id, {"spec": <updated spec>})
```

## Authoring tips

- Start from one of the Gosling editor examples
  (https://gosling.js.org/) and adapt — the grammar has many fields.
- Linear vs circular: set `layout: "circular"` on the root view for ideogram
  / circos-style plots.
- Multi-sample: use `row` channel (categorical) for faceted tracks; or
  compose multiple `views` with `arrangement: "vertical"`.
- For comparative dual-genome views, use two views with linked or fixed
  `assembly`.

## Verify it

`live_view_get_state` is the primary check — `status: ready`, empty
`diagnostics`. Diagnostics usually mean a bad spec (unknown field), an
unreachable data URL, or a HiGlass tile that 404'd.
`live_view_screenshot` works on a best-effort basis (the canvas's WebGL
drawing buffer may be blank, in which case the host's html2canvas captures
the DOM).
