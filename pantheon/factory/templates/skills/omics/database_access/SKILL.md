---
id: database_access_index
name: Database Access Skills Index
description: |
  Skills for querying and downloading data from genomic, transcriptomic,
  3D-genome, and cancer-genomics databases. Covers programmatic access to
  public repositories, gene annotation, sequence retrieval, processed
  functional-genomics tracks, Hi-C / Micro-C contact matrices, TCGA-style
  cohorts, and large-scale single-cell data.
---

# Database Access Skills

Tools and workflows for accessing public biological databases, retrieving
sequencing data, querying gene/protein information, pulling processed
functional / 3D-genome tracks, fetching cancer-cohort data, and
downloading large-scale single-cell datasets.

## Quick map — which skill for what

| If you need... | Use |
|---|---|
| Gene / protein / variant **metadata** (Ensembl, UniProt, NCBI, …) | **gget** |
| Raw sequencing **reads** (FASTQ) from SRA/ENA/GEO/DDBJ/GSA | **iSeq** |
| Large-scale **single-cell** RNA-seq matrices | **CELLxGENE Census** |
| Processed **functional-genomics tracks** (ChIP/ATAC/DNase/RNA-seq bigWig/BAM/peaks) | **ENCODE** |
| **3D-genome** contact matrices (Hi-C / Micro-C / ChIA-PET .mcool / .hic) | **4DN** |
| **liftOver**, UCSC tracks, sequence pulls, large genome catalog | **UCSC** |
| **Cancer-cohort** RNA-seq counts, MAF mutations, CNV, methylation (TCGA / CPTAC) | **GDC** |

The new four (ENCODE / 4DN / UCSC / GDC) are mostly orthogonal to the
existing three — they cover *processed* tracks, *3D* genome data,
*coordinate utilities*, and *cancer cohorts* that gget / iSeq / Census
don't reach.

## Available Skills

### gget — Genomic Database Querying

Python package and CLI tool with 23 interoperable modules for efficiently
querying genomic databases including Ensembl, NCBI, UniProt, ARCHS4,
Enrichr, COSMIC, OpenTargets, CellxGene, cBioPortal, PDB, and Bgee.

**Skill file**: [gget.md](./gget.md)

**When to use**:
- Fetching reference genome/annotation download links (Ensembl)
- Searching genes by keyword or retrieving gene metadata
- Running BLAST/DIAMOND sequence alignment
- Performing enrichment analysis (GO, KEGG, pathway)
- Querying cancer mutations (COSMIC) or drug-target associations (OpenTargets)
- Retrieving single-cell data from CZ CELLxGENE Discover
- Looking up protein structures (PDB, AlphaFold)
- Finding tissue expression patterns (ARCHS4, Bgee)
- Plotting cancer genomics heatmaps (cBioPortal)

---

### iSeq — Sequencing Data Download

Bash CLI tool for downloading sequencing data and metadata from five public
databases (GSA, SRA, ENA, DDBJ, GEO) through a single unified interface.
Supports parallel downloads, Aspera transfers, and automatic format conversion.

**Skill file**: [iseq.md](./iseq.md)

**When to use**:
- Downloading raw sequencing data (FASTQ/SRA) from public repositories
- Fetching metadata for projects, experiments, or runs
- Downloading from Chinese GSA database (CRA/CRR accessions)
- Batch downloading multiple accessions from a file
- Converting SRA files to FASTQ format
- Merging FASTQ files by experiment, sample, or study

---

### CZ CELLxGENE Census — Single-Cell RNA-seq Data Access

Cloud-based Python API for accessing 217M+ single-cell RNA-seq observations
from CZ CELLxGENE Discover via TileDB-SOMA. Supports flexible metadata
queries, gene filtering, and pre-computed embeddings (scVI, Geneformer).

**Skill file**: [cellxgene_census.md](./cellxgene_census.md)

**When to use**:
- Querying large-scale single-cell RNA-seq data by tissue, cell type, disease
- Downloading count matrices as AnnData objects with metadata filters
- Accessing pre-computed embeddings (scVI, Geneformer)
- Finding which datasets contain specific genes or cell types
- Working with larger-than-memory single-cell datasets via streaming
- Exploring CZ CELLxGENE Discover catalog programmatically

