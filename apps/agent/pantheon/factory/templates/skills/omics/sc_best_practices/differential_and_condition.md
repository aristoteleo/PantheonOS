---
id: differential_and_condition
name: "SC Best Practices: Differential & Condition Analysis"
description: |
  Part 5 of SC Best Practices (Chapters 16-19): Differential gene expression
  with pseudobulk methods, compositional analysis, gene set enrichment and
  pathway analysis, and perturbation modeling for single-cell data.
tags: [differential-expression, pseudobulk, compositional, gsea, pathway, perturbation, scanpy, edgeR, decoupler]
---

# Differential & Condition-Responsive Analysis

Covers Chapters 16-19 of the Single-Cell Best Practices guide: differential
gene expression, compositional analysis, gene set enrichment / pathway
analysis, and perturbation modeling.

**Source**: [https://www.sc-best-practices.org](https://www.sc-best-practices.org) (Chapters 16-19)

---

## 1. Differential Gene Expression (Chapter 16)

### The Pseudoreplication Problem

> [!WARNING]
> **Cell-level DE tests inflate false positives dramatically.** Treating each
> cell as an independent observation violates the assumption of independence
> because cells from the same donor/sample are correlated. This leads to
> thousands of spurious hits. **Always use pseudobulk with sample/donor as the
> unit of replication.**

### Recommended Approach: Pseudobulk + edgeR

The gold standard is to **aggregate counts per sample** (sum across cells
within each sample-condition combination), then apply bulk RNA-seq DE tools.

| Method | Framework | Notes |
|--------|-----------|-------|
| edgeR (QLF) | R | **Recommended**. Quasi-likelihood GLM, robust to outliers |
| DESeq2 | R | Good alternative, Wald test or LRT |
| MAST (with random effects) | R | Acceptable if donor is modeled as random effect |
| limma-voom | R | Fast, good for large designs |

**Thresholds**:
- FDR (adjusted p-value): **0.01**
- Log fold-change: **1.5** (absolute value)

### Step 1: Create Pseudobulk Samples

```python
import scanpy as sc
import pandas as pd
import numpy as np

# Define sample identity: combination of replicate/donor and condition
adata.obs["sample"] = [
    f"{rep}_{label}"
    for rep, label in zip(adata.obs["replicate"], adata.obs["label"])
]

# Verify sample-condition mapping
sample_info = (
    adata.obs.groupby("sample")[["replicate", "label"]]
    .first()
    .reset_index()
)
print(sample_info)
print(f"Samples per condition: {sample_info['label'].value_counts().to_dict()}")
```

> [!TIP]
> Ensure you have at least **3 biological replicates per condition**. With
> fewer replicates, statistical power is severely limited regardless of how
> many cells you sequence. Increasing cell count does NOT compensate for
> insufficient biological replicates.

### Step 2: Quick Cell-Level DE (Exploration Only)

Use cell-level tests for **exploratory ranking only**, never for final
reporting of DE genes.

```python
# Cell-level DE for quick exploration (DO NOT use for final results)
sc.tl.rank_genes_groups(adata, groupby="condition", method="t-test")
sc.pl.rank_genes_groups(adata, n_genes=20, sharey=False)

# Volcano-style overview
result = sc.get.rank_genes_groups_df(adata, group=None)
print(result.head(20))
```

> [!WARNING]
> The results from `sc.tl.rank_genes_groups` with cell-level methods (t-test,
> Wilcoxon) are **not valid for publication**. They are useful only for a
> quick visual overview. Always follow up with pseudobulk analysis below.

### Step 3: Pseudobulk DE with edgeR (Recommended)

```r
# ── R: edgeR quasi-likelihood GLM (recommended pseudobulk DE) ──

library(edgeR)
library(scater)

# Assume 'counts_matrix' is the aggregated pseudobulk count matrix
#   rows = genes, columns = samples
# Assume 'sample_meta' has columns: sample, group (condition), replicate

# Create DGEList
y <- DGEList(counts = counts_matrix, samples = sample_meta)

# Filter lowly expressed genes
keep <- filterByExpr(y, group = sample_meta$group)
y <- y[keep, , keep.lib.sizes = FALSE]

# Normalize
y <- calcNormFactors(y)

# Design matrix: model condition + replicate (blocking variable)
design <- model.matrix(~ 0 + group + replicate, data = sample_meta)

# Estimate dispersion
y <- estimateDisp(y, design, robust = TRUE)

# Quasi-likelihood fit
fit <- glmQLFit(y, design, robust = TRUE)

# Define contrast: stimulated vs control
contrasts <- makeContrasts(groupstim - groupctrl, levels = design)

# Test
qlf <- glmQLFTest(fit, contrast = contrasts)

# Results
results <- topTags(qlf, n = Inf, sort.by = "PValue")$table
sig_genes <- results[results$FDR < 0.01 & abs(results$logFC) > 1.5, ]
cat(sprintf("Significant DE genes (FDR<0.01, |logFC|>1.5): %d\n", nrow(sig_genes)))
```

### Step 3 (Alternative): DESeq2 Pseudobulk

```r
# ── R: DESeq2 pseudobulk DE ──

library(DESeq2)

dds <- DESeqDataSetFromMatrix(
  countData = counts_matrix,
  colData   = sample_meta,
  design    = ~ replicate + group
)

dds <- DESeq(dds)
res <- results(dds, contrast = c("group", "stim", "ctrl"),
               alpha = 0.01)

sig <- subset(res, padj < 0.01 & abs(log2FoldChange) > 1.5)
cat(sprintf("DESeq2 significant genes: %d\n", nrow(sig)))
```

### Step 3 (Alternative): MAST with Random Effects

If you need cell-level resolution (e.g., for rare cell types with too few
cells to pseudobulk), MAST can model donor as a random effect:

```r
# ── R: MAST with donor random effect ──

library(MAST)

# sca: SingleCellAssay object
# ngeneson: scaled number of detected genes (cellular detection rate)
zlmCond <- zlm(~ ngeneson + group + (1 | replicate), sca = sca)

# Likelihood ratio test for the 'group' coefficient
summaryCond <- summary(zlmCond, doLRT = "groupstim")
summaryDt <- summaryCond$datatable
fcHurdle <- merge(
  summaryDt[contrast == "groupstim" & component == "H", .(primerid, `Pr(>Chisq)`)],
  summaryDt[contrast == "groupstim" & component == "logFC", .(primerid, coef, ci.hi, ci.lo)],
  by = "primerid"
)
fcHurdle[, fdr := p.adjust(`Pr(>Chisq)`, "fdr")]
```

> [!TIP]
> **Method selection guide**:
> - **>= 3 replicates per condition, enough cells per sample**: Use edgeR or DESeq2 pseudobulk (best statistical properties).
> - **Rare cell type with very few cells per sample**: Use MAST with donor random effect.
> - **Exploratory / hypothesis generation**: Cell-level Wilcoxon in scanpy is acceptable, but always label results as preliminary.

### Step 4: Aggregate Counts in Python (for export to R)

```python
import anndata
import scipy.sparse as sp

def create_pseudobulk(adata, sample_col="sample", layer="counts"):
    """Aggregate raw counts by sample for pseudobulk DE."""
    # Use raw counts
    X = adata.layers[layer] if layer in adata.layers else adata.X

    samples = adata.obs[sample_col].unique()
    pb_counts = []
    pb_meta = []

    for s in samples:
        mask = adata.obs[sample_col] == s
        counts_sum = np.array(X[mask].sum(axis=0)).flatten()
        pb_counts.append(counts_sum)
        # Take first row metadata for this sample
        meta = adata.obs.loc[mask].iloc[0]
        pb_meta.append(meta)

    pb_matrix = np.vstack(pb_counts).T  # genes x samples
    pb_meta_df = pd.DataFrame(pb_meta).reset_index(drop=True)

    return pb_matrix, pb_meta_df, adata.var_names.tolist()


pb_counts, pb_meta, gene_names = create_pseudobulk(adata)
print(f"Pseudobulk matrix: {pb_counts.shape[0]} genes x {pb_counts.shape[1]} samples")

# Export for R
pd.DataFrame(pb_counts, index=gene_names, columns=pb_meta["sample"]).to_csv("pseudobulk_counts.csv")
pb_meta.to_csv("pseudobulk_meta.csv", index=False)
```

---

## 2. Compositional Analysis (Chapter 17)

Changes in cell type proportions between conditions must account for
**compositionality**: proportions across cell types sum to 1, so an increase
in one type necessarily means a relative decrease in others.

### Why Standard Tests Fail

> [!WARNING]
> Using t-tests or Wilcoxon tests on raw cell type proportions ignores the
> compositional constraint. This can lead to false associations, especially
> when one cell type dominates the change and drags others along.

### scCODA: Bayesian Compositional Analysis

```python
# pip install sccoda
from sccoda.util import comp_ana as mod
from sccoda.util import cell_composition_data as dat

# Prepare compositional data from AnnData
# Requires: cell type labels and sample/condition metadata
cell_counts = (
    adata.obs
    .groupby(["sample", "cell_type"])
    .size()
    .unstack(fill_value=0)
)

# Add condition metadata
sample_cond = adata.obs.groupby("sample")["condition"].first()
cell_counts["condition"] = cell_counts.index.map(sample_cond)

# Create scCODA data object
sccoda_data = dat.from_pandas(cell_counts, covariate_columns=["condition"])

# Run scCODA model
model = mod.CompositionalAnalysis(
    sccoda_data,
    formula="condition",
    reference_cell_type="automatic"  # scCODA selects reference automatically
)
result = model.sample_hmc()

# Credible effects (non-zero = significant change)
print(result.summary())
result.set_fdr(est_fdr=0.05)
print(result.credible_effects())
```

> [!TIP]
> - **Minimum replicates**: scCODA requires at least **3 samples per condition**
>   for reliable posterior inference. More replicates improve power.
> - **Reference cell type**: scCODA uses one cell type as reference. The
>   "automatic" setting picks the least variable type, but you can set it
>   manually if you have a known stable population.
> - **Alternatives**: tascCODA (tree-aggregated), Dirichlet regression,
>   propeller (limma-based).

### Quick Proportion Visualization

```python
import matplotlib.pyplot as plt

# Stacked bar plot of cell type proportions per sample
props = cell_counts.drop(columns=["condition"])
props_norm = props.div(props.sum(axis=1), axis=0)

props_norm.plot(kind="bar", stacked=True, figsize=(12, 5))
plt.ylabel("Proportion")
plt.title("Cell Type Composition per Sample")
plt.legend(bbox_to_anchor=(1.05, 1), loc="upper left")
plt.tight_layout()
plt.savefig("composition_barplot.png", dpi=150, bbox_inches="tight")
plt.show()
```

---

## 3. Gene Set Enrichment & Pathway Analysis (Chapter 18)

### Test Types

| Type | Question | Example Tools |
|------|----------|---------------|
| **Competitive** | Is this gene set ranked higher than background? | GSEA, fgsea, decoupler `run_gsea` |
| **Self-contained** | Are genes in this set DE (regardless of others)? | limma `fry`, ROAST |
| **Activity scoring** | What is per-cell pathway activity? | AUCell, decoupler `run_aucell` |

> [!TIP]
> **Gene set size filtering**: Always filter gene sets to keep only those with
> **15-500 genes**. Sets that are too small lack statistical power; sets that
> are too large are uninformative.

### Approach 1: GSEA on Pseudobulk DE Statistics (Recommended)

Run GSEA on the ranked gene statistics from your pseudobulk DE analysis:

```python
import decoupler as dc
import pandas as pd

# Load gene sets (MSigDB example)
gene_sets = dc.get_resource("MSigDB", organism="human")

# Filter to Hallmark or GO:BP
gene_sets_h = gene_sets[gene_sets["collection"] == "hallmark"]

# Ranked statistics from pseudobulk DE (logFC or signed -log10 p-value)
# 'de_results' is a DataFrame with gene names as index and 'logFC' column
ranked_stats = de_results["logFC"].dropna().sort_values(ascending=False)

# Run GSEA
gsea_results = dc.run_gsea(
    mat=ranked_stats.values.reshape(1, -1),
    net=gene_sets_h,
    source="geneset",
    target="genesymbol",
    verbose=True
)

# Inspect top enriched pathways
gsea_df = gsea_results[1].T  # p-values
gsea_es = gsea_results[0].T  # enrichment scores
gsea_combined = pd.concat([gsea_es, gsea_df], axis=1)
gsea_combined.columns = ["ES", "pvalue"]
gsea_combined = gsea_combined.sort_values("pvalue")
print(gsea_combined.head(20))
```

### Approach 2: Per-Cell Pathway Activity with AUCell

Compute pathway activity scores for every cell (useful for UMAP overlay):

```python
import decoupler as dc

# Per-cell pathway activity via AUCell
dc.run_aucell(
    adata,
    net=gene_sets_h,
    source="geneset",
    target="genesymbol",
    use_raw=False
)

# Results stored in adata.obsm['aucell_estimate']
# Visualize top pathway on UMAP
adata.obs["Hallmark_Hypoxia"] = adata.obsm["aucell_estimate"]["HALLMARK_HYPOXIA"]

import scanpy as sc
sc.pl.umap(adata, color=["Hallmark_Hypoxia", "condition"], cmap="RdBu_r")
```

> [!TIP]
> **AUCell vs GSEA**:
> - Use **GSEA** when you want a single enrichment statistic per condition
>   comparison (e.g., "is pathway X enriched in stim vs ctrl?").
> - Use **AUCell** when you want per-cell scores (e.g., "which cells have
>   high hypoxia activity?" or to correlate pathway activity with pseudotime).

### Approach 3: Pseudobulk Pathway Analysis with limma fry (R)

```r
# ── R: Self-contained pathway test with limma fry ──

library(limma)

# 'expr' = log-CPM normalized pseudobulk expression matrix (from edgeR voom)
# 'pathways' = named list of gene sets (list of character vectors)

# Map gene sets to row indices
idx <- ids2indices(pathways, rownames(expr))

# Run fry (self-contained test)
fry_results <- fry(expr, index = idx, design = design, contrast = contrasts[, 1])

# Filter significant pathways
sig_pathways <- fry_results[fry_results$FDR < 0.05, ]
sig_pathways <- sig_pathways[order(sig_pathways$FDR), ]
head(sig_pathways, 20)
```

### Approach 4: decoupler for Transcription Factor Activity

```python
import decoupler as dc

# Get CollecTRI regulons (TF-target interactions)
regulons = dc.get_collectri(organism="human")

# Infer TF activity per cell
dc.run_ulm(adata, net=regulons, use_raw=False)

# Results in adata.obsm['ulm_estimate']
# Compare TF activity between conditions
dc.plot_volcano(
    adata.obsm["ulm_estimate"],
    contrast="stim",
    name="TF Activity",
    top=10
)
```

> [!WARNING]
> Gene set enrichment results are highly sensitive to:
> 1. **The background gene list** -- always use ALL detected genes, not just HVGs.
> 2. **The ranking metric** -- logFC vs. signed p-value can give different results.
> 3. **Multiple testing** -- correct across all tested gene sets (FDR < 0.05).

---

## 4. Perturbation Modeling (Chapter 19)

### MILO: Differential Abundance on KNN Neighborhoods

MILO tests for differential abundance in neighborhoods of the KNN graph,
avoiding the need to define discrete clusters first.

```python
import milopy.core as milo
import scanpy as sc

# Compute KNN graph (if not already done)
sc.pp.neighbors(adata, n_neighbors=30, n_pcs=30)

# Assign cells to neighborhoods
milo.make_nhoods(adata, prop=0.1)

# Count cells per sample in each neighborhood
milo.count_nhoods(adata, sample_col="sample")

# Test for differential abundance
milo.DA_nhoods(
    adata,
    design="~ condition",
    model_contrasts="conditionstim-conditionctrl"
)

# Visualize DA neighborhoods on UMAP
milo.annotate_nhoods(adata, anno_col="cell_type")
milo.plot_nhood_graph(adata, alpha=0.1)
```

> [!TIP]
> MILO is particularly powerful because it:
> - Does not require pre-defined cell type labels.
> - Captures subtle shifts in continuous cell states.
> - Properly accounts for biological replication via a GLM framework.
> - Use `prop=0.1` to `0.2` for neighborhood size (covers ~10-20% of cells).

### Augur: Prioritize Responding Cell Types

Augur uses a classification approach to rank which cell types are most
transcriptionally affected by a perturbation:

```python
import augur

# Run Augur prioritization
results = augur.predict(
    adata,
    label_col="condition",
    cell_type_col="cell_type",
    n_folds=3,
    n_subsample=50,
    random_state=42
)

# AUC per cell type (higher = more responsive to perturbation)
auc_df = results["summary_metrics"]
print(auc_df.sort_values("auc", ascending=False))

# Lollipop plot of cell type responsiveness
augur.plot.lollipop(results)
```

> [!WARNING]
> Augur AUC scores reflect separability between conditions, not the
> magnitude or direction of change. A high AUC means the cell type is
> transcriptionally distinct between conditions, but you still need DE
> analysis to identify specific genes and directions.

### Experimental Design Considerations

> [!TIP]
> **The single most impactful design choice is the number of biological
> replicates, NOT the number of cells.** Adding more cells per sample
> improves power only marginally after a few thousand cells, while adding
> more donors/replicates improves power substantially.
>
> **Guidelines**:
> - Minimum **3 biological replicates** per condition (absolute floor).
> - **5-6 replicates** recommended for robust DE detection.
> - **2,000-5,000 cells per sample** is generally sufficient.
> - Budget permitting: prioritize more samples over deeper sequencing.

### Multiplexed Perturbation Experiments (Perturb-seq)

For CRISPR screen / Perturb-seq data:

```python
# Standard workflow for perturbation screens
# 1. Assign guide identities to cells
# 2. Filter for high-confidence assignments
# 3. Compare each perturbation to non-targeting controls

# Example: simple DE per perturbation
perturbations = adata.obs["guide_identity"].unique()
for pert in perturbations:
    if pert == "non-targeting":
        continue
    mask = adata.obs["guide_identity"].isin([pert, "non-targeting"])
    adata_sub = adata[mask].copy()
    sc.tl.rank_genes_groups(adata_sub, groupby="guide_identity",
                            reference="non-targeting", method="wilcoxon")
    result = sc.get.rank_genes_groups_df(adata_sub, group=pert)
    print(f"\n{pert}: top DE genes")
    print(result.head(10))
```

> [!WARNING]
> For Perturb-seq, pseudobulk aggregation should be done per **guide**
> (treating each guide as a "sample") when biological replicates are
> unavailable. Be aware that batch effects between sequencing lanes can
> confound results -- always include batch in the model.

---

## Summary Decision Tree

```
Experimental question
|
|-- "Which genes differ between conditions?"
|   --> Pseudobulk DE (edgeR QLF or DESeq2)
|       Aggregate by sample, FDR < 0.01, |logFC| > 1.5
|
|-- "Do cell type proportions change?"
|   --> scCODA (Bayesian compositional analysis)
|       Requires >= 3 replicates per condition
|
|-- "Which pathways are enriched?"
|   --> GSEA on pseudobulk DE stats (decoupler / fgsea)
|   --> Per-cell: AUCell for activity scoring
|   --> Self-contained: limma fry
|
|-- "Which cell types respond most?"
|   --> Augur (classification-based prioritization)
|
|-- "Where in the manifold do changes occur?"
|   --> MILO (differential abundance on KNN graph)
```

> [!TIP]
> These analyses are complementary. A typical condition-response study would
> run pseudobulk DE, compositional analysis, and pathway enrichment in
> sequence. Use MILO and Augur as additional lenses to capture effects that
> cluster-level analyses might miss.
