---
id: sc_best_practices_regulatory_and_communication
name: "SC Best Practices: Gene Regulatory Networks & Cell-Cell Communication"
description: |
  Gene regulatory network inference and cell-cell communication analysis for
  single-cell data. Covers pySCENIC/GRNBoost2 for GRN inference, and LIANA,
  CellPhoneDB, NicheNet, CellChat for intercellular signaling. Based on
  Chapters 20-21 of SC Best Practices.
tags: [GRN, pySCENIC, GRNBoost2, cell-communication, LIANA, CellPhoneDB, NicheNet, CellChat, best-practices]
---

# SC Best Practices: Gene Regulatory Networks & Cell-Cell Communication (Chapters 20-21)

Computational approaches for inferring transcription factor regulatory programs
and intercellular communication networks from single-cell transcriptomic data.

**Source**: [https://www.sc-best-practices.org](https://www.sc-best-practices.org) (Chapters 20-21)

---

## 1. Gene Regulatory Networks (Chapter 20)

Gene regulatory network (GRN) inference aims to reconstruct the regulatory
relationships between transcription factors (TFs) and their target genes from
single-cell expression data. The core idea: if a TF and its targets are
coexpressed across cells, a regulatory link may exist.

### Conceptual Framework

GRN inference typically follows a two-step approach:

1. **Infer TF-target links**: Identify coexpression relationships between TFs and candidate target genes using statistical or machine-learning methods.
2. **Prune by motif analysis**: Retain only those links supported by TF binding motif enrichment in the promoter regions of predicted targets.

This two-step design reduces false positives from spurious correlations.

### Methods Overview

| Method | Algorithm | Key Feature |
|--------|-----------|-------------|
| GRNBoost2 | Gradient boosting (tree-based) | Fast, scalable regulatory link inference |
| GENIE3 | Random forest | Original method; slower than GRNBoost2 |
| pySCENIC | GRNBoost2 + motif pruning + AUCell | Full pipeline: inference, pruning, scoring |
| CellOracle | GRN + perturbation simulation | Links GRN to cell state transitions |

### pySCENIC Workflow

pySCENIC is the standard pipeline for GRN inference from scRNA-seq data. It
combines three steps: coexpression-based inference (GRNBoost2), motif-based
pruning, and regulon activity scoring (AUCell).

#### Prerequisites

```python
# Required databases (download once)
# 1. Ranking databases (.feather files) - for motif enrichment
#    e.g., hg38_10kbp_up_10kbp_down_full_tx_v10_clust.genes_vs_motifs.rankings.feather
# 2. Motif annotation file
#    e.g., motifs-v10-nr.hgnc-m0.001-o0.0.tbl
# 3. Curated TF list
#    e.g., allTFs_hg38.txt (from pySCENIC resources)
```

#### Step 1: GRN Inference with GRNBoost2

```python
from arboreto.algo import grnboost2
import pandas as pd

# Prepare expression matrix (cells x genes)
# Use raw or lightly normalized counts (NOT log-transformed)
expression_data = pd.DataFrame(
    adata.X.toarray() if hasattr(adata.X, 'toarray') else adata.X,
    index=adata.obs_names,
    columns=adata.var_names
)

# Load curated TF list
tf_list = [line.strip() for line in open('allTFs_hg38.txt')]
tf_list = [tf for tf in tf_list if tf in expression_data.columns]

# Run GRNBoost2 (infer TF-target adjacencies)
adjacencies = grnboost2(
    expression_data,
    tf_names=tf_list,
    verbose=True
)

# adjacencies is a DataFrame with columns: TF, target, importance
print(f"Inferred {len(adjacencies)} TF-target links")
```

> [!TIP]
> GRNBoost2 is significantly faster than GENIE3 and produces comparable results.
> For large datasets (>50k cells), consider subsampling to 10k-20k cells for
> the GRN inference step -- regulon activity can still be scored on all cells.

#### Step 2: Motif Enrichment and Pruning

```python
from pyscenic.rnkdb import FeatherRankingDatabase
from pyscenic.prune import prune2df, df2regulons
from ctxcore.rnkdb import FeatherRankingDatabase as RankingDatabase

# Load ranking databases
db_fnames = [
    'hg38_10kbp_up_10kbp_down_full_tx_v10_clust.genes_vs_motifs.rankings.feather'
]
dbs = [RankingDatabase(fname=fname) for fname in db_fnames]

# Convert adjacencies to modules (TF -> candidate targets)
from pyscenic.utils import modules_from_adjacencies
modules = list(modules_from_adjacencies(adjacencies, expression_data))

# Prune modules by motif enrichment
motif_annotations_fname = 'motifs-v10-nr.hgnc-m0.001-o0.0.tbl'
df = prune2df(dbs, modules, motif_annotations_fname)

# Convert pruned results to regulons
regulons = df2regulons(df)
print(f"Discovered {len(regulons)} regulons after motif pruning")
```

#### Step 3: Regulon Activity Scoring (AUCell)

```python
from pyscenic.aucell import aucell

# Score regulon activity per cell
auc_mtx = aucell(
    expression_data,
    regulons,
    num_workers=4
)

# Store in AnnData
adata.obsm['X_aucell'] = auc_mtx.values

# Visualize regulon activity on UMAP
import scanpy as sc
for regulon_name in ['PAX6(+)', 'SOX2(+)', 'NEUROD1(+)']:
    if regulon_name in auc_mtx.columns:
        adata.obs[regulon_name] = auc_mtx[regulon_name].values
        sc.pl.umap(adata, color=regulon_name, cmap='viridis')
```

#### Complete pySCENIC Workflow (Simplified)

```python
from arboreto.algo import grnboost2
from pyscenic.rnkdb import FeatherRankingDatabase
from pyscenic.prune import prune2df, df2regulons
from pyscenic.aucell import aucell
from pyscenic.utils import modules_from_adjacencies

# Step 1: GRN inference
adjacencies = grnboost2(expression_data, tf_names=tf_list, verbose=True)

# Step 2: Motif enrichment and pruning
modules = list(modules_from_adjacencies(adjacencies, expression_data))
df = prune2df(dbs, modules, motif_annotations_fname)
regulons = df2regulons(df)

# Step 3: AUCell scoring
auc_mtx = aucell(expression_data, regulons)
```

> [!WARNING]
> pySCENIC is computationally expensive. For a dataset of 20k cells and 20k genes,
> expect the GRNBoost2 step to take 30-60 minutes and the pruning step 10-30 minutes.
> The command-line interface (`pyscenic grn`, `pyscenic ctx`, `pyscenic aucell`)
> is recommended for large-scale runs as it is more memory-efficient than the
> Python API.

### GRN Best Practices

1. **Use curated TF lists**: Always restrict GRN inference to known transcription factors from curated databases (e.g., AnimalTFDB, Lambert et al. 2018). Running on all genes dramatically increases false positives and compute time.
2. **Input data**: Use raw or lightly normalized counts (NOT log-transformed). GRNBoost2 and GENIE3 work best with count-level data.
3. **Validate inferred regulons**: Cross-reference discovered regulons with known biology (literature, ChIP-seq databases like ENCODE). A regulon that does not match any known TF function warrants skepticism.
4. **Run multiple GRN methods**: No single method captures all regulatory relationships. Compare results from pySCENIC, CellOracle, or other methods for cross-validation.
5. **Subsample large datasets**: GRN inference scales poorly with cell number. Subsample to 10k-20k representative cells, then score regulon activity on the full dataset.
6. **Report regulon statistics**: Document the number of regulons discovered, the number of target genes per regulon, and the AUCell score distributions.

> [!TIP]
> Use the AUCell matrix for downstream clustering or dimensionality reduction.
> Clustering cells by regulon activity (rather than gene expression) can reveal
> regulatory states that are not apparent from expression alone.

---

## 2. Cell-Cell Communication (Chapter 21)

Cell-cell communication analysis infers intercellular signaling interactions
from single-cell expression data by evaluating the coexpression of known
ligand-receptor pairs across cell types.

### Conceptual Framework

The basic approach: for each pair of cell types, check whether a ligand is
expressed in the sender cell type and the corresponding receptor is expressed
in the receiver cell type. Significance is assessed against permutation-based
null distributions or statistical models.

### Three Main Approaches

1. **Ligand-receptor databases**: CellPhoneDB, CellChat -- directly test L-R pair coexpression across cell types.
2. **Rank aggregation**: LIANA -- aggregates results from multiple methods for robust consensus predictions.
3. **Prior knowledge networks**: NicheNet -- links extracellular ligands to intracellular target gene regulation in receiver cells.

### Tools Comparison

| Method | Approach | Strengths | Limitations |
|--------|----------|-----------|-------------|
| LIANA | Rank aggregation | Combines multiple methods; most robust first-pass | Does not model intracellular signaling |
| CellPhoneDB | L-R database | Multi-subunit complex support; statistical testing | Database-dependent; no downstream targets |
| NicheNet | Prior knowledge network | Links ligands to intracellular target genes | Requires DEG computation; complex setup |
| CellChat | Pathway-based | Pathway-level communication patterns; rich visualization | Can overfit with small cell populations |

### Workflow 1: LIANA (Recommended First Pass)

LIANA (LIgand-receptor ANalysis frAmework) is recommended as a starting point
because it aggregates results from multiple established methods (CellPhoneDB,
NATMI, Connectome, logFC, SingleCellSignalR) into a consensus ranking.

```python
import liana as li

# Ensure adata has cell type annotations and is normalized (not raw)
# adata.obs must contain the groupby column

# Run rank aggregation (default: all supported methods)
li.mt.rank_aggregate(
    adata,
    groupby='cell_type',
    use_raw=False,
    verbose=True
)

# Results are stored in adata.uns['liana_res']
liana_results = adata.uns['liana_res']
print(liana_results.head())
# Columns include: source, target, ligand_complex, receptor_complex,
# lr_means, cellphone_pvals, specificity_rank, magnitude_rank, ...
```

#### LIANA Visualization

```python
# Dotplot of top interactions
li.pl.dotplot(
    adata=adata,
    colour='lr_means',
    size='cellphone_pvals',
    inverse_size=True,        # smaller dots = higher p-value
    source_labels=['Macrophage', 'Fibroblast'],  # filter senders
    target_labels=['T cell', 'Epithelial'],       # filter receivers
    top_n=20,
    orderby='cellphone_pvals',
    orderby_ascending=True,
    figure_size=(10, 6)
)
```

```python
# Filter for significant interactions
significant = liana_results[
    (liana_results['cellphone_pvals'] <= 0.01) &
    (liana_results['lr_means'] > 0.5)
].sort_values('magnitude_rank')

print(f"Significant interactions: {len(significant)}")
print(significant[['source', 'target', 'ligand_complex', 'receptor_complex',
                    'lr_means', 'cellphone_pvals']].head(20))
```

> [!TIP]
> LIANA's rank aggregation is more robust than any individual method because it
> reduces method-specific biases. Start with LIANA for an unbiased overview,
> then follow up with NicheNet or CellChat for specific hypotheses.

### Workflow 2: NicheNet (Ligand Activity Inference)

NicheNet goes beyond simple L-R coexpression by linking extracellular ligands
to intracellular signaling and target gene regulation in receiver cells. This
makes it particularly useful for understanding HOW a ligand affects the
receiving cell.

```python
# NicheNet workflow (conceptual steps)
# NicheNet is primarily implemented in R; Python wrappers exist but are less mature.

# Step 1: Define sender and receiver cell types
# sender_celltypes = ['Macrophage', 'Fibroblast']
# receiver_celltype = 'T cell'

# Step 2: Identify differentially expressed genes (DEGs) in receiver cells
# These DEGs represent the "response" you want to explain with ligand activity.
# sc.tl.rank_genes_groups(adata, groupby='condition', reference='control',
#                          groups=['treatment'])

# Step 3: Estimate ligand activity
# For each candidate ligand (expressed in sender cells), NicheNet scores how
# well the ligand's known downstream targets overlap with the observed DEGs.
# Scoring metric: AUPR (Area Under Precision-Recall curve)

# Step 4: Infer target genes for top ligands
# Identify which DEGs in the receiver are predicted targets of the top ligands.

# Step 5: Visualize ligand-receptor-target network
# Produce heatmaps of ligand activity, ligand-target links, and
# ligand-receptor associations.
```

```r
# NicheNet in R (standard implementation)
library(nichenetr)
library(Seurat)

# Load NicheNet prior knowledge networks
ligand_target_matrix <- readRDS("ligand_target_matrix.rds")
lr_network <- readRDS("lr_network.rds")
weighted_networks <- readRDS("weighted_networks.rds")

# Define sender/receiver
receiver <- "CD8_T"
sender_celltypes <- c("Macrophage", "DC")

# Get expressed genes per cell type (threshold: 10% of cells)
expressed_genes_receiver <- get_expressed_genes(receiver, srat, pct = 0.10)
expressed_genes_sender <- unique(unlist(lapply(sender_celltypes, function(ct) {
    get_expressed_genes(ct, srat, pct = 0.10)
})))

# Define gene set of interest (DEGs in receiver)
geneset_oi <- DEG_list  # from differential expression analysis

# Predict ligand activities
ligand_activities <- predict_ligand_activities(
    geneset = geneset_oi,
    background_expressed_genes = expressed_genes_receiver,
    ligand_target_matrix = ligand_target_matrix,
    potential_ligands = potential_ligands
)

# Top ligands ranked by AUPR
best_upstream_ligands <- ligand_activities %>%
    arrange(-aupr_corrected) %>%
    pull(test_ligand) %>%
    head(20)
```

> [!WARNING]
> NicheNet requires pre-computed prior knowledge networks (ligand-target matrix,
> L-R network, weighted signaling network). These must be downloaded separately
> from the NicheNet repository. The results are heavily influenced by the
> quality and completeness of these prior knowledge databases.

### Workflow 3: CellChat

CellChat models cell-cell communication at the pathway level and provides
rich visualization capabilities.

```python
# CellChat is primarily an R package
# Python interface available via cellchatpy (limited)
```

```r
library(CellChat)

# Create CellChat object
cellchat <- createCellChat(object = srat, group.by = "cell_type")

# Set ligand-receptor database
CellChatDB <- CellChatDB.human  # or CellChatDB.mouse
cellchat@DB <- CellChatDB

# Preprocessing
cellchat <- subsetData(cellchat)
cellchat <- identifyOverExpressedGenes(cellchat)
cellchat <- identifyOverExpressedInteractions(cellchat)

# Inference
cellchat <- computeCommunProb(cellchat)
cellchat <- filterCommunication(cellchat, min.cells = 10)
cellchat <- computeCommunProbPathway(cellchat)
cellchat <- aggregateNet(cellchat)

# Visualization
netVisual_circle(cellchat@net$count, vertex.weight = table(cellchat@idents))
netVisual_bubble(cellchat, sources.use = c(1, 2), targets.use = c(3, 4))
```

### Expression Thresholds and Filtering

A critical parameter in cell-cell communication analysis is the expression
threshold for considering a gene "expressed" in a cell type.

```python
# Recommended: gene expressed in >= 10% of cells per cell type
min_pct = 0.10

# Calculate per-cell-type expression percentages
import pandas as pd
import numpy as np

pct_expressed = pd.DataFrame(index=adata.var_names)
for ct in adata.obs['cell_type'].unique():
    mask = adata.obs['cell_type'] == ct
    subset = adata[mask]
    if hasattr(subset.X, 'toarray'):
        pct = (subset.X.toarray() > 0).mean(axis=0)
    else:
        pct = (subset.X > 0).mean(axis=0)
    pct_expressed[ct] = pct

# Filter genes by expression threshold
expressed_genes = pct_expressed.index[pct_expressed.max(axis=1) >= min_pct]
print(f"Genes passing {min_pct*100:.0f}% threshold: {len(expressed_genes)}")
```

> [!WARNING]
> Setting the expression threshold too low (e.g., 1%) inflates false positives
> by including genes detected in very few cells. Setting it too high (e.g., 30%)
> may miss biologically relevant but sparsely expressed ligands or receptors.
> The 10% threshold is a widely used default, but adjust based on your dataset's
> depth and sparsity.

### Cell-Cell Communication Best Practices

1. **Use multiple methods for cross-validation**: No single method captures all communication patterns. Run LIANA (aggregation) for a broad overview, then follow up with NicheNet for mechanistic hypotheses or CellChat for pathway-level patterns.

2. **Apply domain knowledge to filter predictions**: Computational predictions are hypotheses, not confirmed interactions. Filter results based on known biology (e.g., is the ligand secreted? Are sender and receiver cell types spatially proximate?).

3. **Expression threshold**: Require a gene to be expressed in at least 10% of cells per cell type. This reduces noise from dropout while retaining biologically meaningful signals.

4. **Run condition-specific analysis**: Perform communication analysis within each condition separately, not across conditions. Comparing communication patterns between conditions (e.g., healthy vs. disease) is valid; pooling conditions into a single analysis confounds the results.

    ```python
    # Correct: analyze conditions separately
    for condition in adata.obs['condition'].unique():
        adata_cond = adata[adata.obs['condition'] == condition].copy()
        li.mt.rank_aggregate(adata_cond, groupby='cell_type', use_raw=False)
        # Store or compare results
    ```

5. **Focus on hypotheses for experimental validation**: The primary value of cell-cell communication analysis is hypothesis generation. Prioritize predictions that can be experimentally tested (e.g., ligand blocking, receptor knockdown, co-culture assays).

6. **Integrate with spatial data if available**: If spatial transcriptomics or imaging data is available, use spatial co-localization to validate predicted interactions. Cell types that are not spatially proximate are unlikely to communicate via short-range signals (e.g., juxtacrine or paracrine signaling).

    ```python
    # Example: filter interactions by spatial proximity (if spatial data available)
    # Use squidpy for spatial co-occurrence analysis
    import squidpy as sq
    sq.gr.co_occurrence(adata_spatial, cluster_key='cell_type')
    sq.pl.co_occurrence(adata_spatial, cluster_key='cell_type')
    ```

> [!TIP]
> For a comprehensive analysis, combine LIANA results with NicheNet's ligand
> activity predictions. Use LIANA to identify the most consistently predicted
> L-R pairs across methods, then use NicheNet to investigate the downstream
> effects of top ligands on receiver cell gene expression.

---

## 3. Integrating GRN and Communication Analysis

GRN inference and cell-cell communication analysis are complementary:

- **Cell-cell communication** identifies WHICH signals are sent between cell types.
- **GRN inference** reveals HOW those signals are processed within receiving cells.

### Combined Workflow

```python
# 1. Identify key ligands from communication analysis (LIANA/NicheNet)
top_ligands = significant[['source', 'ligand_complex']].drop_duplicates().head(10)

# 2. Identify TFs activated in receiver cells (pySCENIC)
receiver_cells = adata[adata.obs['cell_type'] == 'T cell']
# AUCell scores for receiver cells reveal active regulons

# 3. Link: which TFs in receiver cells are downstream of the identified ligands?
# Use NicheNet's ligand-target matrix or pathway databases (e.g., Reactome)
# to connect extracellular signals to intracellular TF activation.
```

> [!TIP]
> CellOracle explicitly models the link between GRN perturbation and cell state
> changes. If you need to predict the effect of perturbing a specific signaling
> pathway on cell fate, CellOracle is a natural choice for integrating GRN and
> communication results.

---

## Best Practices Summary

1. **GRN inference**: Use pySCENIC as the standard pipeline. Always use curated TF lists. Validate regulons against known biology. Subsample large datasets for the inference step.
2. **Cell-cell communication**: Start with LIANA for robust consensus predictions. Follow up with NicheNet for mechanistic depth or CellChat for pathway-level patterns.
3. **Cross-validation**: Run multiple methods and focus on interactions or regulons that are consistently predicted across approaches.
4. **Experimental validation**: Treat all computational predictions as hypotheses. Design experiments to test the most biologically interesting and robust predictions.
5. **Condition-specific analysis**: Analyze each condition separately for communication analysis. Compare results across conditions rather than pooling.
6. **Spatial integration**: When spatial data is available, use co-localization to filter and prioritize predicted interactions.
