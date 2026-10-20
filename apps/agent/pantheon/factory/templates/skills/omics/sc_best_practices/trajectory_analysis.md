---
id: sc_best_practices_trajectory_analysis
name: "SC Best Practices: Trajectory Analysis"
description: |
  Trajectory analysis methods from SC Best Practices (Part 4, Chapters 13-15).
  Covers pseudotemporal ordering (DPT, Palantir, PAGA, Slingshot), RNA velocity
  (scVelo dynamical model, CellRank fate prediction), and CRISPR-based lineage
  tracing (Cassiopeia). Includes method selection guidance and validation strategies.
tags: [trajectory, pseudotime, rna-velocity, lineage-tracing, scvelo, cellrank, cassiopeia, paga, best-practices]
---

# Trajectory Analysis (SC Best Practices Part 4)

Comprehensive reference for trajectory analysis in single-cell genomics, covering
pseudotemporal ordering, RNA velocity, and lineage tracing. Based on Chapters 13-15
of the [SC Best Practices](https://www.sc-best-practices.org) guide.

## When to Use

- Studying cell differentiation and developmental processes
- Inferring directionality of cell state transitions
- Identifying branching points in cell fate decisions
- Reconstructing lineage hierarchies from CRISPR barcoding experiments
- Predicting terminal cell fates from current transcriptomic states

## Methods Overview

| Method | Category | Tool | Best For |
|--------|----------|------|----------|
| Diffusion Pseudotime (DPT) | Pseudotime | scanpy | General trajectory, robust and widely used |
| Palantir | Pseudotime | palantir | Multi-fate probabilistic trajectories |
| PAGA | Topology | scanpy | Trajectory topology and connectivity |
| Slingshot | Pseudotime | R/slingshot | Simple trajectories, manifold-based |
| scVelo (dynamical) | RNA Velocity | scvelo | Directional dynamics, gene-resolved kinetics |
| CellRank | Fate Prediction | cellrank | Combining velocity with other signals |
| Cassiopeia | Lineage Tracing | cassiopeia | CRISPR/Cas9 barcoding tree reconstruction |

---

## Chapter 13: Pseudotemporal Ordering

Pseudotime methods order cells along a trajectory based on transcriptomic similarity,
assigning each cell a value representing its progress through a biological process.

### Method Selection

| Method | Strengths | Limitations |
|--------|-----------|-------------|
| DPT | Robust to noise, handles branching, widely used | Requires root cell specification |
| Palantir | Probabilistic fate assignment, entropy-based | Computationally intensive on large datasets |
| PAGA | Reveals topology before ordering, scalable | Coarse-grained (cluster-level connectivity) |
| Slingshot | Good for simple linear/bifurcating trajectories | Struggles with complex topologies |

> [!TIP]
> Use [dynguidelines](https://dynverse.org/users/3-user-guide/2-guidelines/) to
> systematically select the best trajectory inference method for your data. The choice
> depends on trajectory topology (linear, bifurcation, tree, graph), dataset size, and
> whether you have prior knowledge of start/end states.

### Workflow: PAGA + Diffusion Pseudotime

The recommended approach is to first use PAGA to identify trajectory topology, then
apply DPT for fine-grained pseudotemporal ordering.

#### Step 1: PAGA Topology Inference

```python
import scanpy as sc
import numpy as np

# Prerequisites: PCA, neighbors, clustering must be computed
# sc.pp.pca(adata)
# sc.pp.neighbors(adata, n_neighbors=15, n_pcs=40)
# sc.tl.leiden(adata, resolution=0.8)

# Compute PAGA graph
sc.tl.paga(adata, groups="leiden")

# Visualize PAGA topology
sc.pl.paga(adata, color="leiden", edge_width_scale=0.5,
           threshold=0.1, show=False)

# PAGA-initialized layout for better trajectory visualization
sc.tl.draw_graph(adata, init_pos="paga")
sc.pl.draw_graph(adata, color=["leiden", "cell_type"],
                 legend_loc="right margin")
```

> [!TIP]
> The `threshold` parameter in `sc.pl.paga()` controls which edges are shown. Lower
> values show more connections. Adjust this to reveal meaningful connectivity while
> hiding spurious edges. Start with 0.1 and increase if the graph is too dense.

#### Step 2: Diffusion Pseudotime

```python
# Compute diffusion map
sc.tl.diffmap(adata, n_comps=15)

# Set root cell based on biological knowledge
# Option 1: By cell type annotation
adata.uns["iroot"] = np.flatnonzero(adata.obs["cell_type"] == "HSC")[0]

# Option 2: By marker gene expression (highest expression of stemness marker)
# adata.uns["iroot"] = np.argmax(adata[:, "SOX2"].X.toarray().flatten())

# Compute diffusion pseudotime
sc.tl.diffusion_pseudotime(adata)

# Visualize pseudotime on UMAP
sc.pl.umap(adata, color=["dpt_pseudotime", "cell_type"], cmap="viridis")
```

> [!WARNING]
> The choice of root cell strongly influences pseudotime values. Always set the root
> based on biological knowledge (known progenitor or stem cell population), not
> arbitrarily. Verify that the resulting pseudotime ordering agrees with known marker
> gene dynamics (e.g., stemness markers decrease, maturation markers increase).

#### Step 3: Validate Pseudotime with Gene Dynamics

```python
# Plot marker gene expression along pseudotime
# Expect: progenitor markers high early, maturation markers high late
sc.pl.scatter(
    adata,
    x="dpt_pseudotime",
    y=["SOX2", "NES", "DCX", "RBFOX3"],  # Adjust to your system
    color="cell_type",
    save="_pseudotime_markers.pdf"
)

# Gene expression trends along PAGA paths
sc.pl.paga_path(
    adata,
    nodes=["Stem", "Progenitor", "Immature", "Mature"],  # Adjust to your clusters
    keys=["SOX2", "NES", "DCX", "RBFOX3"],
    save="_paga_path_genes.pdf"
)
```

### Alternative: Palantir (Probabilistic Multi-Fate Trajectories)

```python
import palantir

# Run diffusion maps
dm_res = palantir.utils.run_diffusion_maps(adata)

# Determine terminal states
terminal_states = palantir.utils.determine_multiscale_space(adata)

# Run Palantir (requires specifying a start cell)
start_cell = adata.obs_names[adata.obs["cell_type"] == "HSC"][0]
pr_res = palantir.core.run_palantir(
    adata, start_cell,
    num_waypoints=500,
    terminal_states=terminal_states
)

# Results include:
# - pr_res.pseudotime: pseudotime values
# - pr_res.entropy: differentiation potential (high = multipotent)
# - pr_res.branch_probs: probability of reaching each terminal state
```

### Best Practices for Pseudotemporal Ordering

1. **Use PAGA first** to identify trajectory topology before applying ordering methods
2. **Set root cell** based on biological knowledge, not computationally
3. **Validate with known biology**: check that marker gene dynamics match expectations
4. **Compare multiple methods**: agreement across methods increases confidence
5. **Subsample large datasets** (>100k cells) for initial exploration, then project back

---

## Chapter 14: RNA Velocity

RNA velocity leverages the ratio of unspliced to spliced mRNA to predict the future
transcriptional state of each cell, providing directionality to trajectories.

### Prerequisites

RNA velocity requires spliced and unspliced count matrices. These are typically
generated from BAM files using Velocyto or STARsolo.

```bash
# Generate loom file with spliced/unspliced counts using Velocyto
velocyto run10x /path/to/cellranger_output /path/to/genome.gtf

# Alternative: STARsolo generates these counts during alignment
# STAR --soloType CB_UMI_Simple --soloFeatures Gene Velocyto ...
```

> [!WARNING]
> RNA velocity requires spliced/unspliced count matrices from the original BAM files.
> If you only have a processed count matrix without spliced/unspliced layers, you
> cannot run RNA velocity. Use diffusion pseudotime instead, or re-process from
> BAM files with Velocyto or STARsolo.

### Workflow: scVelo Dynamical Model

The dynamical model is recommended over the stochastic model because it fits
per-gene kinetic parameters and captures transient cell states more accurately.

#### Step 1: Load and Preprocess

```python
import scvelo as scv

# If velocity data is in a separate loom file, merge with existing adata
# ldata = scv.read("velocyto_output.loom")
# adata = scv.utils.merge(adata, ldata)

# Verify spliced/unspliced layers exist
assert "spliced" in adata.layers, "Missing spliced layer"
assert "unspliced" in adata.layers, "Missing unspliced layer"

# Filter and normalize
scv.pp.filter_and_normalize(
    adata,
    min_shared_counts=20,
    n_top_genes=2000
)

# Compute moments (first and second order) for velocity estimation
scv.pp.moments(adata, n_pcs=30, n_neighbors=30)
```

#### Step 2: Fit Dynamical Model and Compute Velocity

```python
# Recover full transcription dynamics per gene
# This fits splicing kinetics (transcription rate, splicing rate, degradation rate)
scv.tl.recover_dynamics(adata)

# Compute velocity using dynamical model
scv.tl.velocity(adata, mode="dynamical")

# Build velocity graph (cell-cell transition probabilities)
scv.tl.velocity_graph(adata)
```

#### Step 3: Visualize Velocity

```python
# Streamline plot on UMAP (most informative visualization)
scv.pl.velocity_embedding_stream(
    adata, basis="umap",
    color="cell_type",
    save="velocity_stream.pdf"
)

# Arrow plot for individual cell velocities
scv.pl.velocity_embedding(
    adata, basis="umap",
    arrow_length=3, arrow_size=2,
    color="cell_type"
)

# Latent time (velocity-based pseudotime)
scv.tl.latent_time(adata)
scv.pl.scatter(adata, color="latent_time", cmap="viridis",
               save="latent_time.pdf")
```

#### Step 4: Assess Velocity Quality

```python
# Velocity confidence (how well velocity explains transitions)
scv.tl.velocity_confidence(adata)
scv.pl.scatter(adata, color=["velocity_confidence", "velocity_length"],
               save="velocity_confidence.pdf")

# Per-gene velocity fit quality
scv.pl.velocity(adata, var_names=["SOX2", "DCX", "RBFOX3"],
                save="gene_velocity_fit.pdf")

# Velocity consistency score
# High values = consistent velocity direction among neighbors
print(f"Mean velocity confidence: {adata.obs['velocity_confidence'].mean():.3f}")
```

> [!TIP]
> Always check the per-gene phase portraits (`scv.pl.velocity()`) for key marker
> genes. A well-fit gene should show a clear clockwise loop in the spliced-unspliced
> phase space. If most genes show poor fits, consider: (1) increasing `n_top_genes`,
> (2) using stochastic mode instead, or (3) verifying spliced/unspliced count quality.

### CellRank: Velocity-Informed Fate Prediction

CellRank combines RNA velocity with transcriptomic similarity to compute more robust
cell fate probabilities and identify driver genes of fate decisions.

```python
import cellrank as cr

# Create CellRank kernel from velocity
vk = cr.kernels.VelocityKernel(adata)
vk.compute_transition_matrix()

# Optionally combine with connectivity kernel for robustness
ck = cr.kernels.ConnectivityKernel(adata)
ck.compute_transition_matrix()
combined_kernel = 0.8 * vk + 0.2 * ck

# Estimator for terminal state identification
estimator = cr.estimators.GPCCA(combined_kernel)
estimator.fit(cluster_key="cell_type")

# Identify terminal and initial states
estimator.predict_terminal_states()
estimator.predict_initial_states()

# Compute fate probabilities toward each terminal state
estimator.compute_fate_probabilities()
cr.pl.fate_probabilities(adata)

# Identify driver genes for specific fate transitions
drivers = estimator.compute_lineage_drivers(
    lineages=["Neuron"],  # Adjust to your terminal states
    return_drivers=True
)
```

### Best Practices for RNA Velocity

1. **Use dynamical model** (`mode="dynamical"`) over stochastic for more accurate kinetics
2. **Always check gene-level fits**: poor phase portraits indicate unreliable velocity
3. **Verify with known biology**: velocity arrows should point in biologically expected directions
4. **Consider CellRank** for fate prediction, as it is more robust than raw velocity
5. **Be cautious of artifacts**: steady-state populations may show spurious velocity

---

## Chapter 15: Lineage Tracing

Lineage tracing uses heritable DNA barcodes (e.g., CRISPR/Cas9 scars) to reconstruct
the clonal relationships between cells, providing ground-truth lineage information
that transcriptomic methods cannot.

### Overview

| Component | Description |
|-----------|-------------|
| Character matrix | cells x target sites; values = indel identities (0 = uncut) |
| Priors | Prior probability of each indel state (from empirical data) |
| Tree solvers | Algorithms to reconstruct phylogenetic trees from character matrices |
| Downstream analyses | Expansion detection, plasticity scoring, fate coupling |

### Quality Metrics for Lineage Tracing Data

Before tree reconstruction, assess data quality:

```python
import cassiopeia as cas
import pandas as pd

# Load character matrix (cells x target sites)
# Values: 0 = uncut, -1 = missing, positive integers = specific indels
cm = pd.read_csv("character_matrix.csv", index_col=0)

# Key quality metrics
n_cells = cm.shape[0]
n_targets = cm.shape[1]

# Percent of sites that are uniquely cut (diversity of indels)
# Higher is better; >5% indicates sufficient barcode diversity
percent_unique = cm.apply(lambda col: col[col > 0].nunique()).mean() / cm.shape[0] * 100

# Cut rate: fraction of sites that have been edited
cut_rate = (cm > 0).sum().sum() / (cm >= 0).sum().sum()

# Missing data rate
missing_rate = (cm == -1).sum().sum() / cm.size

print(f"Cells: {n_cells}")
print(f"Target sites: {n_targets}")
print(f"Percent unique indels: {percent_unique:.1f}%")
print(f"Cut rate: {cut_rate:.2%}")
print(f"Missing data rate: {missing_rate:.2%}")
```

> [!WARNING]
> Low barcode diversity (`percent_unique` < 5%) severely limits tree resolution. This
> can occur when: (1) too few target sites are used, (2) the Cas9 system has low
> editing efficiency, or (3) indel outcomes are highly skewed toward a few dominant
> states. Consider using indel priors to mitigate the effect of low-diversity states.

### Workflow: Cassiopeia Tree Reconstruction

```python
import cassiopeia as cas

# Create CassiopeiaTree object
tree = cas.data.CassiopeiaTree(
    character_matrix=cm,
    priors=priors  # Dict mapping state -> probability (optional but recommended)
)

# Choose and apply a solver
# Option 1: Greedy solver (fast, good for large datasets)
greedy_solver = cas.solver.VanillaGreedySolver()
greedy_solver.solve(tree)

# Option 2: Hybrid solver (greedy top-down, then exact for small subproblems)
# hybrid_solver = cas.solver.HybridSolver(
#     top_solver=cas.solver.VanillaGreedySolver(),
#     bottom_solver=cas.solver.ILPSolver(),
#     cell_cutoff=200
# )
# hybrid_solver.solve(tree)

# Option 3: Neighbor joining (distance-based, handles missing data)
# nj_solver = cas.solver.NeighborJoiningSolver(
#     dissimilarity_function=cas.solver.dissimilarity.weighted_hamming_distance
# )
# nj_solver.solve(tree)
```

### Downstream Analysis: Clonal Expansion

```python
# Detect clonal expansions (clades that are significantly larger than expected)
cas.tl.compute_expansion_pvalues(
    tree,
    min_clade_size=int(0.15 * tree.n_cell),  # Minimum 15% of cells
    copy=False
)

# Visualize tree with expansion annotations
cas.pl.plot_matplotlib(
    tree,
    meta_data=["cell_type"],
    clade_colors=tree.get_expansion_clades(),
    save="lineage_tree_expansions.pdf"
)
```

### Downstream Analysis: Plasticity Scoring

Plasticity scoring quantifies how frequently cell type transitions occur along
the lineage tree (small parsimony score).

```python
# Compute small parsimony score for cell type annotation
# Lower score = more coherent (cells of same type are clonally related)
# Higher score = more plastic (frequent type switching along lineage)
parsimony = cas.tl.score_small_parsimony(
    tree,
    meta_item="cell_type"
)
print(f"Parsimony score for cell_type: {parsimony}")

# Compare against a null distribution (permutation test)
null_scores = []
for _ in range(1000):
    shuffled_tree = tree.copy()
    shuffled_labels = shuffled_tree.cell_meta["cell_type"].sample(frac=1).values
    shuffled_tree.cell_meta["cell_type"] = shuffled_labels
    null_scores.append(cas.tl.score_small_parsimony(shuffled_tree, meta_item="cell_type"))

import numpy as np
pvalue = np.mean(np.array(null_scores) <= parsimony)
print(f"Permutation test p-value: {pvalue:.4f}")
```

### Downstream Analysis: Fate Coupling

```python
# Compute fate coupling matrix (which cell types co-occur in clades)
coupling = cas.tl.compute_morans_i(tree, meta_item="cell_type")

# Alternatively, compute pairwise fate coupling
# This reveals which cell types share recent common ancestors
cas.tl.compute_expansion_pvalues(tree, min_clade_size=10)
```

> [!TIP]
> When integrating lineage tracing with transcriptomic data, you can use CellRank's
> `RealTimeKernel` to combine lineage barcodes with RNA velocity for more accurate
> fate prediction. This is particularly powerful for validating velocity-based
> predictions against ground-truth lineage relationships.

### Best Practices for Lineage Tracing

1. **Assess barcode quality** before tree reconstruction: check diversity, cut rate, and missing data
2. **Use priors** when available: they improve tree accuracy by downweighting common indels
3. **Try multiple solvers** and compare: greedy is fast, but hybrid or ILP may be more accurate
4. **Validate with known biology**: clonal structures should be consistent with expected lineage relationships
5. **Integrate with transcriptomics**: combine lineage trees with gene expression for mechanistic insight

---

## Cross-Method Integration

For the most complete trajectory analysis, combine multiple approaches:

```python
# 1. Pseudotime provides ordering
sc.tl.diffusion_pseudotime(adata)

# 2. RNA velocity provides directionality
scv.tl.velocity(adata, mode="dynamical")
scv.tl.velocity_graph(adata)
scv.tl.latent_time(adata)

# 3. CellRank combines both for fate prediction
vk = cr.kernels.VelocityKernel(adata).compute_transition_matrix()
estimator = cr.estimators.GPCCA(vk)
estimator.fit()
estimator.predict_terminal_states()
estimator.compute_fate_probabilities()

# 4. Compare pseudotime and latent time
import matplotlib.pyplot as plt
fig, ax = plt.subplots()
ax.scatter(adata.obs["dpt_pseudotime"], adata.obs["latent_time"],
           c=adata.obs["cell_type"].cat.codes, s=1, alpha=0.5)
ax.set_xlabel("Diffusion Pseudotime")
ax.set_ylabel("Latent Time (scVelo)")
ax.set_title("Pseudotime vs Latent Time Agreement")
plt.savefig("pseudotime_vs_latent_time.pdf")
plt.show()
```

> [!TIP]
> High correlation between diffusion pseudotime and scVelo latent time increases
> confidence in the inferred trajectory. Discrepancies may highlight regions where
> one method is more appropriate than the other.

## Key References

- **SC Best Practices**: https://www.sc-best-practices.org (Chapters 13-15)
- **DPT**: Haghverdi et al., Nature Methods 2016
- **PAGA**: Wolf et al., Genome Biology 2019
- **scVelo**: Bergen et al., Nature Biotechnology 2020
- **CellRank**: Lange et al., Nature Methods 2022
- **Cassiopeia**: Jones et al., Genome Biology 2020
- **Palantir**: Setty et al., Nature Biotechnology 2019
