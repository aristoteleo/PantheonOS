---
id: gdc_database_access
name: GDC / TCGA — Cancer Genomics Portal
description: |
  Query and download from the NCI Genomic Data Commons (api.gdc.cancer.gov)
  — TCGA, CPTAC, TARGET, FM-AD and other large cancer cohorts. Supports
  case / file / project metadata queries, open-tier file downloads (RNA-seq
  counts, MAF mutations, CNV segments, methylation, …), and controlled-tier
  downloads with a dbGaP / NCI token. Complements `gget cbio` (processed
  cBioPortal view) and `iSeq` (raw FASTQ for non-cancer cohorts).
tags: [gdc, tcga, cancer, mutations, maf, rna-seq, methylation, cnv, cptac, target]
---

# GDC — NCI Genomic Data Commons (TCGA + friends)

The GDC is NCI's central cancer-genomics portal: TCGA (33 cancer types),
TARGET (paediatric), CPTAC (proteogenomics), CGCI, FM-AD, etc. Two-tier
access:

- **Open** — anyone can download. Processed RNA-seq counts, MAF mutation
  files, CNV segments, methylation beta values, clinical metadata.
- **Controlled** — needs a dbGaP-approved token: raw / aligned BAMs,
  germline VCFs, anything re-identifiable.

For *processed* cancer mutations + plots, **gget's cBioPortal module** is
easier. For *anything else from GDC (raw RNA-seq counts, MAF, clinical,
CNV, methylation, controlled BAMs)*, use this skill.

## When to use

- Pull TCGA RNA-seq counts for a cohort to run differential expression.
- Get the MAF mutation file for an entire TCGA project.
- Cross-reference a gene's expression with copy-number / methylation in a
  TCGA cohort.
- Download an aligned BAM **if** you have a controlled-access token.

## API basics

- Base: `https://api.gdc.cancer.gov/`
- GET requests with `?filters=<URL-encoded JSON>` for non-trivial
  queries — or POST a JSON body for big filters.
- Result shape: `{"data": {"hits": [...], "pagination": {...}}, "warnings": {}}`.
- Pagination: `&size=100&from=0`.
- Open data: no auth. Controlled data: pass a token from
  <https://portal.gdc.cancer.gov/> (User → Download Token) as the
  `X-Auth-Token` header.
- All modern GDC data is on **GRCh38**.

### Endpoints

| Endpoint | Use |
|---|---|
| `/projects` | Project list — TCGA-BRCA, TCGA-LUAD, … |
| `/cases` | Patient cases |
| `/files` | The data files — filter by `data_type`, `format`, `access` |
| `/data/<file_id>` | Download a file |
| `/data` (POST `{"ids": [...]}`) | Bulk download — returns tar.gz |
| `/annotations` | Annotation / clinical fields |
| `/_mapping` (on `/files`, `/cases` etc.) | What fields are filterable |

## Recipes

### 1. List TCGA projects

```python
import requests, json
r = requests.get(
    "https://api.gdc.cancer.gov/projects",
    params={
        "filters": json.dumps({"op":"in",
            "content":{"field":"program.name", "value":["TCGA"]}}),
        "fields": "project_id,name,primary_site,disease_type",
        "size": "100", "format": "JSON"},
    timeout=30,
).json()
projects = r["data"]["hits"]
```

### 2. Find open RNA-seq count files for TCGA-BRCA

```python
filters = {
    "op": "and",
    "content": [
        {"op": "in", "content": {"field": "cases.project.project_id",
                                 "value": ["TCGA-BRCA"]}},
        {"op": "in", "content": {"field": "data_category",
                                 "value": ["Transcriptome Profiling"]}},
        {"op": "in", "content": {"field": "data_type",
                                 "value": ["Gene Expression Quantification"]}},
        {"op": "in", "content": {"field": "analysis.workflow_type",
                                 "value": ["STAR - Counts"]}},
        {"op": "in", "content": {"field": "access", "value": ["open"]}},
    ],
}
r = requests.get(
    "https://api.gdc.cancer.gov/files",
    params={"filters": json.dumps(filters),
            "fields": ("file_id,file_name,"
                       "cases.submitter_id,cases.samples.sample_type"),
            "size": "1200", "format": "JSON"},
    timeout=60,
).json()
files = r["data"]["hits"]
ids = [f["file_id"] for f in files]
```

