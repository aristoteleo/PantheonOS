---
id: fourdn_database_access
name: 4DN Nucleome Portal — 3D Genome Data
description: |
  Query and download from the 4D Nucleome Data Portal (data.4dnucleome.org)
  — Hi-C, Micro-C, single-cell Hi-C, ChIA-PET, SPRITE, GAM, FISH and other
  3D-genome assays. Returns processed contact matrices (`.hic` / `.mcool`),
  pairs files (`.pairs.gz`), and metadata. Pairs with the `gosling`
  LiveView (HiGlass back-end natively reads `.mcool` / `.hic`) and with the
  `cooler` Python library.
tags: [4dn, nucleome, hi-c, micro-c, mcool, hic, contact-matrix, 3d-genome, chia-pet, sprite]
---

# 4DN Nucleome — 3D Genome Data Portal

The 4D Nucleome (4DN) Data Portal hosts data from the NIH 4DN consortium —
Hi-C variants (in-situ, intact, single-cell), Micro-C, ChIA-PET, SPRITE,
GAM, FISH imaging. Most data is **open access**; some image stacks require
sign-in.

## When to use

- Need a 3D-genome contact map for a cell type — find Hi-C / Micro-C
  experiments and pull the `.mcool` (multi-resolution cooler) file.
- Comparative 3D-genome work across cell lines / conditions.
- ChIA-PET (loop) data for a TF.
- "Show me a Hi-C heatmap at locus X" → 4DN `.mcool` → Gosling (HiGlass
  back-end) or `cooler` for analysis.

For functional-genomics 1D tracks (ChIP/ATAC/RNA-seq) → **ENCODE**. For
raw FASTQ → **iSeq**. For cancer genomics → **GDC**.

## API basics

- Base: `https://data.4dnucleome.org/`
- `Accept: application/json` (or `?format=json`).
- Result shape: `{ "@graph": [...], "total": N, ... }`.
- `/search/?type=...` is the workhorse.
- No auth for open data; sign-in tokens needed only for restricted ones
  (status `released to project`).

## Recipes

### 1. Find Hi-C experiment sets for a cell line

```python
import requests
r = requests.get(
    "https://data.4dnucleome.org/search/",
    params={
        "type": "ExperimentSetReplicate",
        "experiments_in_set.experiment_type.display_title": "in situ Hi-C",
        "experiments_in_set.biosample.biosource_summary": "GM12878",
        "experimentset_type": "replicate",
        "format": "json",
        "limit": "10",
    },
    headers={"Accept": "application/json"}, timeout=30,
).json()
sets = r["@graph"]
acc = [s["accession"] for s in sets]      # 4DNES... ids
```

Common `experiment_type.display_title`: `in situ Hi-C`, `Dilution Hi-C`,
`Micro-C`, `Hi-C 3.0`, `single cell Hi-C`, `single cell sci-Hi-C`,
`ChIA-PET`, `PLAC-seq`, `SPRITE`, `GAM`, `Fluorescence in situ
hybridization`.

### 2. From an ExperimentSet to its processed files

```python
es = requests.get(
    f"https://data.4dnucleome.org/experiment-set-replicates/{acc[0]}/?format=json",
    headers={"Accept": "application/json"}, timeout=30,
).json()
# `processed_files` is the canonical "use these" list
pf = es.get("processed_files", [])
# .mcool and .hic are what you want for visualisation
mcool = [f for f in pf if f["file_format"]["display_title"] == "mcool"]
hic_url = "https://data.4dnucleome.org" + mcool[0]["href"]
```

If `processed_files` is empty at the set level, try per-experiment:
`es["experiments_in_set"][i]["processed_files"]`.

### 3. Direct file search

```python
r = requests.get(
    "https://data.4dnucleome.org/search/",
    params={
        "type": "FileProcessed",
        "file_format.display_title": "mcool",
        "experiment_type.display_title": "Micro-C",
        "biosource_name": "H1-hESC",
        "status": "released",
        "format": "json", "limit": "10",
    },
    headers={"Accept": "application/json"}, timeout=30,
).json()
```

