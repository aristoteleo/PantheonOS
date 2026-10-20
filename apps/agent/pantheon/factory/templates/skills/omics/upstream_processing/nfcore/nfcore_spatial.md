---
id: nfcore_spatial
name: "nf-core: Spatial Omics Pipelines"
description: |
  nf-core pipelines for processing spatial transcriptomics data from Visium,
  Xenium, MERSCOPE, CosMX, Molecular Cartography, and other platforms.
tags:
  - nf-core
  - spatial
  - Visium
  - Xenium
  - MERSCOPE
  - segmentation
  - SpatialData
---

# nf-core: Spatial Omics Pipelines

## Pipeline Overview

| Pipeline | Technology | Segmentation | Key Output |
|----------|-----------|-------------|------------|
| **spatialvi** | 10x Visium (v1/v2/HD) | N/A (spot-based) | SpatialData zarr, AnnData h5ad |
| **sopa** | Multi-platform (Xenium, Visium HD, MERSCOPE, CosMX, etc.) | Cellpose, Baysor, Proseg, Comseg, Stardist | SpatialData zarr |
| **molkart** | Resolve Molecular Cartography | Cellpose, Mesmer, ilastik | Cell-by-transcript table |
| **spatialxe** | 10x Xenium | Cellpose, Baysor, Proseg | SpatialData zarr |

---

## nf-core/spatialvi

**Status**: Development version

Pipeline for analyzing **10x Genomics Visium** spatial transcriptomics data,
supporting Visium v1, v2, and Visium HD.

### Workflow Steps

