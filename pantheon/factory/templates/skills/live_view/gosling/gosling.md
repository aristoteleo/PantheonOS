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
- **Computed data endpoints** — when the agent computes a small track (for
  example A/B compartments, peak summaries, loop annotations), write a tiny
  endpoint module and expose it with `serve_endpoint(name, path, config?)`.
  Gosling can then load the returned URL as `csv`, `json`, or `bed`. Use
  query/path parameters for runtime controls when a viewer/custom app makes
  requests; use `config` only for registration-time JSON constants such as fixed
  sample names or paths. Keep endpoint handlers lightweight; do the expensive
  computation before serving, then return the precomputed table.

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
- ❌ abandoning gosling to hand-roll a raw HiGlass viewer (a custom LiveView app
  that loads `hglib.min.js` from a CDN). gosling already IS HiGlass + PIXI,
  correctly wired and bundled; a raw build almost always forgets HiGlass's **PIXI**
  peer dependency and dies with `hglib is not defined` / `Cannot read properties of
  undefined (reading 'rgb2hex')`. Use the matrix spec below — not a custom app.

**Default = a genome browser: matrix + gene track.** Don't open a bare matrix.
Build a **two-track linked view** — the contact **matrix** on top, a **gene
annotation track** below — sharing one `linkingId` so they pan/zoom together.
The viewer wraps this in a genome-browser shell with a **gene / locus search
bar** (the user types `TP53` or `chr9:5,450,000-5,470,000` and the view jumps
there), so always bring the gene track along.

Matrix track: `mark:"bar"`, fields `xs`/`xe`/`ys`/`ye`, `color.field:"value"`, a
**single** tileset `url`. Put the **same `linkingId` on the matrix `x` AND `y`**
and on the gene track `x` — that keeps the matrix square when you navigate:

```jsonc
{"spec": {
  "title": "GM12878 Hi-C — Genome Browser",
  "assembly": "hg38",
  "spacing": 0,
  "views": [
    {"tracks": [{
      "data": {"url": "https://higlass.io/api/v1/tileset_info/?d=e5QaKN16SdWyIWKAidq2Kw", "type": "matrix"},
      "mark": "bar",
      "x":  {"field": "xs", "type": "genomic", "axis": "top",  "linkingId": "hic"},
      "xe": {"field": "xe", "type": "genomic"},
      "y":  {"field": "ys", "type": "genomic", "axis": "left", "linkingId": "hic"},
      "ye": {"field": "ye", "type": "genomic"},
      "color": {"field": "value", "type": "quantitative", "range": "viridis", "legend": true},
      "width": 600, "height": 600
    }]},
    {"tracks": [{
      "data": {"url": "https://server.gosling-lang.org/api/v1/tileset_info/?d=gene-annotation", "type": "beddb",
               "genomicFields": [{"index": 1, "name": "start"}, {"index": 2, "name": "end"}],
               "valueFields": [{"index": 5, "name": "strand", "type": "nominal"}, {"index": 3, "name": "name", "type": "nominal"}],
               "exonIntervalFields": [{"index": 12, "name": "start"}, {"index": 13, "name": "end"}]},
      "mark": "rect",
      "x":  {"field": "start", "type": "genomic", "linkingId": "hic"},
      "xe": {"field": "end", "type": "genomic"},
      "row":   {"field": "strand", "type": "nominal", "domain": ["+", "-"]},
      "color": {"field": "strand", "type": "nominal", "domain": ["+", "-"], "range": ["#4C9BE8", "#F28C8C"]},
      "size": {"value": 8},
      "width": 600, "height": 70
    }]}
  ]
}}
```

- **Colormap → `"viridis"`** (or omit `range`). HiGlass honours almost no other
  matrix colormap name — **colour arrays and most names are silently ignored and
  fall back to viridis**; the only other one it honours is `"warm"`, a poor
  magenta ramp. So use `"viridis"`; do **not** pass a `["white", …]` array
  (ignored) or `"warm"` (magenta). The viewer drops a rainbow/`"warm"` range
  back to viridis for you.
- **Gene annotation** = the Gosling server's `?d=gene-annotation` (**hg38**).
  Match the tileset's assembly to the matrix; for hg19/mm10 etc. point at a
  gene-annotation beddb for that assembly on a HiGlass server.
- **Search bar is automatic** — the viewer adds gene autocomplete + `zoomToGene`
  and `chrN:start-end` locus jumps; nothing to author. You can still set an
  initial `domain` on the genomic channels to open at a locus.
- **Layout is automatic** — the viewer fits every track to the same panel width
  (matrix square), and for a matrix+track genome browser it drops the matrix's
  left axis and zeroes inter-view spacing so the matrix and the gene track line
  up on one shared x-axis. It re-fits on resize, so nothing is clipped in a
  narrow side panel. `width`/`height`/`spacing` and the matrix's left axis are
  just hints — the viewer overrides them for a clean, aligned layout.

Any public HiGlass matrix tileset works as the matrix `url` — e.g. higlass.io's
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

`serve_endpoint` can expose a custom `tileset_info` / tile API later, but a
Gosling `matrix` track still requires the HiGlass tileset protocol. Use
`serve_endpoint` directly for small 1D computed tracks; use a real tileset API
for genome-scale matrices.

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

`live_view_screenshot` **works for Gosling** and returns the actual rendered
figure: the adapter captures the view through Gosling's own export canvas
(`api.getCanvas`), so you get the real image, **not a black rectangle**. (A
naive read of the live HiGlass/PixiJS WebGL canvas would come back blank —
that is handled for you.) So screenshot + `observe_images` is a real check:
confirm the matrix / tracks actually drew before reporting done.
