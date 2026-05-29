---
id: encode_database_access
name: ENCODE Portal — Functional Genomics Tracks
description: |
  Query and download from ENCODE (encodeproject.org) — ChIP-seq, ATAC-seq,
  DNase-seq, RNA-seq, eCLIP and many more assays across human / mouse with
  full standardised metadata. Open access, JSON REST API; processed track
  files (BAM, bigWig, narrowPeak, …) and metadata. Pairs cleanly with the
  `igv` and `gosling` LiveViews.
tags: [encode, chip-seq, atac-seq, dnase, rna-seq, bigwig, bam, peaks, functional-genomics]
---

# ENCODE — Functional Genomics Portal

The ENCODE Project hosts standardised functional-genomics data for human
(GRCh38, hg19) and mouse (mm10, mm39). Everything is **open access**, no
authentication needed.

## When to use

- Find ChIP-seq, ATAC-seq, DNase-seq, RNA-seq, eCLIP, etc. for a
  cell type / tissue / target — then get the **processed files** (BAM
  alignments, bigWig signal, narrowPeak / bed peak calls).
- The user mentioned a TF (e.g. CTCF) + cell line (e.g. K562) — fetch
  the standard ENCODE experiments and the official peak / signal tracks.
- Cross-reference an assay across many biosamples without re-running
  a pipeline.

For raw FASTQ → use **iSeq** (GEO/SRA accession). For gene metadata →
**gget**. For *3D* genome data (Hi-C / Micro-C) → **4DN**.

## API basics

- Base: `https://www.encodeproject.org/`
- Always pass `Accept: application/json` (or `?format=json`).
- Result shape: `{ "@graph": [...], "total": N, ... }`. `@graph` is the
  list of hits.
- Pagination: `&limit=100&from=0`; `limit=all` returns everything (use
  sparingly).
- No auth, no published rate limit; be polite (≤ a few req/s for batches).

## Recipes

### 1. Find experiments by assay + target + biosample

```python
import requests
r = requests.get(
    "https://www.encodeproject.org/search/",
    params={
        "type": "Experiment",
        "assay_title": "TF ChIP-seq",
        "target.label": "CTCF",
        "biosample_ontology.term_name": "K562",
        "status": "released",
        "assembly": "GRCh38",
        "format": "json",
        "limit": "10",
    },
    headers={"Accept": "application/json"}, timeout=30,
).json()
hits = r["@graph"]
acc = [h["accession"] for h in hits]   # ENCSR... ids
```

Common `assay_title` values: `TF ChIP-seq`, `Histone ChIP-seq`, `ATAC-seq`,
`DNase-seq`, `total RNA-seq`, `polyA plus RNA-seq`, `eCLIP`, `Mint-ChIP-seq`.

### 2. From an experiment to its processed files

```python
exp = requests.get(
    f"https://www.encodeproject.org/experiments/{acc[0]}/?format=json",
    headers={"Accept": "application/json"}, timeout=30,
).json()

# all files attached to the experiment
files = exp["files"]
# narrow: official bigWig signal on GRCh38
bw = [f for f in files
      if f["file_format"] == "bigWig"
      and f.get("assembly") == "GRCh38"
      and f["output_type"] in ("signal p-value", "fold change over control")
      and f["status"] == "released"]
url = "https://www.encodeproject.org" + bw[0]["href"]
```

### 3. Direct file search (skip the experiment object)

```python
r = requests.get(
    "https://www.encodeproject.org/search/",
    params={
        "type": "File", "file_format": "bam",
        "dataset.assay_title": "TF ChIP-seq",
        "dataset.target.label": "CTCF",
        "dataset.biosample_ontology.term_name": "K562",
        "assembly": "GRCh38", "output_type": "alignments",
        "status": "released", "format": "json", "limit": "5",
    },
    headers={"Accept": "application/json"}, timeout=30,
).json()
```

### Key file fields

- `href` — relative URL, prepend `https://www.encodeproject.org`.
- `file_format` — `bam`, `bigWig`, `bed narrowPeak`, `bed broadPeak`, …
- `output_type` — `alignments`, `signal p-value`, `fold change over control`,
  `IDR thresholded peaks`, `pseudoreplicated peaks`, …
- `assembly` — `GRCh38` / `hg19` / `mm10` / `mm39`. **Always filter on
  it** — many experiments carry both GRCh38 and hg19 files.
- `status` — only `released` for production use.

### Biosample / target fields worth knowing

- `biosample_ontology.term_name` — `K562`, `GM12878`, `liver`,
  `endothelial cell of umbilical vein`, …
- `biosample_ontology.classification` — `cell line`, `tissue`, `primary
  cell`, `in vitro differentiated cells`.
- `target.label` — gene symbol (`CTCF`, `H3K27ac`).
- `award.rfa` — `ENCODE4`, `ENCODE3`, … — useful to favour the latest
  processing pipeline.

## Wire it to a viewer

### bigWig → IGV

```python
url = "https://www.encodeproject.org" + bw_file["href"]
open_live_view(view_type="igv", title="CTCF signal", state={
    "genome": "hg38",
    "locus": "MYC",
    "tracks": [{"name": "ENCODE CTCF K562 fold-change",
                "url": url, "format": "bigwig"}],
})
```

ENCODE files are served from the ENCODE CDN with CORS + range requests,
so IGV streams them directly — no `serve_local_data` needed.

### BAM (+ .bai) → IGV

```python
bam = ...   # the file dict, file_format == "bam"
# the matching index — ENCODE attaches it as a separate File with
# derived_from referring to the BAM, or you can re-query:
r = requests.get(
    "https://www.encodeproject.org/search/",
    params={"type": "File", "file_format": "bai",
            "derived_from.accession": bam["accession"], "format": "json"},
    headers={"Accept": "application/json"}, timeout=30,
).json()
bai = r["@graph"][0]
bam_url = "https://www.encodeproject.org" + bam["href"]
bai_url = "https://www.encodeproject.org" + bai["href"]
open_live_view(view_type="igv", title="BAM", state={
    "genome": "hg38", "locus": "MYC",
    "tracks": [{"name": "BAM", "url": bam_url, "indexURL": bai_url,
                "format": "bam"}],
})
```

### narrowPeak / BED → IGV or Gosling

IGV reads `bed narrowPeak` natively — pass `format: "bed"` (or `"narrowPeak"`).
For a designed multi-sample peak figure (peaks from 5 cell lines around the
genome), download the BEDs (or use the URLs) and feed them as Gosling tracks.

## Common pitfalls

- **Multiple assemblies** — without `assembly=GRCh38` you'll get GRCh38
  AND hg19 files back, doubling the result set.
- **`released` vs `archived`** — older processings are `archived`. For
  reproducibility, restrict to `released`.
- **Replicates** — files have a `biological_replicates` list. The merged
  pipeline output (replicate count > 1) is usually what you want;
  pseudo-replicate / per-replicate files are QC artefacts.
- **CORS exception** — if a particular file 403s when IGV fetches it, fall
  back to downloading it locally and `serve_local_data`.
- **Legacy assemblies** (`mm9`, `hg18`) — don't use unless reproducing an
  old paper.
