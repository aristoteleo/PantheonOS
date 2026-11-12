# Single-cell batch integration

Write `solution.py` defining `integrate(adata) -> embedding`.

**Input.** One `anndata.AnnData` per call: cells x 2000 highly variable genes.
- `adata.X`: log-normalised expression (`log1p` of counts scaled to 10,000 per cell), sparse.
- `adata.layers["counts"]`: the raw counts of the same genes, sparse.
- `adata.obs["batch"]`: the batch (donor, lab or technology) each cell came from.

Cell types are not given.

**Output.** A float array of shape `(n_cells, d)` with `2 <= d <= 100`, one row per cell in input
order: an embedding in which cells of the same type sit together whatever their batch, and
batches mix within each cell type.

**Score.** The scIB benchmark metrics (Luecken et al. 2022), as implemented by `scib-metrics`
with its default metric sets and no min-max scaling:
- bio conservation: isolated labels, KMeans NMI / ARI, label silhouette, cLISI;
- batch correction: batch silhouette (or BRAS), iLISI, kBET, graph connectivity, PCR comparison.

total = 0.6 x bio + 0.4 x batch, averaged over the datasets (real atlases of about 8,000 cells
each: human and mouse, different tissues, batches that are donors, labs or technologies). Higher is better; the starting program (plain PCA,
no correction) is the "unintegrated" baseline. A program that errors, exceeds the time limit
(300 s per dataset, 8 CPU threads, no GPU) or returns a wrong shape scores 0.

**Rules.** Available: numpy, scipy, pandas, scikit-learn, scanpy, anndata, igraph, leidenalg,
pynndescent, numba, jax (CPU). No network, no other packages, nothing read from disk except the
input given. The method must be general: the final score comes from atlases it has never seen -- other
tissues, other species (so other gene names) and other protocols, including single-nucleus data.
