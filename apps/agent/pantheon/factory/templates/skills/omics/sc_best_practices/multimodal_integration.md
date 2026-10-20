---
id: sc_bp_multimodal_integration
name: "SC Best Practices: Multimodal Integration"
description: |
  Integration strategies for multi-modal single-cell data including
  paired and unpaired approaches.
tags: [multimodal, integration, MOFA, WNN, MultiVI, sc-best-practices]
---

# SC Best Practices: Multimodal Integration

Strategies and tools for integrating multi-modal single-cell data,
covering paired (same-cell) and unpaired (different-cell) integration
approaches, data structures, evaluation, and practical workflows.

**Source**: [https://www.sc-best-practices.org](https://www.sc-best-practices.org)

---

## 1. Integration Scenarios

### Paired Integration (Same Cells Measured)

Both modalities are measured from the same individual cells. Cell-level
correspondence is known.

| Technology | Modalities | Example |
|-----------|-----------|---------|
| 10x Multiome | RNA + ATAC | Chromatin accessibility + gene expression |
| CITE-seq | RNA + Protein | Surface proteins + gene expression |
| TEA-seq | RNA + ATAC + Protein | Triple-modal |
| SHARE-seq | RNA + ATAC | Combinatorial indexing-based |
| Perturb-seq | RNA + CRISPR guide | Perturbation + expression |

### Unpaired Integration (Different Cells)

Modalities are measured from different cells or samples. No cell-level
correspondence exists, only shared biological structure.

| Scenario | Example |
|----------|---------|
| Mosaic integration | RNA from sample A + ATAC from sample B |
| Technology bridging | Visium spatial + dissociated scRNA-seq |
| Cross-modality transfer | Reference atlas (RNA) + query (ATAC) |

### Integration Approach Decision Tree

```
Are modalities measured from the SAME cells?
  |
  +-- YES (Paired) --> Use: MOFA+, WNN, totalVI, MultiVI
  |                     Feature: Direct cell-level correspondence
  |
  +-- NO (Unpaired) --> Do you have a BRIDGE reference (paired data)?
        |
        +-- YES --> Use: Bridge integration, GLUE with reference
        |           Feature: Multi-omic reference links modalities
        |
        +-- NO --> Use: GLUE, diagonal integration, CCA
                   Feature: Shared features (gene names, gene activity scores)
```

---

## 2. MuData: Multi-Modal Data Structure

MuData (via the mudata/muon packages) extends AnnData to multi-modal experiments:

```python
import mudata as md
import scanpy as sc

# Create MuData from individual AnnData objects
adata_rna = sc.read_h5ad("rna_processed.h5ad")
adata_atac = sc.read_h5ad("atac_processed.h5ad")

mdata = md.MuData({"rna": adata_rna, "atac": adata_atac})

# Access modalities
print(mdata.mod['rna'])    # AnnData for RNA
print(mdata.mod['atac'])   # AnnData for ATAC

# Shared cell metadata
print(mdata.obs.head())    # Outer join of all modality obs

# Save multi-modal data
mdata.write("multimodal.h5mu")

# Read back
mdata = md.read("multimodal.h5mu")
```

### MuData Structure

```
MuData
  |-- .mod['rna']    -> AnnData (cells x genes)
  |-- .mod['atac']   -> AnnData (cells x peaks)
  |-- .mod['prot']   -> AnnData (cells x proteins)  [optional]
  |-- .obs           -> Shared cell metadata (outer join)
  |-- .obsm          -> Joint embeddings
  |-- .obsp          -> Joint graphs (e.g., WNN graph)
```

### Working with MuData

```python
import muon as mu

# Intersect cells across modalities (keep only shared cells)
mu.pp.intersect_obs(mdata)

# Filter genes/features per modality
mu.pp.filter_var(mdata['rna'], lambda x: x['n_cells_by_counts'] >= 3)

# Run modality-specific preprocessing
sc.pp.normalize_total(mdata['rna'], target_sum=1e4)
sc.pp.log1p(mdata['rna'])
sc.pp.highly_variable_genes(mdata['rna'], n_top_genes=3000)
sc.pp.pca(mdata['rna'], n_comps=30)
```

---

## 3. Paired Integration Methods

### MOFA+ (Multi-Omics Factor Analysis)

MOFA+ learns a set of latent factors that capture shared and
modality-specific variation:

```python
import muon as mu
from muon import atac as ac

# Prepare modalities
# RNA: log-normalized, HVGs selected, PCA computed
# ATAC: TF-IDF normalized or LSI

# Run MOFA+
mu.tl.mofa(
    mdata,
    n_factors=15,
    convergence_mode='slow',    # More accurate convergence
    use_obs='intersection',     # Use cells present in all modalities
    gpu_mode=True,              # Use GPU if available
)

# MOFA factors stored in mdata.obsm['X_mofa']
# Factor weights per modality in mdata.varm['LFs']

# Visualize factors
mu.pl.mofa(mdata)

# Identify factor-modality associations
# Factors with high variance explained in multiple modalities capture shared biology
# Factors specific to one modality capture modality-specific variation
mu.pl.variance_ratio(mdata)
```

### MOFA+ Factor Interpretation

```python
# Get variance explained per factor per modality
r2 = mdata.uns['mofa']['variance_explained']['r2_per_factor']

# Identify shared factors (high R2 in multiple modalities)
# Identify modality-specific factors (high R2 in only one modality)

# Get top feature weights per factor
weights_rna = mdata.varm['LFs_rna']   # Gene weights for RNA
weights_atac = mdata.varm['LFs_atac']  # Peak weights for ATAC
```

### WNN (Weighted Nearest Neighbors)

WNN constructs a joint cell-cell graph by weighting each modality per cell:

```python
import muon as mu

# Compute modality-specific embeddings
sc.pp.pca(mdata['rna'], n_comps=30)
sc.pp.pca(mdata['atac'], n_comps=30)  # Or use LSI for ATAC

# WNN: weighted combination of modality-specific graphs
mu.pp.neighbors(mdata, key_added='wnn')

# Joint UMAP and clustering
mu.tl.umap(mdata, neighbors_key='wnn')
mu.tl.leiden(mdata, neighbors_key='wnn', resolution=0.5, key_added='wnn_leiden')

# Visualize
mu.pl.embedding(mdata, basis='X_umap', color='wnn_leiden')
```

### WNN Modality Weights

WNN assigns per-cell weights to each modality. Inspecting these reveals
which modality is more informative for different cell populations:

```python
# Access modality weights
# Cells where ATAC is more informative will have higher ATAC weights
# (e.g., cell types with distinctive chromatin but similar transcriptomes)
```

### MultiVI (Joint RNA + ATAC)

MultiVI jointly models RNA and ATAC data using a variational autoencoder:

```python
import scvi

# Concatenate modalities into a single AnnData
# MultiVI expects a specific format
adata_multi = scvi.data.organize_multiome_anndatas(
    mdata['rna'], mdata['atac']
)

# Setup and train
scvi.model.MULTIVI.setup_anndata(
    adata_multi,
    batch_key='modality',
)

model = scvi.model.MULTIVI(
    adata_multi,
    n_latent=20,
    n_genes=mdata['rna'].n_vars,
    n_regions=mdata['atac'].n_vars,
)
model.train(max_epochs=500, early_stopping=True)

# Get joint latent representation
adata_multi.obsm['X_multivi'] = model.get_latent_representation()

# Cluster on joint embedding
sc.pp.neighbors(adata_multi, use_rep='X_multivi')
sc.tl.umap(adata_multi)
sc.tl.leiden(adata_multi, resolution=0.5)
```

### totalVI (Joint RNA + Protein)

For CITE-seq data, totalVI jointly models RNA and protein:

```python
import scvi

# Setup (see surface_protein.md for full workflow)
scvi.model.TOTALVI.setup_anndata(
    adata,
    protein_expression_obsm_key="protein_expression",
    layer="counts",
    batch_key="batch",
)

model = scvi.model.TOTALVI(adata, n_latent=20)
model.train(max_epochs=400, early_stopping=True)

adata.obsm['X_totalVI'] = model.get_latent_representation()
```

---

## 4. Unpaired / Mosaic Integration

### GLUE (Graph-Linked Unified Embedding)

GLUE uses a knowledge graph (e.g., gene regulatory connections) to link
features across modalities, enabling integration of unpaired data:

```python
import scglue

# Prepare individual modalities
# RNA: standard preprocessing
# ATAC: gene activity scores or peak matrix

# Build guidance graph (links genes to peaks via genomic proximity)
guidance = scglue.genomics.rna_atac_guidance(
    adata_rna.var,      # Gene annotations
    adata_atac.var,     # Peak annotations
    gtf="genes.gtf",   # Gene annotation file
)

# Configure and train GLUE
scglue.models.configure_dataset(
    adata_rna, "NormalDataset",
    use_highly_variable=True,
    use_layer="counts",
    use_rep="X_pca",
)
scglue.models.configure_dataset(
    adata_atac, "ATACDataset",
    use_highly_variable=True,
    use_rep="X_lsi",
)

glue = scglue.models.fit_SCGLUE(
    {"rna": adata_rna, "atac": adata_atac},
    guidance,
    fit_kws={"directory": "glue_model"},
)

# Get joint embedding
adata_rna.obsm["X_glue"] = glue.encode_data("rna", adata_rna)
adata_atac.obsm["X_glue"] = glue.encode_data("atac", adata_atac)

# Combine for joint analysis
import anndata as ad
adata_combined = ad.concat(
    [adata_rna, adata_atac],
    label="modality",
    keys=["rna", "atac"],
    join="outer",
)
adata_combined.obsm["X_glue"] = np.concatenate([
    adata_rna.obsm["X_glue"],
    adata_atac.obsm["X_glue"],
])
```

### Bridge Integration

Bridge integration uses a multi-omic reference dataset (where both
modalities are measured in the same cells) to connect unpaired datasets:

```
Bridge reference: Cells with BOTH RNA + ATAC (e.g., 10x Multiome)
    |
    +-- Query 1: scRNA-seq only (different cells)
    +-- Query 2: scATAC-seq only (different cells)
    |
    -> Bridge reference connects the two query datasets
       through shared latent space
```

```python
# In Seurat v5 (R):
# bridge <- FindBridgeIntegration(
#     object.list = list(rna_query, atac_query),
#     bridge = multiome_reference,
#     ...
# )

# In Python: Use GLUE with the bridge reference as part of the guidance graph
```

---

## 5. Method Comparison

### Paired Integration Methods

| Method | Modalities | Approach | Scalability | Key Strength |
|--------|-----------|----------|-------------|-------------|
| **MOFA+** | Any (2+) | Factor analysis | Medium | Interpretable factors; variance decomposition |
| **WNN** | Any (2+) | Weighted graph | Good | Per-cell modality weighting; simple |
| **MultiVI** | RNA + ATAC | VAE | Good | Handles missing data; probabilistic |
| **totalVI** | RNA + Protein | VAE | Good | Joint denoising; batch correction |
| **Cobolt** | RNA + ATAC | Multi-modal VAE | Good | Barcode-level integration |

### Unpaired Integration Methods

| Method | Approach | Requirements | Key Strength |
|--------|----------|-------------|-------------|
| **GLUE** | Graph-linked VAE | Feature-level guidance graph | No paired data needed |
| **Bridge integration** | Transfer via reference | Paired multi-omic reference | Leverages existing paired data |
| **Diagonal integration** | Shared features (gene activity) | Overlapping feature space | Simple; no special tools |
| **UnionCom** | Coupled NMF | No correspondence needed | Handles arbitrary modalities |

---

## 6. Evaluation: Is Multi-Modal Better?

### Clustering Quality Comparison

Always test whether multi-modal integration improves over single-modality analysis:

```python
from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score

# Ground truth labels (if available, e.g., from expert annotation)
ground_truth = adata.obs['cell_type_manual']

# Single-modality clustering (RNA only)
sc.pp.neighbors(mdata['rna'], use_rep='X_pca')
sc.tl.leiden(mdata['rna'], resolution=0.5, key_added='leiden_rna')
ari_rna = adjusted_rand_score(ground_truth, mdata['rna'].obs['leiden_rna'])
nmi_rna = normalized_mutual_info_score(ground_truth, mdata['rna'].obs['leiden_rna'])

# Multi-modal clustering (WNN)
mu.pp.neighbors(mdata, key_added='wnn')
mu.tl.leiden(mdata, neighbors_key='wnn', resolution=0.5, key_added='leiden_wnn')
ari_wnn = adjusted_rand_score(ground_truth, mdata.obs['leiden_wnn'])
nmi_wnn = normalized_mutual_info_score(ground_truth, mdata.obs['leiden_wnn'])

print(f"RNA only  - ARI: {ari_rna:.3f}, NMI: {nmi_rna:.3f}")
print(f"WNN joint - ARI: {ari_wnn:.3f}, NMI: {nmi_wnn:.3f}")
```

### Biological Validation

```python
# Check if multi-modal integration resolves cell types that RNA alone cannot
# Example: NK cells vs. CD8 T cells (similar RNA, different protein markers)

# RNA-only UMAP colored by CD56 (NK marker protein)
sc.pl.umap(mdata['rna'], color='leiden_rna', title='RNA only')

# WNN UMAP colored by same marker
sc.pl.embedding(mdata, basis='X_umap', color='leiden_wnn', title='WNN')
```

### When Multi-Modal Integration May Not Help

- One modality has very low quality (noise overwhelms signal)
- Cell types are already well-separated by a single modality
- Modalities capture redundant rather than complementary information
- Technical artifacts in one modality propagate to the joint embedding

> [!TIP]
> Always compare multi-modal results against single-modality baselines.
> Multi-modal integration should be used because it demonstrably improves
> biological resolution, not simply because multi-modal data is available.

---

## 7. Practical Workflow: 10x Multiome (RNA + ATAC)

```python
import scanpy as sc
import muon as mu
import mudata as md

# 1. Load multi-modal data
mdata = mu.read_10x_h5("filtered_feature_bc_matrix.h5")

# 2. Process RNA modality
rna = mdata.mod['rna']
rna.var_names_make_unique()
sc.pp.filter_genes(rna, min_cells=3)
rna.layers['counts'] = rna.X.copy()
sc.pp.normalize_total(rna, target_sum=1e4)
sc.pp.log1p(rna)
sc.pp.highly_variable_genes(rna, n_top_genes=3000)
sc.pp.pca(rna, n_comps=30)

# 3. Process ATAC modality
atac = mdata.mod['atac']
import snapatac2 as snap
snap.pp.select_features(atac)
snap.tl.spectral(atac)  # LSI-like dimensionality reduction

# 4. Joint integration (WNN)
mu.pp.neighbors(mdata, key_added='wnn')
mu.tl.umap(mdata, neighbors_key='wnn')
mu.tl.leiden(mdata, neighbors_key='wnn', resolution=0.5, key_added='wnn_leiden')

# 5. Visualize
mu.pl.embedding(mdata, basis='X_umap', color='wnn_leiden')

# 6. Save
mdata.write("multiome_integrated.h5mu")
```

---

## Best Practices Summary

1. **Choose the right integration strategy**: Paired data enables direct methods (MOFA+, WNN, MultiVI); unpaired data requires bridge references or graph-based approaches (GLUE).
2. **Use MuData for multi-modal data**: Store all modalities in a single `.h5mu` file for reproducibility and portability.
3. **Preprocess modalities independently first**: Each modality has its own normalization and feature selection requirements. Apply modality-specific preprocessing before integration.
4. **Evaluate against single-modality baselines**: Integration should demonstrably improve clustering quality or biological resolution.
5. **Inspect modality weights**: In WNN, check per-cell modality weights to understand which modality drives the joint embedding for different cell populations.
6. **Use MOFA+ for interpretability**: When understanding which factors are shared vs. modality-specific is important, MOFA+ provides explicit variance decomposition.
7. **Handle missing modalities carefully**: MultiVI and totalVI can handle cells with missing modalities; simpler methods (WNN) require all modalities per cell.
8. **Document integration parameters**: Record the method, number of latent dimensions, and key hyperparameters for reproducibility.