### Key file fields

- `href` — relative path. Prepend `https://data.4dnucleome.org`.
- `file_format.display_title` — `mcool`, `hic`, `pairs`, `bam`,
  `bigwig`, `bed`, …
- `genome_assembly` — `GRCh38`, `GRCm38`, …
- `status` — only `released` (open public). `released to project` is
  consortium-internal.
- `higlass_uid` — present on tile-indexed `.mcool` / `.bw` / `.beddb`
  files; the key for HiGlass/Gosling tile fetching.

### File-format guide

- **`.mcool`** — multi-resolution Cooler, the canonical Hi-C output;
  HiGlass and `cooler` read it directly.
- **`.hic`** — Juicer/Juicebox format; convert with `hic2cool` if you
  need cooler.
- **`.pairs.gz`** — short-read pair lists; for re-binning or custom
  analysis (`cooler cload`, `pairix`).
- **`.bw` / `.bedpe`** — derived 1D / loop annotation tracks.

## Wire it to a viewer

### `.mcool` → Gosling (HiGlass tile server)

Gosling.js uses the HiGlass tile protocol; 4DN serves Cooler tiles
through `https://higlass.4dnucleome.org/api/v1/`. The `higlass_uid` on
the file is the tile ID.

```python
file = {...}     # the mcool FileProcessed dict
hg_uid = file["higlass_uid"]
spec = {
  "title": "Micro-C contact map",
  "tracks": [{
    "data": {"type": "matrix",
             "url": f"https://higlass.4dnucleome.org/api/v1/tileset_info/?d={hg_uid}",
             "server": "https://higlass.4dnucleome.org/api/v1"},
    "mark": "rect",
    "x":  {"field": "xs", "type": "genomic", "axis": "top"},
    "xe": {"field": "xe", "type": "genomic"},
    "y":  {"field": "ys", "type": "genomic", "axis": "left"},
    "ye": {"field": "ye", "type": "genomic"},
    "color": {"field": "value", "type": "quantitative",
              "range": ["white", "red"]},
    "width": 700, "height": 700,
  }]
}
open_live_view(view_type="gosling", title="Hi-C", state={"spec": spec})
```

Gosling matrix specs are the trickiest part of the grammar — verify
against an example in https://gosling.js.org before assuming this exact
shape works for your file.

### `.mcool` → `cooler` (analysis, no viewer)

```python
import cooler
c = cooler.Cooler("/path/to/file.mcool::resolutions/10000")
mat = c.matrix(balance=True).fetch("chr1:1,000,000-2,000,000")
```

`cooler ls file.mcool` first to see available resolutions.

### `.pairs.gz` → `pairix`

```python
import pairix
p = pairix.open("file.pairs.gz")
for r in p.querys2D("chr1", 0, 1_000_000, "chr1", 0, 1_000_000):
    ...
```

### Download → `serve_local_data` for non-HiGlass cases

```python
import urllib.request
urllib.request.urlretrieve(hic_url, "matrix.mcool")
url = serve_local_data("matrix.mcool")["url"]
# pass the URL to a custom viewer / tool that reads cooler over HTTP range
```

## Common pitfalls

- **`released` vs `released to project`** — open-released data is fully
  public; "released to project" is consortium-internal and 401s without
  a token.
- **No `higlass_uid`** — older / non-tile-indexed files don't have one.
  Download the `.mcool` and use `cooler` instead of HiGlass.
- **Mixed assemblies** — 4DN files exist for GRCh38, hg38, GRCm38; check
  `genome_assembly` before pairing with other tracks.
- **ExperimentSet vs Experiment** — the "set" rolls up replicates; use it
  unless you specifically need per-replicate files.
- **Matrix-track Gosling specs** — fragile; if the rendering looks wrong,
  start from a Gosling editor matrix example and substitute the tileset
  URL.
