---
id: igv
name: View Genome Tracks with IGV.js
description: |
  Open and drive an interactive IGV.js genome browser on the desktop — view BAM/CRAM alignments, VCF variants,
  BED/GFF annotations, bigWig coverage, on a reference genome (hg38, mm10,
  or a custom FASTA).
tags: [igv, genome, genomics, bam, vcf, bed, bigwig, gff, tracks]
---

# Viewing Genome Tracks with IGV.js

IGV.js is the standard embeddable web genome browser. Open it as a desktop window
to put genomic data — alignments, variants, peaks, annotations — on a
reference genome where the user can pan and zoom.

## When to use

- Any task that ends with **"look at this region in a genome browser"** —
  RNA-seq pileups, ChIP/ATAC peaks, variant calling, CRISPR-screen hits,
  splice junctions, copy-number, regulatory annotations.
- BAM / CRAM / VCF / BED / GFF / bigWig / wig data on a reference genome.
- A specific gene or coordinate range to display.

## Quick demo — built in

```
desktop_open(app="igv", title="Genome Browser")
```

No `state` → opens the bundled `demo.json` (hg38 at the MYC region with the
default Gencode annotation track).

## The state — what IGV shows

```jsonc
{
  // REQUIRED — a built-in genome id or a custom genome object.
  "genome": "hg38",
  // Built-in ids include: hg38, hg19, mm10, mm39, GRCh38, panTro6, dm6, ce11,
  // sacCer3, danRer11, … (IGV ships a registry).
  // Custom: { "id": "myGenome", "fastaURL": "...", "indexURL": "...",
  //          "tracks": [{annotation track for gene-symbol search}] }

  "locus": "chr8:127,736,588-127,739,371",
  // OR a gene symbol — works on hg38 because the bundled Gencode track is
  // `searchable: true`. For a custom genome with no searchable annotation
  // track, use coordinates.

  "tracks": [
    {
      "name": "HG00103 alignments",
      "url": "https://.../sample.bam",
      "indexURL": "https://.../sample.bam.bai",
      "format": "bam"
    },
    {
      "name": "Variants",
      "url": "https://.../variants.vcf.gz",
      "indexURL": "https://.../variants.vcf.gz.tbi",
      "format": "vcf"
    }
  ]
}
```

Common `format` values: `bam`, `cram`, `vcf`, `bed`, `gff3` / `gtf`,
`bigwig` / `bw`, `wig`. Indexed binary formats (BAM, CRAM, indexed VCF /
BED) **need an `indexURL`** — fetching just the URL without an index won't
work (IGV requests byte ranges via the index).

## Cloud vs local data

- **Cloud / public** — pass the URL directly (1000 Genomes / ENCODE / UCSC
  / your S3, etc.). The remote server must support HTTP **range requests**
  and CORS — most public bioinformatics endpoints do.
- **Local** — serve the file + its index with `serve_local_data`:
  ```
  serve_local_data("/path/to/sample.bam")     -> { url: bam_url }
  serve_local_data("/path/to/sample.bam.bai") -> { url: bai_url }
  desktop_open(app="igv", title="...", state={
    "genome": "hg38",
    "locus": "BRCA1",
    "tracks": [{"name":"...", "url": bam_url, "indexURL": bai_url, "format":"bam"}]
  })
  ```
  Our data server supports range requests, so indexed BAM/CRAM/VCF work.

## Driving the view

The adapter is smart about updates:
- Same genome + tracks, only `locus` changed → IGV navigates in place
  (`browser.search`), no rebuild, no flicker.
- `genome` or `tracks` changed → IGV is rebuilt with the new config.

```python
# jump to a region
desktop_update(window_id, {"locus": "chr17:43,044,295-43,125,483"})  # BRCA1

# or, on hg38 (Gencode is searchable):
desktop_update(window_id, {"locus": "BRCA1"})

# add a track — pass the FULL new tracks array (deep-merging a list patch
# would corrupt it):
get_state = desktop_read(window_id)
new_tracks = [*get_state["state"]["tracks"], {
    "name": "ChIP peaks", "url": ..., "format": "bed"
}]
desktop_update(window_id, {"tracks": new_tracks})
```

## Verify it

`desktop_read` is the reliable check — `status: ready` and empty
`diagnostics` (a diagnostic typically means an unreachable URL or a missing
index). `desktop_screenshot` works on a best-effort basis via html2canvas
(IGV uses 2D canvases, so capture is usually fine).

---

## Desktop runtime (Atrium)

This viewer is installed as the desktop app `igv` ("IGV"). Everything above drives it through the `desktop` tools, which reach every window of it — including ones the USER opened. What follows is what is specific to this app.

- **Open by state**: `desktop_open(app="igv", state={...})` with the state contract documented above.
- **Force this viewer** for a file another app also claims: `desktop_open(app="igv", path=...)`. `desktop_apps()` lists every installed app with its id and file claims.
- **Drive any window** (yours or the user's): `desktop_windows()` lists them; `desktop_read(window_id)` returns the current state; `desktop_update(window_id, patch)` deep-merges; `desktop_call(window_id, action, args)` runs the same handlers the app's menus trigger. `desktop_call(w, "$close")` closes.
- **Fix in place**: when a view comes out wrong, correct THAT window (update/set/call, or `desktop_open(path=..., window_id=...)` for a different file) — do not open another window.