---

### ENCODE — Functional Genomics Tracks (ChIP/ATAC/DNase/RNA-seq)

Query and download from the ENCODE Portal — standardised processed files
(BAM, bigWig, narrowPeak) for ChIP-seq, ATAC-seq, DNase-seq, RNA-seq,
eCLIP, etc. across human / mouse cell types and tissues. Pairs with the
`igv` and `gosling` LiveViews.

**Skill file**: [encode.md](./encode.md)

**When to use**:
- "Find CTCF ChIP-seq peaks in K562 from ENCODE" — TF / histone / chromatin
  data by target + biosample
- ENCODE bigWig signal → IGV track for a paper-quality view
- Cross-cell-line / cross-tissue comparison of a regulatory assay
- Anywhere you need ENCODE's processed outputs, not raw FASTQ

---

### 4DN — 3D Genome Data (Hi-C, Micro-C, ChIA-PET)

Query and download from the 4D Nucleome Data Portal — Hi-C variants,
Micro-C, ChIA-PET, SPRITE, GAM, FISH. Returns `.mcool` / `.hic` contact
matrices and `.pairs.gz` files. Pairs with the `gosling` LiveView via the
HiGlass back-end, and with `cooler` / `pairix` for Python analysis.

**Skill file**: [fourdn.md](./fourdn.md)

**When to use**:
- 3D-genome contact map for a cell line / condition
- `.mcool` for use with Cooler / HiGlass / Gosling
- ChIA-PET loop calls for a TF
- Comparative Hi-C across replicates / conditions

---

### UCSC — Genome Browser API + liftOver

Programmatic access to UCSC's REST API (assemblies, tracks, sequence,
gene-symbol search, Track Hubs) and the canonical liftOver coordinate-
conversion utility. Use UCSC where Ensembl / NCBI fall short: cross-
assembly liftOver, public Track Hubs, the large UCSC assembly catalog
(panTro6, calJac4, etc.).

**Skill file**: [ucsc.md](./ucsc.md)

**When to use**:
- liftOver a BED / VCF / single coordinate between assemblies
- Find a public Track Hub for a non-model organism
- One-off sequence / track pulls without writing pysam boilerplate
- Browse UCSC's assembly catalog (assemblies Ensembl doesn't have)

---

### GDC — NCI Genomic Data Commons (TCGA + friends)

Query and download from the NCI GDC — TCGA, CPTAC, TARGET, FM-AD cancer
cohorts. Open-tier files (RNA-seq counts, MAF mutations, CNV, methylation,
clinical) need no auth; controlled-tier (BAM, gVCF) needs a dbGaP /
NCI token. Complements `gget cbio` (processed cBioPortal view) with the
raw GDC files.

**Skill file**: [gdc.md](./gdc.md)

**When to use**:
- TCGA RNA-seq counts for a project — differential expression input
- MAF mutation file for an entire TCGA cohort
- CNV / methylation / clinical metadata from TCGA / CPTAC / TARGET
- Token-required BAM / gVCF download

---

## Using Skills

1. **Identify your goal**: use the quick-map table above to pick the
   right portal (metadata vs raw reads vs processed tracks vs 3D-genome
   vs cancer cohort vs single-cell vs coordinate utilities).
2. **Load skill file**: Read the full skill document for detailed guidance
   on its API, filter patterns, and viewer wiring.
3. **Follow examples**: each skill's recipes section is copy-paste-able.
4. **Combine tools**: they're orthogonal by design — e.g. gget to look up
   a gene's coordinates → ENCODE for ChIP-seq tracks at that locus →
   IGV LiveView to display; or GDC for TCGA RNA-seq counts → gget for
   gene-set enrichment on the result.
5. **Viewer wiring**: every new skill includes a "Wire it to a viewer"
   section showing how to pipe its files into the `igv` / `gosling` /
   `molstar` LiveViews (download → `serve_local_data` → viewer track).
