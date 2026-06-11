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

## Hi-C / contact matrices

Gosling renders Hi-C contact matrices with the **`matrix` data type** — but the
data must be a **HiGlass-tiled cooler tileset** (a `tileset_info` URL from a
HiGlass server). A raw JSON / numpy contact matrix **cannot** be loaded. Two
traps make this fail (both observed in practice):

- ❌ a hand-rolled matrix track — `mark:"rect"`, fields `position1`/`position2`,
  `color.field:"count"`, or a split `data:{url, value}` → runtime crash
  (`Cannot read properties of undefined (reading 'includes')`). Use the **exact**
  spec below.
- ❌ giving up and rendering the matrix as a full-resolution Plotly / matplotlib
  heatmap → **not scalable**, the UI lags badly. Don't.

**Correct matrix track** — `mark:"bar"`, fields `xs`/`xe`/`ys`/`ye`,
`color.field:"value"`, a **single** tileset `url`:

```jsonc
{"spec": {"title": "Hi-C Matrix", "tracks": [{
  "data": {"url": "https://server.gosling-lang.org/api/v1/tileset_info/?d=leung2015-hg38",
           "type": "matrix"},
  "mark": "bar",
  "x":  {"field": "xs", "type": "genomic", "axis": "top"},
  "xe": {"field": "xe", "type": "genomic"},
  "y":  {"field": "ys", "type": "genomic", "axis": "left"},
  "ye": {"field": "ye", "type": "genomic"},
  "color": {"field": "value", "type": "quantitative", "range": "warm", "legend": true},
  "width": 600, "height": 600
}]}}
```

Any public HiGlass matrix tileset works as the `url` — e.g. higlass.io's
`https://higlass.io/api/v1/tileset_info/?d=<tileset-uid>`.

**Local Hi-C data** (your own `.cool` / contact matrix): Gosling needs a tiled
tileset, so make one and serve it — do **not** plot a dense matrix:

```bash
cooler zoomify matrix.cool        # -> matrix.mcool (multi-resolution, required)
```

Then serve `matrix.mcool` from a local HiGlass server (`higlass-server`, or the
`higlass` Python package which embeds one) and point the track's `data.url` at
that server's local `tileset_info` URL. If a HiGlass server is genuinely out of
scope, a **coarse downsampled** matrix (≤ ~500 bins) as a static heatmap is OK
for a quick look — but never a full-resolution one.

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
