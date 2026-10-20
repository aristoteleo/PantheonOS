---
id: cell_type_annotation
name: Cell Type Annotation
description: |
  Approaches for annotating cell types in single-cell data using
  marker genes and reference-based methods.
tags: [annotation, cell types, markers, scanpy]
---

# Cell Type Annotation Strategies

Methods for assigning cell type labels to clusters in single-cell RNA-seq data.

## Approach Overview

| Method | When to Use | Pros | Cons |
|--------|-------------|------|------|
| Marker genes | Known tissue, well-defined types | Interpretable, flexible | Requires domain knowledge |
| Reference mapping | Good reference available | Objective, comprehensive | Dependent on reference quality |
| Automated tools | Quick exploration | Fast, unbiased | May miss rare types |

## 1. Marker Gene-Based Annotation

### Step 1: Find Cluster Markers

```python
import scanpy as sc
import pandas as pd
import matplotlib.pyplot as plt

# Check if .raw is populated (standard practice implies raw contains all genes)
# If raw exists, use it to ensure we search ALL genes, not just HVGs.
use_raw = True if adata.raw is not None else False

# Compute differentially expressed genes
# pts=True is required to calculate the percentage of cells expressing the gene
sc.tl.rank_genes_groups(
    adata, 
    groupby='leiden', 
    method='wilcoxon',
    pts=True,        
    use_raw=use_raw
)

# Quick visual check of top ranking genes
sc.pl.rank_genes_groups(adata, n_genes=10, sharey=False, show=False)
plt.savefig('cluster_markers.png', dpi=150, bbox_inches='tight')
plt.show()
```

### Step 2: Extract and Review Markers

```python
# 1. Extract the full results table
markers_df = sc.get.rank_genes_groups_df(adata, group=None)

# 2. Map 'pts' (percentage in group) and 'pts_rest' (percentage in rest)
# These are stored in adata.uns and need to be mapped to the dataframe
pts = adata.uns['rank_genes_groups']['pts']
pts_rest = adata.uns['rank_genes_groups']['pts_rest']

markers_df['pct_nz_group'] = markers_df.apply(
    lambda x: pts.loc[x['names'], x['group']], axis=1
)
markers_df['pct_nz_rest'] = markers_df.apply(
    lambda x: pts_rest.loc[x['names'], x['group']], axis=1
)

# 3. Apply Quality Filters
# LogFC > 0.5 (Significant upregulation)
# pct_nz_group > 0.25 (Expressed in >25% of cluster cells)
# pct_nz_rest < 0.3 (Specific, low background)
markers_filtered = markers_df[
    (markers_df['logfoldchanges'] > 0.5) & 
    (markers_df['pct_nz_group'] > 0.25) & 
    (markers_df['pct_nz_rest'] < 0.3) & 
    (markers_df['pvals_adj'] < 0.05)
].copy()

# 4. Remove Biological Noise (Mitochondrial, Ribosomal, etc.)
exclude_prefixes = ('MT-', 'RPS', 'RPL', 'MALAT1', 'HB') 
markers_filtered = markers_filtered[
    ~markers_filtered['names'].str.startswith(exclude_prefixes)
]

# 5. Get Top 5 Unique Markers per cluster for review
top_markers = markers_filtered.sort_values(
    ['group', 'logfoldchanges'], ascending=[True, False]
).groupby('group').head(5)

print("Top specific markers per cluster:")
print(top_markers[['group', 'names', 'logfoldchanges', 'pct_nz_group', 'pct_nz_rest']])

# 6. Convert to markers_dict format for visualization in Step 3
markers_dict = (
    top_markers
    .groupby('group')['names']
    .apply(list)
    .to_dict()
)
```

> [!NOTE]
> **Marker Gene Sources**:
> - **Data-driven** (above): Use DEG results directly. Best for exploratory analysis or novel tissues.
> - **Literature/Database**: Cross-reference the top markers with CellMarker, PanglaoDB, or tissue-specific publications for well-characterized tissues (e.g., PBMC, brain). This validates your data-driven markers. (Optional) You can inject Canonical Markers if you suspect a specific tissue type.

### Step 3: Visualize Markers

> [!TIP]
> **Diagonal Pattern**: Sort genes by their peak expression cluster, aligned with dendrogram order.

```python
# 1. Flatten and validate genes (check both .var and .raw for consistency)
all_genes = set(adata.var_names) | (set(adata.raw.var_names) if adata.raw else set())
flat_genes = list({g for genes in markers_dict.values() for g in genes if g in all_genes})

if not flat_genes:
    print("Warning: No valid markers found.")
else:
    # 2. Compute dendrogram and get visual cluster order
    sc.tl.dendrogram(adata, groupby='leiden')
    ordered_clusters = adata.uns['dendrogram_leiden']['categories_ordered']
    cluster_order = {c: i for i, c in enumerate(ordered_clusters)}
    
    # 3. Find peak expression cluster for each gene
    X = adata.raw[:, flat_genes].X if adata.raw else adata[:, flat_genes].X
    if hasattr(X, "toarray"): X = X.toarray()
    
    expr_df = pd.DataFrame(X, columns=flat_genes)
    expr_df['cluster'] = adata.obs['leiden'].values
    peak_cluster = expr_df.groupby('cluster').mean().idxmax()
    
    # 4. Sort genes by dendrogram cluster order (creates diagonal)
    sorted_genes = sorted(flat_genes, key=lambda g: (cluster_order.get(str(peak_cluster[g]), 999), g))
    
    # 5. Dotplot
    sc.pl.dotplot(adata, var_names=sorted_genes, groupby='leiden',
                  standard_scale='var', dendrogram=True, show=False)
    plt.savefig('markers_dotplot.png', bbox_inches='tight')
    plt.show()
```

