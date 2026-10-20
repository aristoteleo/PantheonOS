---
id: sc_bp_chromatin_accessibility
name: "SC Best Practices: Chromatin Accessibility"
description: |
  scATAC-seq data processing, quality control, and gene regulatory
  network inference from chromatin accessibility data.
tags: [scATAC, chromatin, accessibility, epigenomics, sc-best-practices]
---

# SC Best Practices: Chromatin Accessibility (scATAC-seq)

Processing, quality control, and analysis of single-cell chromatin
accessibility data. Covers technology fundamentals, tool ecosystems,
QC metrics, peak calling, motif analysis, GRN inference, and
integration with scRNA-seq.

**Source**: [https://www.sc-best-practices.org](https://www.sc-best-practices.org)

---

## 1. Technology Overview

### scATAC-seq Principle

The Assay for Transposase-Accessible Chromatin (ATAC-seq) uses the
hyperactive Tn5 transposase to simultaneously cut and tag open chromatin
regions with sequencing adapters. In single-cell mode, individual cells
are barcoded (droplet-based or combinatorial indexing) before tagmentation
or after nuclei isolation.

```
Nuclei isolation -> Tn5 tagmentation (cuts open chromatin)
    -> Barcode + amplify -> Sequence -> Fragments file
```

### Key Output: Fragments File

The primary output is a **fragments file** -- a BED-like file listing
every sequenced fragment with its cell barcode:

```
# chr    start    end       barcode                 count
chr1     10073    10357     AGTTCGATAGTCTCGA-1      1
chr1     10128    10352     CGTCACTGTCAAACTG-1      1
chr1     180202   180572    TGCCAAACAGCATACT-1      2
```

### Technology Platforms

| Platform | Cell Isolation | Throughput | Notes |
|----------|---------------|------------|-------|
| 10x Chromium scATAC | Droplet | 5k-10k cells | Most widely used |
| 10x Multiome | Droplet | 5k-10k cells | Joint ATAC + RNA from same cell |
| sci-ATAC-seq | Combinatorial indexing | 10k-100k+ cells | No microfluidics needed |
| snATAC-seq | Plate or droplet | Variable | Nuclear isolation for tissues |

---

## 2. Tool Ecosystems

| Tool | Language | Key Features |
|------|----------|--------------|
| **SnapATAC2** | Python | Fast, scalable, AnnData-native; recommended for Python workflows |
| **ArchR** | R | Feature-rich, handles large datasets; Arrow file-based |
| **Signac** | R | Seurat-compatible; integrates with Seurat WNN |
| **cellranger-atac** | CLI | Official 10x pipeline for alignment and cell calling |
| **chromVAR** | R | TF motif deviation scoring |
| **MACS2/3** | Python/CLI | Peak calling (standard for bulk, used in single-cell) |

> [!TIP]
> For Python-based workflows, **SnapATAC2** provides a streamlined experience
> with native AnnData compatibility. For R users or Seurat-integrated analyses,
> **ArchR** or **Signac** are well-supported alternatives.

---

## 3. Preprocessing Pipeline

### Alignment and Fragment Generation

```bash
# 10x Chromium scATAC: Use cellranger-atac
cellranger-atac count \
    --id=sample_1 \
    --reference=refdata-cellranger-arc-GRCh38-2020-A-2.0.0 \
    --fastqs=/path/to/fastqs \
    --sample=Sample1 \
    --localcores=16 \
    --localmem=64
```

### SnapATAC2 Workflow

```python
import snapatac2 as snap

# Import fragments file
data = snap.pp.import_data(
    "fragments.tsv.gz",
    chrom_sizes=snap.genome.hg38,
    min_num_fragments=500,
    sorted_by_barcode=False,
)

# Basic QC metrics
snap.metrics.tsse(data, snap.genome.hg38)

# Filter cells
snap.pp.filter_cells(data, min_counts=1000, min_tsse=5)

# Create tile matrix (genome-wide bins)
snap.pp.add_tile_matrix(data, bin_size=500)

# Dimensionality reduction
snap.pp.select_features(data)
snap.tl.spectral(data)
snap.tl.umap(data)

# Clustering
snap.tl.leiden(data)
```

---

## 4. Quality Control Metrics

### TSS Enrichment Score

The TSS enrichment score measures the aggregate signal at transcription
start sites relative to flanking regions. High-quality cells show strong
enrichment at TSSs:

```python
import snapatac2 as snap

# Calculate TSS enrichment
snap.metrics.tsse(data, snap.genome.hg38)

# Plot TSS enrichment distribution
snap.pl.tsse(data, min_fragment=500)
```

**Thresholds:**
- TSS enrichment >= 5: Standard threshold for high-quality cells
- TSS enrichment >= 8: Stringent threshold
- TSS enrichment < 2: Likely low-quality or dead cells

### Fragment Size Distribution

Chromatin wraps around nucleosomes in ~147 bp units with ~50 bp linkers.
High-quality scATAC data shows a characteristic nucleosome banding pattern:

```
Expected peaks:
  ~200 bp   : Sub-nucleosomal / nucleosome-free region (NFR)
  ~400 bp   : Mono-nucleosomal
  ~600 bp   : Di-nucleosomal
  ~800 bp   : Tri-nucleosomal
```

```python
# Plot fragment size distribution
snap.pl.frag_size_distr(data)
```

> [!WARNING]
> Absence of nucleosome banding in the fragment size distribution indicates
> poor tagmentation or excessive cell lysis. Such libraries should be excluded
> or the experiment repeated.

### Additional QC Metrics

| Metric | Good Quality | Poor Quality | Notes |
|--------|-------------|--------------|-------|
| Unique fragments per cell | > 1,000 | < 500 | Library complexity |
| TSS enrichment | > 5 | < 2 | Signal-to-noise ratio |
| Fraction in peaks (FRiP) | > 0.3 | < 0.1 | Signal specificity |
| Duplicate rate | < 50% | > 80% | Library saturation |
| Nucleosome banding | Clear pattern | Absent | Tagmentation quality |

---

## 5. Feature Matrix Construction

### Peak Calling

Aggregate fragments across cells (or clusters) to call peaks using MACS2/3:

```python
import snapatac2 as snap

# Call peaks per cluster for better sensitivity
snap.tl.macs3(data, groupby='leiden', replicate='sample')

# Create peak-by-cell matrix
peak_matrix = snap.pp.make_peak_matrix(data, peaks)
```

### Alternative: Tile Matrix

For initial exploration or when peak calling is not yet done, use
fixed-width genomic bins (tiles):

```python
# 500bp tile matrix (genome-wide)
snap.pp.add_tile_matrix(data, bin_size=500)
```

### Feature Matrix Comparison

| Feature Type | Resolution | Use Case |
|-------------|-----------|----------|
| Peak matrix | Variable (peak widths) | Standard analysis, motif enrichment |
| Tile matrix | Fixed (e.g., 500bp) | Initial exploration, cross-sample comparison |
| Gene activity | Gene body + promoter | Integration with scRNA-seq |

---

## 6. TF Motif Enrichment Analysis

### chromVAR

chromVAR computes per-cell motif deviation scores, enabling identification
of transcription factor activity variation across cells:

```python
import snapatac2 as snap

# Run motif analysis with chromVAR-like approach in SnapATAC2
snap.tl.motif_enrichment(
    data,
    motifs=snap.datasets.cis_bp(unique=True),
    genome_fasta="genome.fa",
)
```

```r
# chromVAR in R
library(chromVAR)
library(motifmatchr)
library(BSgenome.Hsapiens.UCSC.hg38)

# Get motif matches in peaks
motif_ix <- matchMotifs(pwms, peaks, genome = BSgenome.Hsapiens.UCSC.hg38)

# Compute deviations
dev <- computeDeviations(object = fragment_counts, annotations = motif_ix)

# Access deviation scores (cells x motifs)
deviation_scores <- deviationScores(dev)
```

> [!TIP]
> chromVAR deviation scores can be used as a feature space for clustering,
> providing a biologically interpretable alternative to peak-based analysis.
> Cluster on deviation scores to group cells by regulatory activity rather
> than individual peak accessibility.

---

## 7. Gene Regulatory Network Inference from ATAC

### Linking Peaks to Target Genes

Accessible chromatin regions (peaks) near gene promoters or within gene
bodies may regulate transcription. Peak-gene linkage connects distal
regulatory elements to their target genes:

```python
import snapatac2 as snap

# Compute gene activity scores (promoter + gene body accessibility)
gene_matrix = snap.pp.make_gene_matrix(data, snap.genome.hg38)
```

### TF Binding Prediction

Combine peak accessibility with TF motif presence to predict regulatory
relationships:

```
Peak accessible in cell type X + Contains TF motif Y
    -> TF Y may regulate genes near this peak in cell type X
```

### GRN Inference Pipeline

A complete GRN inference from scATAC typically involves:

1. **Peak calling** per cell type or cluster
2. **Motif scanning** to identify TF binding sites in peaks
3. **Peak-gene linking** using distance, correlation, or co-accessibility
4. **Network construction** connecting TFs -> peaks -> genes

```python
# Example: Peak-gene correlation using SnapATAC2
# Requires paired ATAC + RNA (e.g., 10x Multiome)

# Co-accessibility analysis
snap.tl.co_accessibility(data)

# Peak-gene linkage
snap.tl.peak_gene_linkage(
    data,
    gene_matrix=gene_activity,
    peak_matrix=peak_matrix,
    distance=500000,  # Maximum distance in bp
)
```

### Specialized GRN Tools

| Tool | Input | Approach |
|------|-------|----------|
| SCENIC+ | scATAC + scRNA | Motif enrichment + peak-gene + expression correlation |
| FigR | scATAC + scRNA | DORC (domains of regulatory chromatin) identification |
| Dictys | scATAC + scRNA | Context-specific GRN with network dynamics |
| CellOracle | scATAC + scRNA | Perturbation simulation from GRN |

---

## 8. Integration with scRNA-seq

### Paired Multi-omic (10x Multiome)

When ATAC and RNA are measured from the same cells:

```python
import muon as mu

# Read multiome data
mdata = mu.read_10x_h5("filtered_feature_bc_matrix.h5")

# Separate modalities
rna = mdata.mod['rna']
atac = mdata.mod['atac']

# Process RNA
mu.pp.filter_var(rna, lambda x: x['n_cells_by_counts'] >= 3)
sc.pp.normalize_total(rna, target_sum=1e4)
sc.pp.log1p(rna)
sc.pp.highly_variable_genes(rna, n_top_genes=3000)

# Process ATAC
snap.pp.select_features(atac)
snap.tl.spectral(atac)

# Joint embedding (WNN-style)
mu.pp.neighbors(mdata, key_added='wnn')
mu.tl.umap(mdata, neighbors_key='wnn')
```

### Unpaired Integration

When ATAC and RNA are from different cells, use label transfer or
joint embedding approaches:

| Method | Approach | Notes |
|--------|----------|-------|
| Seurat v4 WNN | Weighted nearest neighbors | Requires conversion to gene activity scores |
| ArchR gene scores | Gene activity from ATAC | Transfer labels from scRNA reference |
| GLUE | Graph-linked unified embedding | Deep learning-based, handles unpaired data |
| Bridge integration | Multi-omic bridge reference | Uses paired reference to link unpaired datasets |

```python
# Gene activity scores for integration
gene_activity = snap.pp.make_gene_matrix(data_atac, snap.genome.hg38)

# Use gene activity as common feature space with scRNA-seq
# Then apply standard integration methods (Harmony, scVI, etc.)
```

---

## 9. Differential Accessibility

```python
import snapatac2 as snap

# Differential accessibility between clusters
diff_peaks = snap.tl.diff_test(
    data,
    groupby='leiden',
    method='t-test',
)

# Filter significant peaks
sig_peaks = diff_peaks[diff_peaks['adjusted p-value'] < 0.05]
```

---

## Best Practices Summary

1. **Start with QC**: Always check TSS enrichment score and fragment size distribution before proceeding. Remove cells with TSS enrichment < 5 and fragments < 1,000.
2. **Use tile matrix for exploration**: Fixed-width tiles provide a quick, unbiased feature space for initial clustering and QC visualization.
3. **Call peaks per cluster**: Cluster-level peak calling improves sensitivity for cell-type-specific regulatory elements.
4. **Interpret motifs, not peaks**: Individual peak accessibility is noisy. Aggregate to motif deviation scores (chromVAR) for robust TF activity estimation.
5. **Integrate with RNA when possible**: scATAC alone identifies *where* chromatin is open; paired RNA data reveals *what* is expressed, enabling true regulatory inference.
6. **Use gene activity scores cautiously**: Gene activity derived from ATAC is a rough proxy for expression. Validate key findings with actual RNA measurements.
7. **Account for sparsity**: scATAC data is extremely sparse (1-10% of peaks detected per cell). Methods designed for this sparsity (SnapATAC2, ArchR) outperform generic single-cell tools.