### 3. Download

```python
# single file (open access — no token needed)
r = requests.get(f"https://api.gdc.cancer.gov/data/{ids[0]}", timeout=120)
with open(f"{ids[0]}.tsv.gz", "wb") as fp:
    fp.write(r.content)

# bulk download as one .tar.gz
r = requests.post(
    "https://api.gdc.cancer.gov/data",
    json={"ids": ids[:1000]},          # don't exceed a few thousand per POST
    headers={"Content-Type": "application/json"},
    timeout=600, stream=True,
)
with open("tcga_brca_rnaseq.tar.gz", "wb") as fp:
    for chunk in r.iter_content(chunk_size=65536):
        fp.write(chunk)
```

### 4. Mutations (MAF) for a project

```python
filters = {
    "op": "and",
    "content": [
        {"op": "in", "content": {"field": "cases.project.project_id",
                                 "value": ["TCGA-LUAD"]}},
        {"op": "in", "content": {"field": "data_type",
                                 "value": ["Masked Somatic Mutation"]}},
        {"op": "in", "content": {"field": "access", "value": ["open"]}},
    ],
}
```

The result is a `.maf.gz` per file — open with
`pandas.read_csv(..., sep="\t", comment="#")` and you have the variant
table.

### 5. Controlled-access files (BAM, gVCF)

Get a token at <https://portal.gdc.cancer.gov/> (User → Download Token).
Pass it as a header:

```python
headers = {"X-Auth-Token": open(token_path).read().strip()}
r = requests.get(f"https://api.gdc.cancer.gov/data/{file_id}",
                 headers=headers, timeout=300, stream=True)
```

A controlled-access file (`access == "controlled"`) without the token
returns HTTP 403.

## Wire it to a viewer

### TCGA BAM (controlled, with token) → IGV

IGV.js can't easily carry custom auth headers on its HTTP fetches, so the
clean path is: download with the token, then `serve_local_data` → IGV.

```python
# bam_path, bai_path already downloaded with X-Auth-Token above
bam_url = serve_local_data(bam_path)["url"]
bai_url = serve_local_data(bai_path)["url"]
open_live_view(view_type="igv", title="TCGA BAM", state={
    "genome": "hg38", "locus": "BRCA1",
    "tracks": [{"name": "TCGA", "url": bam_url, "indexURL": bai_url,
                "format": "bam"}],
})
```

### MAF mutations → IGV variant track

Convert MAF → VCF with `maf2vcf.pl` (from `mskcc/vcf2maf`) or build a
small BED of mutation positions, then `serve_local_data` and pass as an
IGV `bed` / `vcf` track. For per-gene mutation lollipops, `gget cbio` is
faster than going through GDC.

### Expression matrix → analysis (no viewer)

```python
# build a counts matrix from the per-sample STAR-Counts files
import pandas as pd, gzip
mats = {}
for f in files:
    df = pd.read_csv(f"{f['file_id']}.tsv.gz", sep="\t", comment="#",
                     header=1, index_col=0)
    sample = f["cases"][0]["submitter_id"]
    mats[sample] = df["unstranded"]
counts = pd.DataFrame(mats)
```

## Common pitfalls

- **Open vs controlled** — always include `{"field":"access","value":["open"]}`
  in your filter unless you have a token. Without it you'll see controlled
  files in results that 403 on download.
- **Filter JSON encoding** — pass the JSON as a string via `params={...}`;
  `requests` URL-encodes it. For very large filters, POST instead.
- **Workflow types** — TCGA has multiple processing workflows per sample:
  `STAR - Counts` (current standard), `HTSeq - Counts` (legacy),
  `STAR - FPKM-UQ`, `STAR - TPM`. Filter `analysis.workflow_type` to pin
  one — otherwise duplicates per sample.
- **Legacy hg19** — older "Legacy Archive" data on hg19 is at
  `legacy.api.gdc.cancer.gov/` and is mostly frozen. Modern data is
  GRCh38-only.
- **Bulk POST limits** — keep `ids` lists ≤ a few thousand per POST;
  chunk if larger.
- **File ID vs Case ID** — both are UUIDs, both look the same; don't mix.