1. Raw data processing (optional Space Ranger alignment)
2. Quality control and filtering (spot filtering by total counts, genes, tissue presence)
3. Normalization
4. Dimensionality reduction (UMAP/t-SNE) and Leiden clustering
5. Spatially variable gene identification (Moran's I scoring)
6. Differential gene expression testing

### Samplesheet Format (Raw Data)

```csv
sample,fastq_dir,image,slide,area
SAMPLE_1,/data/fastqs/,/data/tissue_image.tif,V19S23-039,A1
```

| Column | Description |
|--------|-------------|
| `sample` | Unique ID (must match FASTQ file prefix) |
| `fastq_dir` | Directory or `.tar.gz` containing FASTQs |
| `image` | Brightfield microscopy image |
| `cytaimage` | Cytassist brightfield image (optional) |
| `slide` | Visium slide ID |
| `area` | Slide area containing tissue |
| `manual_alignment` | Manual alignment file (optional) |
| `slidefile` | Slide specification JSON (optional) |

At least one image column required (`image`, `cytaimage`, `darkimage`, or `colorizedimage`).

### Samplesheet Format (Pre-processed)

```csv
sample,spaceranger_dir
SAMPLE_1,/data/spaceranger/SAMPLE_1/outs
```

### Key Parameters

```bash
nextflow run nf-core/spatialvi \
  --input samplesheet.csv \
  --outdir results \
  --spaceranger_reference /ref/refdata-gex-GRCh38-2024-A \
  -profile docker
```

| Parameter | Description |
|-----------|-------------|
| `--input` | Samplesheet CSV |
| `--spaceranger_reference` | Reference genome for Space Ranger |
| `--spaceranger_probeset` | Required for FFPE/Cytassist experiments |
| `--hd_bin_size` | Bin size for Visium HD (default: 8 microns) |

> [!TIP]
> Space Ranger requires 64 GB RAM, 8 threads, and only supports human
> and mouse genomes.

### Output Files

| Directory | Contents |
|-----------|----------|
| `<SAMPLE>/spaceranger/outs/` | Tissue images, positions, feature matrix (MTX) |
| `<SAMPLE>/data/` | `sdata_processed.zarr`, `adata_processed.h5ad`, spatially variable genes CSV |
| `<SAMPLE>/reports/` | QC, clustering, and SVG HTML reports (Quarto) |
| `multiqc/` | Aggregated MultiQC report |

---

## nf-core/sopa

**Status**: Development version

**Technology-agnostic** spatial omics pipeline built on the
[SpatialData](https://spatialdata.scverse.org/) framework. Supports the
widest range of spatial technologies.

### Supported Technologies

- 10x Xenium
- 10x Visium HD
- MERSCOPE (Vizgen)
- CosMX (Nanostring)
- PhenoCycler (Akoya)
- MACSima (Miltenyi)
- Resolve Molecular Cartography
- Any technology convertible to SpatialData

### Workflow Steps

1. Raw data processing (Space Ranger for Visium HD, or direct import)
2. Tissue segmentation (optional)
3. Cell segmentation (Cellpose, Baysor, Proseg, Comseg, Stardist)
4. Aggregation (transcript counts and channel intensity per cell)
5. Cell-type annotation (optional)
6. Output creation (visualization formats)
7. SpatialData export (`.zarr` directories)

### Key Parameters

```bash
nextflow run nf-core/sopa \
  --input samplesheet.csv \
  --outdir results \
  -profile docker
```

### Output Files

- `.zarr` directories (SpatialData objects) for downstream analysis
- `.explorer` directories for interactive visualization

> [!TIP]
> sopa is the most versatile spatial pipeline in nf-core. Use it when your
> technology is not Visium v1/v2 (which spatialvi handles well) or when
> you need advanced segmentation options.

**Citation**: Blampey et al., "Sopa: a technology-invariant pipeline for
analyses of image-based spatial omics." *Nature Communications* (2024).
doi: 10.1038/s41467-024-48981-z

---

## nf-core/molkart

**Latest version**: 1.2.0 | **DOI**: 10.5281/zenodo.10650983

Pipeline for processing **Resolve Bioscience Molecular Cartography** data
(combinatorial FISH).

### Workflow Steps

1. **Image preprocessing**: Grid artifact removal (MindaGap), optional CLAHE,
   multichannel stack generation
2. **Cell segmentation**: Cellpose (deep learning), Mesmer (cell morphology),
   or ilastik (interactive machine learning)
3. **Spot processing**: Duplicate spot removal, FISH spot-to-cell assignment
4. **QC**: Spatial transcriptomics-specific metrics, MultiQC integration

### Samplesheet Format

```csv
sample,image,spot_table,membrane
SAMPLE_1,/data/nuclear_dapi.tif,/data/spots.csv,/data/membrane_wga.tif
```

| Column | Required | Description |
|--------|----------|-------------|
| `sample` | Yes | Sample ID |
| `image` | Yes | Nuclear staining image (TIFF) |
| `spot_table` | Yes | FISH spot locations table |
| `membrane` | No | Membrane staining image for improved segmentation |

### Key Parameters

```bash
nextflow run nf-core/molkart \
  --input samplesheet.csv \
  --outdir results \
  -profile docker
```

### Output Files

- Cell-by-transcript abundance table
- Segmentation masks
- QC reports (MultiQC)

---

## nf-core/spatialxe

**Status**: Development version

Pipeline for processing **10x Genomics Xenium** in situ data specifically.

### Analysis Modes

| Mode | Description | Default Tools |
|------|-------------|--------------|
| Image-based segmentation | Segment from images | Cellpose → Baysor → SpatialData |
| Coordinate-based segmentation | Segment from transcript coordinates | Proseg → Baysor → SpatialData |
| Segmentation-free | Skip segmentation | Direct analysis |
| Data preview | Quick visualization | — |

### Key Parameters

```bash
nextflow run nf-core/spatialxe \
  --input samplesheet.csv \
  --outdir results \
  -profile docker
```

---

## Pipeline Selection Guide

```
Is your data from 10x Visium (v1/v2)?
  ├─ Yes → nf-core/spatialvi
  └─ No
     Is your data from 10x Xenium?
       ├─ Yes → nf-core/spatialxe or nf-core/sopa
       └─ No
          Is your data from Resolve Molecular Cartography?
            ├─ Yes → nf-core/molkart
            └─ No (MERSCOPE, CosMX, PhenoCycler, etc.)
               → nf-core/sopa
```

---

## Connecting to Downstream Analysis

After running any spatial pipeline, load results in Python:

```python
import spatialdata as sd
import scanpy as sc

# Load SpatialData zarr (from spatialvi, sopa, spatialxe)
sdata = sd.read_zarr("results/SAMPLE_1/data/sdata_processed.zarr")

# Or load AnnData h5ad directly
adata = sc.read_h5ad("results/SAMPLE_1/data/adata_processed.h5ad")

# Standard spatial analysis with squidpy
import squidpy as sq
sq.gr.spatial_neighbors(adata)
sq.gr.nhood_enrichment(adata, cluster_key="leiden")
```
