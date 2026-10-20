---
id: openst_computational
name: "OpenST: Computational Analysis Pipeline"
description: |
  End-to-end computational workflow for Open-ST spatial transcriptomics,
  from raw BCL files through barcode preprocessing, transcriptomic alignment,
  spatial registration, cell segmentation, 3D reconstruction, to downstream
  exploratory analysis.
tags: [openst, spatial, upstream, spacemake, cellpose, alignment, segmentation, 3d-reconstruction]
---

# OpenST Computational Analysis Pipeline

Complete computational workflow for processing Open-ST spatial transcriptomics
data. The pipeline consists of 6 sequential stages that transform raw sequencing
data into spatially-resolved single-cell expression matrices.

**Source**: [https://rajewsky-lab.github.io/openst/latest/computational/getting_started/](https://rajewsky-lab.github.io/openst/latest/computational/getting_started/)

---

## Pipeline Overview

```
Stage 1: Barcode Preprocessing    (BCL -> barcode-coordinate maps)
    |
Stage 2: Transcriptomic Preprocessing  (FASTQ -> spatially-resolved h5ad via spacemake)
    |
Stage 3: Pairwise Alignment      (register coordinates to tissue image)
    |
Stage 4: Segmentation            (cell boundaries + transcript-to-cell assignment)
    |
Stage 5: 3D Reconstruction       (optional: serial section registration)
    |
Stage 6: Downstream Analysis     (QC, clustering, spatial visualization)
```

---

## 1. Installation & Environment Setup

### Required Packages

| Package | Purpose |
|---------|---------|
| `openst` | Core Open-ST processing pipeline |
| `spacemake` (v0.7.9+) | Transcriptomic alignment (wraps STAR, Drop-seq tools) |
| `cellpose` | Cell segmentation |
| `scanpy` | Downstream single-cell analysis |
| `squidpy` | Spatial analysis and visualization |
| `stimwrap` / `stim` | 3D reconstruction |
| `bcl2fastq` or `bclconvert` | Basecall conversion |
| `Drop-seq_tools` v2.5.1+ | UMI/barcode processing |

### Installation

```bash
# Create dedicated environment
WORKDIR="~/openst_project"
mkdir -p $WORKDIR && cd $WORKDIR

# spacemake environment
wget "https://raw.githubusercontent.com/rajewsky-lab/spacemake/master/environment.yaml"
mamba env create -n openst -f environment.yaml
mamba activate openst
pip install spacemake

# openst
pip install openst

# 3D reconstruction (optional)
mamba install -c conda-forge stim
pip install stimwrap

# downstream analysis
pip install scanpy squidpy
```

### Docker Alternative

```bash
docker pull rajewsky/openst
docker run -it --rm -v /path/to/data:/data --entrypoint bash rajewsky/openst

# Apple Silicon: add platform flag
docker run --platform linux/amd64 -it --rm --entrypoint bash rajewsky/openst
```

> [!TIP]
> System requirements: Linux with at least **128 GB RAM** recommended.
> Use `--device cuda` flags for GPU acceleration on segmentation and alignment steps.

---

## 2. Stage 1: Capture Area Barcode Preprocessing

Generate barcode-to-coordinate maps from the flow cell. This is done **once per
flow cell** and can serve 80-300 experiments.

### Prerequisites

Ensure `bcl2fastq` or `bclconvert` is on PATH:

```bash
export PATH=/path/to/bcl2fastq:$PATH
```

### Full Flow Cell Processing (Recommended)

```bash
openst flowcell_map \
    --bcl-in /path/to/fc/bcl \
    --tiles-out /path/to/fc_tiles \
    --crop-seq 5:30 \
    --rev-comp
```

### Single Tile Processing (Alternative)

```bash
openst barcode_preprocessing \
    --fastq-in /path/to/tile.fastq \
    --tilecoords-out /path/to/fc_tiles \
    --out-prefix fc_1_ \
    --crop-seq 5:30 \
    --rev-comp \
    --single-tile
```

### Parameters

| Parameter | Description | Notes |
|-----------|-------------|-------|
| `--bcl-in` | Input BCL directory | Required for `flowcell_map` |
| `--tiles-out` | Output directory for tile coordinate files | Required |
| `--crop-seq` | Python slice notation (e.g., `5:30`) | Extracts 25 nt (positions 6-30) |
| `--rev-comp` | Compute reverse complement after cropping | Recommended for standard Open-ST |
| `--parallel-processes` | Number of parallel processes | Optional, for `flowcell_map` |

### Output Format

Compressed text files (`*.txt.gz`) with three columns:

| cell_bc | x_pos | y_pos |
|---------|-------|-------|
| CGCGAGGGGAAAATGGGGACTAGCG | 6343 | 1016 |

> [!WARNING]
> Always use `openst flowcell_map` (not `barcode_preprocessing`) to ensure
> **cross-tile barcode deduplication**. Per-tile processing misses duplicates
> spanning tile boundaries.

---

## 3. Stage 2: Transcriptomic Library Preprocessing (spacemake)

Map transcriptomic reads to a reference genome and generate spatially-resolved
h5ad files.

### Step 1: Initialize spacemake

```bash
mkdir -p $WORKDIR/spacemake && cd $WORKDIR/spacemake
spacemake init --dropseq_tools /path/to/Drop-seq_tools-2.5.1
```

### Step 2: Add Species References

```bash
# Genome reference (mouse example)
spacemake config add_species \
    --name mouse \
    --reference genome \
    --sequence GRCm39vM30.genome.fa \
    --annotation gencodevM30.annotation.gtf

# rRNA reference
spacemake config add_species \
    --name mouse \
    --reference rRNA \
    --sequence mouse.rRNA.fa

# phiX control (recommended)
spacemake config add_species \
    --name mouse \
    --reference phiX \
    --sequence phiX.fa
```

For human samples, use `--name human` with GRCh38 genome and Gencode v41 annotation.

### Step 3: Add Open-ST Sample

```bash
spacemake projects add_sample \
    --project-id <project_id> \
    --sample-id <sample_id> \
    --R1 <R1.fastq.gz> \
    --R2 <R2.fastq.gz> \
    --species <species> \
    --puck openst \
    --run-mode openst \
    --barcode-flavor openst \
    --puck-barcode-file /path/to/fc_tiles/*.txt.gz \
    --map-strategy "bowtie2:phiX->bowtie2:rRNA->STAR:genome:final"
```

Multiple FASTQ pairs (e.g., resequencing) are supported:

```bash
--R1 file1_R1.fastq.gz file2_R1.fastq.gz \
--R2 file1_R2.fastq.gz file2_R2.fastq.gz
```

### Step 4: Configure Coordinate System

In spacemake `config.yaml`:

```yaml
openst:
    coordinate_system: puck_data/openst_coordinate_system.csv
    spot_diameter_um: 0.6
    width_um: 1200
```

### Step 5: Run spacemake

```bash
spacemake run --cores 16 --keep-going
```

### Output Structure

```
spacemake_folder/projects/<project_id>/processed_data/<sample_id>/illumina/complete_data/
    dge/          # h5ad files with gene expression
    qc_sheets/    # HTML sequencing QC reports
```

> [!WARNING]
> Use output files with `spatial_beads_` in the name. Do **NOT** use files
> containing `mesh`, `hexagon`, or `circle` -- those are aggregated representations.

### Puck Barcode File Format

Tab/comma-separated with mandatory header:

| Accepted column names | Purpose |
|-----------------------|---------|
| `cell_bc`, `barcodes`, `barcode` | Cell barcode |
| `xcoord`, `x_pos` | X coordinate |
| `ycoord`, `y_pos` | Y coordinate |

> [!TIP]
> If tiles are incorrectly included/excluded, adjust `spatial_barcode_min_matches`
> in the run_mode configuration. Set to `0` for maximum inclusiveness during
> troubleshooting.

---

## 4. Stage 3: Pairwise Alignment

Register transcriptomic spatial coordinates to the tissue staining image.
This is a two-stage process: coarse alignment (global) then fine alignment
(per-tile using fiducial markers).

### Prerequisites

- Single h5ad from spacemake
- High-resolution `.tiff` tissue image

### Image Stitching (for multi-tile scans)

```bash
openst image_stitch \
    --microscope='keyence' \
    --imagej-bin=<fiji_path> \
    --image-indir=raw_data/imaging/<sample_id> \
    --image-out=imaging/Image_Stitched_Composite.tif
```

### Image Quality Enhancement (Optional)

```bash
openst image_preprocess \
    --image-in Image_Stitched_Composite.tif \
    --image-out Image_Stitched_Composite_Restored.tif \
    --device cuda
```

### Spatial Stitching

```bash
openst from_spacemake \
    --project-id <project_id> \
    --sample-id <sample_id> \
    spatial_stitch \
    --tile-coordinates <coordinate_file.csv>
```

### Merge Modalities

```bash
openst from_spacemake \
    --project-id <project_id> \
    --sample-id <sample_id> \
    merge_modalities \
    --image-in /path/to/image.tif
```

### Method A: Fully Automatic Alignment

```bash
openst from_spacemake \
    --project-id <project_id> \
    --sample-id <sample_id> \
    pairwise_aligner \
    --device cuda \
    --metadata alignment.json
```

### Method B: Semi-Automatic (Coarse Auto + Manual Fine)

```bash
# Step 1: Automatic coarse alignment
openst from_spacemake \
    --project-id <project_id> \
    --sample-id <sample_id> \
    pairwise_aligner \
    --only-coarse \
    --device cuda

# Step 2: Manual fine alignment via GUI
openst from_spacemake \
    --project-id <project_id> \
    --sample-id <sample_id> \
    manual_pairwise_aligner \
    --spatial-key obsm/spatial_pairwise_aligned_coarse \
    --image-key uns/spatial_pairwise_aligned/staining_image_transformed

# Step 3: Apply per-tile transformation from keypoints
openst from_spacemake \
    --project-id <project_id> \
    --sample-id <sample_id> \
    apply_transform \
    --keypoints-in keypoints.json \
    --spatial-key-in obsm/spatial_pairwise_aligned_coarse \
    --spatial-key-out obsm/spatial_manual_fine \
    --per-tile
```

### Manual GUI Workflow

1. Perform coarse alignment (non-tile-specific) and click "Apply to data"
2. Save transformed coordinates to `obsm/spatial_manual_coarse`
3. Re-render and select **3+ keypoint pairs** per tile (using fiducial markers)
4. Save keypoints to `keypoints.json`
5. Apply `apply_transform --per-tile` to generate fine-aligned coordinates

### Output Spatial Keys in h5ad

| Key | Description |
|-----|-------------|
| `obsm/spatial` | Original unaligned coordinates |
| `obsm/spatial_pairwise_aligned_coarse` | Coarse alignment result |
| `obsm/spatial_pairwise_aligned_fine` | Fine alignment (automatic) |
| `obsm/spatial_manual_fine` | Fine alignment (manual) |
| `uns/spatial_pairwise_aligned/staining_image_transformed` | Aligned image |

### Troubleshooting Parameters

| Parameter | Effect |
|-----------|--------|
| `--rescale-factor-coarse/fine` | Higher = lower resolution, more global features |
| `--threshold-counts-coarse/fine` | Higher = fewer spots in pseudoimage |
| `--ransac-coarse-residual-threshold` | RANSAC matching threshold |
| `--ransac-coarse-max-trials` | Maximum RANSAC iterations |

### HTML Report Generation

```bash
openst report \
    --metadata=alignment.json \
    --html-out=alignment_report.html
```

> [!TIP]
> If fiducial markers are invisible (thick tissue >10 um), use morphological
> features as alignment landmarks, but expect reduced sub-cellular precision.

---

## 5. Stage 4: Segmentation & Single-Cell Quantification

Segment the tissue image into individual cells and aggregate transcriptomic
data per cell.

### Step 1: Cell Segmentation

```bash
openst from_spacemake \
    --project-id <project_id> \
    --sample-id <sample_id> \
    segment \
    --model HE_cellpose_rajewsky \
    --image-in 'uns/spatial_pairwise_aligned/staining_image_transformed' \
    --mask-out 'uns/spatial/staining_image_mask' \
    --dilate-px 10 \
    --device cuda
```

### Segmentation Parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| `--model` | Segmentation model | `HE_cellpose_rajewsky` (custom H&E model) |
| `--dilate-px` | Radial dilation (pixels) to approximate cytoplasm | `10` |
| `--diameter` | Expected cell diameter | Model default |
| `--device` | Compute device | `cuda` for GPU |
| `--rna-segment` | RNA-based segmentation mode | flag |

Alternative models: Cellpose presets (`cyto2`, `nuclei`) or user-trained models.

### Step 2: Multi-Scale Segmentation (Optional)

For tissues with heterogeneous cell sizes:

```bash
# Large-cell segmentation
openst from_spacemake \
    --project-id <project_id> \
    --sample-id <sample_id> \
    segment \
    --model HE_cellpose_rajewsky \
    --mask-out uns/spatial/staining_image_mask_large \
    --dilate-px 50 \
    --diameter 50

# Merge masks (first mask has priority)
openst segment_merge \
    --h5-in spatial_stitched_spots.h5ad \
    --mask-in uns/spatial/staining_image_mask uns/spatial/staining_image_mask_large \
    --mask-out uns/spatial/staining_image_mask_combined
```

### Step 3: Preview Segmentation (QC)

```bash
openst from_spacemake \
    --project-id <project_id> \
    --sample-id <sample_id> \
    preview \
    --image-keys uns/spatial/staining_image uns/spatial/staining_image_mask
```

Opens an interactive Napari window. Convert the mask layer to a "label layer"
for distinct random colors per cell.

### Step 4: Transcript Assignment

Aggregate transcripts to segmented cells:

```bash
# After automatic alignment
openst from_spacemake \
    --project-id <project_id> \
    --sample-id <sample_id> \
    transcript_assign \
    --spatial-key obsm/spatial_pairwise_aligned_fine \
    --mask-in uns/spatial_pairwise_aligned/staining_image_transformed

# After manual alignment
openst from_spacemake \
    --project-id <project_id> \
    --sample-id <sample_id> \
    transcript_assign \
    --spatial-key obsm/spatial_manual_fine \
    --mask-in uns/spatial/staining_image_mask
```

> [!CAUTION]
> The cell with `cell_ID_mask == 0` corresponds to **background**. Always
> remove it before downstream analysis:
> ```python
> adata = adata[adata.obs.cell_ID_mask != 0].copy()
> ```

---

## 6. Stage 5: 3D Reconstruction (Optional)

Register serial tissue sections into a 3D volume using spatial transcriptomics
data.

### Prerequisites

```bash
mamba install -c conda-forge stim
pip install stimwrap scanpy
```

### Step 1: Create N5 Container

```python
import stimwrap as st

st.set_bin_path("/path/to/conda/bin")
container_path = "openst_3d.n5"
sections = ["section_1.h5ad", "section_2.h5ad", "section_3.h5ad"]
st.add_slices(container=container_path, inputs=sections)
```

### Step 2: Select Genes for Alignment

```python
import scanpy as sc

adata = sc.read_h5ad(sections[0])
sc.pp.highly_variable_genes(adata, flavor="seurat", min_mean=0.2, max_mean=0.6)
hvg_genes = adata.var_names[adata.var['highly_variable']].tolist()
```

### Step 3: Pairwise Registration

```python
st.align_pairs(
    container=container_path,
    max_epsilon=0,
    genes=hvg_genes[:10],
    range=2,
    scale=0.03,
    num_threads=8,
    overwrite=True,
)
```

### Step 4: Global Alignment

```python
st.align_global(container=container_path, skip_icp=True)
```

### Step 5: Apply Transformations and Concatenate

```python
import anndata as ad

container = st.Container(container_path)
for z_axis, dataset_name in zip(section_numbers, container.get_dataset_names()):
    with container.get_dataset(dataset_name, mode="r+") as dataset:
        dataset.apply_save_transform(
            transformation="model_sift",
            locations='spatial',
            destination='spatial_transform_sift',
            z_coord=z_axis,
        )

# Concatenate for 3D visualization
adata_3d = ad.concat(
    [ad.read_h5ad(path) for path in sections],
    join='inner', index_unique="_",
)

# 3D visualization
sc.pl.embedding(
    adata_3d,
    color=['gene_of_interest'],
    projection='3d',
    basis='spatial_transform_sift_plotting',
)
```

### 3D Registration Parameters

| Parameter | Value | Notes |
|-----------|-------|-------|
| `scale` | `0.03` | For segmented cell-level data; `0.0003` for hexbin |
| `genes` | ~10 HVGs | Genes with clear spatial patterns |
| `range` | 2-3 | Sections above/below to consider |
| `skip_icp` | `True` | Recommended for initial run |
| `num_threads` | 8 | Parallel threads |

---

## 7. Stage 6: Downstream Exploratory Analysis

Standard single-cell analysis on the spatially-resolved cell-by-gene matrix.

```python
import scanpy as sc
import matplotlib.pyplot as plt

# 1. Load and remove background
adata = sc.read_h5ad("by_cell.h5ad")
adata = adata[adata.obs.cell_ID_mask != 0].copy()

# 2. QC metrics
adata.var["mt"] = adata.var_names.str.startswith("MT-")  # "mt-" for mouse
sc.pp.calculate_qc_metrics(adata, qc_vars=["mt"], inplace=True)

# 3. QC visualization
fig, axs = plt.subplots(1, 3, figsize=(12, 4))
import seaborn as sns
sns.histplot(adata.obs["total_counts"], kde=False, bins=60, ax=axs[0])
sns.histplot(adata.obs["pct_counts_mt"], kde=False, bins=60, ax=axs[1])
sns.histplot(adata.obs["n_genes_by_counts"], kde=False, bins=60, ax=axs[2])
plt.tight_layout()
plt.show()

# 4. Cell filtering
sc.pp.filter_cells(adata, min_counts=75)
sc.pp.filter_cells(adata, max_counts=10000)
adata = adata[adata.obs["pct_counts_mt"] < 30].copy()
sc.pp.filter_genes(adata, min_cells=10)

# 5. Normalization
sc.pp.normalize_total(adata, inplace=True)
sc.pp.log1p(adata)

# 6. Feature selection
sc.pp.highly_variable_genes(adata, flavor="seurat", n_top_genes=2000)

# 7. Dimensionality reduction and clustering
sc.pp.pca(adata)
sc.pp.neighbors(adata)
sc.tl.leiden(adata, resolution=0.9, key_added="leiden")

# 8. Spatial visualization
sc.pl.spatial(adata, img_key=None, color=["leiden", "total_counts"], spot_size=40)
```

### Dataset-Specific QC Thresholds

| Parameter | Mouse Hippocampus | E13 Mouse Head |
|-----------|-------------------|----------------|
| `min_counts` | 75 | 250 |
| `max_counts` | 10000 | 10000 |
| MT prefix | `mt-` | `mt-` |
| MT threshold | < 30% | < 20% |
| Leiden resolution | 0.9 | 0.9 |

> [!WARNING]
> Standard scRNA-seq normalization may not be optimal for spatial data due to
> spatial autocorrelation. Consider validating normalization choices against
> known spatial gene expression patterns.

---

## 8. CLI Command Reference

| Subcommand | Purpose |
|------------|---------|
| `flowcell_map` | Process BCL to barcode-coordinate maps (full flow cell) |
| `barcode_preprocessing` | Convert spatial barcode raw data (single tile) |
| `image_stitch` | Stitch multi-tile microscopy images |
| `image_preprocess` | CUT-based image restoration |
| `spatial_stitch` | Merge h5ad tile objects |
| `merge_modalities` | Combine spatial locations and images |
| `pairwise_aligner` | Automatic coarse + fine alignment |
| `manual_pairwise_aligner` | GUI for manual alignment |
| `apply_transform` | Apply precomputed transformation |
| `segment` | Cellpose-based cell segmentation |
| `segment_merge` | Combine multiple segmentation masks |
| `transcript_assign` | Aggregate transcripts to segmented cells |
| `pseudoimage` | Generate pseudoimages from RNA |
| `preview` | Napari interactive visualization |
| `report` | Generate HTML reports |
| `from_spacemake` | Wrapper for spacemake integration |
| `to_3d_registration` | Prepare data for 3D registration |
| `from_3d_registration` | Convert 3D registration results back to h5ad |

---

## Best Practices Summary

1. **Flow cell processing**: Always use `openst flowcell_map` over per-tile processing to ensure cross-tile barcode deduplication.
2. **spacemake output**: Use files with `spatial_beads_` naming only; avoid `mesh`/`hexagon`/`circle` aggregated files.
3. **Alignment strategy**: Start with fully automatic alignment. Fall back to semi-automatic (coarse auto + manual fine) if automatic fine alignment fails.
4. **Fiducial markers**: Minimum 3 keypoint pairs per tile for manual fine alignment.
5. **Segmentation model**: Default `HE_cellpose_rajewsky` is trained for fresh-frozen H&E. Train custom models for other tissue/staining types.
6. **Dilation**: Default `--dilate-px 10` approximates cytoplasm from nuclear segmentation. Use `0` for strict nuclear boundaries; increase for larger cells.
7. **Background removal**: Always filter `cell_ID_mask == 0` before downstream analysis.
8. **3D registration**: Use ~10 HVGs with clear spatial patterns; scale `0.03` for cell-level data.
9. **GPU acceleration**: Use `--device cuda` for `pairwise_aligner`, `segment`, and `image_preprocess`.
10. **System resources**: Minimum 128 GB RAM recommended for full pipeline execution.
