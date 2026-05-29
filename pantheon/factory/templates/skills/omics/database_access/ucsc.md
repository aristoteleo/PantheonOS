---
id: ucsc_database_access
name: UCSC Genome Browser API & liftOver
description: |
  Programmatic access to UCSC Genome Browser data (api.genome.ucsc.edu) —
  assembly catalog, track data, sequence retrieval, gene-symbol search,
  Track Hub discovery; plus the canonical liftOver coordinate-conversion
  utility (UCSC `liftOver` CLI, `pyliftover`, `CrossMap`). Use UCSC where
  Ensembl / NCBI fall short: cross-assembly liftOver, public Track Hubs,
  large genome catalog (hundreds of assemblies Ensembl doesn't host).
tags: [ucsc, liftover, crossmap, track-hub, genome-browser, assembly, sequence]
---

# UCSC Genome Browser — Programmatic Access

UCSC's REST API and command-line utilities fill the gaps the Ensembl /
NCBI APIs don't cover. The big three: **liftOver** (assembly conversion),
**Track Hubs** (data discovery), and the **genome catalog** (assemblies
Ensembl doesn't ship).

## When to use

- **liftOver** a region or BED / VCF file between assemblies (hg19 →
  GRCh38, GRCh38 → CHM13, mm9 → mm10, …).
- **Find a Track Hub** for a non-model organism or specialised dataset
  (UCSC indexes thousands of public hubs).
- **Pull a sequence or annotation at a coordinate** without writing
  pysam / pyfasta boilerplate.
- **List UCSC's full assembly catalog** — handy when you need a less
  common reference (`panTro6`, `calJac4`, …).
- **Get a UCSC track at a region** — RefSeq / GENCODE / repeats / cytobands.

For gene metadata → **gget** is friendlier. For ENCODE / 4DN files →
the dedicated portals.

## REST API basics

- Base: `https://api.genome.ucsc.edu/`
- All endpoints return JSON. No auth.
- Reference: <https://genome.ucsc.edu/goldenPath/help/api.html>.

### Endpoints

| Endpoint | Use |
|---|---|
| `/list/ucscGenomes` | All UCSC-hosted assemblies |
| `/list/publicHubs` | Public Track Hubs UCSC indexes |
| `/list/hubGenomes?hubUrl=...` | Assemblies inside a Track Hub |
| `/list/tracks?genome=hg38` | Tracks on a given assembly |
| `/list/chromosomes?genome=hg38` | Chrom sizes |
| `/getData/track?genome=hg38;track=refGene;chrom=chr8;start=127700000;end=127800000` | Track features in a window |
| `/getData/sequence?genome=hg38;chrom=chr8;start=127700000;end=127700100` | Sequence (BED-style 0-based, half-open) |
| `/search?genome=hg38;search=BRCA1` | Coordinate from a gene symbol or rs id |

Note: UCSC's `/getData/` and `/search` endpoints accept **`;`** as the
separator (per the docs). `&` works for `/list/...`.

### Sequence pull

```python
import requests
r = requests.get(
    "https://api.genome.ucsc.edu/getData/sequence",
    params={"genome": "hg38", "chrom": "chr8",
            "start": 127700000, "end": 127700100},
    timeout=30,
).json()
print(r["dna"])         # 100 nt
```

### Track features in a window

```python
r = requests.get(
    "https://api.genome.ucsc.edu/getData/track",
    params={"genome": "hg38", "track": "knownGene",
            "chrom": "chr8", "start": 127700000, "end": 127800000},
    timeout=30,
).json()
# r["knownGene"] is the list of features
```

## liftOver — coordinate conversion

The REST API does **not** expose liftOver. Pick one of:

### A. UCSC `liftOver` CLI (best for bulk BED / VCF)

```bash
# fetch the chain file (hg19 -> GRCh38)
curl -O https://hgdownload.soe.ucsc.edu/goldenPath/hg19/liftOver/hg19ToHg38.over.chain.gz
# convert a BED
liftOver input.hg19.bed hg19ToHg38.over.chain.gz output.hg38.bed unmapped.bed
```

Install with conda: `conda install -c bioconda ucsc-liftover`.

### B. `pyliftover` (single coordinates / small BEDs, pure Python)

```python
from pyliftover import LiftOver
lo = LiftOver("hg19", "hg38")         # auto-downloads the chain file
new = lo.convert_coordinate("chr1", 1000000)
# -> [("chr1", 1064620, "+", ...)] or [] if unmappable
```

`pip install pyliftover`. Best for one-off coordinate flips.

### C. `CrossMap` (BED / VCF / BAM, Python wrapper)

```bash
pip install CrossMap
CrossMap bed hg19ToHg38.over.chain.gz input.hg19.bed output.hg38.bed
CrossMap vcf hg19ToHg38.over.chain.gz input.hg19.vcf hg19.fa.gz output.hg38.vcf
```

## Track Hubs

A Track Hub is a static `hub.txt` URL hosting BAM / bigWig / bigBed
tracks on the open web. UCSC indexes thousands.

```python
hubs = requests.get(
    "https://api.genome.ucsc.edu/list/publicHubs", timeout=30,
).json()["publicHubs"]
# filter by shortLabel / longLabel / dbList
```

IGV.js can consume a Track Hub indirectly: parse the hub's `genomes.txt`
+ `trackDb.txt` to extract child file URLs, then pass them as IGV tracks.

## Wire it to a viewer

### Sequence + coordinate → context for the user

```python
# find a gene's coordinates, then open IGV there
r = requests.get(
    "https://api.genome.ucsc.edu/search",
    params={"genome": "hg38", "search": "BRCA1"}, timeout=30,
).json()
hit = r["matches"][0]
locus = f"{hit['chrom']}:{hit['chromStart']}-{hit['chromEnd']}"
open_live_view(view_type="igv", title="BRCA1", state={
    "genome": "hg38", "locus": locus, "tracks": []})
```

### Track-hub bigWig → IGV

```python
open_live_view(view_type="igv", title="Track-hub bw", state={
    "genome": "hg38",
    "tracks": [{"name": "Hub bigWig", "format": "bigwig",
                "url": "https://hgdownload.soe.ucsc.edu/.../signal.bw"}],
})
```

### liftOver a BED then visualise in IGV

```python
# lift a hg19 BED of peaks to hg38, serve it, view it
import subprocess
subprocess.run(["liftOver", "peaks.hg19.bed", "hg19ToHg38.over.chain.gz",
                "peaks.hg38.bed", "unmapped.bed"], check=True)
url = serve_local_data("peaks.hg38.bed")["url"]
open_live_view(view_type="igv", title="Lifted peaks", state={
    "genome": "hg38", "locus": "BRCA1",
    "tracks": [{"name": "peaks (lifted)", "url": url, "format": "bed"}],
})
```

## Common pitfalls

- **0-based, half-open coordinates** in `/getData/sequence` and
  `/getData/track`. IGV displays 1-based, fully-closed — convert when
  reporting to the user.
- **Chain-file direction** — `hg19ToHg38.over.chain.gz` is one-way; need
  the reverse-named file for the reverse mapping.
- **`hg38` (UCSC) vs `GRCh38` (Ensembl / ENCODE / 4DN)** — same sequence,
  different chromosome naming: `chr1` (UCSC) vs `1` (Ensembl). To mix
  UCSC + Ensembl data, also rename chromosomes (`sed 's/^chr//'` or
  `bcftools annotate --rename-chrs`).
- **API rate** — UCSC's docs suggest ≤ a couple of requests / second.
  No hard limit but be polite for batches.
- **Semicolons in queries** — `/getData/` and `/search` are documented
  with `;` as the param separator; prefer it.