### Step 4: Assign Annotations
> **TIP**: Reuse existing UMAP when only changing visualization (colors, title, legend).
> Compute new UMAP if analysis changed (different clustering resolution, batch correction, etc.).
```python
# Create mapping based on analysis of Step 2 and Step 3
cluster_to_celltype = {
    # '0': 'T cells',
    # '1': 'Monocytes',
    # '2': 'B cells',
    # ...
}

# Apply annotation
adata.obs['cell_type'] = adata.obs['leiden'].map(cluster_to_celltype)

# Handle unmapped clusters
if adata.obs['cell_type'].isnull().any():
    print("Warning: Some clusters were not assigned types. Filling with 'Unknown'.")
    adata.obs['cell_type'] = adata.obs['cell_type'].fillna('Unknown')

# Final Visualization
sc.pl.umap(adata, color='cell_type', title="Annotated Cell Types", legend_loc='right margin', show=False)
plt.savefig('annotation_umap.png', bbox_inches='tight')
plt.show()
```

## 2. Reference-Based Annotation

### Using CellTypist (Automated)

```python
import celltypist
from celltypist import models

# Download model (run once)
models.download_models(model='Immune_All_Low.pkl')

# Load model
model = models.Model.load('Immune_All_Low.pkl')

# Predict cell types
predictions = celltypist.annotate(
    adata,
    model=model,
    majority_voting=True,  # Smooth predictions over clusters
)

# Add predictions to adata
adata.obs['celltypist_label'] = predictions.predicted_labels.majority_voting
```

### Using scANVI (Transfer Learning)

```python
import scvi

# Train on reference
scvi.model.SCANVI.setup_anndata(
    adata_ref,
    labels_key='cell_type',
    unlabeled_category='Unknown',
)
model = scvi.model.SCANVI(adata_ref)
model.train()

# Transfer to query
scvi.model.SCANVI.setup_anndata(adata_query)
model_query = scvi.model.SCANVI.load_query_data(adata_query, model)
adata_query.obs['scANVI_pred'] = model_query.predict()
```

## 3. Create Annotation Table

```python
import pandas as pd

# Create annotation summary
annotation_table = pd.DataFrame({
    'Cluster': sorted(adata.obs['leiden'].unique()),
    'Cell Type': [cluster_to_celltype.get(c, 'Unknown') for c in sorted(adata.obs['leiden'].unique())],
    'n_cells': adata.obs.groupby('leiden').size().values,
    'Top Markers': ['GENE1, GENE2, GENE3' for _ in adata.obs['leiden'].unique()],
    'Confidence': ['High', 'Medium', 'High', ...]  # Your assessment
})

annotation_table.to_csv('cell_type_annotations.csv', index=False)
print(annotation_table.to_markdown())
```

## Quality Checks

After annotation, verify the quality of your cell type assignments.

> [!IMPORTANT]
> You should verify marker specificity before proceeding with downstream analysis.

### Validation Checklist

1. **Diagonal pattern check**: High expression should be on-diagonal (marker in its cluster)
2. **Off-diagonal contamination**: If markers appear in unrelated clusters, investigate:
   - Ambient RNA contamination (see Ambient RNA section in quality_control skill)
   - Doublet contamination
   - True biological co-expression
3. **Expression scale**: If one marker has much higher scale than others, 
   this may indicate ambient RNA contamination
4. **Dotplot axes**: Confirm y-axis=cell types / clusters, x-axis=genes (not swapped)
5. **UMAP plots**: For consistency, avoid unnecessary UMAP recomputation



### Verify Marker Specificity

```python
# Heatmap of marker expression
sc.pl.heatmap(
    adata,
    var_names=[m for markers in markers_dict.values() for m in markers],
    groupby='cell_type',
    cmap='viridis',
    dendrogram=True,
    show=False
)
plt.savefig('markers_heatmap.png', dpi=150, bbox_inches='tight')
plt.show()
```

### Check for Mixed Clusters

```python
# Look for clusters expressing multiple lineage markers
mixed_markers = {
    'T_cell': ['CD3D'],
    'B_cell': ['CD79A'],
    'Myeloid': ['CD14'],
}

for cluster in adata.obs['leiden'].unique():
    cluster_data = adata[adata.obs['leiden'] == cluster]
    print(f"\nCluster {cluster}:")
    for lineage, genes in mixed_markers.items():
        for gene in genes:
            if gene in cluster_data.var_names:
                expr = cluster_data[:, gene].X.mean()
                pct = (cluster_data[:, gene].X > 0).mean() * 100
                print(f"  {lineage} ({gene}): mean={expr:.2f}, %exp={pct:.1f}%")
```

### Action Required

- If contamination detected: Return to QC and apply correction
- If clusters cannot be distinguished: Consider merging
- Document your observations in the analysis report

## Tips

> [!TIP]
> - Always verify annotations with multiple marker genes
> - Consider sub-clustering for large heterogeneous clusters
> - Use literature to validate marker selections
> - For novel tissues, combine marker-based and reference-based approaches

> [!WARNING]
> - Marker genes can be dataset-specific
> - Some gene names differ between human and mouse (case sensitivity)
> - Rare populations may be missed or merged with larger clusters
